/**
 * チケット制御を使うかの読み取り。答えは実行ファイルの `--explain --json` の `settings.ticket_control`。
 *
 * 実行ファイルは、Claude Code が hook に渡すのと同じ順（プロセスの環境 → `.claude/settings.json` →
 * `.claude/settings.local.json` の env）で値を重ね、判定と同じ解決（読めない値は enable）を通して答える。
 * 拡張は設定ファイルを自分で読まない。読み方を 2 か所に持つと、判定と画面の答えが分かれうるため。
 */
import type { BoardJson } from "./model.js";

export const TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL";
export type TicketControl = "enable" | "disable";

/**
 * ボードの JSON からチケット制御の値を決める。実行ファイルが disable と答えたときだけ disable。
 * 読めなかった（`undefined`）ときは enable として入口を出す。入口を消すと、実行ファイルが無いことに
 * 気づく場所（各画面の失敗の表示）までユーザがたどり着けないため
 */
export function ticketControlOf(board: BoardJson | undefined): TicketControl {
  return board?.settings.ticket_control.trim().toLowerCase() === "disable" ? "disable" : "enable";
}
