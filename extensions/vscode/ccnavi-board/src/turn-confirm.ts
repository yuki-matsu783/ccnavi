import * as vscode from "vscode";
import { loadBoard } from "./ccnavi.js";
import { turnWarning, type Lock } from "./core/lock.js";

/**
 * ターン中の保存を続けるか。ターン中のセッションが無ければ聞かずに続ける。
 * ターン中に設定ファイルを保存しても、次のツール呼び出しの実行後チェックが「その呼び出しが起こした変更」
 * として戻すことがある。保存は止めず、押した時点で知らせて選ばせる。
 */
export async function confirmDuringTurn(lock: Lock): Promise<boolean> {
  return confirmTurns(lock.turns ?? []);
}

/** `confirmDuringTurn` の、セッションの一覧を直に渡す形（保存の錠を持たない画面が使う） */
export async function confirmTurns(sessions: readonly string[]): Promise<boolean> {
  const warning = turnWarning({ locked: false, reason: "", doing: [], turns: sessions });
  if (warning === "") {
    return true;
  }
  const go = "それでも保存する";
  const picked = await vscode.window.showWarningMessage(warning, { modal: true }, go);
  return picked === go;
}

/**
 * ボードを聞き直して、ターン中なら確認する。錠を持たない書き込み（取り込み・ファイルの新規作成・ミラーの同期）が使う。
 * ボードが読めないときは、ターン中かが分からないので確認を出さない（書き込み自体の検査は実行ファイルの側にある）。
 */
export async function confirmIfTurn(root: string, setting: string): Promise<boolean> {
  const board = await loadBoard(root, setting);
  return confirmTurns(board.ok ? (board.board.turns).map((t) => t.session) : []);
}
