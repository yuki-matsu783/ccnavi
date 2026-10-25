/**
 * 計画の図の中身（`src/core/plan-graph.ts`）。承認のオーバーレイと計画編集タブが共有する図の部品
 * （`webview/shared/PlanGraph.tsx`）は、これが組んだ点と線をそのまま描く。DOM に触れないので単体で試す。
 *
 * 図は判定をしない。どのステップが終端に届かないか（`loose`）、すぐ始まるか（`ready`）、延期を誰が引き受けるか
 * （`review_at`）は実行ファイルが `plans` に入れて返し、図は印を付けるだけ。図が自分で組むのは、線と点だけで
 * 決まる形（start・end の仮の点と線、置き場所、引けない線）まで。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { ApprovePlan } from "../../src/core/approvemodel.js";
import { canConnect, canDisconnect, COLUMN, FIT_START_PAD, fitStalled, layoutOf, PLAN_FIT_MIN_ZOOM, planGraphOf, viewportAtStart, withLayout, type PlanGraph } from "../../src/core/plan-graph.js";
import { plansOf, reorderCases, type RawItem } from "../helpers/plan-reorder.js";

function plan(items: readonly RawItem[], extra: Partial<ApprovePlan> = {}, feedback?: readonly RawItem[]): ApprovePlan {
  const plans = plansOf(items, feedback);
  return { ...(feedback === undefined ? plans[0] : plans[1]), ...extra };
}

/** 線を「元 → 先」で並べる（id は見ない） */
function lines(graph: PlanGraph): string[] {
  return graph.edges.map((edge) => `${edge.source}->${edge.target}`).sort();
}

const DIAMOND: readonly RawItem[] = ["research", { type: "design", after: [1] }, { type: "acceptance", after: [1] }, { type: "implement", after: [2, 3] }];

test("CB-T355 ステップごとに点を置き、after を線にする。先行のないステップへ start から、後続のないステップから end へ仮の線を引く", () => {
  const graph = planGraphOf(plan(DIAMOND));
  assert.deepEqual(
    graph.nodes.map((node) => [node.id, node.kind, node.number]),
    [
      ["start", "start", null],
      ["n1", "item", 1],
      ["n2", "item", 2],
      ["n3", "item", 3],
      ["n4", "item", 4],
      ["end", "end", null],
    ],
  );
  assert.deepEqual(lines(graph), ["n1->n2", "n1->n3", "n2->n4", "n3->n4", "n4->end", "start->n1"].sort());
  assert.deepEqual(
    graph.edges.filter((edge) => edge.kind !== "after").map((edge) => [edge.source, edge.target, edge.kind]).sort(),
    [
      ["n4", "end", "end"],
      ["start", "n1", "start"],
    ],
  );
  // 全体計画の start と end の名前。保存しない図だけの点
  assert.equal(graph.nodes[0].label, "開始");
  assert.equal(graph.nodes[graph.nodes.length - 1].label, "終了");
  // 点は番号と題を持つ
  assert.deepEqual(graph.nodes.filter((node) => node.kind === "item").map((node) => node.label), ["research", "design", "acceptance", "implement"]);
});

test("CB-T356 フィードバック計画の start は「全体計画のレビュー後」、end は「親をクローズする」。番号は全体計画の続きのまま描く", () => {
  const feedback = plan(["design"], {}, ["design-feedback", "skill-improve", { type: "implement-feedback", after: [2, 3] }]);
  const graph = planGraphOf(feedback);
  assert.equal(graph.part, "feedback");
  assert.equal(graph.nodes[0].label, "全体計画のレビュー後");
  assert.equal(graph.nodes[graph.nodes.length - 1].label, "親をクローズする");
  assert.deepEqual(graph.nodes.filter((node) => node.kind === "item").map((node) => node.number), [2, 3, 4]);
  assert.deepEqual(lines(graph), ["n2->n4", "n3->n4", "n4->end", "start->n2", "start->n3"].sort());
});

test("CB-T357 終端に届かないステップは実行ファイルの loose どおりに印を付け、そのステップから end への線も印を付ける。loose が空なら形が同じでも付けない", () => {
  const items: readonly RawItem[] = ["research", "design", { type: "implement", after: [1] }];
  const marked = planGraphOf(plan(items, { loose: [2] }));
  assert.deepEqual(marked.nodes.filter((node) => node.loose).map((node) => node.id), ["n2"]);
  assert.deepEqual(marked.edges.filter((edge) => edge.loose).map((edge) => `${edge.source}->${edge.target}`), ["n2->end"]);
  assert.deepEqual(lines(marked), ["n1->n3", "n2->end", "n3->end", "start->n1", "start->n2"].sort());
  // 図は自分で「どれが終端に届かないか」を決めない。実行ファイルが言わなければ印は無い
  const plain = planGraphOf(plan(items, { loose: [] }));
  assert.deepEqual(plain.nodes.filter((node) => node.loose), []);
  assert.deepEqual(plain.edges.filter((edge) => edge.loose), []);
});

test("CB-T358 点は定義の題・見る場所・延期の引き受け手・固定した番号を実行ファイルの値のまま持つ", () => {
  const base = plan(DIAMOND);
  const graph = planGraphOf({
    ...base,
    items: base.items.map((item) =>
      item.number === 2 ? { ...item, title: "設計", review: "mr", deferred: true, review_at: 4 } : item.number === 1 ? { ...item, title: "調査", review: "none", locked: true } : item,
    ),
  });
  const node = (id: string) => graph.nodes.find((n) => n.id === id);
  assert.deepEqual([node("n1")?.label, node("n1")?.review, node("n1")?.locked, node("n1")?.deferred], ["調査", "none", true, false]);
  assert.deepEqual([node("n2")?.label, node("n2")?.deferred, node("n2")?.reviewAt, node("n2")?.locked], ["設計", true, 4, false]);
  assert.equal(node("n2")?.type, "design");
});

test("CB-T359 置き場所は start からの最長の段数で列に分け、列の中は番号順。start は先頭の列、end は最後の列", () => {
  const graph = planGraphOf(plan(DIAMOND));
  const at = new Map(graph.nodes.map((node) => [node.id, node]));
  assert.deepEqual(
    graph.nodes.map((node) => [node.id, node.column]),
    [
      ["start", 0],
      ["n1", 1],
      ["n2", 2],
      ["n3", 2],
      ["n4", 3],
      ["end", 4],
    ],
  );
  assert.equal(at.get("n4")?.x, 3 * COLUMN);
  assert.ok((at.get("n2")?.y ?? 0) < (at.get("n3")?.y ?? 0), "同じ列は番号順に上から");
  // 番号の小さいステップが後ろの列に来ることもある（after だけで決まる）
  const skew = planGraphOf(plan(["research", "design", { type: "implement", after: [2] }, { type: "docs", after: [1, 3] }]));
  assert.deepEqual(
    skew.nodes.filter((node) => node.kind === "item").map((node) => [node.number, node.column]),
    [
      [1, 1],
      [2, 1],
      [3, 2],
      [4, 3],
    ],
  );
});

test("CB-T360 振り直しの見本の表（tests/fixtures/plan-reorder.json）の計画を、振り直す前も後も plans どおりに点と線にする", () => {
  const cases = reorderCases();
  assert.ok(cases.length >= 5, `見本の表を読めていない（${cases.length} 件）`);
  let checked = 0;
  for (const c of cases) {
    const shapes = [plansOf(c.plan, c.feedback, c.fixed ?? [])];
    if (c.expect !== undefined) {
      shapes.push(plansOf(c.expect.plan, c.expect.feedback, c.fixed ?? []));
    }
    for (const plans of shapes) {
      for (const p of plans) {
        const graph = planGraphOf(p);
        const items = graph.nodes.filter((node) => node.kind === "item");
        assert.deepEqual(
          items.map((node) => [node.number, node.label, node.locked]),
          p.items.map((item) => [item.number, item.title, item.locked]),
          c.name,
        );
        // after の線は、書いてあるとおり（元 → 後続側）。足しも削りもしない
        const expected = Object.entries(p.after).flatMap(([n, before]) => before.map((m) => `n${m}->n${n}`));
        const drawn = graph.edges.filter((edge) => edge.kind === "after").map((edge) => `${edge.source}->${edge.target}`);
        assert.deepEqual(drawn.sort(), expected.sort(), c.name);
        // start からの線は先行の無いステップへ、end への線は後続の無いステップから
        const waiting = new Set(Object.keys(p.after).map(Number));
        const waited = new Set(Object.values(p.after).flat());
        assert.deepEqual(
          graph.edges.filter((edge) => edge.kind === "start").map((edge) => edge.target),
          p.items.filter((item) => !waiting.has(item.number)).map((item) => `n${item.number}`),
          c.name,
        );
        assert.deepEqual(
          graph.edges.filter((edge) => edge.kind === "end").map((edge) => edge.source),
          p.items.filter((item) => !waited.has(item.number)).map((item) => `n${item.number}`),
          c.name,
        );
        checked += 1;
      }
    }
  }
  assert.ok(checked > cases.length, `確認した計画が少ない（${checked}）`);
});

test("CB-T361 引ける線はステップからステップへだけ。自分へ・start と end に触れる線・固定した番号へ入る線・循環になる線は断る", () => {
  const base = plan(DIAMOND);
  const graph = planGraphOf({ ...base, items: base.items.map((item) => (item.number === 3 ? { ...item, locked: true } : item)) });
  // 並びの逆向き（大きい番号から小さい番号へ）でも、循環にならなければ引ける
  assert.equal(canConnect(graph, "n3", "n2"), true);
  assert.equal(canConnect(graph, "n2", "n2"), false, "自分へ");
  assert.equal(canConnect(graph, "n1", "start"), false, "start へ入る");
  assert.equal(canConnect(graph, "end", "n1"), false, "end から出る");
  assert.equal(canConnect(graph, "start", "n4"), false, "start からの線は図が引く（先行を全部削除できば出る）");
  assert.equal(canConnect(graph, "n2", "end"), false, "end への線は図が引く");
  assert.equal(canConnect(graph, "n2", "n3"), false, "固定した番号へ入る");
  assert.equal(canConnect(graph, "n4", "n1"), false, "循環（1 から 4 へ辿れる）");
  assert.equal(canConnect(graph, "n9", "n1"), false, "ない点");
});

test("CB-T363 線を削除できるのはステップどうしの線（after）だけ。start と end の仮の線と、固定した番号へ入る線は削除できない", () => {
  const base = plan(DIAMOND);
  const graph = planGraphOf({ ...base, items: base.items.map((item) => (item.number === 3 ? { ...item, locked: true } : item)) });
  assert.equal(canDisconnect(graph, "n1", "n2"), true);
  assert.equal(canDisconnect(graph, "n2", "n4"), true);
  assert.equal(canDisconnect(graph, "n1", "n3"), false, "固定した番号へ入る線");
  assert.equal(canDisconnect(graph, "start", "n1"), false, "start からの線は図が引く");
  assert.equal(canDisconnect(graph, "n4", "end"), false, "end への線は図が引く");
  assert.equal(canDisconnect(graph, "n1", "n4"), false, "ない線");
});

test("CB-T364 点は振り直す前の番号（from）を持つ。置き場所は from で覚え、番号が振り直っても同じステップは同じ場所に置く。end は右端のステップの次の列", () => {
  const before = plan(DIAMOND);
  const layout = layoutOf(planGraphOf(before));
  assert.deepEqual(Object.keys(layout).sort(), ["1", "2", "3", "4"]);
  // 2 と 3 が入れ替わった答え（3 番だったステップが 2 番に、2 番だったステップが 3 番に）。線は 2 → 3（元の 3 → 2）
  const after = {
    ...before,
    items: [before.items[0], { ...before.items[2], number: 2, from: 3 }, { ...before.items[1], number: 3, from: 2 }, before.items[3]],
    after: { "2": [1], "3": [1, 2], "4": [3] },
  };
  const moved = withLayout(planGraphOf(after), layout);
  const at = (from: number) => moved.nodes.find((node) => node.from === from);
  assert.equal(at(3)?.number, 2);
  assert.equal(at(2)?.number, 3);
  assert.deepEqual([at(3)?.x, at(3)?.y], [layout["3"].x, layout["3"].y], "振り直しても置き場所は動かない");
  assert.deepEqual([at(2)?.x, at(2)?.y], [layout["2"].x, layout["2"].y]);
  const end = moved.nodes.find((node) => node.kind === "end");
  assert.equal(end?.x, Math.max(...Object.values(layout).map((p) => p.x)) + COLUMN);
  assert.equal(moved.nodes.find((node) => node.kind === "start")?.x, 0);
  // 覚えていないステップ（無いはずだが）は組んだ場所のまま
  const fresh = withLayout(planGraphOf(after), {});
  assert.deepEqual(
    fresh.nodes.map((node) => [node.id, node.x, node.y]),
    planGraphOf(after).nodes.map((node) => [node.id, node.x, node.y]),
  );
  // start と end は from を持たない
  assert.equal(planGraphOf(before).nodes[0].from, null);
});

test("CB-T1224 下限の倍率で止まったら、開始の点を枠の左端に、縦は開始の中心を枠の中心に置く。下限に届かなければ寄せない", () => {
  assert.ok(fitStalled(PLAN_FIT_MIN_ZOOM));
  assert.ok(fitStalled(PLAN_FIT_MIN_ZOOM + 1e-9));
  assert.ok(!fitStalled(PLAN_FIT_MIN_ZOOM + 0.01));
  // 開始が左端でなくても（並べ替えで x=300）、その点が画面の x=16 に来る。縦は中心が枠の中心（300）に来る
  const start = { x: 300, y: 100, width: 160, height: 60 };
  const view = viewportAtStart(PLAN_FIT_MIN_ZOOM, start, 600);
  assert.equal(view.zoom, PLAN_FIT_MIN_ZOOM);
  assert.ok(Math.abs(start.x * view.zoom + view.x - FIT_START_PAD) < 1e-9);
  assert.ok(Math.abs((start.y + start.height / 2) * view.zoom + view.y - 300) < 1e-9);
});
