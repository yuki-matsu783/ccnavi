/**
 * Webview パネルの生成・更新・破棄と、ファイル監視、Webview からの操作の受け付け。
 * VS Code の API に触れるので単体テストの対象外。README の手動確認の手順で確かめる。
 */
import * as crypto from "node:crypto";
import * as vscode from "vscode";

import { loadBoard } from "./ccnavi.js";
import { buildBoard, isKnownPath, parentTreeOf, type Board } from "./core/board.js";
import { acceptCommand, approveCommand, wrapupCommand, type Launcher } from "./core/commands.js";
import { escapeHtml, renderBoard } from "./core/render.js";
import { runInTerminal } from "./terminal.js";

/** ファイルの変化を束ねる待ち時間（ミリ秒）。参考にした拡張と同じ */
const DEBOUNCE_MS = 120;

/**
 * 監視する場所。提案（main と全作業ツリー、プロジェクト向けの置き場も）、写しと印、
 * 作業ツリーの登録。glob は OS によらず "/" 区切り。
 */
const WATCH_PATTERNS = [
  "wip/**/tickets/**",
  ".claude/worktrees/*/wip/**/tickets/**",
  ".claude/ccnavi/tickets/**",
  ".git/worktrees/*",
  "projects/*/.git/worktrees/*",
] as const;

type Message =
  | { readonly type: "open"; readonly filePath: string }
  | { readonly type: "refresh" }
  | { readonly type: "approve" }
  | { readonly type: "accept"; readonly parent: string; readonly phase: number }
  | { readonly type: "wrapup"; readonly parent: string };

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
}

let state: PanelState | undefined;

function binSetting(): string {
  return vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
}

/** `ccnaviBoard.open` の本体 */
export async function openBoard(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    vscode.window.showInformationMessage("ワークスペースが開かれていないため、ccnavi ボードを表示できない");
    return;
  }
  if (state !== undefined) {
    state.wasVisible = true;
    state.panel.reveal(state.panel.viewColumn);
    void update();
    return;
  }

  // 開く前に 1 度読む。実行ファイルが無い・JSON が読めないなら、ボードを開かずに伝える。
  const first = await loadBoard(folder.uri.fsPath, binSetting());
  if (!first.ok) {
    vscode.window.showErrorMessage(`ccnavi ボードを表示できない: ${first.error}`);
    return;
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
  sendApprove(folder.uri.fsPath, state?.launcher);
}

function registerPanelHandlers(current: PanelState): void {
  const { panel, folder } = current;

  panel.webview.onDidReceiveMessage((message: unknown) => {
    void handleMessage(asMessage(message));
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
}

function renderError(error: string): string {
  return `<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none';"><title>ccnavi ボード</title></head><body><p>ボードを読み直せなかった。直してから「ccnavi ボード: ボードを更新」を実行する。</p><pre>${escapeHtml(error)}</pre></body></html>`;
}

async function handleMessage(message: Message | undefined): Promise<void> {
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
    case "approve":
      sendApprove(root, current.launcher);
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
    case "wrapup": {
      const tree = current.board ? parentTreeOf(current.board, message.parent) : undefined;
      if (tree === undefined) {
        vscode.window.showWarningMessage(`親 ${message.parent} の作業ツリーが無いので wrapup を送れない`);
        return;
      }
      const reason = await vscode.window.showInputBox({
        title: `${message.parent} を締める`,
        prompt: "締める理由（--reason）。残りは別の issue に写る",
        validateInput: (value) => (value.trim() === "" ? "理由は空にできない" : undefined),
      });
      if (reason === undefined || reason.trim() === "") {
        return;
      }
      const choice = await vscode.window.showQuickPick(
        [
          { label: "残りを issue に起こす", makeIssue: true },
          { label: "起こさない（--no-issue）", makeIssue: false },
        ],
        { title: `${message.parent} を締める`, placeHolder: "残った指摘の扱い" },
      );
      if (choice === undefined) {
        return;
      }
      runInTerminal(root, wrapupCommand(tree, reason.trim(), choice.makeIssue));
      return;
    }
  }
}

function sendApprove(root: string, launcher: Launcher | undefined): void {
  if (launcher === undefined) {
    vscode.window.showErrorMessage("ccnavi の実行ファイルが見つからないので --approve を送れない");
    return;
  }
  runInTerminal(root, approveCommand(launcher, root));
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
  const m = message as { type?: unknown; filePath?: unknown; parent?: unknown; phase?: unknown };
  switch (m.type) {
    case "refresh":
    case "approve":
      return { type: m.type };
    case "open":
      return typeof m.filePath === "string" ? { type: "open", filePath: m.filePath } : undefined;
    case "accept":
      return typeof m.parent === "string" && typeof m.phase === "number" && Number.isInteger(m.phase)
        ? { type: "accept", parent: m.parent, phase: m.phase }
        : undefined;
    case "wrapup":
      return typeof m.parent === "string" ? { type: "wrapup", parent: m.parent } : undefined;
    default:
      return undefined;
  }
}
