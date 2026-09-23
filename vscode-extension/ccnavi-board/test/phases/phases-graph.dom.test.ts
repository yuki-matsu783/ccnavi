/**
 * フェーズ管理画面の図を、束ねた 1 本のまま動かす。
 *
 * **大きさの偽物が要る**（`openGraph` が渡す `measure`）。happy-dom の `ResizeObserver` は
 * 何もしないので、細工をしないと React Flow は点を隠したまま線を 1 本も描かず、
 * 「空の絵」を見て緑になる。だから最初に「線が本当に描かれていること」を見る。
 *
 * ここで見ないもの: 線の経路と、パン・ズーム（大きさを偽っているので、座標の正しさは見られない）。
 * それは README の手動確認に回す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { openGraph, openGraphJsdom, openPhases } from "../helpers/phases.js";
import { readPhases } from "../../src/core/phases-doc.js";

/** 2 つの種類が requires で結ばれ、1 つは独り。線は 1 本 */
const LINKED = `version: 1
phases:
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    overlap: [implement]
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    requires: [acceptance]
  docs:
    kind: work
    title: 文書
    review: mr
    requires: [外の層の種類]
`;

function model(text: string) {
  return readPhases(text).model;
}

test("CB-D81 図は点と線を描く（線が 0 本なら、それは描けていないということ）", async () => {
  const dom = await openGraph({ model: model(LINKED) });
  try {
    assert.equal(dom.all(".react-flow__node").length, 3, "点が 3 つ出ていない");
    // acceptance と implement は overlap と requires の両方で結ばれるので 2 本。
    // docs の requires は行き先がこのファイルに無いので線にならない
    assert.equal(dom.all(".react-flow__edge").length, 2, "線が描けていない（測定の偽物が効いていない）");
    assert.equal(dom.all(".react-flow__edge.rel-requires").length, 1);
    assert.equal(dom.all(".react-flow__edge.rel-overlap").length, 1);
    // 点は隠れていない（測れていない点は visibility: hidden で置かれる）
    for (const node of dom.all(".react-flow__node")) {
      assert.notEqual((node as unknown as { style: { visibility: string } }).style.visibility, "hidden", "測れていない点がある");
    }
    // 矢印は付けない（向きが無い）
    assert.equal(dom.all(".react-flow__arrowhead, marker").length, 0, "線に矢印が付いている");
  } finally {
    await dom.close();
  }
});

test("CB-D74 図の下は凡例と、当てはまるときだけの注意。線が落ちた理由は断定しない", async () => {
  const dom = await openGraph({ model: model(LINKED) });
  try {
    const legend = dom.one(".graph-legend").textContent ?? "";
    assert.match(legend, /先に済ませる（after）/);
    assert.match(legend, /一緒に必要（requires）/);
    assert.match(legend, /並行できる（overlap）/);
    assert.match(legend, /枠の間の矢印はレビュー後の順/);
    // docs の requires はこのファイルに無い種類を指すので、線にしていないと件数で言う
    const notes = dom.all(".graph-note").map((note) => note.textContent ?? "");
    assert.deepEqual(notes.length, 1);
    assert.match(notes[0], /このファイルに無い種類を指す関係が 1 件あり、線にしていない/);
    // 線が落ちた理由は断定しない（綴り違いかもしれない。ADR-0035）。良し悪しも言わない
    assert.doesNotMatch(notes[0], /他の層の種類を指す/);
    assert.doesNotMatch(notes[0], /循環|不正|エラー|直して/);
    // sequential でも after が無ければ、効かないという注意は出さない
    assert.doesNotMatch(notes.join(""), /sequential/);
    // 「人が見る」の意味は札のツールチップにある
    assert.match(dom.one(".tag.hitl").getAttribute("title") ?? "", /種類の宣言（review）/);
  } finally {
    await dom.close();
  }

  // sequential なのに after がある。矢印が判定に効かないことを言う
  const seq = await openGraph({ model: model("version: 1\nphases:\n  a:\n    kind: work\n    review: mr\n  b:\n    kind: work\n    review: mr\n    after: [a]\n") });
  try {
    assert.match(seq.one(".graph-note").textContent ?? "", /待ち方が sequential なので、矢印（after）は判定に効かない/);
  } finally {
    await seq.close();
  }
});

test("CB-D75 点を押すと一覧へ戻り、その種類の行が開く", async () => {
  const dom = await openGraph({ model: model(LINKED) });
  try {
    assert.ok(dom.one("#phases").className.includes("hidden"), "図を出しているのに一覧が出ている");
    const node = dom.all('.react-flow__node[data-id="implement"]')[0];
    assert.ok(node !== undefined, "implement の点が無い");
    dom.click(node);
    await dom.settle();
    // 一覧に戻り、その行が開いている
    assert.ok(!dom.one("#phases").className.includes("hidden"), "一覧に戻っていない");
    assert.deepEqual((dom.state() as { open?: string[] }).open, ["implement"]);
    assert.equal((dom.state() as { view?: string }).view, "list");
  } finally {
    await dom.close();
  }
});

test("CB-D76 一覧と図はタブで切り替わり、見ていたほうは控えに残る", async () => {
  const dom = await openPhases();
  try {
    // 既定は一覧
    assert.ok(!dom.one("#phases").className.includes("hidden"));
    assert.equal(dom.all("#phase-graph").length, 0);
    dom.click(dom.one('[data-action="show-graph"]'));
    await dom.settle();
    assert.equal((dom.state() as { view?: string }).view, "graph");
    assert.ok(dom.one("#phases").className.includes("hidden"), "図にしたのに一覧が出ている");
    // 絞り込みは一覧のものなので、図では出さない
    assert.equal(dom.all("#find").length, 0);
    dom.click(dom.one('[data-action="show-list"]'));
    await dom.settle();
    assert.equal((dom.state() as { view?: string }).view, "list");
    assert.equal(dom.all("#find").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D77 控えてある位置で点が置かれ、図を触っても phases.yml には渡らない", async () => {
  const dom = await openGraph({ model: model(LINKED) }, { spots: { implement: { x: 40, y: 80 } } });
  try {
    // 控えてある位置で置かれる（React Flow は CSSOM で transform を当てるので、style に出る）
    const node = dom.all('.react-flow__node[data-id="implement"]')[0];
    assert.match((node as unknown as { style: { transform: string } }).style.transform, /translate\(40px,\s*80px\)/);

    // 図を触っても保存には渡らない（座標は人が持つ設定に入れない）
    assert.deepEqual(dom.posted.filter((message) => message.type === "save"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D80 点を掴んで離すと、その位置が控えに入る（jsdom）", async () => {
  // **この 1 本だけ jsdom で走る。** happy-dom では d3-drag の待ちが終わらず固まる
  // （`test/helpers/jsdom.ts` の頭）。控えに入る道（onNodeDragStop → withSpot → saveSpots）は
  // ここでしか通らない
  const dom = await loadDrag();
  try {
    const node = dom.one('.react-flow__node[data-id="implement"]');
    const before = (node as unknown as { style: { transform: string } }).style.transform;
    await dom.drag(node, 60, 40);
    const after = (node as unknown as { style: { transform: string } }).style.transform;
    assert.notEqual(after, before, "掴んで離しても点が動いていない");

    const spots = (dom.state() as { spots?: Record<string, { x: number; y: number }> }).spots ?? {};
    assert.deepEqual(Object.keys(spots), ["implement"], "動かした種類の控えが無い");
    assert.ok(Number.isFinite(spots.implement.x) && Number.isFinite(spots.implement.y), "控えが数でない");
    // 動いた先は図の倍率で決まるので、値そのものは約束しない

    // ドラッグしても保存には渡らない（座標は人が持つ設定に入れない）
    assert.deepEqual(dom.posted.filter((message) => message.type === "save"), []);
  } finally {
    dom.close();
  }
});

function loadDrag(): ReturnType<typeof openGraphJsdom> {
  return openGraphJsdom({ model: model(LINKED) });
}

test("CB-D78 id が空の種類は図に出ず、その数を一言が言う", async () => {
  const dom = await openGraph({ model: model("version: 1\nphases:\n  a:\n    kind: work\n    review: mr\n") });
  try {
    assert.equal(dom.all(".react-flow__node").length, 1);
    // 空の id が無いときは、その行を出さない
    assert.equal(dom.all(".graph-note").length, 0);
  } finally {
    await dom.close();
  }

  // 一覧で種類を足すと id が空の行が 1 つできる。図はその数を言う
  const added = await openPhases();
  try {
    added.click(added.one('[data-action="add"]'));
    await added.settle();
    added.click(added.one('[data-action="show-graph"]'));
    await added.settle();
    assert.match(added.one(".graph-note").textContent ?? "", /id が空の種類は図に出ない（1 件）/);
  } finally {
    await added.close();
  }
});

test("CB-D79 同じ組が requires と overlap の両方を持つとき、2 本が重ならない", async () => {
  // このリポジトリの設定（acceptance と implement）と雛形が、まさにこの形。
  // 同じ端どうしを結ぶと破線が実線の下に隠れ、overlap が 1 本も見えなくなる
  const dom = await openGraph({ model: model(LINKED) });
  try {
    const solid = dom.one(".react-flow__edge.rel-requires path.react-flow__edge-path");
    const dashed = dom.one(".react-flow__edge.rel-overlap path.react-flow__edge-path");
    const a = solid.getAttribute("d") ?? "";
    const b = dashed.getAttribute("d") ?? "";
    assert.ok(a !== "" && b !== "", "線の経路が空");
    assert.notEqual(a, b, "requires と overlap が同じ経路で描かれている（破線が実線の下に隠れる）");
    // 線にラベルは付けない（同じ組の 2 本はラベルが重なって読めない）。読み方は下の一言が言う
    assert.equal(dom.all(".react-flow__edge-text").length, 0, "線にラベルが付いている");
  } finally {
    await dom.close();
  }
});

test("CB-D89 雛形の図は after の矢印で流れを描き、work と feedback を枠で分けて「レビュー後」の矢印で結ぶ", async () => {
  // 雛形は dag で、調査 → 設計と受入テスト作成 → 実装とテスト。implement-feedback は feedback の枠
  const dom = await openGraph();
  try {
    const after = dom.all(".react-flow__edge.rel-after path.react-flow__edge-path");
    assert.equal(after.length, 4, "research→design、research→acceptance、design→implement、acceptance→implement");
    for (const path of after) {
      assert.ok(path.getAttribute("marker-end") !== null || path.getAttribute("marker-start") !== null, "after の線に矢印が無い");
      assert.notEqual(path.getAttribute("d") ?? "", "", "線の経路が空");
    }
    assert.match(dom.one('.phase-group[data-kind="work"]').textContent ?? "", /作業（plan:）/);
    assert.match(dom.one('.phase-group[data-kind="feedback"]').textContent ?? "", /フィードバック対応（feedback:）/);
    assert.match(dom.one(".phase-group-arrow").textContent ?? "", /レビュー後/);
    // 雛形は dag で、落ちた線も id の空の種類も無いので、注意は 1 つも出ない
    assert.equal(dom.all(".graph-note").length, 0);
  } finally {
    await dom.close();
  }
});
