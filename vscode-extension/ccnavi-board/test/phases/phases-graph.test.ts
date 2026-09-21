/**
 * フェーズ管理画面の図を組む純関数（`src/core/phases-graph.ts`）。
 *
 * 見るところは 4 つ。**線に向きが無いこと**（`requires` は一緒に置く条件で、順序ではない）、
 * **判定をしないこと**（循環も到達不能も見つけない。ADR-0035）、**置き場所が id だけで
 * 決まること**（保存のたびに中身が届き直すので、関係を直して絵が飛ぶと使いものにならない）、
 * そして**線が黙って消えないこと**（id にハイフンが使えるので、名前の作り方を誤ると潰れる）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { graphOf, keepSpots, withSpot } from "../../src/core/phases-graph.js";
import type { PhaseForm, PhasesForm } from "../../src/core/phases-view.js";

/** 種類 1 つ。要るところだけ渡す */
function phase(id: string, overrides: Partial<PhaseForm> = {}): PhaseForm {
  return { origin: null, id, title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], overlap: [], requires: [], agent: "", when: "", ...overrides };
}

function form(...phases: PhaseForm[]): PhasesForm {
  return { phases };
}

/** 線を `関係 a b` の組で取る（名前そのものは読む形を約束しない） */
function edges(f: PhasesForm): string[][] {
  return graphOf(f).edges.map((edge) => [edge.relation, edge.a, edge.b]);
}

/** 点の置き場所 */
function spots(f: PhasesForm): Record<string, string> {
  return Object.fromEntries(graphOf(f).nodes.map((node) => [node.id, `${node.x},${node.y}`]));
}

test("CB-T185 requires と overlap は向きの無い 1 本の線になり、両側から挙げても重ならない", () => {
  // 片側だけが挙げている
  assert.deepEqual(edges(form(phase("b", { requires: ["a"] }), phase("a"))), [["requires", "a", "b"]]);
  // 両側が挙げている。同じ組なので 1 本
  assert.deepEqual(edges(form(phase("a", { requires: ["b"] }), phase("b", { requires: ["a"] }))), [["requires", "a", "b"]]);
  // overlap も同じ。requires と overlap の両方があれば、関係ごとに 1 本ずつ
  assert.deepEqual(edges(form(phase("a", { overlap: ["b"] }), phase("b", { requires: ["a"] }))), [
    ["overlap", "a", "b"],
    ["requires", "a", "b"],
  ]);
  // 線は a < b の順で持つ。どちらが挙げたかは残さない（残すと向きになる）
  for (const edge of graphOf(form(phase("z", { requires: ["a"] }), phase("a"))).edges) {
    assert.ok(edge.a < edge.b, `${edge.a} < ${edge.b}`);
  }
});

test("CB-T186 図は判定をしない（循環も、行き先の無い参照も、そのまま並べるだけ）", () => {
  // a → b → c → a。実行ファイルは循環を見ないので、画面も見ない。線が 3 本あるだけ
  const cycle = form(phase("a", { requires: ["b"] }), phase("b", { requires: ["c"] }), phase("c", { requires: ["a"] }));
  const graph = graphOf(cycle);
  assert.equal(graph.edges.length, 3);
  assert.equal(graph.nodes.length, 3);
  // 図の形に「循環」「不正」を名指しする欄は無い
  assert.deepEqual(Object.keys(graph).sort(), ["edges", "nodes", "unnamed"]);

  // このファイルに無い種類への参照は、黙って線にならない（綴り違いか他の層かは、画面は言わない）
  assert.deepEqual(edges(form(phase("a", { requires: ["外の種類"] }))), []);
  // 自分自身への参照も線にしない（--lint が警告する。画面は何も言わない）
  assert.deepEqual(edges(form(phase("a", { requires: ["a"], overlap: ["a"] }))), []);
});

test("CB-T187 置き場所は id だけで決まる。関係を直しても、触っていない点は動かない", () => {
  const base = [phase("acceptance", { overlap: ["implement"] }), phase("implement", { requires: ["acceptance"] }), phase("design"), phase("docs"), phase("research"), phase("staging")];
  const before = spots(form(...base));

  // 並べ替えても同じ
  assert.deepEqual(spots(form(...base.slice().reverse())), before, "ファイルの並びが変わると絵が変わっている");

  // **関係を 1 本足しても、どの点も動かない。** 保存が通るたびに中身は丸ごと届き直すので、
  // 関係を直しながら確かめる間に絵が組み替わると、この画面の用を成さない
  const linked = base.map((p) => (p.id === "docs" ? { ...p, requires: ["design"] } : p));
  assert.deepEqual(spots(form(...linked)), before, "関係を足したら点が動いた");

  // 綴りを間違えて線が落ちても動かない
  const typo = base.map((p) => (p.id === "staging" ? { ...p, requires: ["acceptence"] } : p));
  assert.deepEqual(spots(form(...typo)), before, "行き先の無い参照で点が動いた");

  // 関係の無い欄を直しても動かない
  const edited = base.map((p) => (p.id === "docs" ? { ...p, when: "打ち直した", scope: ["x/*"], inherit: false } : p));
  assert.deepEqual(spots(form(...edited)), before, "関係と無関係な編集で点が動いた");

  // 同じ場所に 2 つ置かない
  const places = Object.values(before);
  assert.equal(new Set(places).size, places.length, "点が重なっている");
});

test("CB-T188 id が空の種類は図に出ず、数だけ返る。同じ id は先に出てきたほうだけ", () => {
  const graph = graphOf(form(phase("a"), phase("  "), phase(""), phase("a", { title: "あと", requires: ["b"] }), phase("b")));
  assert.deepEqual(graph.nodes.map((node) => node.id), ["a", "b"]);
  assert.equal(graph.nodes[0].title, "", "後ろの同じ id で上書きされている");
  assert.equal(graph.unnamed, 2, "id が空の種類を数えていない");
  // 後ろの重複が持つ関係は線にしない。出ている点の欄に無い線が描かれることになるため
  assert.deepEqual(edges(form(phase("a"), phase("a", { requires: ["b"] }), phase("b"))), []);
});

test("CB-T189 点は id・題・区分・レビューを持ち、前後の空白は落とす", () => {
  const graph = graphOf(form(phase(" a ", { title: " 調査 ", kind: "feedback", review: "none", requires: [" b "] }), phase("b")));
  assert.deepEqual(graph.nodes[0], { id: "a", title: "調査", kind: "feedback", review: "none", x: graph.nodes[0].x, y: graph.nodes[0].y });
  // 参照の側の空白も落として突き合わせる（落とさないと線にならない）
  assert.deepEqual(edges(form(phase(" a ", { requires: [" b "] }), phase("b"))), [["requires", "a", "b"]]);
});

test("CB-T190b id にハイフンが入っていても、線が別の線に潰されない", () => {
  // id はハイフンを含められる（phasetypes.py の _ID は [A-Za-z0-9._-]）。線の名前を
  // `関係:a--b` と繋げると、この 2 組が同じ文字列になり、片方が黙って消える
  const graph = graphOf(form(phase("x", { requires: ["y--z"] }), phase("y--z"), phase("x--y", { requires: ["z"] }), phase("z")));
  assert.equal(graph.edges.length, 2, "ハイフンを含む id で線が消えている");
  assert.deepEqual(
    graph.edges.map((edge) => [edge.a, edge.b]).sort(),
    [
      ["x", "y--z"],
      ["x--y", "z"],
    ].sort(),
  );
  // 名前は線ごとに違う（React Flow は名前で 1 本と数える）
  assert.equal(new Set(graph.edges.map((edge) => edge.id)).size, 2);
});

test("CB-T191 控えは、動かした点を丸めて入れ、図から消えた種類を落とす", () => {
  // 掴んで離す仕草は自動で試せない（d3-drag が happy-dom の下で終わらない）。
  // 仕草が呼ぶ中身はここで見る
  assert.deepEqual(withSpot({}, "a", 10.4, 20.6), { a: { x: 10, y: 21 } });
  assert.deepEqual(withSpot({ a: { x: 1, y: 2 } }, "b", 3, 4), { a: { x: 1, y: 2 }, b: { x: 3, y: 4 } });
  // 同じ種類を動かし直すと上書き
  assert.deepEqual(withSpot({ a: { x: 1, y: 2 } }, "a", 9, 9), { a: { x: 9, y: 9 } });

  // 図に出ている種類の控えだけを残す（id を打ち替えるたびに溜まるため）
  assert.deepEqual(keepSpots({ a: { x: 1, y: 2 }, b: { x: 3, y: 4 } }, ["a"]), { a: { x: 1, y: 2 } });
  // 変わらないときは、同じものをそのまま返す（返す形が変わると図が描き直される）
  const same = { a: { x: 1, y: 2 } };
  assert.equal(keepSpots(same, ["a", "b"]), same);
  assert.equal(keepSpots(same, ["a"]), same);
});
