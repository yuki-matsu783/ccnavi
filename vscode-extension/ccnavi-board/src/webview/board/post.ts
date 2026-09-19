/**
 * ボード画面が拡張ホストへ返す操作。判定も実行ファイルの呼び出しも画面はしない（ADR-0035）。
 * 送れるのは契約（`core/board-view.ts` の `BoardMessage`）に書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { BoardMessage } from "../../core/board-view.js";

export const post = poster<BoardMessage>();
