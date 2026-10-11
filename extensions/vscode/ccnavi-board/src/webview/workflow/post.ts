/**
 * ワークフロー編集タブが拡張ホストへ返す操作。送れるのは契約（`core/workflow-view.ts` の `WorkflowMessage`）にあるものだけ。
 */
import { poster } from "../vscode.js";
import type { WorkflowMessage } from "../../core/workflow-view.js";

export const post = poster<WorkflowMessage>();
