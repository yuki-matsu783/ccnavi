/**
 * 取り込み待ち画面が拡張ホストへ返す操作。送るのは契約（`core/handoff-view.ts` の `HandoffMessage`）に
 * 書いてあるものだけ。
 */
import { poster } from "../vscode.js";
import type { HandoffMessage } from "../../core/handoff-view.js";

export const post = poster<HandoffMessage>();
