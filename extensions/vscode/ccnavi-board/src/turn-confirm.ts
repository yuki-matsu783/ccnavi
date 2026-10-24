import * as vscode from "vscode";
import { turnWarning, type Lock } from "./core/lock.js";

/**
 * ターン中の保存を続けるか。ターン中のセッションが無ければ聞かずに続ける。
 * ターン中に設定ファイルを保存しても、次のツール呼び出しの実行後チェックが「その呼び出しが起こした変更」
 * として戻すことがある。保存は止めず、押した時点で知らせて選ばせる。
 */
export async function confirmDuringTurn(lock: Lock): Promise<boolean> {
  const warning = turnWarning(lock);
  if (warning === "") {
    return true;
  }
  const go = "それでも保存する";
  const picked = await vscode.window.showWarningMessage(warning, { modal: true }, go);
  return picked === go;
}
