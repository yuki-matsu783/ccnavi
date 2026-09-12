/**
 * チケット制御を使うかの読み取り。`.claude/settings.json` と `.claude/settings.local.json` の
 * env `CCNAVI_TICKET_CONTROL` を見る。Claude Code は両方の env を hook に渡し、local が勝つ。
 *
 * 拡張は hook が受け取るプロセスの環境を見られないので、設定ファイルの本文から読む。
 * シェルから渡された値は拾えない（README に「設定ファイルに書く」と決めてある）。
 * 読めない値は enable に倒す。ccnavi の解決（selfguard.resolve）と同じ向きで、
 * 綴りを誤った設定でボードが消えると、切れたと思い込む。
 */
import { envFromSettingsJson } from "./hooks.js";

export const TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL";
export type TicketControl = "enable" | "disable";

export interface SettingsTexts {
  /** `.claude/settings.json` の本文。無ければ undefined */
  readonly settings: string | undefined;
  /** `.claude/settings.local.json` の本文。無ければ undefined */
  readonly local: string | undefined;
}

/** 2 つの設定ファイルの本文から、チケット制御の値を決める。local が先、無ければ settings */
export function ticketControlFrom(texts: SettingsTexts): TicketControl {
  for (const text of [texts.local, texts.settings]) {
    if (text === undefined) {
      continue;
    }
    const value = envFromSettingsJson(text, TICKET_CONTROL_ENV).trim().toLowerCase();
    if (value === "disable") {
      return "disable";
    }
    if (value === "enable") {
      return "enable";
    }
    // 空や読めない語は「書いていない」と同じ。次のファイルを見る。
  }
  return "enable";
}

/** 実行ファイルの答え（ボードの JSON の settings.ticket_control）と設定ファイルの読みが食い違うか */
export function ticketControlMismatch(fromFiles: TicketControl, fromBoard: string): string {
  const board = fromBoard.trim().toLowerCase();
  if (board === "" || board === fromFiles) {
    return "";
  }
  return `${TICKET_CONTROL_ENV} の読みが食い違う（設定ファイル: ${fromFiles}、実行ファイル: ${board}）。セッションを開き直したか、シェルの環境から渡していないかを確かめる`;
}
