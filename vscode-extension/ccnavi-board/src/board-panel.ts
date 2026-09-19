/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runApprovePreview, runApproveYes } from "./ccnavi.js";
import { buildBoard, isKnownPath, parentTreeOf, phaseChipOf, type Board } from "./core/board.js";
import {
  acceptCommand,
  PUSH_APPROVED_SCRIPT,
  pushApprovedCommand,
  reviewedPrompt,
  type Launcher,
} from "./core/commands.js";
import type { ApprovalOverlay, BoardData, BoardMessage, ToBoard } from "./core/board-view.js";
import { renderBoardPage } from "./core/render.js";
import { ticketControlMismatch } from "./core/ticket-control.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { runInTerminal } from "./terminal.js";
import { webviewScript } from "./webview-script.js";
import { requireTickets, ticketControl } from "./ticket-control.js";

/** ファイルの変化を束ねる待ち時間（ミリ秒）。参考にした拡張と同じ */
const DEBOUNCE_MS = 120;

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  watchers: vscode.FileSystemWatcher[];
  /** 1 枚目の HTML を入れたか。入れた後は、表に出ている間は postMessage で中身だけ渡す */
  htmlSet: boolean;
  timer?: NodeJS.Timeout;
  board?: Board;
  /** 読み直せなかったときの文面。描き直しでオーバーレイだけを載せ替えるために覚えておく */
  error?: string;
  launcher?: Launcher;
  /** 読み直しの最中か。最中にもう 1 回頼まれたら、終わってからもう 1 回だけ走らせる */
  loading: boolean;
  again: boolean;
  wasVisible: boolean;
  /** 次に描いたときに選ぶ絞り込み。1 度使ったら消す（以後は Webview の state が覚える） */
  filter?: string;
  /** 承認のオーバーレイ。あれば描くたびにボードの上に被せる。監視の更新で消えない */
  approval?: ApprovalOverlay;
  /** そのオーバーレイが見せている一覧の絞り（ボードの絞り込みで見えている識別子）。空なら全部 */
  approvalOnly?: readonly string[];
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/**
 * `ccnaviBoard.open` の本体。`project` を渡すと、開いたボードの絞り込みをそのプロジェクトにする
 * （`""` はワークスペース自身、`"*"` は全部）。プロジェクト管理画面からの導線。
 */
export async function openBoard(project?: string): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、ccnavi ボードを表示できない");
    return;
  }
  if (!requireTickets("ボード")) {
    return;
  }
  if (state !== undefined) {
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    state.filter = project;
    void update();
    return;
  }

  // 開く前に 1 度読む。実行ファイルが無い・JSON が読めないなら、ボードを開かずに伝える。
  const first = await loadBoard(folder.uri.fsPath, binSetting());
  if (!first.ok) {
    vscode.window.showErrorMessage(`ccnavi ボードを表示できない: ${first.error}`);
    return;
  }
  // 設定ファイルの読みと実行ファイルの答えが食い違えば言う。判定は実行ファイルの側で動いている。
  const mismatch = ticketControlMismatch(ticketControl(), first.board.settings.ticket_control);
  if (mismatch) {
    vscode.window.showWarningMessage(`ccnavi ボード: ${mismatch}`);
  }

  // 画面は束ねたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript("board.js");
  } catch (error) {
    vscode.window.showErrorMessage(`ccnavi ボードを表示できない: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  const panel = vscode.window.createWebviewPanel("ccnaviBoard", "ccnavi ボード", vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  followAppearance(panel);
  const current: PanelState = {
    panel,
    folder,
    watchers: [],
    htmlSet: false,
    loading: false,
    again: false,
    wasVisible: panel.visible,
    launcher: first.launcher,
    filter: project,
  };
  state = current;
  registerPanelHandlers(current);
  show(current, buildBoard(first.board));
}

/** `ccnaviBoard.refresh` の本体 */
export function refreshBoard(): void {
  if (state === undefined) {
    vscode.window.showInformationMessage("ccnavi ボードが開かれていない");
    return;
  }
  void update();
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    handleMessage(asMessage(message));
  });

  panel.onDidChangeViewState(() => {
    const becameVisible = panel.visible && !current.wasVisible;
    current.wasVisible = panel.visible;
    if (becameVisible) {
      void update();
    }
  });

  panel.onDidDispose(() => {
    if (current.timer !== undefined) {
      clearTimeout(current.timer);
      current.timer = undefined;
    }
    for (const watcher of current.watchers) {
      watcher.dispose();
    }
    current.watchers = [];
    if (state === current) {
      state = undefined;
    }
  });

  for (const pattern of WATCH_PATTERNS) {
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(folder, pattern));
    watcher.onDidCreate(scheduleUpdate);
    watcher.onDidChange(scheduleUpdate);
    watcher.onDidDelete(scheduleUpdate);
    current.watchers.push(watcher);
  }
}

function scheduleUpdate(): void {
  const current = state;
  if (current === undefined) {
    return;
  }
  if (current.timer !== undefined) {
    clearTimeout(current.timer);
  }
  current.timer = setTimeout(() => {
    current.timer = undefined;
    if (state === current) {
      void update();
    }
  }, DEBOUNCE_MS);
}

/** 実行ファイルを走らせ直して Webview の内容を差し替える */
async function update(): Promise<void> {
  const current = state;
  if (current === undefined) {
    return;
  }
  if (current.loading) {
    current.again = true;
    return;
  }
  current.loading = true;
  try {
    const result = await loadBoard(current.folder.uri.fsPath, binSetting());
    if (state !== current) {
      return;
    }
    current.launcher = result.launcher;
    if (!result.ok) {
      // 開いている間の失敗は閉じない。前の表示を消して、何が起きたかを見せる。
      // 承認のオーバーレイは載せ替えて残す。承認した文は取り返しがつかないので、
      // ボードが描けないことを理由に消さない。
      current.board = undefined;
      showError(current, result.error);
      return;
    }
    show(current, buildBoard(result.board));
  } finally {
    current.loading = false;
    if (current.again) {
      current.again = false;
      void update();
    }
  }
}

/**
 * ボードを見せる。中身を渡すのは `send`。
 */
function show(current: PanelState, board: Board): void {
  current.board = board;
  current.error = undefined;
  send(current, { kind: "board", board, approval: current.approval });
}

/** 読み直せなかったことを見せる。承認のオーバーレイがあれば、ボードのときと同じように被せる */
function showError(current: PanelState, error: string): void {
  current.error = error;
  send(current, { kind: "error", error, approval: current.approval });
}

/**
 * いま見せるものを画面に渡す。
 *
 * 1 枚目は HTML ごと入れる。2 枚目からは、表に出ている間は `postMessage` で中身だけ渡し、
 * React に要るところだけ描き直させる。HTML を入れ直すと画面が作り直されるので、開いている
 * オーバーレイの焦点も、畳んだ列も、途中のスクロールも飛ぶ。
 *
 * 裏に回っている間は別で、HTML を入れ直す。`retainContextWhenHidden` が偽なので画面は捨てられていて、
 * postMessage は誰にも届かない。表に戻ったとき VS Code はここで入れた HTML から作り直すので、
 * 入れておけば戻った瞬間に新しい中身が出る。nonce は毎回変わるので、中身が同じでも作り直される。
 */
function send(current: PanelState, data: BoardData): void {
  const filter = current.filter;
  current.filter = undefined;
  if (current.htmlSet && current.panel.visible) {
    const message: ToBoard = { type: "data", data };
    void current.panel.webview.postMessage(message);
    if (filter !== undefined) {
      const pick: ToBoard = { type: "filter", project: filter };
      void current.panel.webview.postMessage(pick);
    }
    return;
  }
  // 1 枚目の絞り込みは HTML に埋めて渡す。postMessage は画面が組み上がる前に出すと届かない
  current.panel.webview.html = renderBoardPage(withFilter(data, filter), {
    nonce: crypto.randomBytes(16).toString("base64"),
    script: webviewScript("board.js"),
    appearance: readAppearance(),
  });
  current.htmlSet = true;
}

function withFilter(data: BoardData, filter: string | undefined): BoardData {
  if (filter === undefined) {
    return data;
  }
  return data.kind === "board" ? { ...data, filter } : { ...data, filter };
}

function handleMessage(message: BoardMessage | undefined): void {
  const current = state;
  if (message === undefined || current === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "ready":
      // 裏から表へ戻って作り直された画面。入れてある HTML は少し古いことがあるので、いまの中身を渡し直す
      redraw(current);
      return;
    case "refresh":
      void update();
      return;
    case "open":
      openTicket(current, message.filePath);
      return;
    case "approve": {
      // 絞り込んでいなければ承認待ち全部。絞り込んでいれば見えている分だけを承認の対象にする。
      // カードの「この 1 件を承認」は、そのカードの識別子だけを絞りとして送ってくる。
      let only: readonly string[] = [];
      if (message.filtered) {
        if (message.tickets.length === 0) {
          vscode.window.showWarningMessage("絞り込みで見えている承認待ちが無い");
          return;
        }
        const tickets = pendingOf(current, message.tickets);
        if (tickets === undefined) {
          vscode.window.showWarningMessage("ボードが古く、承認待ちが変わっている。更新してから承認する");
          void update();
          return;
        }
        only = tickets;
      }
      // 待たない。読み込み中もオーバーレイを出しておき、終わったら描き直す。
      void openApproval(current, only);
      return;
    }
    case "approveConfirm":
      void confirmApproval(current, message.tickets);
      return;
    case "approveCancel":
      if (current.approval?.kind !== "approving") {
        current.approval = undefined;
        redraw(current);
      }
      return;
    case "promptCopy":
    case "promptOpen":
      void handOverPrompt(current, message.type);
      return;
    case "accept": {
      const tree = current.board ? parentTreeOf(current.board, message.parent) : undefined;
      if (tree === undefined) {
        vscode.window.showWarningMessage(`親 ${message.parent} のワークツリーが無いので accept を送れない`);
        return;
      }
      runInTerminal(root, acceptCommand(root, tree, message.phase));
      return;
    }
    case "reviewed": {
      // マーカーは置かない。レビューを終えたことを Claude Code に伝える文を組み、承認の文と同じ
      // オーバーレイ（コピー / 新しいセッションで開く）で渡す。check を打つのは文を受けたエージェント。
      // 承認のオーバーレイ（読み込み中・一覧・承認中・承認した文）の上には被せない。承認した文は取り返せないので、
      // 渡し終えるか閉じるまで消さない。前の連絡（prompt）と読めなかった（error）は差し替えてよい
      if (current.approval !== undefined && current.approval.kind !== "error" && current.approval.kind !== "prompt") {
        return;
      }
      const tree = current.board ? parentTreeOf(current.board, message.parent) : undefined;
      const chip = current.board ? phaseChipOf(current.board, message.parent, message.phase) : undefined;
      if (tree === undefined || chip === undefined) {
        vscode.window.showWarningMessage(`親 ${message.parent} のワークツリーかフェーズ ${message.phase} が無いので、レビュー済みの連絡を組めない`);
        return;
      }
      // ボタンが出る条件（人のレビュー待ち）を受け側でも持つ。待ちでなければ check の前提（依頼のマーカー）が無い
      if (!chip.reviewWaiting) {
        vscode.window.showWarningMessage(`親 ${message.parent} のフェーズ ${chip.label} は人のレビュー待ちではない。ボードを更新する`);
        void update();
        return;
      }
      current.approval = {
        kind: "prompt",
        title: `フェーズ ${chip.label} のレビュー済みを連絡`,
        note: "レビューを終えたことを Claude Code に伝える文を用意した。コピーして進行中のセッションに貼るか、新しいセッションで開く。送るときは自分で Enter を押す。マーカーはエージェントが check を打って置く。",
        prompt: reviewedPrompt(realRoot(root), message.parent, message.phase, chip.label, tree, chip.mrUrl),
      };
      redraw(current);
      return;
    }
  }
}

/**
 * 文面に書くワークスペースルート。実行ファイルの案内（`settings.script_command`）は realpath で解いた綴りを出すので、
 * 同じ綴りにする（macOS の /tmp → /private/tmp など）。解けなければ渡された綴りのまま
 */
function realRoot(root: string): string {
  try {
    return fs.realpathSync.native(root);
  } catch {
    return root;
  }
}

/**
 * いまの状態で描き直す（オーバーレイの出し入れ）。読み直せていないときはエラー画面のほうに
 * 載せ替える。ボードが無いことを理由にここで捨てると、承認した文が人に届かない。
 */
function redraw(current: PanelState): void {
  if (current.board !== undefined) {
    show(current, current.board);
    return;
  }
  if (current.error !== undefined) {
    showError(current, current.error);
  }
}

/**
 * 「承認」。一覧を読んでオーバーレイに出す。承認済みチケットはまだ置かれない。
 * 読んでいる間も「読んでいる…」のオーバーレイを出し、二重に開かない。
 */
async function openApproval(current: PanelState, only: readonly string[] = []): Promise<void> {
  // 承認の途中（読み込み中・一覧・承認中）は二重に開かない。読めなかった・承認した文・レビュー済みの連絡の上には開ける
  if (current.approval !== undefined && current.approval.kind !== "error" && current.approval.kind !== "done" && current.approval.kind !== "prompt") {
    return;
  }
  current.approval = { kind: "loading" };
  // 読み直し（一覧が変わったとき）も同じ絞りを通す。絞りを忘れると、絞り込んで見せた
  // つもりのオーバーレイが承認待ち全部に化ける。
  current.approvalOnly = only;
  redraw(current);
  const result = await runApprovePreview(current.folder.uri.fsPath, binSetting(), only);
  if (state !== current || current.approval?.kind !== "loading") {
    return;
  }
  current.approval = result.ok ? { kind: "preview", preview: result.value } : { kind: "error", error: result.error };
  redraw(current);
}

/**
 * 「この N 件を承認する」。見せた識別子をそのまま `--yes` に渡す。実行ファイルが一覧と本文の一致を
 * 確かめ、違えば何も置かずに `mismatch` を返すので、一覧を読み直して出し直す。
 * 承認できたら、承認済みチケットをコミットして push する sh をターミナルに送り、
 * 同じオーバーレイを「承認した」に切り替え、Claude Code に渡す文を
 * 2 ボタン（コピー / 新しいセッションで開く）で渡す。右下の通知は見落とすので使わない。
 * 押すまで何もしない。ボードの読み直しは承認済みチケットの監視が起こす。
 */
async function confirmApproval(current: PanelState, tickets: readonly string[]): Promise<void> {
  if (current.approval?.kind !== "preview" || tickets.length === 0) {
    return;
  }
  const preview = current.approval.preview;
  current.approval = { kind: "approving", preview };
  redraw(current);
  // 見せたときと同じ絞りを渡す。渡さないと、実行ファイルは絞らないときの対象と比べて食い違いにする。
  // 見せた指紋（承認画面の本文と承認済みチケットに写る中身）も渡す。識別子が同じでも、見せたあとに提案の中身が変われば承認しない。
  const outcome = await runApproveYes(
    current.folder.uri.fsPath,
    binSetting(),
    tickets,
    preview.digest,
    current.approvalOnly ?? [],
  );
  if (state !== current) {
    return;
  }
  if (outcome.ok) {
    const count = outcome.value.approved.length;
    // 文を渡すより先に送る。ボタンは押されるまで待つが、運ぶ 1 行は承認と同じ時点で端末に出しておく。
    const carried = count > 0 && carryApproved(current.folder.uri.fsPath);
    current.approval = { kind: "done", count, prompt: outcome.value.prompt, carried };
    current.approvalOnly = [];
    redraw(current);
    return;
  }
  if ("mismatch" in outcome) {
    // まず同じ絞りで読み直す。カードの「この 1 件を承認」で全部の一覧に切り替わると、1 件のつもりで
    // 押し続けて全部を承認しかねない。絞りが通らない（その識別子がもう承認待ちに無い、親の改版が
    // 承認待ちに入った）ときだけ絞りを外し、いま何が承認待ちなのかを全部見せる。
    const only = current.approvalOnly ?? [];
    let again = await runApprovePreview(current.folder.uri.fsPath, binSetting(), only);
    if (state !== current) {
      return;
    }
    if (!again.ok && only.length > 0) {
      current.approvalOnly = [];
      again = await runApprovePreview(current.folder.uri.fsPath, binSetting());
      if (state !== current) {
        return;
      }
    }
    const sameIds = outcome.mismatch.expected.join(",") === outcome.mismatch.current.join(",");
    const notice = sameIds
      ? "見せた承認画面と今の本文が違った（提案の中身が変わった）。見直してから承認する"
      : "見せた一覧と今の一覧が違った（提案が増えたか減った）。見直してから承認する";
    current.approval = again.ok
      ? { kind: "preview", preview: again.value, notice }
      : { kind: "error", error: again.error };
    redraw(current);
    return;
  }
  current.approval = { kind: "error", error: outcome.error };
  redraw(current);
}

/**
 * 承認済みチケットをコミットして push する sh（`ccnavi-push-approved.sh`）をターミナルに送る。
 * 承認の実行ファイルは承認済みチケットを置くだけで、運ぶのはこの sh。y/N は無い。
 * sh が無ければ送らずに警告し、false を返す。送って `No such file` を見せるより、
 * 何をすればよいかが先に分かる。
 */
function carryApproved(root: string): boolean {
  if (!isFile(path.join(root, PUSH_APPROVED_SCRIPT))) {
    void vscode.window.showWarningMessage(
      `承認済みチケットはまだコミットされていない。${PUSH_APPROVED_SCRIPT} が無いので、導入スクリプト（scripts/ccnavi-setup.sh）で配る`,
    );
    return false;
  }
  runInTerminal(root, pushApprovedCommand(root));
  return true;
}

function isFile(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

/**
 * 承認の文を Claude Code に渡す。走っているセッションに送る公開の API は無いので、
 * クリップボードに入れて進行中のセッションに貼るか、`vscode://anthropic.claude-code/open?prompt=…`
 * で新しいセッションに文を埋める（送信は人が Enter）。
 * 文は拡張が持っている分を使う。Webview から届いた文を URL に埋めない。
 * 渡したらオーバーレイを閉じる。
 */
async function handOverPrompt(current: PanelState, how: "promptCopy" | "promptOpen"): Promise<void> {
  if (current.approval?.kind !== "done" && current.approval?.kind !== "prompt") {
    return;
  }
  const what = current.approval.kind === "done" ? "承認の文" : "レビュー済みの連絡の文";
  const prompt = current.approval.prompt;
  current.approval = undefined;
  redraw(current);
  if (how === "promptCopy") {
    await vscode.env.clipboard.writeText(prompt);
    vscode.window.setStatusBarMessage(`${what}をクリップボードに入れた。Claude Code に貼って送る`, 5000);
  } else {
    await vscode.env.openExternal(vscode.Uri.parse(`vscode://anthropic.claude-code/open?prompt=${encodeURIComponent(prompt)}`));
  }
}

/**
 * ボードが絞り込みで見えている承認待ちとして送ってきた識別子。1 つでもいまのボードで
 * 承認待ちでなければ undefined（ボードが古い）。落として送ると、見せた 2 件のつもりが
 * 1 件になるので、削らずに止める。
 */
function pendingOf(current: PanelState, tickets: readonly string[]): readonly string[] | undefined {
  const pending = new Set(current.board?.pendingApproval ?? []);
  return tickets.every((id) => pending.has(id)) ? tickets : undefined;
}

function openTicket(current: PanelState, filePath: string): void {
  if (current.board === undefined || !isKnownPath(current.board, filePath)) {
    return;
  }
  void showTicketPreview(filePath).catch(() => {
    vscode.window.showInformationMessage(`チケットのファイルを開けなかった: ${filePath}`);
    void update();
  });
}

/**
 * チケットは Markdown なので、素のテキストではなくプレビューで見せる。
 * プレビューは組み込みの Markdown 拡張のもので、無いファイルを渡しても失敗を返さない前提で書いている
 * （ここからは確かめられないので、README の手動確認で見る）。だから先に在るかを確かめ、無ければ
 * 呼び手に失敗を返す（通知して、ボードを読み直す）。確かめてから描くまでに消えた場合は拾えない。
 * プレビューのコマンドが無い環境（組み込みの Markdown 拡張が無効）では、これまでどおりエディタで開く。
 */
async function showTicketPreview(filePath: string): Promise<void> {
  const uri = vscode.Uri.file(filePath);
  await vscode.workspace.fs.stat(uri);
  try {
    await vscode.commands.executeCommand("markdown.showPreview", uri);
  } catch {
    const document = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(document);
  }
}

function asMessage(message: unknown): BoardMessage | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as {
    type?: unknown;
    filePath?: unknown;
    parent?: unknown;
    phase?: unknown;
    tickets?: unknown;
    filtered?: unknown;
  };
  switch (m.type) {
    case "ready":
    case "refresh":
    case "approveCancel":
    case "promptCopy":
    case "promptOpen":
      return { type: m.type };
    case "approve":
      // 形が崩れていたら捨てる。「全部承認」に丸めると、検証の失敗が広がる向きに倒れる。
      return Array.isArray(m.tickets) &&
        m.tickets.every((t) => typeof t === "string") &&
        typeof m.filtered === "boolean"
        ? { type: "approve", tickets: m.tickets, filtered: m.filtered }
        : undefined;
    case "approveConfirm":
      return Array.isArray(m.tickets) && m.tickets.every((t) => typeof t === "string")
        ? { type: "approveConfirm", tickets: m.tickets as string[] }
        : undefined;
    case "open":
      return typeof m.filePath === "string" ? { type: "open", filePath: m.filePath } : undefined;
    case "accept":
    case "reviewed":
      return typeof m.parent === "string" && typeof m.phase === "number" && Number.isInteger(m.phase)
        ? { type: m.type, parent: m.parent, phase: m.phase }
        : undefined;
    default:
      return undefined;
  }
}
