/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

import { followAppearance, postAppearance, readAppearance } from "./appearance.js";
import { loadBoard, runApprovePreview, runApproveYes, runDecidePreview, runDecideYes } from "./ccnavi.js";
import { buildBoard, flowCardOf, isKnownPath, parentTreeOf, phaseChipOf, type Board } from "./core/board.js";
import { flowTicketOf } from "./core/flow-view.js";
import { screens } from "./core/screens.js";
import { movedStep, NOTHING_MOVED, type MovedState } from "./core/board-moved.js";
import {
  PUSH_APPROVED_SCRIPT,
  pushApprovedCommand,
  type Launcher,
} from "./core/commands.js";
import {
  approvalStep,
  CLOSED,
  type ApprovalEffect,
  type ApprovalInput,
  type ApprovalState,
} from "./core/approval-machine.js";
import type { BoardData, BoardMessage, ToBoard } from "./core/board-view.js";
import { renderBoardPage } from "./core/render.js";
import { showLoading } from "./loading.js";
import { screenHost, type ScreenHost } from "./core/screen-host.js";
import { ticketControlMismatch } from "./core/ticket-control.js";
import { WATCH_PATTERNS } from "./core/watch.js";
import { runInTerminal, scriptShell } from "./terminal.js";
import { markTourSeen, tourSeen } from "./tour.js";
import { webviewScript, webviewStyle } from "./webview-asset.js";
import { requireTickets, ticketControl } from "./ticket-control.js";

/** 画面の名前。束ねの綴りは `src/webview/<名前>/main.tsx` → `out/webview/<名前>.js`、`style.css` → `<名前>.css` */
const SCREEN = "board";
/** ファイルの変化を束ねる待ち時間（ミリ秒）。参考にした拡張と同じ */
const DEBOUNCE_MS = 120;
const TITLE = "ccnavi チケット管理";

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  watchers: vscode.FileSystemWatcher[];
  /** 画面に中身を渡す段取り（`core/screen-host.ts`）。いつ送れるかはここが持つ */
  readonly host: ScreenHost<BoardData>;
  timer?: NodeJS.Timeout;
  board?: Board;
  /** 読み直せなかったときの文面。描き直しでオーバーレイだけを載せ替えるために覚えておく */
  error?: string;
  launcher?: Launcher;
  /** 読み直しの最中か。最中にもう 1 回頼まれたら、終わってからもう 1 回だけ走らせる */
  loading: boolean;
  again: boolean;
  wasVisible: boolean;
  /** 設定ファイルの読みと実行ファイルの答えの食い違いを確かめたか。確かめるのは最初に読めた 1 度だけ */
  checked: boolean;
  /** 次に描いたときに選ぶ絞り込み。1 度使ったら消す（以後は Webview の state が覚える） */
  filter?: string;
  /**
   * 承認のオーバーレイ。あれば描くたびにボードの上に被せる（監視の更新で消えない）。
   * 遷移の規則は `core/approval-machine.ts` が持ち、ここは持ち直すだけ
   */
  approval: ApprovalState;
  /**
   * 前の読み直しから動いたカード。**画面ではなくここが持つ。** 画面は裏に回ると捨てられ、
   * 表に戻ると作り直されるので（`retainContextWhenHidden` は偽）、そちらに持たせると
   * 承認の文を渡してボードに戻った瞬間に印が消える。決めるのは `core/board-moved.ts`
   */
  moved: MovedState;
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/**
 * `ccnaviBoard.open` の本体。`project` を渡すと、開いたボードの絞り込みをそのプロジェクトにする
 * （`""` はワークスペース（プロジェクト外）、`"*"` は全部）。プロジェクト管理画面からの導線。
 */
export async function openBoard(project?: string): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、チケット管理画面を表示できません");
    return;
  }
  if (!requireTickets("チケット管理画面")) {
    return;
  }
  if (state !== undefined) {
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    state.filter = project;
    void update();
    return;
  }

  // 画面と CSS は束ねたものを読んで流し込む。無ければ開かずに言う（パネルだけ出しても白いまま）
  try {
    webviewScript(SCREEN);
    webviewStyle(SCREEN);
  } catch (error) {
    vscode.window.showErrorMessage(`チケット管理画面を表示できません: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }

  // タブは読む前に作る。実行ファイルの答えを待ってから作ると、押しても何も起きないように見え、
  // 押し直した分だけタブが増える（`state` を先に立てるので、2 度目の押下は下の `reveal` に入る）。
  // 読めなかったときもタブは閉じず、中にエラーを出す（`update` の `showError`）
  const panel = vscode.window.createWebviewPanel("ccnaviBoard", TITLE, vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  showLoading(panel, TITLE, SCREEN, "チケット");
  const current: PanelState = {
    panel,
    folder,
    watchers: [],
    host: boardHost(panel),
    loading: false,
    again: false,
    wasVisible: panel.visible,
    checked: false,
    filter: project,
    approval: CLOSED,
    moved: NOTHING_MOVED,
  };
  state = current;
  followAppearance(panel, current.host);
  registerPanelHandlers(current);
  void update();
}

/** `ccnaviBoard.refresh` の本体 */
export function refreshBoard(): void {
  if (state === undefined) {
    vscode.window.showInformationMessage("チケット管理画面が開かれていません");
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
    if (!panel.visible) {
      // `retainContextWhenHidden` は偽なので、裏に回った画面は捨てられる。表に戻ると
      // ここで入れてある HTML から作り直され、組み上がったら `ready` が届く
      current.host.hidden();
    }
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
    if (result.ok && !current.checked) {
      current.checked = true;
      // 設定ファイルの読みと実行ファイルの答えが食い違えば言う。判定は実行ファイルの側で動いている。
      const mismatch = ticketControlMismatch(ticketControl(), result.board.settings.ticket_control);
      if (mismatch) {
        vscode.window.showWarningMessage(`チケット管理: ${mismatch}`);
      }
    }
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
 *
 * **読めたボードはここを必ず通る**ので、動いたカードもここで数え直す。同じボードを渡し直すだけの
 * 描き直し（オーバーレイの出し入れ）でも通るが、列が動いていなければ `movedStep` が前の印を
 * そのまま返すので、承認の文を閉じた拍子に印が消えることはない。
 */
function show(current: PanelState, board: Board): void {
  current.moved = movedStep(current.moved, board);
  current.board = board;
  current.error = undefined;
  send(current, { kind: "board", board, approval: current.approval.overlay, moved: current.moved.moved });
}

/** 読み直せなかったことを見せる。承認のオーバーレイがあれば、ボードのときと同じように被せる */
function showError(current: PanelState, error: string): void {
  current.error = error;
  send(current, { kind: "error", error, approval: current.approval.overlay });
}

/**
 * いま見せるものを画面に渡す。どう渡るか（送る・入れ物ごと・作り直し中で持ち越し）は
 * `screenHost` が決めて返す。ここが持つのは「1 度きりの絞り込みをいつ消すか」だけ。
 */
function send(current: PanelState, data: BoardData): void {
  const filter = current.filter;
  // 入れ物ごと入れ直す道になったときだけ、絞り込みを埋めたほうが使われる
  const delivery = current.host.send(data, withFilter(data, filter));
  if (delivery === "rebuilt") {
    current.filter = undefined;
    return;
  }
  if (delivery === "deferred") {
    // 作り直している最中。組み上がったら `ready` が届くので、そこで渡し直す（絞り込みも持ち越す）
    return;
  }
  // 届いたときだけ消す。届かないまま消すと「このプロジェクトで絞って開く」が二度と渡らない
  if (filter !== undefined && current.host.post({ type: "filter", project: filter } satisfies ToBoard)) {
    current.filter = undefined;
  }
}

/** 開いた直後に選ぶ絞り込み。入れ物に埋めて渡すときだけ使う */
function withFilter(data: BoardData, filter: string | undefined): BoardData {
  return filter === undefined ? data : { ...data, filter };
}

/**
 * ボードの画面に渡す口。VS Code のパネルを `screenHost` の形に合わせる。
 * nonce は呼ぶたびに変える（同じ文字列を `webview.html` に入れても VS Code は何もしない）。
 */
function boardHost(panel: vscode.WebviewPanel): ScreenHost<BoardData> {
  return screenHost<BoardData>(
    {
      get visible(): boolean {
        return panel.visible;
      },
      html(text: string): void {
        panel.webview.html = text;
      },
      post(message: unknown): void {
        void panel.webview.postMessage(message);
      },
    },
    (data) =>
      renderBoardPage(data, {
        nonce: crypto.randomBytes(16).toString("base64"),
        script: webviewScript(SCREEN),
        style: webviewStyle(SCREEN),
        appearance: readAppearance(),
      }),
  );
}

function handleMessage(message: BoardMessage | undefined): void {
  const current = state;
  if (message === undefined || current === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "ready":
      // 画面が組み上がった。入れてある HTML は少し古いことがあるので、いまの中身を渡し直す。
      // 作り直している間に見送った更新（send）も、絞り込みも、ここで届く
      current.host.ready();
      redraw(current);
      // 裏にいる間に見た目が変わっていたら、入れてある HTML の body のクラスは古い。
      // `followAppearance` がそのとき送ったものは、段取りが「送れない」と見て落としている
      postAppearance(current.host);
      // 初回だけ吹き出しの案内を頼む。画面は指す先が出てから始め、閉じたら `tourDone` を返す。
      // 閉じずにタブを閉じたら印は残らないので、次に開いたときにもう 1 度出る
      if (!tourSeen(SCREEN)) {
        current.host.post({ type: "tour" } satisfies ToBoard);
      }
      return;
    case "tourDone":
      markTourSeen(SCREEN);
      return;
    case "refresh":
      void update();
      return;
    case "open":
      openTicket(current, message.filePath);
      return;
    case "approve":
      // 絞り込んでいなければ承認待ち全部。絞り込んでいれば見えている分だけを承認の対象にする。
      // カードの「この 1 件を承認」は、そのカードの識別子だけを絞りとして送ってくる。
      // 突き合わせる相手（いまのボードの承認待ち）を添えて渡し、決めるのは遷移の側
      dispatch(current, {
        kind: "approve",
        tickets: message.tickets,
        filtered: message.filtered,
        pending: current.board?.pendingApproval ?? [],
      });
      return;
    case "approveConfirm":
      dispatch(current, { kind: "confirm", tickets: message.tickets });
      return;
    case "approveCancel":
      dispatch(current, { kind: "cancel" });
      return;
    case "promptCopy":
    case "promptOpen":
      dispatch(current, { kind: "handOver", how: message.type });
      return;
    case "decide":
      // 残った指摘の行き先を決めるオーバーレイを開く。sh が指摘を取ってきて、人が指摘ごとに選ぶ。
      // ボードから引くもの（親のワークツリー・フェーズ）を添えて渡し、開いてよいかは遷移の側が決める
      dispatch(current, {
        kind: "decide",
        parent: message.parent,
        phase: message.phase,
        tree: current.board ? parentTreeOf(current.board, message.parent) : undefined,
        chip: current.board ? phaseChipOf(current.board, message.parent, message.phase) : undefined,
      });
      return;
    case "decideConfirm":
      dispatch(current, { kind: "decideConfirm", choices: message.choices });
      return;
    case "reviewed":
      // マーカーは置かない。レビューを終えたことを Claude Code に伝える文を組み、承認の文と同じ
      // オーバーレイ（コピー / 新しいセッションで開く）で渡す。confirm を打つのは文を受けたエージェント。
      // ボードから引くもの（親のワークツリー・フェーズ）を添えて渡し、被せてよいかは遷移の側が決める
      dispatch(current, {
        kind: "reviewed",
        parent: message.parent,
        phase: message.phase,
        tree: current.board ? parentTreeOf(current.board, message.parent) : undefined,
        chip: current.board ? phaseChipOf(current.board, message.parent, message.phase) : undefined,
        root: realRoot(root),
      });
      return;
    case "flow":
      // 画面が言った識別子をそのまま信じず、いまのボードに子のカードとして在るものだけを開く
      if (current.board !== undefined && flowCardOf(current.board, message.ticket) !== undefined) {
        void screens().flow(message.ticket);
      }
      return;
    default: {
      // `BoardMessage` に操作を足したのに、ここに処理を書いていなければ型が合わなくなる。
      // 画面のボタンだけ足して受け側を忘れる、を止める
      const unhandled: never = message;
      void unhandled;
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
 * 承認のオーバーレイに 1 つ入力を入れる。**遷移の規則はここに書かない**
 * （`core/approval-machine.ts`。VS Code に触れないので単体で試せる）。
 *
 * ここがするのは 3 つだけ。返った状態を持ち直す、描き直す、やることを行う。**順を変えない**:
 * 描き直しが先で、やることが後（逆にすると、文を渡すときに画面が古いまま残る）。
 */
function dispatch(current: PanelState, input: ApprovalInput): void {
  const step = approvalStep(current.approval, input);
  current.approval = step.state;
  if (step.redraw) {
    redraw(current);
  }
  for (const effect of step.effects) {
    void runEffect(current, effect);
  }
}

/**
 * 遷移が返した「やること」を行う。外へ出るのはここだけ（実行ファイル・端末・クリップボード・
 * 新しいセッション・通知）。返事が要るもの（一覧と承認の結果）は、返ってきたらまた `dispatch` に入れる。
 *
 * **返事を入れる前に、パネルがまだ同じかを見る。** 開き直された後のパネルに、前のパネルの
 * 承認の結果を入れない。
 */
async function runEffect(current: PanelState, effect: ApprovalEffect): Promise<void> {
  const root = current.folder.uri.fsPath;
  switch (effect.kind) {
    case "loadPreview": {
      const result = await runApprovePreview(root, binSetting(), effect.only);
      if (state === current) {
        dispatch(current, { kind: "previewed", result });
      }
      return;
    }
    case "approve": {
      // 見せたときと同じ絞りを渡す。渡さないと、実行ファイルは絞らないときの対象と比べて食い違いにする。
      // 見せた指紋（承認画面の本文と承認済みチケットに写る中身）も渡す。識別子が同じでも、
      // 見せたあとに提案の中身が変われば承認しない
      const outcome = await runApproveYes(root, binSetting(), effect.tickets, effect.digest, effect.only);
      if (state === current) {
        // 運ぶ sh があるかは、承認が返ったこの時点で見る
        dispatch(current, { kind: "approved", outcome, carrier: isFile(path.join(root, PUSH_APPROVED_SCRIPT)) });
      }
      return;
    }
    // 承認の実行ファイルは承認済みチケットを置くだけで、運ぶ（コミットして push する）のはこの sh
    case "carry":
      runInTerminal(root, pushApprovedCommand(root));
      return;
    case "copy":
      await vscode.env.clipboard.writeText(effect.prompt);
      vscode.window.setStatusBarMessage(`${effect.what}をコピーしました。Claude Code に貼って送ってください`, 5000);
      return;
    case "openSession":
      // 走っているセッションに送る公開の API は無いので、文を埋めて新しいセッションを開く（送信は人が Enter）
      await vscode.env.openExternal(
        vscode.Uri.parse(`vscode://anthropic.claude-code/open?prompt=${encodeURIComponent(effect.prompt)}`),
      );
      return;
    case "loadDecide": {
      const result = await runDecidePreview(root, scriptShell(), effect.tree, effect.phase);
      if (state === current) {
        dispatch(current, { kind: "decidePreviewed", result });
      }
      return;
    }
    case "decide": {
      const outcome = await runDecideYes(
        root,
        scriptShell(),
        effect.tree,
        effect.phase,
        effect.choices,
        effect.digest,
      );
      if (state === current) {
        dispatch(current, { kind: "decided", outcome });
      }
      return;
    }
    case "warn":
      vscode.window.showWarningMessage(effect.text);
      return;
    case "refresh":
      void update();
      return;
    default: {
      // `ApprovalEffect` に足したのに、ここに書いていなければ型が合わなくなる
      const unhandled: never = effect;
      void unhandled;
      return;
    }
  }
}

/** ファイルとして在るか。無いものを読もうとして投げるのは「無い」として扱う */
function isFile(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function openTicket(current: PanelState, filePath: string): void {
  if (current.board === undefined || !isKnownPath(current.board, filePath)) {
    return;
  }
  void showTicketPreview(filePath).catch(() => {
    vscode.window.showInformationMessage(`チケットのファイルを開けませんでした: ${filePath}`);
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

/**
 * 形を確かめる操作の一覧。**`BoardMessage` に足したのにここへ足していなければ、型が合わなくなる。**
 * `handleMessage` の網羅検査（`never`）は処理の書き忘れしか止めないので、入口の側でも同じことをする。
 * 足し忘れると、画面のボタンは押せるのに、届いたものが黙って捨てられる。
 */
const KNOWN: Readonly<Record<BoardMessage["type"], true>> = {
  ready: true,
  refresh: true,
  open: true,
  approve: true,
  approveConfirm: true,
  approveCancel: true,
  promptCopy: true,
  promptOpen: true,
  decide: true,
  decideConfirm: true,
  reviewed: true,
  flow: true,
  tourDone: true,
};

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
    choices?: unknown;
    ticket?: unknown;
  };
  if (typeof m.type !== "string" || !(m.type in KNOWN)) {
    return undefined;
  }
  switch (m.type) {
    case "ready":
    case "refresh":
    case "tourDone":
    case "approveCancel":
    case "promptCopy":
    case "promptOpen":
      return { type: m.type };
    case "approve":
      // 形が崩れていたら捨てる。「全部承認」に丸めると、検証の失敗が広がる向きになる。
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
    case "decideConfirm":
      // 値が文字列でない選択は捨てる。行き先が見せた指摘の全部に付いているかは遷移の側が見る
      return typeof m.choices === "object" &&
        m.choices !== null &&
        !Array.isArray(m.choices) &&
        Object.values(m.choices).every((v) => typeof v === "string")
        ? { type: "decideConfirm", choices: { ...(m.choices as Record<string, string>) } }
        : undefined;
    case "decide":
    case "reviewed":
      return typeof m.parent === "string" && typeof m.phase === "number" && Number.isInteger(m.phase)
        ? { type: m.type, parent: m.parent, phase: m.phase }
        : undefined;
    case "flow": {
      const ticket = flowTicketOf(m);
      return ticket === undefined ? undefined : { type: "flow", ticket };
    }
    default:
      return undefined;
  }
}
