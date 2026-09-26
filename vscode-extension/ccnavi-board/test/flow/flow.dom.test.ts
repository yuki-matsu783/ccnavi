/**
 * フロー編集画面（React）を happy-dom で動かす。図・印・錠・保存・注意を見る。
 *
 * 図は大きさの偽物（`openPage` が渡す `measure`）で描かせる。見るのは「点と線がその数あるか」
 * 「押すと何が起きるか」まで。線の経路とドラッグは README の手動確認に回す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement, HTMLInputElement, HTMLTextAreaElement } from "happy-dom" with { "resolution-mode": "import" };
import { addNode, parseFlow, patchData, templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { lockedReason } from "../../src/core/flow-view.js";
import { openFlow, SAMPLE, sampleData, savedDoc } from "../helpers/flow.js";

function sample(): FlowDoc {
  const read = parseFlow(SAMPLE);
  assert.ok(read.ok);
  return read.doc;
}

/** 印のある 2 種類（問いとサブエージェント）を足した雛形 */
function marked(): FlowDoc {
  let doc = templateFlow("i0001-01", "調査");
  doc = addNode(doc, "askUserQuestion", { x: 200, y: 300 }).doc;
  doc = addNode(doc, "subAgent", { x: 400, y: 300 }).doc;
  return patchData(doc, "subAgent-1", { description: "資料を深掘りする" });
}

test("CB-D107 図はノードと線を描き、問いには「メインに戻る」、サブエージェントには「入れ子」の印を付ける", async () => {
  const dom = await openFlow({ doc: marked() });
  try {
    assert.equal(dom.all(".react-flow__node").length, 4);
    assert.equal(dom.all(".react-flow__edge").length, 1, "線が描けていない（測定の偽物が効いていない）");
    for (const node of dom.all(".react-flow__node")) {
      assert.notEqual((node as unknown as { style: { visibility: string } }).style.visibility, "hidden", "測れていない点がある");
    }
    const ask = dom.one('.react-flow__node[data-id="askUserQuestion-1"]');
    assert.equal(ask.querySelector(".flow-badge.ask")?.textContent, "メインに戻る（利用者に聞く）");
    const sub = dom.one('.react-flow__node[data-id="subAgent-1"]');
    assert.equal(sub.querySelector(".flow-badge.nest")?.textContent, "入れ子（上限なら戻る）");
    assert.equal(sub.querySelector(".flow-node-summary")?.textContent, "資料を深掘りする");
    // 開始・終了には印が無い
    assert.equal(dom.one('.react-flow__node[data-id="start"]').querySelector(".flow-badge"), null);
    // 問いの出口は選択肢ごと
    assert.deepEqual(
      Array.from(ask.querySelectorAll(".flow-port")).map((port) => port.getAttribute("data-port")),
      ["branch-0", "branch-1"],
    );
    // 部品箱は 8 種類
    assert.deepEqual(
      dom.all('[data-action="add-node"]').map((button) => button.getAttribute("data-type")),
      ["start", "end", "prompt", "subAgent", "askUserQuestion", "ifElse", "switch", "skill"],
    );
    // 読んだまま（在るファイル）なら保存は押せない
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled);
    assert.equal(dom.all("#lock").length, 0);
    assert.match(dom.one(".path").textContent ?? "", /\.ccnavi\/approved\/flows\/i0001-01\.yml/);
  } finally {
    await dom.close();
  }
});

test("CB-D108 ファイルが無ければ雛形を見せ、そのまま保存できる（保存でファイルを作る）", async () => {
  const dom = await openFlow({ exists: false });
  try {
    assert.equal(dom.all(".react-flow__node").length, 2);
    assert.match(dom.one(".toolbar").textContent ?? "", /まだ無い。保存すると作る/);
    const save = dom.one<HTMLButtonElement>("#save");
    assert.ok(!save.disabled);
    assert.ok(dom.one<HTMLButtonElement>('[data-action="open-flow"]').disabled, "無いファイルはエディタで開けない");
    dom.click(save);
    await dom.settle();
    assert.deepEqual(savedDoc(dom), templateFlow("i0001-01", "調査"));
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled, "往復の間は止める");
    assert.match(dom.one("#status").textContent ?? "", /着手中でないかを確かめて保存中/);
    // 拡張ホストが断った（錠を聞き直したら着手中だった）
    await dom.send({ type: "failed", message: lockedReason("i0001-01") });
    assert.match(dom.one("#status").textContent ?? "", /DENY_TICKET_FLOW_LOCKED/);
    assert.ok(dom.one(".foot").classList.contains("error"));
  } finally {
    await dom.close();
  }
});

test("CB-D109 錠が掛かっていれば読むだけ。理由の帯を出し、部品箱・欄・保存を止める。外れれば戻る", async () => {
  const lock = { locked: true, reason: lockedReason("i0001-02") };
  const dom = await openFlow({ ticket: "i0001-02", doc: marked(), lock, exists: false });
  try {
    const banner = dom.one("#lock").textContent ?? "";
    assert.match(banner, /着手中なので、フローは書き換えられない/);
    assert.match(banner, /finish で終わるか cancel で取り消されると外れる/);
    for (const button of dom.all<HTMLButtonElement>('[data-action="add-node"]')) {
      assert.ok(button.disabled);
    }
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled, "無いファイルでも、錠が掛かっていれば保存できない");
    assert.equal(dom.one("#flow-graph").getAttribute("data-readonly"), "1");
    // ノードを押して欄を見ても、欄は止まっている
    dom.click(dom.one('.react-flow__node[data-id="subAgent-1"]'));
    await dom.settle();
    assert.equal(dom.one("#inspector").getAttribute("data-selected"), "node");
    assert.ok(dom.one<HTMLInputElement>("#inspector input.f-description").disabled);
    assert.ok(dom.one<HTMLButtonElement>('[data-action="remove-node"]').disabled);
    // 何も押していないので、保存は送っていない
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0);
    // 実行ファイルの答えが変わった（finish した）と拡張ホストが知らせる。欄が戻る
    await dom.send({ type: "lock", lock: { locked: false, reason: "" } });
    assert.equal(dom.all("#lock").length, 0);
    assert.ok(!dom.one<HTMLInputElement>("#inspector input.f-description").disabled);
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    // 開いている最中に着手されたら、そこから読むだけに戻る
    await dom.send({ type: "lock", lock });
    assert.ok(dom.one<HTMLInputElement>("#inspector input.f-description").disabled);
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled);
  } finally {
    await dom.close();
  }
});

test("CB-D110 部品箱で足して欄で直して保存すると、知らない欄と知らない種類はそのまま送る", async () => {
  const dom = await openFlow({ doc: sample() });
  try {
    assert.equal(dom.all(".react-flow__node").length, 4);
    // 知らない種類（mcp）は印を付けず、欄を持たないと言う
    const mcp = dom.one('.react-flow__node[data-id="mcp-1"] .flow-node');
    assert.equal(mcp.getAttribute("data-type"), "other");
    dom.click(dom.one('.react-flow__node[data-id="mcp-1"]'));
    await dom.settle();
    assert.match(dom.one("#inspector").textContent ?? "", /この画面で欄を持たない種類/);
    assert.match(dom.one("#inspector pre.flow-raw").textContent ?? "", /serverId: srv\ntoolName: t\n/);
    // 足して、欄で直す
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 5);
    assert.equal(dom.one("#inspector").getAttribute("data-type"), "prompt");
    dom.type(dom.one<HTMLTextAreaElement>("#inspector textarea.f-prompt"), "資料を読んで要点を 3 つ挙げる");
    await dom.settle();
    // 問いの選択肢の名前も直す
    dom.click(dom.one('.react-flow__node[data-id="ask-1"]'));
    await dom.settle();
    dom.type(dom.one<HTMLInputElement>('#inspector .branch[data-index="1"] input.f-branch-label'), "B（急ぐ）");
    await dom.settle();
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    dom.click(dom.one("#save"));
    await dom.settle();
    const saved = savedDoc(dom) as unknown as Record<string, unknown>;
    const original = sampleData();
    for (const key of ["id", "name", "version", "schemaVersion", "metadata", "subAgentFlows"]) {
      assert.deepEqual(saved[key], original[key], key);
    }
    const nodes = saved.nodes as Record<string, unknown>[];
    assert.deepEqual(nodes[2], (original.nodes as unknown[])[2], "知らない種類はそのまま");
    assert.deepEqual((nodes[1].data as Record<string, unknown>).options, [
      { label: "A", description: "" },
      { label: "B（急ぐ）", description: "" },
    ]);
    assert.equal((nodes[1].data as Record<string, unknown>).extra, 1);
    assert.deepEqual(nodes[1].style, { width: 220 });
    assert.deepEqual(nodes[4], { id: "prompt-1", type: "prompt", name: "プロンプト", position: nodes[4].position, data: { prompt: "資料を読んで要点を 3 つ挙げる" } });
    assert.deepEqual(saved.connections, original.connections);
    // 画面が送るのはフローだけで、未保存かは別に知らせる
    assert.ok(dom.posted.some((m) => m.type === "dirty" && m.dirty === true));
  } finally {
    await dom.close();
  }
});

test("CB-D111 入れ子が子の下 2 段を超えるときだけ注意を出す。サブフローの中身は描かないことも言う", async () => {
  const quiet = await openFlow({ doc: marked() });
  try {
    assert.equal(quiet.all("#flow-notices").length, 0);
  } finally {
    await quiet.close();
  }
  const deep: FlowDoc = {
    nodes: [
      { id: "start", type: "start", name: "開始", position: { x: 0, y: 0 } },
      { id: "outer", type: "subAgentFlow", name: "外側", position: { x: 200, y: 0 }, data: { subAgentFlowId: "sf-1" } },
    ],
    connections: [{ id: "c", from: "start", to: "outer", fromPort: "output", toPort: "input" }],
    subAgentFlows: [
      { id: "sf-1", name: "1", nodes: [{ id: "mid", type: "subAgentFlow", name: "中", data: { subAgentFlowId: "sf-2" } }], connections: [] },
      { id: "sf-2", name: "2", nodes: [{ id: "leaf", type: "subAgent", name: "奥", data: {} }], connections: [] },
    ],
  };
  const dom = await openFlow({ doc: deep });
  try {
    const notices = dom.all("#flow-notices li").map((li) => li.textContent ?? "");
    assert.ok(notices.some((n) => /子の下に 3 段重なる/.test(n)), notices.join("\n"));
    assert.ok(notices.some((n) => /サブフロー（subAgentFlows）が 2 本/.test(n)));
    // サブフローのノードにも入れ子の印
    assert.equal(dom.one('.react-flow__node[data-id="outer"]').querySelector(".flow-badge.nest")?.textContent, "入れ子（上限なら戻る）");
    // 注意は保存を止めない（判定ではない）
    dom.click(dom.one('.react-flow__node[data-id="outer"]'));
    await dom.settle();
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name"), "外側の深掘り");
    await dom.settle();
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
  } finally {
    await dom.close();
  }
});

test("CB-D112 外のファイルを取り込むボタンは無い。中身（data）が届くと編集は捨てられて未保存が消える", async () => {
  const dom = await openFlow();
  try {
    assert.equal(dom.all('[data-action="import"]').length, 0);
    assert.doesNotMatch(dom.one(".toolbar").textContent ?? "", /取り込/);
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001-01", title: "調査", parent: "i0001", flowPath: "x.yml", flowRel: ".ccnavi/approved/flows/i0001-01.yml", exists: true, doc: sample(), lock: { locked: false, reason: "" } } } });
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.all(".react-flow__node").length, 4);
  } finally {
    await dom.close();
  }
});
