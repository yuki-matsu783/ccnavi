/**
 * 子のフローの読み書き（`core/flow-doc.ts`）。YAML の読み書き、知らない欄と知らない種類を落とさないこと、雛形、
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
import { parse } from "yaml";

import { SAMPLE, sampleData } from "../helpers/flow.js";

function sample(): FlowDoc {
  const read = parseFlow(SAMPLE);
  assert.ok(read.ok, read.ok ? "" : read.error);
  return read.doc;
}

/** 書き出した本文をもう 1 度読んだ中身（読み手は拡張の外の YAML の読み方で） */
function roundTrip(doc: FlowDoc): unknown {
  return parse(serializeFlow(doc));
}

test("CB-T218 YAML を読んで書くだけなら、知らない欄も知らない種類も元のまま出る", () => {
  // 手で書いた形（コメント・流れ形式・BOM）も読める
  assert.deepEqual(sample(), sampleData());
  const bom = parseFlow(`\uFEFF${SAMPLE}`);
  assert.ok(bom.ok);
  assert.deepEqual(bom.doc, sampleData());
  assert.deepEqual(roundTrip(sample()), sampleData());
  // 書き出しはブロック形式の 2 字下げで、末尾に改行。別名（& / *）は使わない
  const text = serializeFlow(sample());
  assert.ok(text.endsWith("\n"));
  assert.match(text, /^id: wf-1\nname: 調べて聞く\n/);
  assert.match(text, /\nnodes:\n {2}- id: start-1\n {4}type: start\n/);
  assert.doesNotMatch(text, /[&*]\w/);
  assert.doesNotMatch(text, /^\s*\{/m);
});

test("CB-T239 書き出しは人が読める形で、実行ファイル（YAML 1.1）が別の型に読む綴りは引用符で囲む", () => {
  // 同じ中身を 2 か所で持っても別名にしない（実行ファイルは別名を読まない）
  const shared = { x: 1, y: 2 };
  const doc: FlowDoc = {
    nodes: [
      { id: "a", type: "prompt", name: "yes", position: shared, data: { prompt: "1 行目\n2 行目\n", mode: "0755", on: "2026-01-01", empty: "" } },
      { id: "b", type: "prompt", name: "no", position: shared, data: { prompt: "続き" } },
    ],
  };
  const text = serializeFlow(doc);
  assert.doesNotMatch(text, /[&*]\w/);
  // 複数行の文は | の形
  assert.match(text, /prompt: \|\n {8}1 行目\n {8}2 行目\n/);
  // YAML 1.1 で真偽値・八進・日付に読まれる綴りは引用符で囲む（キーも）
  assert.match(text, /name: "yes"/);
  assert.match(text, /name: "no"/);
  assert.match(text, /mode: "0755"/);
  assert.match(text, /"on": "2026-01-01"/);
  assert.match(text, /empty: ""/);
  const read = parseFlow(text);
  assert.ok(read.ok);
  assert.deepEqual(read.doc, doc);
});

test("CB-T240 読みはルール設定の画面と同じ yaml の既定で、YAML 1.1 の読み方は真似しない（型の答えは実行ファイル）", () => {
  const read = parseFlow("nodes:\n  - {id: a, type: askUserQuestion, position: {x: 1, y: 2}, data: {multiSelect: yes, off: n, when: 2026-01-01}}\n");
  assert.ok(read.ok, read.ok ? "" : read.error);
  // `yes` `off` は文字のまま（真偽値に差し替えない）。`y` `n` も文字。日付も文字
  assert.deepEqual(read.doc.nodes[0].position, { x: 1, y: 2 });
  assert.deepEqual(read.doc.nodes[0].data, { multiSelect: "yes", off: "n", when: "2026-01-01" });
  // 書き出しは実行ファイル（YAML 1.1）が文字以外に読む綴りを囲む（書式の側の制約。ADR-0035）。y は囲まない
  const text = serializeFlow(read.doc);
  assert.match(text, /multiSelect: "yes"/);
  assert.match(text, /"off": n/);
  assert.match(serializeFlow(templateFlow("x", "")), /\n {6}y: 160\n/);
});

test("CB-T219 編集は触ったところだけを差し替え、ほかの欄（data・position・最上位・線）は残す", () => {
  let doc = sample();
  doc = patchData(doc, "ask-1", { questionText: "どれにする？" });
  doc = renameNode(doc, "mcp-1", "外の道具");
  doc = moveNode(doc, "ask-1", { x: 250.4, y: 10.6 });
  doc = setConditionAt(doc, 0, "いつも");
  const out = roundTrip(doc) as Record<string, unknown>;
  const original = sampleData();
  for (const key of ["id", "name", "version", "schemaVersion", "metadata", "subAgentFlows"]) {
    assert.deepEqual(out[key], original[key], key);
  }
  const nodes = out.nodes as Record<string, unknown>[];
  const ask = nodes[1];
  assert.deepEqual(ask.data, { questionText: "どれにする？", options: [{ label: "A", description: "" }, { label: "B", description: "" }], multiSelect: false, extra: 1 });
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

test("CB-T221 画面が断るのは描けないときだけ。正しいか（id の重なり・線の形・別名）は決めず、例外を外に出さない", () => {
  const refused: [string, RegExp][] = [
    ["nodes: [", /画面の YAML の読み手で読めないので描けない/],
    ["a: 1\na: 2\n", /画面の YAML の読み手で読めないので描けない/],
    ["nodes: []\n---\nnodes: []\n", /画面の YAML の読み手で読めないので描けない/],
    ["", /描けない/],
    ["- 1\n", /描けない/],
    ["name: x\n", /描けない/],
    ["nodes:\n  - {type: start}\n", /描けない/],
    ["nodes: [1]\n", /描けない/],
  ];
  for (const [text, reason] of refused) {
    const result = parseFlow(text);
    assert.equal(result.ok, false, text);
    assert.match(result.ok ? "" : result.error, reason, text);
  }
  // 形の誤りでも描けるものは読む。正しいかは開く前と保存の前に実行ファイル（--lint --flow）が言う
  for (const text of ["nodes:\n  - {id: a}\n  - {id: a}\n", "nodes: []\nconnections: {}\n", "nodes: []\nconnections: [1]\n", "x: &a [1]\nnodes: [{id: a, data: *a}]\n"]) {
    assert.ok(parseFlow(text).ok, text);
  }
  // 別名を重ねて膨らませる形（billion laughs）は、yaml の読み手が辿る数の上限で断る（落ちない）
  const lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"];
  for (let i = 1; i < 12; i += 1) {
    lines.push(`a${i}: &a${i} [${Array.from({ length: 10 }, () => `*a${i - 1}`).join(", ")}]`);
  }
  lines.push("nodes: [{id: a, data: {v: *a11}}]");
  const bomb = parseFlow(lines.join("\n"));
  assert.equal(bomb.ok, false);
  // connections が無いフローも読める。描くときに空として扱う
  const bare = parseFlow("nodes:\n  - {id: a, type: prompt}\n");
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
  // 足すと後ろに付く。1 件だけ直すと他は元のまま
  const grown = patchBranch(addBranch(trimmed, "ask-1", { label: "C", description: "" }), "ask-1", 0, { description: "いまのまま" });
  assert.deepEqual((grown.nodes[1].data as Record<string, unknown>).options, [
    { label: "B", description: "いまのまま" },
    { label: "C", description: "" },
  ]);
  // 線
  const same = connect(sample(), "start-1", "output", "ask-1", "input");
  assert.equal(connectionsOf(same).length, 4, "同じ線は足さない");
  assert.equal(connectionsOf(connect(sample(), "ask-1", "branch-0", "ask-1", "input")).length, 4, "自分へ戻る線は足さない");
  const linked = connect(sample(), "start-1", "output", "end-1", "input");
  assert.deepEqual(connectionsOf(linked)[4], { id: "c-start-1-end-1", from: "start-1", to: "end-1", fromPort: "output", toPort: "input" });
  assert.equal(connectionsOf(removeConnectionAt(linked, 4)).length, 4);
  // connections が無かったフローには欄ができる
  const bare = parseFlow("nodes:\n  - {id: a, type: start}\n  - {id: b, type: end}\n");
  assert.ok(bare.ok);
  assert.equal(connectionsOf(connect(bare.doc, "a", "output", "b", "input")).length, 1);
  // 足したノードは使われていない id と既定の中身を持つ
  const added = addNode(templateFlow("x", ""), "askUserQuestion", { x: 1, y: 2 });
  assert.equal(added.id, "askUserQuestion-1");
  assert.deepEqual(added.doc.nodes[2].position, { x: 1, y: 2 });
  assert.deepEqual(added.doc.nodes[2].data, { questionText: "", multiSelect: false, options: [{ label: "はい", description: "" }, { label: "いいえ", description: "" }] });
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
  // 人が書いた線が別の綴りの出口を使っていれば、その出口も描く
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
