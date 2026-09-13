/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { loadBoard, runApprovePreview, runApproveYes } from "./ccnavi.js";
import { buildBoard, isKnownPath, parentTreeOf, type Board } from "./core/board.js";
import { acceptCommand, type Launcher } from "./core/commands.js";
import { escapeHtml, renderBoard, type ApprovalOverlay } from "./core/render.js";
import { TICKET_CONTROL_ENV, ticketControlMismatch } from "./core/ticket-control.js";
import { runInTerminal } from "./terminal.js";
import { ticketControl } from "./ticket-control.js";

/** ファイルの変化を束ねる待ち時間（ミリ秒）。参考にした拡張と同じ */
const DEBOUNCE_MS = 120;

/**
 * 監視する場所。提案（ワークスペース、プロジェクト、全作業ツリーの `wip/tickets/`）、
 * 承認済みチケットと印（同じツリーの `.ccnavi/tickets/`）、作業ツリーの登録。
 * glob は OS によらず "/" 区切り。
 */
export const WATCH_PATTERNS = [
  "wip/**/tickets/**",
  "projects/*/wip/**/tickets/**",
  ".claude/worktrees/*/wip/**/tickets/**",
  ".ccnavi/tickets/**",
  "projects/*/.ccnavi/tickets/**",
  ".claude/worktrees/*/.ccnavi/tickets/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;

type Message =
  | { readonly type: "open"; readonly filePath: string }
  | { readonly type: "refresh" }
  | { readonly type: "approve"; readonly tickets: readonly string[]; readonly filtered: boolean }
  | { readonly type: "approveConfirm"; readonly tickets: readonly string[] }
  | { readonly type: "approveCancel" }
  | { readonly type: "accept"; readonly parent: string; readonly phase: number };

interface PanelState {
  readonly panel: vscode.WebviewPanel;
  readonly folder: vscode.WorkspaceFolder;
  watchers: vscode.FileSystemWatcher[];
  timer?: NodeJS.Timeout;
  board?: Board;
  launcher?: Launcher;
  /** 読み直しの最中か。最中にもう 1 回頼まれたら、終わってからもう 1 回だけ走らせる */
  loading: boolean;
  again: boolean;
  wasVisible: boolean;
  /** 次に描いたときに選ぶ絞り込み。1 度使ったら消す（以後は Webview の state が覚える） */
  filter?: string;
  /** 承認のオーバーレイ。あれば描くたびにボードの上に被せる。監視の更新で消えない */
  approval?: ApprovalOverlay;
  /** そのオーバーレイが見せている束の絞り（ボードの絞り込みで見えている識別子）。空なら全部 */
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
  if (!ticketsEnabled()) {
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

  const panel = vscode.window.createWebviewPanel("ccnaviBoard", "ccnavi ボード", vscode.ViewColumn.One, {
    enableScripts: true,
    enableForms: false,
    localResourceRoots: [],
    retainContextWhenHidden: false,
  });
  const current: PanelState = {
    panel,
    folder,
    watchers: [],
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

/**
 * チケット制御が disable なら、その旨を伝えて偽を返す。コマンドパレットは `when` で隠れるが、
 * キーバインドや他の拡張からの呼び出しはそこを通らない。
 */
function ticketsEnabled(): boolean {
  if (ticketControl() === "enable") {
    return true;
  }
  vscode.window.showInformationMessage(
    `このワークスペースはチケット制御を使っていない（${TICKET_CONTROL_ENV}=disable）。ルール管理だけが使える`,
  );
  return false;
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
      current.board = undefined;
      current.panel.webview.html = renderError(result.error);
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

function show(current: PanelState, board: Board): void {
  current.board = board;
  current.panel.webview.html = renderBoard(board, {
    nonce: crypto.randomBytes(16).toString("base64"),
    approval: current.approval,
  });
  if (current.filter !== undefined) {
    // HTML の差し替えの後に届く。Webview の中のスクリプトが select を合わせる。
    void current.panel.webview.postMessage({ type: "filter", project: current.filter });
    current.filter = undefined;
  }
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi ボード</title></head><body><p>ボードを読み直せなかった。直してから「ccnavi ボード: ボードを更新」を実行する。</p><pre>${escapeHtml(error)}</pre></body></html>`;
}

function handleMessage(message: Message | undefined): void {
  const current = state;
  if (message === undefined || current === undefined) {
    return;
  }
  const root = current.folder.uri.fsPath;
  switch (message.type) {
    case "refresh":
      void update();
      return;
    case "open":
      openTicket(current, message.filePath);
      return;
    case "approve": {
      // 絞り込んでいなければ承認待ち全部。絞り込んでいれば見えている分だけを束にする。
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
    case "accept": {
      const tree = current.board ? parentTreeOf(current.board, message.parent) : undefined;
      if (tree === undefined) {
        vscode.window.showWarningMessage(`親 ${message.parent} の作業ツリーが無いので accept を送れない`);
        return;
      }
      runInTerminal(root, acceptCommand(tree, message.phase));
      return;
    }
  }
}

/** いまの状態でボードを描き直す（オーバーレイの出し入れ） */
function redraw(current: PanelState): void {
  if (current.board !== undefined) {
    show(current, current.board);
  }
}

/**
 * 「承認」。束を読んでオーバーレイに出す。承認済みチケットはまだ置かれない。
 * 読んでいる間も「読んでいる…」のオーバーレイを出し、二重に開かない。
 */
async function openApproval(current: PanelState, only: readonly string[] = []): Promise<void> {
  if (current.approval !== undefined && current.approval.kind !== "error") {
    return;
  }
  current.approval = { kind: "loading" };
  // 読み直し（束が変わったとき）も同じ絞りを通す。絞りを忘れると、絞り込んで見せた
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
 * 「この N 件を承認する」。見せた識別子をそのまま `--yes` に渡す。実行ファイルが束の一致を
 * 確かめ、違えば何も置かずに `mismatch` を返すので、束を読み直して出し直す。
 * 承認できたら、Claude Code に渡す文を通知の 2 ボタン（コピー / 新しいセッションで開く）で渡す。
 * 押すまで何もしない。ボードの読み直しは承認済みチケットの監視が起こす。
 */
async function confirmApproval(current: PanelState, tickets: readonly string[]): Promise<void> {
  if (current.approval?.kind !== "preview" || tickets.length === 0) {
    return;
  }
  const preview = current.approval.preview;
  current.approval = { kind: "approving", preview };
  redraw(current);
  // 見せたときと同じ絞りを渡す。渡さないと、実行ファイルは絞らない束と比べて食い違いにする。
  const outcome = await runApproveYes(
    current.folder.uri.fsPath,
    binSetting(),
    tickets,
    current.approvalOnly ?? [],
  );
  if (state !== current) {
    return;
  }
  if (outcome.ok) {
    current.approval = undefined;
    redraw(current);
    void offerPrompt(outcome.value.approved.length, outcome.value.prompt);
    return;
  }
  if ("mismatch" in outcome) {
    // 絞りは外す。束が変わったのだから、いま何が承認待ちなのかを全部見せる。
    current.approvalOnly = [];
    const again = await runApprovePreview(current.folder.uri.fsPath, binSetting());
    if (state !== current) {
      return;
    }
    current.approval = again.ok
      ? { kind: "preview", preview: again.value, notice: "見せた束と今の束が違った（提案が増えたか減った）。見直してから承認する" }
      : { kind: "error", error: again.error };
    redraw(current);
    return;
  }
  current.approval = { kind: "error", error: outcome.error };
  redraw(current);
}

const COPY_PROMPT = "コピー";
const OPEN_PROMPT = "新しいセッションで開く";

/**
 * 承認の文を Claude Code に渡す。走っているセッションに送る公開の API は無いので、
 * クリップボードに入れて進行中のセッションに貼るか、`vscode://anthropic.claude-code/open?prompt=…`
 * で新しいセッションに文を埋める（送信は人が Enter）。
 */
async function offerPrompt(count: number, prompt: string): Promise<void> {
  const chosen = await vscode.window.showInformationMessage(
    `${count} 件を承認した。Claude Code に伝える文を用意した`,
    COPY_PROMPT,
    OPEN_PROMPT,
  );
  if (chosen === COPY_PROMPT) {
    await vscode.env.clipboard.writeText(prompt);
    vscode.window.setStatusBarMessage("承認の文をクリップボードに入れた。Claude Code に貼って送る", 5000);
  } else if (chosen === OPEN_PROMPT) {
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

function asMessage(message: unknown): Message | undefined {
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
    case "refresh":
    case "approveCancel":
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
      return typeof m.parent === "string" && typeof m.phase === "number" && Number.isInteger(m.phase)
        ? { type: "accept", parent: m.parent, phase: m.phase }
        : undefined;
    default:
      return undefined;
  }
}
