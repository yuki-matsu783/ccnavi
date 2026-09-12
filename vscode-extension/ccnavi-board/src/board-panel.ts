/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { loadBoard } from "./ccnavi.js";
import { buildBoard, isKnownPath, parentTreeOf, type Board } from "./core/board.js";
import { acceptCommand, approveCommand, type Launcher } from "./core/commands.js";
import { escapeHtml, renderBoard } from "./core/render.js";
import { TICKET_CONTROL_ENV, ticketControlMismatch } from "./core/ticket-control.js";
import { runInTerminal } from "./terminal.js";
import { ticketControl } from "./ticket-control.js";

/** ファイルの変化を束ねる待ち時間（ミリ秒）。参考にした拡張と同じ */
const DEBOUNCE_MS = 120;

/**
 * 監視する場所。提案（main と全作業ツリー、プロジェクト向けの置き場も）、写しと印、
 * 作業ツリーの登録。glob は OS によらず "/" 区切り。
 */
export const WATCH_PATTERNS = [
  "wip/**/tickets/**",
  ".claude/worktrees/*/wip/**/tickets/**",
  ".claude/ccnavi/tickets/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;

type Message =
  | { readonly type: "open"; readonly filePath: string }
  | { readonly type: "refresh" }
  | { readonly type: "approve"; readonly tickets: readonly string[] }
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

/** `ccnaviBoard.approve` の本体。ボードが開いていなくても打てる */
export function approveFromPalette(): void {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていない");
    return;
  }
  if (!ticketsEnabled()) {
    return;
  }
  sendApprove(folder.uri.fsPath, state?.launcher);
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
      const tickets = pendingOf(current, message.tickets);
      if (tickets === undefined) {
        vscode.window.showWarningMessage("ボードが古く、承認待ちが変わっている。更新してから承認する");
        void update();
        return;
      }
      sendApprove(root, current.launcher, tickets);
      return;
    }
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

/**
 * ボードが絞り込みで見えている承認待ちとして送ってきた識別子。空は「絞り込み無し」で、
 * 承認待ち全部を束にする。1 つでもいまのボードで承認待ちでなければ undefined（ボードが古い）。
 * 落として送ると、見せた 2 件のつもりが 1 件や全部になるので、削らずに止める。
 */
function pendingOf(current: PanelState, tickets: readonly string[]): readonly string[] | undefined {
  const pending = new Set(current.board?.pendingApproval ?? []);
  return tickets.every((id) => pending.has(id)) ? tickets : undefined;
}

function sendApprove(root: string, launcher: Launcher | undefined, tickets: readonly string[] = []): void {
  if (launcher === undefined) {
    vscode.window.showErrorMessage("ccnavi の実行ファイルが見つからないので --approve を送れない");
    return;
  }
  runInTerminal(root, approveCommand(launcher, root, tickets));
}

function openTicket(current: PanelState, filePath: string): void {
  if (current.board === undefined || !isKnownPath(current.board, filePath)) {
    return;
  }
  void vscode.workspace.openTextDocument(filePath).then(
    (document) => vscode.window.showTextDocument(document),
    () => {
      vscode.window.showInformationMessage(`チケットのファイルを開けなかった: ${filePath}`);
      void update();
    },
  );
}

function asMessage(message: unknown): Message | undefined {
  if (typeof message !== "object" || message === null) {
    return undefined;
  }
  const m = message as { type?: unknown; filePath?: unknown; parent?: unknown; phase?: unknown; tickets?: unknown };
  switch (m.type) {
    case "refresh":
      return { type: m.type };
    case "approve":
      return Array.isArray(m.tickets) && m.tickets.every((t) => typeof t === "string")
        ? { type: "approve", tickets: m.tickets }
        : { type: "approve", tickets: [] };
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
