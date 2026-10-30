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
  const waiting = pending.get(terminal);
  if (waiting !== undefined) {
    // 作った直後でまだ送っていない行がある。追い越さないよう後ろに並べる
    waiting.push(command);
    return;
  }
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

/**
 * 作った直後のシェルはまだ起動中で、すぐ送った行が行入力の途中で中断されることがある。立ち上がりを待つ時間の上限。
 * シェルの起動がこれより遅いと、待ちきれずに送るので防げない
 */
const READY_TIMEOUT_MS = 3000;

/** シェルの準備を待っていて、まだ送っていない行（ターミナルごと。送ったら消える） */
const pending = new WeakMap<vscode.Terminal, string[]>();

/**
 * 作ったばかりのターミナルで、シェル統合が立ち上がってから行を送る。待っている間に runInTerminal が
 * 積んだ行も、送った順に送る。シェル統合の API が無い版では待たずに送る。API があっても統合が働かない
 * 環境（設定で無効・対応しないシェルなど）では、上限の時間まで待ってから送る
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
  const queue = [command];
  pending.set(terminal, queue);
  let sent = false;
  const send = (): void => {
    if (sent) {
      return;
    }
    sent = true;
    subscription.dispose();
    clearTimeout(timer);
    pending.delete(terminal);
    for (const line of queue) {
      terminal.sendText(line, true);
    }
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
