/**
 * フロー編集画面（React）を happy-dom で動かす。`test/helpers/phases.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/flow.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * この入口の綴り（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * 図を描くので、大きさの偽物（`measure`）を入れて読ませる。入れないと React Flow は点を隠したまま
 * 線を 1 本も描かない（`test/helpers/dom.ts` の頭）。
 */
import { templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { renderFlowPage, type RenderOptions } from "../../src/core/flow-render.js";
import { OPEN_LOCK, type FlowData, type FlowPage } from "../../src/core/flow-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

export function flowHtml(data: FlowData, options: Partial<RenderOptions> = {}): string {
  return renderFlowPage(data, { nonce: NONCE, script: screenScript("flow"), style: screenStyle("flow"), ...options });
}

/** 見本の中身（雛形の 開始 → 終了）。差し替えたいところだけ渡す */
export function page(overrides: Partial<FlowPage> = {}): FlowPage {
  return {
    root: "/ws",
    ticket: "i0001-01",
    title: "調査",
    parent: "i0001",
    flowPath: ".claude/worktrees/i0001/references/i0001-01/flow.json",
    flowRel: "references/i0001-01/flow.json",
    declared: false,
    exists: true,
    doc: templateFlow("i0001-01", "調査"),
    lock: OPEN_LOCK,
    ...overrides,
  };
}

export async function openPage(data: FlowData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(flowHtml(data), initialState, { measure: true });
  await dom.settle();
  return dom;
}

/** 見本のフローを開く */
export async function openFlow(overrides: Partial<FlowPage> = {}): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) });
}

/** 直前に送った保存の中身 */
export function savedDoc(dom: DomPage): FlowDoc {
  const saves = dom.posted.filter((message) => message.type === "save");
  if (saves.length === 0) {
    throw new Error("保存を送っていない");
  }
  return saves[saves.length - 1].doc as FlowDoc;
}

/** cc-wf-studio が書き出す形に寄せた見本の本文。画面が知らない欄・知らない種類（mcp）・サブフローを持つ */
export const SAMPLE = JSON.stringify({
  id: "wf-1",
  name: "調べて聞く",
  version: "1.0.0",
  schemaVersion: "1.2.0",
  metadata: { tags: ["x"], createdAt: "2026-09-01T00:00:00Z" },
  nodes: [
    { id: "start-1", type: "start", name: "Start", position: { x: 0, y: 0 }, data: { label: "Start" } },
    {
      id: "ask-1",
      type: "askUserQuestion",
      name: "方針を聞く",
      position: { x: 200, y: 0, z: 3 },
      style: { width: 220 },
      data: { questionText: "どちら？", options: [{ label: "A", description: "" }, { label: "B", description: "" }], multiSelect: false, outputPorts: 2, extra: 1 },
    },
    { id: "mcp-1", type: "mcp", name: "MCP", position: { x: 400, y: 0 }, data: { serverId: "srv", toolName: "t", parameters: [{ name: "p" }] } },
    { id: "end-1", type: "end", name: "End", position: { x: 600, y: 0 }, data: { label: "End" } },
  ],
  connections: [
    { id: "c1", from: "start-1", to: "ask-1", fromPort: "output", toPort: "input" },
    { id: "c2", from: "ask-1", to: "mcp-1", fromPort: "branch-0", toPort: "input" },
    { id: "c3", from: "ask-1", to: "end-1", fromPort: "branch-1", toPort: "input", extra: true },
    { id: "c4", from: "mcp-1", to: "end-1", fromPort: "output", toPort: "input" },
  ],
  subAgentFlows: [{ id: "sf-1", name: "深掘り", nodes: [{ id: "sa", type: "subAgent", name: "x", position: { x: 0, y: 0 }, data: {} }], connections: [] }],
});
