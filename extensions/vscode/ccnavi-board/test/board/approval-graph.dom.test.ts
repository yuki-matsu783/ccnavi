/**
 * 承認のオーバーレイの計画の図。`--agree --preview --json` の `plans`（承認する提案そのものの順序）を、
 * 共有の図の部品（`webview/shared/PlanGraph.tsx`）で読むだけで描く。
 *
 * **大きさの偽物が要る**（`openBoard` の `measure`）。React Flow は点の大きさを測れないと線を 1 本も描かず、
 * テストは空の絵を見て通ってしまう（`test/helpers/dom.ts` の `LoadOptions`）。
 *
 * 図は判定をしない。終端に当たらない項（`loose`）・すぐ始まる項（`ready`）・延期の引き受け手（`review_at`）・
 * 順序の検査の理由（`problems`）は実行ファイルの答えをそのまま出す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { ApprovePlan, ApprovePreview } from "../../src/core/approvemodel.js";
import { fixture } from "../helpers/fixture.js";
import { approvePreview, openBoard } from "../helpers/board.js";
import { screenStyle } from "../helpers/bundle.js";
import { plansOf, reorderCases, type RawItem } from "../helpers/plan-reorder.js";
import type { DomPage } from "../helpers/dom.js";

const DIAMOND: readonly RawItem[] = ["research", { type: "design", after: [1] }, { type: "acceptance", after: [1] }, { type: "implement", after: [2, 3] }];
const FEEDBACK: readonly RawItem[] = ["design-feedback", "skill-improve", { type: "implement-feedback", after: [5] }];

/** 全体計画（1〜4。1 は子が承認済み、2 は延期で 4 が引き受ける）と、終端に当たらない項のあるフィードバック計画（5〜7） */
function samplePlans(): ApprovePlan[] {
  const [main, feedback] = plansOf(DIAMOND, FEEDBACK, [1]);
  return [
    {
      ...main,
      items: main.items.map((item) =>
        item.number === 1 ? { ...item, title: "調査", review: "none" } : item.number === 2 ? { ...item, title: "設計", deferred: true, review_at: 4 } : item,
      ),
      ready: [1],
    },
    {
      ...feedback,
      loose: [6],
      ready: [5, 6],
      problems: ["フィードバック計画の最後の項（7）が 6 を待っていない。最後の項へ線を引くか、合流の項を置く"],
    },
  ];
}

function preview(plans: readonly ApprovePlan[]): ApprovePreview {
  return { ...approvePreview(), plans };
}

async function openOverlay(plans: readonly ApprovePlan[]): Promise<DomPage> {
  const dom = await openBoard(fixture(), { approval: { kind: "preview", preview: preview(plans) }, measure: true });
  await dom.settle();
  return dom;
}

/** その計画の図の中の、線の id（React Flow の `data-id`） */
function edgeIds(dom: DomPage, part: string): string[] {
  return dom
    .all(`.plan-graph[data-part="${part}"] .react-flow__edge`)
    .map((edge) => edge.getAttribute("data-id") ?? "")
    .sort();
}

test("CB-D155 計画を持つ親があれば、本文の上に計画ごとの図を読むだけで出す。start・end の仮の点、延期の引き受け手、固定した番号、終端に当たらない項、すぐ始まる項、順序の検査の理由は実行ファイルの答えのまま", async () => {
  const dom = await openOverlay(samplePlans());
  try {
    const graphs = dom.all(".approval .plan-graph");
    assert.deepEqual(
      graphs.map((graph) => graph.getAttribute("data-part")),
      ["plan", "feedback"],
    );
    assert.deepEqual(
      dom.all(".approval .plan-graph h3").map((h) => h.textContent),
      ["i0001 の全体計画（plan:）", "i0001 のフィードバック計画（feedback:）"],
    );
    // 図は本文（承認画面の文字）より前に出る
    const box = dom.one(".approval");
    const order = Array.from(box.querySelectorAll(".plan-graphs, pre.approval-text")).map((el) => el.className);
    assert.deepEqual(order, ["plan-graphs", "approval-text"]);

    // 点は項と start・end。線は after と、start・end への仮の線
    assert.equal(dom.all('.plan-graph[data-part="plan"] .react-flow__node').length, 6);
    assert.deepEqual(edgeIds(dom, "plan"), ["after-1-2", "after-1-3", "after-2-4", "after-3-4", "end-4", "start-1"]);
    assert.equal(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="start"]').textContent, "開始");
    assert.equal(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="end"]').textContent, "終了");
    assert.equal(dom.one('.plan-graph[data-part="feedback"] .react-flow__node[data-id="start"]').textContent, "全体計画のレビュー後");
    assert.equal(dom.one('.plan-graph[data-part="feedback"] .react-flow__node[data-id="end"]').textContent, "親を閉じる");

    // 点は番号・題・見る場所。延期なら引き受け手、子が承認された番号は固定の印
    const n1 = dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n1"] .plan-node');
    assert.match(n1.textContent ?? "", /^1調査/);
    assert.match(n1.textContent ?? "", /レビューなし/);
    assert.equal(n1.getAttribute("data-locked"), "1");
    assert.match(n1.textContent ?? "", /子が承認済み/);
    const n2 = dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n2"] .plan-node');
    assert.match(n2.textContent ?? "", /延期 → 4/);
    assert.equal(n2.getAttribute("data-locked"), "0");

    // 終端に当たらない項は、実行ファイルが言ったものだけに印。その項から end への線も
    assert.deepEqual(
      dom.all('.plan-graph[data-part="feedback"] .plan-node[data-loose="1"]').map((node) => node.getAttribute("data-number")),
      ["6"],
    );
    assert.equal(dom.all('.plan-graph[data-part="plan"] .plan-node[data-loose="1"]').length, 0);
    assert.ok(dom.one('.plan-graph[data-part="feedback"] .react-flow__edge[data-id="end-6"]').classList.contains("loose"));
    assert.ok(!dom.one('.plan-graph[data-part="feedback"] .react-flow__edge[data-id="end-7"]').classList.contains("loose"));

    // 図の下。すぐ始まる項・終端に当たらない項・順序の検査の理由
    const plain = dom.one('.plan-graph[data-part="plan"] .plan-graph-notes').textContent ?? "";
    assert.match(plain, /すぐ始まる: 1/);
    assert.match(plain, /延期: 2 → 4/);
    assert.doesNotMatch(plain, /最後の項がこの項を待っていない/);
    const notes = dom.one('.plan-graph[data-part="feedback"] .plan-graph-notes').textContent ?? "";
    assert.match(notes, /すぐ始まる: 5, 6/);
    assert.match(notes, /最後の項がこの項を待っていない: 6。最後の項へ線を引くか、合流の項を置く/);
    assert.match(notes, /フィードバック計画の最後の項（7）が 6 を待っていない/);
  } finally {
    await dom.close();
  }
});

test("CB-D156 図は読むだけ。線を引く取っ手も、点のドラッグも、選ぶことも無く、点を押しても何も送らない", async () => {
  const dom = await openOverlay(samplePlans());
  try {
    assert.ok(dom.all(".plan-graph .react-flow__handle").length > 0, "線の端を留める取っ手が無い（点が描けていない）");
    assert.equal(dom.all(".plan-graph .react-flow__handle.connectable").length, 0, "線を引ける取っ手がある");
    assert.equal(dom.all(".plan-graph .react-flow__node.draggable, .plan-graph .react-flow__node.selectable").length, 0, "点を動かせる・選べる");
    assert.equal(dom.all(".plan-graph .react-flow__edge.selectable").length, 0, "線を選べる");
    assert.equal(dom.all(".plan-graph").filter((graph) => graph.getAttribute("data-editable") !== "0").length, 0);
    const sent = dom.posted.length;
    dom.click(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n2"]'));
    await dom.settle();
    assert.equal(dom.posted.length, sent, "点を押して何か送った");
    assert.equal(dom.all(".plan-graph .react-flow__node.selected").length, 0);
    // 拡大・縮小と全体表示はできる（パン・ズームは読むだけでもできる）
    assert.equal(dom.all('.plan-graph[data-part="plan"] .react-flow__controls').length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D157 plans が無い・空なら図を出さない。見本の preview（実行ファイルの出力）の plans は、その項と線のまま描く", async () => {
  const none = await openOverlay([]);
  try {
    assert.equal(none.all(".plan-graph, .plan-graphs").length, 0);
    assert.ok(none.all("pre.approval-text").length === 1, "本文は出る");
  } finally {
    await none.close();
  }
  const dom = await openBoard(fixture(), { approval: { kind: "preview", preview: approvePreview() }, measure: true });
  try {
    await dom.settle();
    assert.equal(dom.all(".plan-graph").length, 1);
    assert.deepEqual(
      dom.all('.plan-graph[data-part="plan"] .plan-node').map((node) => node.textContent?.replace(/レビュー.*$/, "")),
      ["1調査", "2設計"],
    );
    assert.deepEqual(edgeIds(dom, "plan"), ["after-1-2", "end-2", "start-1"]);
  } finally {
    await dom.close();
  }
});

test("CB-D158 振り直しの見本の表の計画（振り直す前と後）を、plans どおりの線で描く", async () => {
  const cases = reorderCases().filter((c) => c.expect !== undefined && (c.name.includes("逆向き") || c.name.includes("フィードバック計画")));
  assert.equal(cases.length, 2, "見本の表から使う 2 件が見つからない");
  for (const c of cases) {
    for (const plans of [plansOf(c.plan, c.feedback), plansOf(c.expect?.plan ?? [], c.expect?.feedback)]) {
      const dom = await openOverlay(plans);
      try {
        for (const p of plans) {
          const expected = [
            ...Object.entries(p.after).flatMap(([n, before]) => before.map((m) => `after-${m}-${n}`)),
            ...p.items.filter((item) => p.after[String(item.number)] === undefined).map((item) => `start-${item.number}`),
            ...p.items.filter((item) => !Object.values(p.after).flat().includes(item.number)).map((item) => `end-${item.number}`),
          ].sort();
          assert.deepEqual(edgeIds(dom, p.part), expected, `${c.name} ${p.part}`);
          assert.deepEqual(
            dom.all(`.plan-graph[data-part="${p.part}"] .plan-node`).map((node) => node.getAttribute("data-number")),
            p.items.map((item) => String(item.number)),
            `${c.name} ${p.part}`,
          );
        }
      } finally {
        await dom.close();
      }
    }
  }
});

test("CB-D159 ワークフロー編集タブへの入口（「ワークフローを編集」）は、タブができるまで出さない", async () => {
  const dom = await openOverlay(samplePlans());
  try {
    assert.equal(dom.all('[data-action="edit-workflow"]').length, 0);
    assert.doesNotMatch(dom.one(".approval").textContent ?? "", /ワークフローを編集/);
  } finally {
    await dom.close();
  }
});

test("CB-T336 ボードの CSS は図の部品の CSS（React Flow と、その色を VS Code のテーマ変数に置き換える規則）を持つ", () => {
  const style = screenStyle("board");
  assert.ok(style.includes(".react-flow"), "React Flow の CSS が入っていない");
  assert.match(style, /\.plan-graph \.react-flow \{[^}]*--xy-background-color-default: var\(--vscode-editor-background\)/);
  assert.match(style, /\.plan-node\[data-loose="1"\] \{[^}]*var\(--vscode-editorWarning-foreground\)/);
});
