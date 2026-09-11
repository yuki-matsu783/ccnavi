/**
 * 承認コマンドを送る統合ターミナル。「ccnavi」という名前の 1 本を使い回す。
 * Windows では Git Bash を使う（sh のスクリプトと `cd ... && ...` の形をそのまま通すため）。
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
  }
  terminal.show(true);
  terminal.sendText(command, true);
}

/** Windows だけシェルを指定する。他の OS は既定のシェル（sh が使える前提） */
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
