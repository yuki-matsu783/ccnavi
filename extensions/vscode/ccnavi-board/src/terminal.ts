/**
 * clone / fetch / pull と、承認のあと承認済みチケットをコミットして push する sh を送る統合ターミナル。
 * 「ccnavi」という名前の 1 本を使い回す。
 * 承認と残った指摘の行き先はここを通らない（ボードのオーバーレイから子プロセスで打つ。ccnavi.ts）。
 * Windows では Git Bash を使う（sh のスクリプトと `cd ... && ...` の形をそのまま通すため）。
 * 子プロセスで sh を走らせるときも同じシェルを使う（`scriptShell`）。
 */
import * as fs from "node:fs";
import * as vscode from "vscode";

export const TERMINAL_NAME = "ccnavi";
const GIT_BASH = "C:\\Program Files\\Git\\bin\\bash.exe";

export function runInTerminal(root: string, command: string): void {
  let terminal = vscode.window.terminals.find(
    (t) => t.name === TERMINAL_NAME && t.exitStatus === undefined,
  );
  if (terminal === undefined) {
    const options: vscode.TerminalOptions = { name: TERMINAL_NAME, cwd: root };
    const shell = shellPath();
    if (shell !== undefined) {
      options.shellPath = shell;
    }
    terminal = vscode.window.createTerminal(options);
    terminal.show(true);
    sendWhenReady(terminal, command);
    return;
  }
  terminal.show(true);
  terminal.sendText(command, true);
}

/**
 * 実行のたびに新しい専用ターミナルを作って 1 行を送る。「ccnavi」ターミナルは承認の push の sh などが
 * 使っていて、走っている間に送った行はそのプロセスの標準入力に入ってしまうので、取り合わないよう分ける
 */
export function runInNewTerminal(root: string, name: string, command: string): void {
  const options: vscode.TerminalOptions = { name, cwd: root };
  const shell = shellPath();
  if (shell !== undefined) {
    options.shellPath = shell;
  }
  const terminal = vscode.window.createTerminal(options);
  terminal.show(true);
  sendWhenReady(terminal, command);
}

/** 作った直後のシェルはまだ起動中で、すぐ送った行が行入力の途中で中断されることがある。立ち上がりを待つ時間の上限 */
const READY_TIMEOUT_MS = 3000;

/**
 * 作ったばかりのターミナルへ、シェルの準備ができてから 1 行を送る。シェル統合が使えれば、その
 * 立ち上がりを待つ。使えない場合や、上限の時間までに立ち上がらない場合は、そのまま送る
 * （シェル統合の API は VS Code 1.93 から使える。それより前の版では待たずに送る）
 */
function sendWhenReady(terminal: vscode.Terminal, command: string): void {
  const window = vscode.window as unknown as {
    onDidChangeTerminalShellIntegration?: (
      listener: (e: { terminal: vscode.Terminal }) => void,
    ) => vscode.Disposable;
  };
  if (window.onDidChangeTerminalShellIntegration === undefined) {
    terminal.sendText(command, true);
    return;
  }
  let sent = false;
  const send = (): void => {
    if (sent) {
      return;
    }
    sent = true;
    subscription.dispose();
    clearTimeout(timer);
    terminal.sendText(command, true);
  };
  const subscription = window.onDidChangeTerminalShellIntegration((e) => {
    if (e.terminal === terminal) {
      send();
    }
  });
  const timer = setTimeout(send, READY_TIMEOUT_MS);
}

/**
 * 子プロセスで sh のスクリプトを走らせるシェル。Windows は統合ターミナルと同じ Git Bash、
 * 他の OS は `sh`
 */
export function scriptShell(): string {
  return shellPath() ?? "sh";
}

/** Windows だけシェルを指定する。他の OS は既定のシェル（sh が利用できる前提） */
function shellPath(): string | undefined {
  if (process.platform !== "win32") {
    return undefined;
  }
  const configured = vscode.workspace.getConfiguration("ccnaviBoard").get<string>("bashPath", "");
  if (configured !== "") {
    return configured;
  }
  try {
    if (fs.statSync(GIT_BASH).isFile()) {
      return GIT_BASH;
    }
  } catch {
    // 無ければ PATH の bash に任せる
  }
  return "bash";
}
