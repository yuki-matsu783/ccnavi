/**
 * ルール設定画面が拡張ホストへ返す操作。判定もせず、ファイルも書かない（判定は実行ファイル）。
 * 送れるのは契約（`core/rules-view.ts` の `RulesMessage`）に書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { RulesMessage } from "../../core/rules-view.js";

export const post = poster<RulesMessage>();
