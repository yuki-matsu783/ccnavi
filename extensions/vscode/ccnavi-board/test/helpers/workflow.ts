/**
 * ワークフロー編集タブ（React）を happy-dom で動かす。`test/helpers/flow.ts` と同じ役割。
 *
 * 画面はバンドルした 1 本（`out/webview/workflow.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * この入口のパス（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * 図を描くので、大きさの偽物（`measure`）を入れて読ませる。入れないと React Flow は点を隠したまま
 * 線を 1 本も描かない（`test/helpers/dom.ts` の頭）。中身は Python 側が書き出した `--plan-order` の
 * 答えの実例（`test/helpers/plan-order.ts`）。
 */
import { renderWorkflowPage, type RenderOptions } from "../../src/core/workflow-render.js";
import type { WorkflowData, WorkflowPage } from "../../src/core/workflow-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage, type Posted } from "./dom.js";
import { planOrderSample } from "./plan-order.js";

export const NONCE = "TEST-NONCE-123";

export function workflowHtml(data: WorkflowData, options: Partial<RenderOptions> = {}): string {
  return renderWorkflowPage(data, { nonce: NONCE, script: screenScript("workflow"), style: screenStyle("workflow"), ...options });
}

/** 見本の中身（開いたときの答え）。差し替えたいところだけ渡す */
export function workflowPage(overrides: Partial<WorkflowPage> = {}): WorkflowPage {
  return { root: "/ws", ticket: "i0001", answer: planOrderSample("open"), ...overrides };
}

export async function openWorkflowData(data: WorkflowData): Promise<DomPage> {
  const dom = await loadPage(workflowHtml(data), undefined, { measure: true });
  await dom.settle();
  return dom;
}

/** 見本の答えでタブを開く */
export async function openWorkflow(overrides: Partial<WorkflowPage> = {}): Promise<DomPage> {
  return openWorkflowData({ kind: "page", page: workflowPage(overrides) });
}

/** 画面が送ったもののうち、その種類の最後のもの */
export function lastPosted(dom: DomPage, type: string): Posted | undefined {
  const found = dom.posted.filter((message) => message.type === type);
  return found[found.length - 1];
}
