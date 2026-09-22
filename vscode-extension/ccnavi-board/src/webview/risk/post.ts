/**
 * リスク管理画面が拡張ホストへ返す操作。点も数えず、ファイルも書かない（ADR-0035）。
 * 送れるのは契約（`core/risk-view.ts` の `RiskMessage`）に書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { RiskMessage } from "../../core/risk-view.js";

export const post = poster<RiskMessage>();
