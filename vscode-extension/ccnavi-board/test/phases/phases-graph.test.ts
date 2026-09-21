/**
 * フェーズ管理画面の図を組む純関数（`src/core/phases-graph.ts`）。
 *
 * 見るところは 3 つ。**線に向きが無いこと**（`requires` は同席の条件で、順序ではない）、
 * **判定をしないこと**（循環も到達不能も見つけない。ADR-0035）、そして
 * **置き場所が id だけで決まること**（保存のたびに中身が届き直すので、並びが変わると絵が飛ぶ）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { graphOf } from "../../src/core/phases-graph.js";
import type { PhaseForm, PhasesForm } from "../../src/core/phases-view.js";

/** 種類 1 つ。要るところだけ渡す */
function phase(id: string, overrides: Partial<PhaseForm> = {}): PhaseForm {
  return { origin: null, id, title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], overlap: [], requires: [], agent: "", when: "", ...overrides };
}

function form(...phases: PhaseForm[]): PhasesForm {
  return { phases };
}

/** 線を `関係:a--b` の並びで取る（順は graphOf が決める） */
function edgeIds(f: PhasesForm): string[] {
  return graphOf(f).edges.map((edge) => edge.id);
}

test("CB-T185 requires と overlap は向きの無い 1 本の線になり、両側から挙げても重ならない", () => {
  // 片側だけが挙げている
  assert.deepEqual(edgeIds(form(phase("b", { requires: ["a"] }), phase("a"))), ["requires:a--b"]);
  // 両側が挙げている。同じ組なので 1 本
  assert.deepEqual(edgeIds(form(phase("a", { requires: ["b"] }), phase("b", { requires: ["a"] }))), ["requires:a--b"]);
  // overlap も同じ。requires と overlap の両方があれば、関係ごとに 1 本ずつ
  assert.deepEqual(edgeIds(form(phase("a", { overlap: ["b"] }), phase("b", { requires: ["a"] }))), ["overlap:a--b", "requires:a--b"]);
  // 線は a < b の順で持つ。どちらが挙げたかは残さない（残すと向きになる）
  for (const edge of graphOf(form(phase("z", { requires: ["a"] }), phase("a"))).edges) {
    assert.ok(edge.a < edge.b, `${edge.a} < ${edge.b}`);
  }
});

test("CB-T186 図は判定をしない（循環も、行き先の無い参照も、そのまま並べるだけ）", () => {
  // a → b → c → a。実行ファイルは循環を見ないので、画面も見ない。線が 3 本あるだけ
  const cycle = form(phase("a", { requires: ["b"] }), phase("b", { requires: ["c"] }), phase("c", { requires: ["a"] }));
  const graph = graphOf(cycle);
  assert.deepEqual(graph.edges.map((edge) => edge.id), ["requires:a--b", "requires:a--c", "requires:b--c"]);
  assert.equal(graph.nodes.length, 3);
  // 図の形に「循環」「不正」を名指しする欄は無い
  assert.deepEqual(Object.keys(graph).sort(), ["edges", "nodes", "unnamed"]);

  // このファイルに無い種類への参照は、黙って線にならない（共通層の種類かもしれないので、無いとは言わない）
  assert.deepEqual(edgeIds(form(phase("a", { requires: ["外の種類"] }))), []);
  // 自分自身への参照も線にしない（--lint が警告する。画面は何も言わない）
  assert.deepEqual(edgeIds(form(phase("a", { requires: ["a"], overlap: ["a"] }))), []);
});

test("CB-T187 置き場所は id の集合だけで決まる（並べ替えても同じ絵になる）", () => {
  const a = phase("a", { requires: ["b"] });
  const b = phase("b");
  const c = phase("c");
  const forward = graphOf(form(a, b, c));
  const backward = graphOf(form(c, b, a));
  assert.deepEqual(forward, backward, "ファイルの並びが変わると絵が変わっている");

  // 関係の無い欄を直しても動かない（保存のたびに中身が届き直すので、ここが動くと絵が飛ぶ）
  const edited = graphOf(form({ ...a, when: "打ち直した", scope: ["x/*"], inherit: false }, b, c));
  assert.deepEqual(edited.nodes.map((node) => [node.id, node.x, node.y]), forward.nodes.map((node) => [node.id, node.x, node.y]));

  // 繋がっている組は同じ行に並ぶ。独りの種類はそのあと
  const place = new Map(forward.nodes.map((node) => [node.id, { x: node.x, y: node.y }]));
  assert.equal(place.get("a")?.y, place.get("b")?.y, "繋がった組が同じ行にいない");
  assert.notEqual(place.get("c")?.y, place.get("a")?.y, "独りの種類が組と同じ行にいる");
  // 同じ場所に 2 つ置かない
  const spots = forward.nodes.map((node) => `${node.x},${node.y}`);
  assert.equal(new Set(spots).size, spots.length, "点が重なっている");
});

test("CB-T188 id が空の種類は図に出ず、数だけ返る。同じ id は先に出てきたほうだけ", () => {
  const graph = graphOf(form(phase("a"), phase("  "), phase(""), phase("a", { title: "あと" })));
  assert.deepEqual(graph.nodes.map((node) => node.id), ["a"]);
  assert.equal(graph.nodes[0].title, "", "後ろの同じ id で上書きされている");
  assert.equal(graph.unnamed, 2, "id が空の種類を数えていない");
});

test("CB-T189 点は id・題・区分・レビューを持ち、前後の空白は落とす", () => {
  const graph = graphOf(form(phase(" a ", { title: " 調査 ", kind: "feedback", review: "none", requires: [" b "] }), phase("b")));
  assert.deepEqual(graph.nodes[0], { id: "a", title: "調査", kind: "feedback", review: "none", x: graph.nodes[0].x, y: graph.nodes[0].y });
  // 参照の側の空白も落として突き合わせる（落とさないと線にならない）
  assert.deepEqual(graph.edges.map((edge) => edge.id), ["requires:a--b"]);
});
