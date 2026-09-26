/**
 * フロー編集画面が拡張ホストへ返す操作。送れるのは契約（`core/flow-view.ts` の `FlowMessage`）にあるものだけ。
 */
import { poster } from "../vscode.js";
import type { FlowMessage } from "../../core/flow-view.js";

export const post = poster<FlowMessage>();
