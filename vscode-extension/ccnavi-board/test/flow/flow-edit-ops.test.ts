/**
 * フロー編集画面の編集の道具。線を引ける先（`canConnect` / `connect`）、写す・貼る・複製する（`flow-doc.ts`）、
 * 元に戻す履歴（`flow-history.ts`）、読み込んだ時点との見比べ（`flow-diff.ts`）を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { diffFlows, isEmptyDiff, sameFlow, sameValue } from "../../src/core/flow-diff.js";
import {
  absolutePosition,
  addNode,
  canConnect,
  connect,
  connectionsOf,
  copyNodes,
  duplicateNodes,
  flowNotices,
  groupNodes,
  groupOf,
  parseFlow,
  pasteNodes,
  patchData,
  removeNode,
  renameNode,
  setConditionAt,
  templateFlow,
  type FlowDoc,
  type FlowNode,
} from "../../src/core/flow-doc.js";
import { asFlowMessage } from "../../src/core/flow-view.js";
import { canRedo, canUndo, emptyHistory, HISTORY_LIMIT, MERGE_MS, record, redo, seal, undo } from "../../src/core/flow-history.js";
import { SAMPLE } from "../helpers/flow.js";

function sample(): FlowDoc {
  const read = parseFlow(SAMPLE);
  assert.ok(read.ok, read.ok ? "" : read.error);
  return read.doc;
}

function byId(doc: FlowDoc, id: string): FlowNode {
  const node = doc.nodes.find((n) => n.id === id);
  assert.ok(node !== undefined, id);
  return node;
}

/** 開始 → 分岐（if / else）→ 真: プロンプト / 偽: 終了。分岐とプロンプトはグループの中 */
function branched(): FlowDoc {
  const doc: FlowDoc = {
    id: "wf",
    nodes: [
      { id: "start", type: "start", name: "開始", position: { x: 0, y: 0 }, data: { label: "開始" } },
      {
        id: "if-1",
        type: "ifElse",
        name: "分ける",
        position: { x: 200, y: 0 },
        data: { evaluationTarget: "x", branches: [{ label: "真", condition: "a" }, { label: "偽", condition: "b" }] },
      },
      { id: "p-1", type: "prompt", name: "書く", position: { x: 450, y: 0 }, data: { prompt: "書く" }, extra: { keep: true } },
      { id: "end", type: "end", name: "終了", position: { x: 450, y: 200 }, data: { label: "終了" } },
    ],
    connections: [
      { id: "c1", from: "start", to: "if-1", fromPort: "output", toPort: "input" },
      { id: "c2", from: "if-1", to: "p-1", fromPort: "branch-0", toPort: "input", condition: "真なら" },
      { id: "c3", from: "if-1", to: "end", fromPort: "branch-1", toPort: "input" },
      { id: "c4", from: "p-1", to: "end", fromPort: "output", toPort: "input" },
    ],
  };
  const grouped = groupNodes(doc, ["if-1", "p-1"]);
  assert.ok(grouped !== undefined);
  return grouped.doc;
}

test("CB-T271 開始へ入る線・終了から出る線・グループ・自分・無いノードへは線を引かない。connect も同じ規則で断る", () => {
  const doc = sample();
  assert.ok(canConnect(doc, "start-1", "end-1"));
  assert.ok(!canConnect(doc, "ask-1", "start-1"), "開始へは入らない");
  assert.ok(!canConnect(doc, "end-1", "mcp-1"), "終了からは出ない");
  assert.ok(!canConnect(doc, "ask-1", "ask-1"), "自分へは戻らない");
  assert.ok(!canConnect(doc, "ask-1", "nothing"), "無いノードへは引かない");
  assert.equal(connect(doc, "ask-1", "branch-0", "start-1", "input"), doc);
  assert.equal(connect(doc, "end-1", "output", "mcp-1", "input"), doc);
  assert.equal(connect(doc, "ask-1", "branch-0", "nothing", "input"), doc);
  const grouped = branched();
  const group = grouped.nodes.find((n) => n.type === "group");
  assert.ok(group !== undefined);
  assert.ok(!canConnect(grouped, "start", group.id));
  // 開始から終了への線は雛形に既にある
  assert.equal(connectionsOf(connect(templateFlow("x", ""), "start", "output", "end", "input")).length, 1, "同じ線は足さない");
});

test("CB-T272 写して貼ると id を振り直し、選んだノード同士の線だけ新しい id に付け替える。出口の表記と知らない欄は元のまま", () => {
  const doc = branched();
  const clip = copyNodes(doc, ["if-1", "p-1", "end"]);
  assert.ok(clip !== undefined);
  assert.deepEqual(clip.nodes.map((n) => n.id), ["if-1", "p-1", "end"]);
  // 片方しか写していない線（開始 → 分岐）は写さない
  assert.deepEqual(clip.connections.map((c) => c.id), ["c2", "c3", "c4"]);
  const pasted = pasteNodes(doc, clip);
  assert.deepEqual(pasted.ids, ["ifElse-1", "prompt-1", "end-1"]);
  const next = pasted.doc;
  assert.equal(next.nodes.length, doc.nodes.length + 3);
  // 元のノードと線はそのまま
  assert.deepEqual(next.nodes.slice(0, doc.nodes.length), doc.nodes);
  assert.deepEqual(connectionsOf(next).slice(0, 4), connectionsOf(doc));
  // 新しい線。出口の表記（branch-0 / branch-1）と条件はそのまま、両端は新しい id
  assert.deepEqual(connectionsOf(next).slice(4), [
    { id: "c-ifElse-1-prompt-1", from: "ifElse-1", to: "prompt-1", fromPort: "branch-0", toPort: "input", condition: "真なら" },
    { id: "c-ifElse-1-end-1", from: "ifElse-1", to: "end-1", fromPort: "branch-1", toPort: "input" },
    { id: "c-prompt-1-end-1", from: "prompt-1", to: "end-1", fromPort: "output", toPort: "input" },
  ]);
  // 中身と知らない欄は深いコピー（元を触っても貼ったものは変わらない）
  assert.deepEqual(byId(next, "prompt-1").extra, { keep: true });
  assert.deepEqual(byId(next, "ifElse-1").data, byId(doc, "if-1").data);
  assert.notEqual(byId(next, "ifElse-1").data, byId(doc, "if-1").data);
  // グループは写していないが、元のグループが残っているので同じグループの中で 40 ずらす
  const group = groupOf(doc, byId(doc, "if-1"));
  assert.ok(group !== undefined);
  assert.equal(byId(next, "ifElse-1").parentId, group.id);
  assert.deepEqual(absolutePosition(next, "ifElse-1"), { x: absolutePosition(doc, "if-1").x + 40, y: absolutePosition(doc, "if-1").y + 40 });
  // 終了は写せる。外に置いて 40 ずらす
  assert.equal(byId(next, "end-1").parentId, undefined);
  assert.deepEqual(byId(next, "end-1").position, { x: 490, y: 240 });
  // 同じものをもう 1 度貼ると、また別の id
  const again = pasteNodes(next, clip, { x: 80, y: 80 });
  assert.deepEqual(again.ids, ["ifElse-2", "prompt-2", "end-2"]);
});

test("CB-T273 開始は写さない。グループを写すと中のノードも一緒に写り、新しいグループの中で相対位置が同じ", () => {
  const doc = branched();
  assert.equal(copyNodes(doc, ["start"]), undefined, "開始だけなら写すものが無い");
  assert.equal(copyNodes(doc, []), undefined);
  assert.equal(duplicateNodes(doc, ["start"]), undefined);
  const withStart = copyNodes(doc, ["start", "end"]);
  assert.deepEqual(withStart?.nodes.map((n) => n.id), ["end"]);
  const group = doc.nodes.find((n) => n.type === "group");
  assert.ok(group !== undefined);
  const made = duplicateNodes(doc, [group.id]);
  assert.ok(made !== undefined);
  assert.deepEqual(made.ids, ["group-2", "ifElse-1", "prompt-1"]);
  const next = made.doc;
  // 新しいグループは中のノードより前に並ぶ
  const order = next.nodes.map((n) => n.id);
  assert.ok(order.indexOf("group-2") < order.indexOf("ifElse-1"));
  // 中のノードは新しいグループに入り、枠からの位置は元と同じ
  assert.equal(byId(next, "ifElse-1").parentId, "group-2");
  assert.equal(byId(next, "prompt-1").parentId, "group-2");
  assert.deepEqual(byId(next, "ifElse-1").position, byId(doc, "if-1").position);
  assert.deepEqual(byId(next, "prompt-1").position, byId(doc, "p-1").position);
  // 枠は 40 ずれる。大きさは元のまま
  assert.deepEqual(byId(next, "group-2").position, { x: (group.position as { x: number }).x + 40, y: (group.position as { y: number }).y + 40 });
  assert.deepEqual(byId(next, "group-2").style, group.style);
  // 中のノード同士の線（分岐の真 → プロンプト）は写る
  assert.deepEqual(connectionsOf(next).slice(4).map((c) => [c.from, c.fromPort, c.to]), [["ifElse-1", "branch-0", "prompt-1"]]);
  // 元のグループが消えていたら、写した時点の図の上の位置から 40 ずらして外に置く
  const clip = copyNodes(doc, ["p-1"]);
  assert.ok(clip !== undefined);
  const gone = removeNode(doc, group.id);
  const outside = pasteNodes(gone, clip);
  const placed = byId(outside.doc, outside.ids[0]);
  assert.equal(placed.parentId, undefined);
  assert.deepEqual(placed.position, { x: absolutePosition(doc, "p-1").x + 40, y: absolutePosition(doc, "p-1").y + 40 });
});

test("CB-T274 履歴は直す前の内容を積み、戻す・やり直すで行き来する。戻してから直すとやり直しは消える。上限を超えたら古いほうから捨てる", () => {
  const a = templateFlow("x", "");
  const b = addNode(a, "prompt", { x: 0, y: 0 }).doc;
  const c = renameNode(b, "prompt-1", "書く");
  let history = record(emptyHistory(), a);
  history = record(history, b);
  assert.ok(canUndo(history));
  assert.ok(!canRedo(history));
  const back = undo(history, c);
  assert.ok(back !== undefined);
  assert.equal(back.doc, b);
  const back2 = undo(back.history, back.doc);
  assert.ok(back2 !== undefined);
  assert.equal(back2.doc, a);
  assert.equal(undo(back2.history, a), undefined, "もう戻せない");
  const forward = redo(back2.history, a);
  assert.ok(forward !== undefined);
  assert.equal(forward.doc, b);
  // 戻してから別の操作をすると、やり直しのリストは消える
  const branched = record(forward.history, b);
  assert.ok(!canRedo(branched));
  assert.equal(redo(branched, b), undefined);
  // 上限
  let many = emptyHistory();
  for (let i = 0; i < HISTORY_LIMIT + 5; i += 1) {
    many = record(many, renameNode(a, "start", `n${i}`));
  }
  assert.equal(many.past.length, HISTORY_LIMIT);
  assert.equal(many.past[0].nodes[0].name, "n5");
  assert.equal(record(emptyHistory(), a, { limit: 2 }).past.length, 1);
});

test("CB-T275 同じ欄に続けて打った字は 1 件にまとめる。間が空く・別の欄・区切り（フォーカスが外れた）・別の操作なら別の 1 件", () => {
  const a = templateFlow("x", "");
  const typed = (text: string): FlowDoc => patchData(a, "start", { label: text });
  let history = record(emptyHistory(), a, { key: "node:start:label", now: 1000 });
  history = record(history, typed("a"), { key: "node:start:label", now: 1200 });
  history = record(history, typed("ab"), { key: "node:start:label", now: 1400 });
  assert.equal(history.past.length, 1, "続けて打った 3 字で 1 件");
  assert.equal(history.past[0], a, "残るのは打つ前の内容");
  // 間が空いた
  history = record(history, typed("abc"), { key: "node:start:label", now: 1400 + MERGE_MS + 1 });
  assert.equal(history.past.length, 2);
  // 別の欄
  history = record(history, typed("abcd"), { key: "node:start:name", now: 1400 + MERGE_MS + 2 });
  assert.equal(history.past.length, 3);
  // 区切った後は同じ欄でも別の 1 件
  history = seal(history);
  history = record(history, typed("abcde"), { key: "node:start:name", now: 1400 + MERGE_MS + 3 });
  assert.equal(history.past.length, 4);
  // 欄でない操作（key 無し）の後も別の 1 件
  history = record(history, typed("x"));
  history = record(history, typed("xy"), { key: "node:start:name", now: 1400 + MERGE_MS + 4 });
  assert.equal(history.past.length, 6);
  // 区切りは何も積んでいなければ同じものを返す
  const plain = emptyHistory();
  assert.equal(seal(plain), plain);
  // 戻すと区切られる
  const back = undo(history, typed("xyz"));
  assert.ok(back !== undefined);
  const after = record(back.history, typed("q"), { key: "node:start:name", now: 1400 + MERGE_MS + 5 });
  assert.equal(after.past.length, back.history.past.length + 1);
});

test("CB-T276 未保存の見比べはキーの順序を見ず、差分は足した・消した・変えたノードと線と、フローの欄を言う", () => {
  const doc = branched();
  assert.ok(sameFlow(doc, JSON.parse(JSON.stringify(doc)) as FlowDoc));
  assert.ok(sameValue({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 }));
  assert.ok(!sameValue({ a: 1 }, { a: 1, b: 2 }));
  assert.ok(!sameValue([1, 2], [2, 1]));
  assert.ok(!sameValue(1, "1"));
  assert.ok(isEmptyDiff(diffFlows(doc, doc)));
  // 直して元の中身に戻せば同じ
  const renamed = renameNode(doc, "p-1", "別名");
  assert.ok(!sameFlow(doc, renamed));
  assert.ok(sameFlow(doc, renameNode(renamed, "p-1", "書く")));

  let next = removeNode(doc, "end");
  next = addNode(next, "skill", { x: 0, y: 400 }).doc;
  next = renameNode(next, "p-1", "書き直す");
  next = connect(next, "p-1", "output", "skill-1", "input");
  next = setConditionAt(next, 1, "変えた");
  next = { ...next, name: "新しい名前" };
  const diff = diffFlows(doc, next);
  assert.deepEqual(diff.meta, ["名前"]);
  assert.deepEqual(diff.addedNodes.map((n) => n.id), ["skill-1"]);
  assert.deepEqual(diff.removedNodes.map((n) => [n.id, n.label]), [["end", "終了（end）"]]);
  assert.deepEqual(diff.changedNodes.map((n) => [n.id, n.fields]), [["p-1", ["名前"]]]);
  assert.deepEqual(diff.addedConnections.map((c) => c.label), ["書き直す → スキル"]);
  // 終了を消したので、終了に入る線が 2 本消えた
  assert.deepEqual(diff.removedConnections.map((c) => c.label), ["分ける → 終了（branch-1）", "書く → 終了"]);
  assert.deepEqual(diff.changedConnections.map((c) => [c.label, c.fields]), [["分ける → 書き直す（branch-0）", ["条件"]]]);
  assert.ok(!isEmptyDiff(diff));
});

test("CB-T277 画面から届く保存用の編集中の内容と、保存前の確かめの設定は形を確かめてから受ける", () => {
  const doc = templateFlow("i0001-01", "調査");
  assert.deepEqual(asFlowMessage({ type: "draft", doc }), { type: "draft", doc });
  assert.deepEqual(asFlowMessage({ type: "draft", doc: null }), { type: "draft", doc: null });
  assert.equal(asFlowMessage({ type: "draft", doc: { nodes: "x" } }), undefined);
  assert.equal(asFlowMessage({ type: "draft" }), undefined);
  assert.deepEqual(asFlowMessage({ type: "reviewSave", value: false }), { type: "reviewSave", value: false });
  assert.equal(asFlowMessage({ type: "reviewSave", value: "no" }), undefined);
});

test("CB-T279 確かめ直しの頼みは番号と読める内容があるときだけ受ける。実行ファイルの答えがあれば、開始が無いという画面の注意は出さない", () => {
  const doc = templateFlow("i0001-01", "調査");
  assert.deepEqual(asFlowMessage({ type: "check", seq: 3, doc }), { type: "check", seq: 3, doc });
  assert.equal(asFlowMessage({ type: "check", seq: "3", doc }), undefined);
  assert.equal(asFlowMessage({ type: "check", seq: Number.NaN, doc }), undefined);
  assert.equal(asFlowMessage({ type: "check", seq: 1, doc: { nodes: 1 } }), undefined);
  // 開始が無い: 画面だけなら画面が言い、実行ファイルの答えがあれば（実行ファイルが「start が無い」と言う）言わない
  const noStart = removeNode(doc, "start");
  assert.ok(flowNotices(noStart).some((n) => /開始（start）のノードが無い/.test(n)));
  assert.ok(!flowNotices(noStart, { exe: true }).some((n) => /開始（start）のノードが無い/.test(n)));
  // 開始が 2 つは実行ファイルが言わないので、答えがあっても画面が言う
  const twoStarts = addNode(doc, "start", { x: 0, y: 400 }).doc;
  assert.ok(flowNotices(twoStarts, { exe: true }).some((n) => /開始（start）のノードが 2 つある/.test(n)));
});
