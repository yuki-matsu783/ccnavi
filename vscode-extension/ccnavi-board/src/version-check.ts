/**
 * 起動のときに 1 度、実行ファイルの版（`--version --json`）を聞き、拡張と食い違っていれば知らせる。
 * どちらを新しくするか（実行ファイルの組み立て直し・配り直し、拡張の入れ直し）を名指しする。
 * 止めはしない。画面ごとに要るフラグは、使う前に `ccnavi.ts` がもう一度見る（`runFlowLint` など）。
 * VS Code の API に触れるので単体テストの対象外。言い分けは core/version.ts。
 */
import * as vscode from "vscode";

import { hasSource, probeVersion } from "./ccnavi.js";
import { skewMessage } from "./core/version.js";

export async function warnVersionSkew(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder === undefined) {
    return;
  }
  const root = folder.uri.fsPath;
  const setting = vscode.workspace.getConfiguration("ccnaviBoard").get<string>("binPath", "");
  const probe = await probeVersion(root, setting);
  // 実行ファイルが見つからないことは、画面を開いたときにその画面が言う
  if (probe === undefined) {
    return;
  }
  const said = skewMessage(probe, hasSource(root));
  if (said !== undefined) {
    void vscode.window.showWarningMessage(said);
  }
}
