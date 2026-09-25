/**
 * 子のフローの読み書き（`core/flow-doc.ts`）。知らない欄と知らない種類を落とさないこと、雛形、取り込み、
 * 編集の関数、入れ子の段の数え方を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addBranch,
  addNode,
  connect,
  connectionLabel,
  connectionsOf,
  flowNotices,
  importFlow,
  moveNode,
  nesting,
  parseFlow,
  patchBranch,
  patchData,
  portsOf,
  removeBranch,
  removeConnectionAt,
  removeNode,
  renameNode,
  serializeFlow,
  setConditionAt,
  templateFlow,
  type FlowDoc,
} from "../../src/core/flow-doc.js";
import { SAMPLE } from "../helpers/flow.js";
function sample(): FlowDoc {
  const read = parseFlow(SAMPLE);
  assert.ok(read.ok, read.ok ? "" : read.error);
  return read.doc;
}

/** JSON として同じか（書き出した本文をもう 1 度読んで比べる） */
function roundTrip(doc: FlowDoc): unknown {
  return JSON.parse(serializeFlow(doc));
}

test("CB-T218 読んで書くだけなら、知らない欄も知らない種類も元のまま出る", () => {
  assert.deepEqual(roundTrip(sample()), JSON.parse(SAMPLE));
  // 2 字下げで、末尾に改行
  assert.ok(serializeFlow(sample()).endsWith("}\n"));
  assert.match(serializeFlow(sample()), /^\{\n  "id": "wf-1",/);
});

test("CB-T219 編集は触ったところだけを差し替え、ほかの欄（data・position・最上位・線）は残す", () => {
  let doc = sample();
  doc = patchData(doc, "ask-1", { questionText: "どれにする？" });
  doc = renameNode(doc, "mcp-1", "外の道具");
  doc = moveNode(doc, "ask-1", { x: 250.4, y: 10.6 });
  doc = setConditionAt(doc, 0, "いつも");
  const out = roundTrip(doc) as Record<string, unknown>;
  const original = JSON.parse(SAMPLE) as Record<string, unknown>;
  for (const key of ["id", "name", "version", "schemaVersion", "metadata", "subAgentFlows"]) {
    assert.deepEqual(out[key], original[key], key);
  }
  const nodes = out.nodes as Record<string, unknown>[];
  const ask = nodes[1];
  assert.deepEqual(ask.data, { questionText: "どれにする？", options: [{ label: "A", description: "" }, { label: "B", description: "" }], multiSelect: false, outputPorts: 2, extra: 1 });
  // 位置は丸め、知らない欄（z）は残す。ノードの知らない欄（style）も残す
  assert.deepEqual(ask.position, { x: 250, y: 11, z: 3 });
  assert.deepEqual(ask.style, { width: 220 });
  // 知らない種類（mcp）は名前だけが変わる
  assert.deepEqual(nodes[2], { id: "mcp-1", type: "mcp", name: "外の道具", position: { x: 400, y: 0 }, data: { serverId: "srv", toolName: "t", parameters: [{ name: "p" }] } });
  const connections = out.connections as Record<string, unknown>[];
  assert.equal(connections[0].condition, "いつも");
  assert.equal(connections[2].extra, true);
  // 条件を空に戻すと、欄ごと外れる（書いていなかった線と同じ）
  const cleared = roundTrip(setConditionAt(doc, 0, "")) as { connections: Record<string, unknown>[] };
  assert.equal("condition" in cleared.connections[0], false);
});

test("CB-T220 雛形は 開始 → 終了 の 2 ノードと線 1 本で、そのまま読み直せて注意が出ない", () => {
  const doc = templateFlow("i0001-01", "調査");
  assert.deepEqual(
    doc.nodes.map((node) => node.type),
    ["start", "end"],
  );
  assert.equal(doc.id, "i0001-01-flow");
  assert.equal(doc.name, "i0001-01 調査");
  assert.deepEqual(connectionsOf(doc), [{ id: "c-start-end", from: "start", to: "end", fromPort: "output", toPort: "input" }]);
  const read = parseFlow(serializeFlow(doc));
  assert.ok(read.ok);
  assert.deepEqual(read.doc, doc);
  assert.deepEqual(flowNotices(doc), []);
  // 題が空なら名前は識別子だけ
  assert.equal(templateFlow("i0002-01", "").name, "i0002-01");
});

test("CB-T221 取り込みは形を確かめて中身を変えない。読めない形は理由を言って断る", () => {
  const read = importFlow(`﻿${SAMPLE}`);
  assert.ok(read.ok);
  assert.deepEqual(roundTrip(read.doc), JSON.parse(SAMPLE));
  const refused: [string, RegExp][] = [
    ["{", /JSON として読めない/],
    ["[]", /最上位がオブジェクトではない/],
    ['{"name":"x"}', /`nodes` の並びが無い/],
    ['{"nodes":[{"type":"start"}]}', /nodes\[0\] に id が無い/],
    ['{"nodes":[1]}', /nodes\[0\] がオブジェクトではない/],
    ['{"nodes":[{"id":"a"},{"id":"a"}]}', /id が重なっている（a）/],
    ['{"nodes":[],"connections":{}}', /`connections` が並びではない/],
    ['{"nodes":[],"connections":[1]}', /connections\[0\] がオブジェクトではない/],
  ];
  for (const [text, reason] of refused) {
    const result = importFlow(text);
    assert.equal(result.ok, false, text);
    assert.match(result.ok ? "" : result.error, reason, text);
  }
  // connections が無いフローも読める（実行ファイルと同じ）。描くときに空として扱う
  const bare = importFlow('{"nodes":[{"id":"a","type":"prompt"}]}');
  assert.ok(bare.ok);
  assert.deepEqual(connectionsOf(bare.doc), []);
});

test("CB-T222 ノードを消すと線も消え、出口を消すと後ろの出口の線は番号を詰める。重なる線と自分へ戻る線は足さない", () => {
  let doc = sample();
  doc = removeNode(doc, "mcp-1");
  assert.deepEqual(
    connectionsOf(doc).map((c) => c.id),
    ["c1", "c3"],
  );
  // 選択肢 A（branch-0）を消すと、B へ出ていた線は branch-0 に詰まる
  const trimmed = removeBranch(sample(), "ask-1", 0);
  assert.deepEqual(
    connectionsOf(trimmed).map((c) => [c.id, c.fromPort]),
    [
      ["c1", "output"],
      ["c3", "branch-0"],
      ["c4", "output"],
    ],
  );
  const ask = trimmed.nodes[1].data as Record<string, unknown>;
  assert.deepEqual(ask.options, [{ label: "B", description: "" }]);
  assert.equal(ask.outputPorts, 1);
  // 足すと outputPorts も合わせる。1 件だけ直すと他は元のまま
  const grown = patchBranch(addBranch(trimmed, "ask-1", { label: "C", description: "" }), "ask-1", 0, { description: "いまのまま" });
  assert.deepEqual((grown.nodes[1].data as Record<string, unknown>).options, [
    { label: "B", description: "いまのまま" },
    { label: "C", description: "" },
  ]);
  assert.equal((grown.nodes[1].data as Record<string, unknown>).outputPorts, 2);
  // 線
  const same = connect(sample(), "start-1", "output", "ask-1", "input");
  assert.equal(connectionsOf(same).length, 4, "同じ線は足さない");
  assert.equal(connectionsOf(connect(sample(), "ask-1", "branch-0", "ask-1", "input")).length, 4, "自分へ戻る線は足さない");
  const linked = connect(sample(), "start-1", "output", "end-1", "input");
  assert.deepEqual(connectionsOf(linked)[4], { id: "c-start-1-end-1", from: "start-1", to: "end-1", fromPort: "output", toPort: "input" });
  assert.equal(connectionsOf(removeConnectionAt(linked, 4)).length, 4);
  // connections が無かったフローには欄ができる
  const bare = importFlow('{"nodes":[{"id":"a","type":"start"},{"id":"b","type":"end"}]}');
  assert.ok(bare.ok);
  assert.equal(connectionsOf(connect(bare.doc, "a", "output", "b", "input")).length, 1);
  // 足したノードは使われていない id と既定の中身を持つ
  const added = addNode(templateFlow("x", ""), "askUserQuestion", { x: 1, y: 2 });
  assert.equal(added.id, "askUserQuestion-1");
  assert.deepEqual(added.doc.nodes[2].position, { x: 1, y: 2 });
  assert.equal((added.doc.nodes[2].data as Record<string, unknown>).outputPorts, 2);
});

/** サブフローを重ねたフロー。`depth` はメインの流れから数えた subAgentFlow の重なり */
function nested(depth: number, cyclic = false): FlowDoc {
  const flows = [];
  for (let i = 1; i <= depth; i += 1) {
    const last = i === depth;
    const inner = last && !cyclic ? { id: `sa-${i}`, type: "subAgent", name: "深掘り", data: {} } : { id: `sf-node-${i}`, type: "subAgentFlow", name: "次", data: { subAgentFlowId: last ? "sf-1" : `sf-${i + 1}` } };
    flows.push({ id: `sf-${i}`, name: `サブフロー ${i}`, nodes: [inner], connections: [] });
  }
  return {
    nodes: [
      { id: "start", type: "start", name: "開始" },
      { id: "sf-node-0", type: "subAgentFlow", name: "入口", data: { subAgentFlowId: "sf-1" } },
    ],
    connections: [],
    subAgentFlows: flows,
  };
}

test("CB-T223 入れ子の段は subAgent と subAgentFlow で 1 段ずつ数え、子の下 2 段を超えたときだけ言う", () => {
  const single = patchData(addNode(templateFlow("x", ""), "subAgent", { x: 0, y: 0 }).doc, "subAgent-1", {});
  assert.deepEqual(nesting(single), { depth: 1, cyclic: false });
  assert.deepEqual(flowNotices(single), []);
  // サブフロー（1 段）の中の subAgent（2 段）。子の下 2 段で、まだ上限の中
  assert.equal(nesting(nested(1)).depth, 2);
  assert.ok(!flowNotices(nested(1)).some((n) => n.includes("段重なる")));
  // もう 1 段重ねると 3 段。既定の上限（メインの下 3 段、子は 1 段目）を超える
  assert.equal(nesting(nested(2)).depth, 3);
  const warned = flowNotices(nested(2)).find((n) => n.includes("段重なる"));
  assert.ok(warned !== undefined);
  assert.match(warned, /子の下に 3 段重なる/);
  assert.match(warned, /メインの下 3 段/);
  assert.match(warned, /そのノードで止まってメインへ戻る/);
  // 巡るサブフローは数えきれないと言う
  const loop = nested(2, true);
  assert.equal(nesting(loop).cyclic, true);
  assert.ok(flowNotices(loop).some((n) => n.includes("巡っている")));
  // サブフローの中身は描かないことも言う
  assert.ok(flowNotices(nested(1)).some((n) => n.includes("サブフロー（subAgentFlows）が 1 本")));
});

test("CB-T224 出入口は種類の既定に、読んだ線が使う綴りを足す。線の言葉は条件か出口の名前", () => {
  const doc = sample();
  const ask = doc.nodes[1];
  assert.deepEqual(portsOf(ask, connectionsOf(doc)), {
    inputs: [{ id: "input", label: "" }],
    outputs: [
      { id: "branch-0", label: "A" },
      { id: "branch-1", label: "B" },
    ],
  });
  // 複数選択の問いは出口が 1 本
  const multi = patchData(doc, "ask-1", { multiSelect: true });
  assert.deepEqual(
    portsOf(multi.nodes[1], []).outputs.map((p) => p.id),
    ["output"],
  );
  // 開始に入口は無く、終了に出口は無い
  assert.deepEqual(portsOf(doc.nodes[0], []).inputs, []);
  assert.deepEqual(portsOf(doc.nodes[3], []).outputs, []);
  // 取り込んだ線が別の綴りの出口を使っていれば、その出口も描く
  const odd = connect(doc, "mcp-1", "success", "end-1", "in-2");
  const mcpPorts = portsOf(odd.nodes[2], connectionsOf(odd));
  assert.deepEqual(
    mcpPorts.outputs.map((p) => p.id),
    ["output", "success"],
  );
  assert.deepEqual(
    portsOf(odd.nodes[3], connectionsOf(odd)).inputs.map((p) => p.id),
    ["input", "in-2"],
  );
  // 線の言葉
  const connections = connectionsOf(doc);
  assert.equal(connectionLabel(doc, connections[1]), "A");
  assert.equal(connectionLabel(doc, connections[0]), "");
  assert.equal(connectionLabel(setConditionAt(doc, 1, "急ぐとき"), connectionsOf(setConditionAt(doc, 1, "急ぐとき"))[1]), "急ぐとき");
});
