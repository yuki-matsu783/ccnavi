/**
 * 承認のオーバーレイの計画の図。`--agree --preview --json` の `plans`（承認する提案そのものの順序）を、
 * 共有の図の部品（`webview/shared/PlanGraph.tsx`）で読むだけで描く。
 *
 * **大きさの偽物が要る**（`openBoard` の `measure`）。React Flow は点の大きさを測れないと線を 1 本も描かず、
 * テストは空の絵を見て通ってしまう（`test/helpers/dom.ts` の `LoadOptions`）。
 *
 * 図は判定をしない。終端にヒットしないステップ（`loose`）・すぐ始まるステップ（`ready`）・延期の引き受け手（`review_at`）・
 * 順序の検査の理由（`problems`）は実行ファイルの答えをそのまま出す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { ApprovePlan, ApprovePreview } from "../../src/core/approvemodel.js";
import { fixture } from "../helpers/fixture.js";
import { approvePreview, openBoard } from "../helpers/board.js";
import { screenStyle } from "../helpers/bundle.js";
import { plansOf, reorderCases, type RawItem } from "../helpers/plan-reorder.js";
import { PLAN_FIT_MIN_ZOOM } from "../../src/core/plan-graph.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };
import { buildBoard } from "../../src/core/board.js";

const DIAMOND: readonly RawItem[] = ["research", { type: "design", after: [1] }, { type: "acceptance", after: [1] }, { type: "implement", after: [2, 3] }];
const FEEDBACK: readonly RawItem[] = ["design-feedback", "skill-improve", { type: "implement-feedback", after: [5] }];

/** 全体計画（1〜4。1 は子が承認済み、2 は延期で 4 が引き受ける）と、終端にヒットしないステップのあるフィードバック計画（5〜7） */
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
      problems: ["フィードバック計画の最後のステップ（7）に 6 が先行していない。最後のステップへ線を引くか、合流のステップを置く"],
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

test("CB-D160 計画を持つ親があれば、本文の上に計画ごとの図を読むだけで出す。start・end の仮の点、延期の引き受け手、固定した番号、終端にヒットしないステップ、すぐ始まるステップ、順序の検査の理由は実行ファイルの答えのまま", async () => {
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

    // 点はステップと start・end。線は after と、start・end への仮の線
    assert.equal(dom.all('.plan-graph[data-part="plan"] .react-flow__node').length, 6);
    assert.deepEqual(edgeIds(dom, "plan"), ["after-1-2", "after-1-3", "after-2-4", "after-3-4", "end-4", "start-1"]);
    assert.equal(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="start"]').textContent, "開始");
    assert.equal(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="end"]').textContent, "終了");
    assert.equal(dom.one('.plan-graph[data-part="feedback"] .react-flow__node[data-id="start"]').textContent, "全体計画のレビュー後");
    assert.equal(dom.one('.plan-graph[data-part="feedback"] .react-flow__node[data-id="end"]').textContent, "親をクローズする");

    // 点は番号・題・見る場所。延期なら引き受け手、子が承認された番号は固定の印
    const n1 = dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n1"] .plan-node');
    assert.match(n1.textContent ?? "", /^1調査/);
    // 見る場所の言葉は、同じオーバーレイの本文（承認画面）と同じ言葉の短い形
    assert.match(n1.textContent ?? "", /レビュー不要/);
    assert.equal(n1.getAttribute("data-locked"), "1");
    assert.match(n1.textContent ?? "", /子が承認済み/);
    const n2 = dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n2"] .plan-node');
    // 延期したステップは、本文の先行の一覧と同じ言い方で引き受け手を言い、見る場所は出さない（本文も出さない）
    assert.match(n2.textContent ?? "", /レビューは 4 と一緒に/);
    assert.doesNotMatch(n2.textContent ?? "", /レビュー必須/);
    assert.match(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n3"] .plan-node').textContent ?? "", /レビュー必須（MR）/);
    assert.equal(n2.getAttribute("data-locked"), "0");

    // 終端にヒットしないステップは、実行ファイルが言ったものだけに印。そのステップから end への線も
    assert.deepEqual(
      dom.all('.plan-graph[data-part="feedback"] .plan-node[data-loose="1"]').map((node) => node.getAttribute("data-number")),
      ["6"],
    );
    assert.equal(dom.all('.plan-graph[data-part="plan"] .plan-node[data-loose="1"]').length, 0);
    assert.ok(dom.one('.plan-graph[data-part="feedback"] .react-flow__edge[data-id="end-6"]').classList.contains("loose"));
    assert.ok(!dom.one('.plan-graph[data-part="feedback"] .react-flow__edge[data-id="end-7"]').classList.contains("loose"));

    // 図の下。すぐ始まるステップ・終端にヒットしないステップ・順序の検査の理由
    const plain = dom.one('.plan-graph[data-part="plan"] .plan-graph-notes').textContent ?? "";
    assert.match(plain, /すぐ始まる: 1/);
    assert.match(plain, /延期したレビュー: 2 は 4 と一緒に/);
    assert.doesNotMatch(plain, /最後のステップの先行になっていない/);
    const notes = dom.one('.plan-graph[data-part="feedback"] .plan-graph-notes').textContent ?? "";
    assert.match(notes, /すぐ始まる: 5, 6/);
    assert.match(notes, /最後のステップの先行になっていない: 6。最後のステップへ線を引くか、合流のステップを置いてください/);
    assert.match(notes, /フィードバック計画の最後のステップ（7）に 6 が先行していない/);
  } finally {
    await dom.close();
  }
});

test("CB-D161 図は読むだけ。線を引く取っ手も、点のドラッグも、選ぶことも無く、点を押しても何も送らない", async () => {
  const dom = await openOverlay(samplePlans());
  try {
    assert.ok(dom.all(".plan-graph .react-flow__handle").length > 0, "線の端を留める取っ手がない（点が描けていない）");
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

test("CB-D162 plans がない・空なら図を出さない。見本の preview（実行ファイルの出力）の plans は、そのステップと線のまま描く", async () => {
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

test("CB-D163 振り直しの見本の表の計画（振り直す前と後）を、plans どおりの線で描く", async () => {
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

test("CB-D164 図のある親ごとに「計画を編集」を出す。押すと親の識別子を送る（閉じてタブを開くのは拡張ホスト）。承認中は出さない", async () => {
  const dom = await openOverlay(samplePlans());
  try {
    // 親 1 つに全体計画とフィードバック計画の 2 つの図。入口は親ごとに 1 つ、その親の図の前
    const buttons = dom.all('.approval [data-action="edit-workflow"]');
    assert.deepEqual(
      buttons.map((b) => [b.getAttribute("data-ticket"), b.textContent]),
      [["i0001", "計画を編集"]],
    );
    const family = dom.one('.approval .plan-family[data-ticket="i0001"]');
    assert.deepEqual(
      Array.from(family.querySelectorAll('[data-action="edit-workflow"], .plan-graph')).map((el) => el.getAttribute("data-action") ?? el.getAttribute("data-part")),
      ["edit-workflow", "plan", "feedback"],
    );
    dom.click(buttons[0]);
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "editWorkflow"), [{ type: "editWorkflow", ticket: "i0001" }]);
  } finally {
    await dom.close();
  }
  const approving = await openBoard(fixture(), { approval: { kind: "approving", preview: preview(samplePlans()) }, measure: true });
  try {
    await approving.settle();
    assert.equal(approving.all(".plan-graph").length, 2, "承認中も図は出す");
    assert.equal(approving.all('[data-action="edit-workflow"]').length, 0);
  } finally {
    await approving.close();
  }
});

test("CB-D180 計画編集タブに未保存の変更がある親を含む一覧は、その親の図の上に理由を出し、承認のボタンを押せなくする。外れたら押せる", async () => {
  const dom = await openBoard(fixture(), { approval: { kind: "preview", preview: preview(samplePlans()) }, measure: true, unsaved: ["i0001"] });
  try {
    await dom.settle();
    const note = dom.one('.approval .plan-family[data-ticket="i0001"] .plan-unsaved');
    assert.equal(note.textContent, "計画編集タブに保存していない変更があります。変更を保存または破棄するまで、表示中のチケットは承認できません。");
    const confirm = dom.one<HTMLButtonElement>('[data-action="approve-confirm"]');
    assert.equal(confirm.disabled, true);
    assert.equal(dom.one("#approval-blocked").textContent, "親 i0001 の計画編集タブに未保存の変更があります。保存するか破棄してから承認してください");
    assert.equal(confirm.getAttribute("title"), dom.one("#approval-blocked").textContent);
    // 未保存のままでも、タブへの入口は押せる（保存しに行く）
    assert.equal(dom.one<HTMLButtonElement>('[data-action="edit-workflow"]').disabled, false);
    // タブを閉じた・破棄した・保存した。拡張ホストがボードを描き直す
    await dom.send({ type: "data", data: { kind: "board", board: buildBoard(fixture()), approval: { kind: "preview", preview: preview(samplePlans()) }, unsaved: [] } });
    await dom.settle();
    assert.equal(dom.all(".plan-unsaved, #approval-blocked").length, 0);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="approve-confirm"]').disabled, false);
  } finally {
    await dom.close();
  }
  // 別の親のタブだけが未保存なら、この一覧は押せる
  const other = await openBoard(fixture(), { approval: { kind: "preview", preview: preview(samplePlans()) }, measure: true, unsaved: ["i0002"] });
  try {
    await other.settle();
    assert.equal(other.one<HTMLButtonElement>('[data-action="approve-confirm"]').disabled, false);
    assert.equal(other.all(".plan-unsaved").length, 0);
  } finally {
    await other.close();
  }
});

test("CB-D181 タブで保存したあとに提案が変わった親（拡張ホストが見つけた）は、その親の図の上に注意を出す。承認は止めない", async () => {
  const dom = await openBoard(fixture(), { approval: { kind: "preview", preview: preview(samplePlans()), changedSinceSave: ["i0001"] }, measure: true });
  try {
    await dom.settle();
    const note = dom.one('.approval .plan-family[data-ticket="i0001"] .plan-changed');
    assert.equal(
      note.textContent,
      "計画編集タブで保存したあとに、提案が変わっています（エージェントが変更した可能性があります）。図を確認してから承認してください。",
    );
    assert.equal(dom.one<HTMLButtonElement>('[data-action="approve-confirm"]').disabled, false);
  } finally {
    await dom.close();
  }
  const plain = await openOverlay(samplePlans());
  try {
    assert.equal(plain.all(".plan-changed, .plan-unsaved, #approval-blocked").length, 0);
  } finally {
    await plain.close();
  }
});

/** 見えている図の倍率と、左上の位置（React Flow の viewport の transform） */
function viewportOf(dom: DomPage, part: string): { x: number; y: number; zoom: number } {
  const style = dom.one(`.plan-graph[data-part="${part}"] .react-flow__viewport`).getAttribute("style") ?? "";
  const found = /translate\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px\)\s*scale\(\s*([\d.]+)\s*\)/.exec(style);
  assert.ok(found !== null, `viewport の transform が読み込めない: ${style}`);
  return { x: Number(found[1]), y: Number(found[2]), zoom: Number(found[3]) };
}

test("CB-D165 大きな計画でも、開いた最初の図は縮めすぎない（文字が読める倍率の下限で止め、はみ出す分はパンで見せる）。そのとき左端の開始が見える。小さな計画は等倍より大きくしない", async () => {
  const chain: RawItem[] = ["research", ...Array.from({ length: 59 }, (_, i) => ({ type: "implement", after: [i + 1] }))];
  const big = await openOverlay(plansOf(chain, undefined));
  try {
    await big.settle();
    assert.equal(big.all('.plan-graph[data-part="plan"] .plan-node').length, 60);
    const view = viewportOf(big, "plan");
    assert.ok(view.zoom >= PLAN_FIT_MIN_ZOOM - 1e-6, `縮めすぎ: ${view.zoom}`);
    // 開始（いちばん左の列、x = 0）が図の左端の中に見えている
    assert.ok(view.x >= 0 && view.x <= 80, `開始が見えていない: x=${view.x}`);
  } finally {
    await big.close();
  }
  const small = await openOverlay(plansOf(["research", { type: "design", after: [1] }], undefined));
  try {
    await small.settle();
    const view = viewportOf(small, "plan");
    assert.ok(view.zoom <= 1 + 1e-6, `小さな計画を拡大している: ${view.zoom}`);
  } finally {
    await small.close();
  }
});

test("CB-T362 ボードの CSS は図の部品の CSS（React Flow と、その色を VS Code のテーマ変数に置き換える規則）を持つ", () => {
  const style = screenStyle("board");
  assert.ok(style.includes(".react-flow"), "React Flow の CSS が入っていない");
  assert.match(style, /\.plan-graph \.react-flow \{[^}]*--xy-background-color-default: var\(--vscode-editor-background\)/);
  assert.match(style, /\.plan-node\[data-loose="1"\] \{[^}]*var\(--vscode-editorWarning-foreground\)/);
});
