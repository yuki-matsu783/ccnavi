/**
 * チケット管理画面の行を親ごとに分ける（`webview/board/Lanes.tsx`）。親は必ず行の見出しにあり、列の中には子だけが並ぶ。
 * 列の件数は子だけ。「フェーズを表示」で親の行を開くと、フェーズごとの状態と子の一覧が出る（矢印は描かない）。順序は
 * 開いた行の「計画の流れを見る」で、図を見るだけで開く。
 * 親のカードがチケット管理画面に無い子は、親の識別子だけを見出しにした行に入る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import type { BoardJson } from "../../src/core/model.js";
import { boardOf, closedJson, parentJson, phaseJson, proposalJson, reviewJson, ticketJson, waitingPhase } from "../helpers/cards.js";
import { boardPage, openBoard } from "../helpers/board.js";
import { flatStyle } from "../helpers/bundle.js";
import { plansOf } from "../helpers/plan-reorder.js";

const laneKey = (project: string, family: string): string => JSON.stringify([project, family]);

/** フェーズの多い作業中の親・子の無い承認待ちの親・親がチケット管理画面に無い子 */
function scene(): BoardJson {
  return {
    ...boardOf(
      [
        ticketJson("p1", { title: "作業中の親" }),
        closedJson("p1-01-01", { parent: "p1", phase: 1 }),
        ticketJson("p1-02-01", { parent: "p1", phase: 2 }),
        reviewJson("p1-02-02", { parent: "p1", phase: 2, attention: true }),
        proposalJson("p1-03-01", { parent: "p1", phase: 3, attention: true }),
        closedJson("p1-02-03", { parent: "p1", phase: 2, cancelled_at: "2026-10-03T00:00:00+0900", cancel_reason: "不要になった" }),
        proposalJson("p2", { title: "子の無い親", proposal: { state: "todo", tree: "p2", tree_root: "/p2", path: "/p2/p.md", has_plan: true }, attention: true }),
        ticketJson("gone-01-01", { parent: "gone", phase: 1, title: "親が無い子" }),
      ],
      [
        parentJson("p1", [
          phaseJson(1, { label: "1（調査）", state: "ended" }),
          waitingPhase(2, 8, "https://example.com/o/r/pull/8", { label: "2（設計）" }),
          phaseJson(3, { label: "3（実装）", state: "planned" }),
          phaseJson(4, { label: "4（試験）", state: "planned" }),
        ]),
      ],
    ),
    pending_approval: ["p1-03-01", "p2"],
  };
}

test("CB-D1294 行は親ごと。親は行の見出しにだけ置き（バッジ・局面・承認待ちの親の「承認」「計画編集」）、列の中には子だけ。列の件数は子だけを数える", async () => {
  const page = await openBoard(scene(), { state: { folded: [] } });
  try {
    // 行の順は親の識別子の順。親の無い子は親の識別子の行に入る
    assert.deepEqual(page.all(".lane").map((lane) => lane.getAttribute("data-lane")), ["gone", "p1", "p2"]);
    // 親はどの列のセルにも入らない
    assert.equal(page.all(".cell .card.parent").length, 0);
    const head = page.one('.lane[data-lane="p1"] > .card.lane-head');
    assert.equal(head.getAttribute("data-id"), "p1");
    assert.equal(head.querySelector(".status")?.textContent, "レビュー待ち");
    // 子は自分の列のセルに入る
    const cells = (lane: string): string[][] =>
      page.all(`.lane[data-lane="${lane}"] .cell`).map((cell) => [cell.getAttribute("data-cell") ?? "", ...Array.from(cell.querySelectorAll(".card")).map((c) => c.getAttribute("data-id") ?? "")]);
    assert.deepEqual(cells("p1"), [["todo", "p1-03-01"], ["doing", "p1-02-01", "p1-02-02"], ["done", "p1-01-01"], ["cancelled", "p1-02-03"]]);
    // 列の件数は子だけ（承認待ちの親p2は数えない）
    assert.deepEqual(page.all(".column").map((c) => c.querySelector(".count")?.textContent), ["子1", "子3", "子1", "子1"]);
    // 承認待ちの親の見出しには「承認」（主ボタン）と「計画編集」。子が無いことを言う
    const p2 = page.one('.lane[data-lane="p2"] > .card.lane-head');
    assert.deepEqual(Array.from(p2.querySelectorAll("button")).map((b) => b.getAttribute("data-action")), ["approve-one", "workflow"]);
    assert.equal(page.one('.lane[data-lane="p2"] .lane-empty').textContent, "子はまだありません");
    // 親がチケット管理画面に無い子の行は、親の識別子と、そのことを言う見出し（選べない）
    const orphan = page.one('.lane[data-lane="gone"] > .lane-orphan');
    assert.match(orphan.textContent, /親 gone/);
    assert.match(orphan.textContent, /親のチケットはチケット管理画面にありません/);
    assert.equal(orphan.classList.contains("card"), false);
    assert.equal(page.all('.lane[data-lane="gone"] .cell[data-cell="doing"] .card[data-id="gone-01-01"]').length, 1);
  } finally {
    await page.close();
  }
});

test("CB-D1295 絞り込みで子が残る親の見出しは残り、親も子も隠れた行は出さない。畳んだ列のセルは件数だけで、親の見出しは畳んでも隠れない", async () => {
  const page = await openBoard(scene());
  try {
    // 最初は完了と取り消しを畳む。畳んだ列のセルは件数だけ
    assert.equal(page.one('.lane[data-lane="p1"] .cell[data-cell="done"] .folded-count').textContent, "子1");
    assert.ok(page.one('.lane[data-lane="p1"] .cell[data-cell="done"]').classList.contains("folded-cell"));
    // ユーザ待ちのみ: p1の子は承認待ち（p1-03-01）とレビュー待ち（p1-02-02）が残るので、見出しのp1も残る
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(!page.one('.lane[data-lane="p1"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="p1"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="p1-02-01"]').classList.contains("hidden"));
    // 親の無い子は要対応でないので、行ごと隠れる
    assert.ok(page.one('.lane[data-lane="gone"]').classList.contains("hidden"));
    // 承認の数は見えている承認待ち（子p1-03-01と親p2）
    assert.equal(page.one('.controls button[data-action="approve"]').textContent, "承認待ち2件を承認");
    // 完了の列に畳んだ親を選んでも、親は見出しにあるので列を広げない
    await page.send({ type: "select", ticket: "p1" });
    await page.settle();
    assert.equal(page.one("aside.card-panel").getAttribute("data-panel"), "p1");
    assert.deepEqual((page.state() as { folded: string[] }).folded, ["done", "cancelled"]);
  } finally {
    await page.close();
  }
});

test("CB-D1296 「フェーズを表示」で親の行を開くと、フェーズごとの状態と子の一覧を出す（矢印で順序を描かない）。開いた行はstateに残る", async () => {
  const page = await openBoard(scene());
  try {
    assert.equal(page.all(".lane-phases").length, 0);
    // 子の無い承認待ちの親（フェーズが無い）には出さない
    assert.equal(page.all('.lane[data-lane="p2"] [data-action="lane-phases"]').length, 0);
    const button = page.one('.lane[data-lane="p1"] [data-action="lane-phases"]');
    assert.equal(button.textContent, "フェーズを表示");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    page.click(button);
    await page.settle();
    // 押しても親を選ばない（ボタンの操作だけ）
    assert.equal(page.all("aside.card-panel").length, 0);
    assert.deepEqual((page.state() as { opened: string[] }).opened, [laneKey("", "p1")]);
    const rows = page.all('.lane[data-lane="p1"] .lane-phases .phase');
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-label")?.textContent), ["1（調査）", "2（設計）", "3（実装）", "4（試験）"]);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-state")?.textContent), ["終了", "レビュー待ち", "これから", "これから"]);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-children")?.textContent), [
      "p1-01-01 完了",
      "p1-02-01 要確認、p1-02-02 レビュー待ち、p1-02-03 取り消し",
      "p1-03-01 承認待ち",
      "子なし",
    ]);
    // 順序を矢印で描かない
    assert.doesNotMatch(page.one(".lane-phases").textContent, /→/);
    assert.equal(page.one('.lane[data-lane="p1"] [data-action="lane-phases"]').textContent, "フェーズを隠す");
    page.click(page.one('.lane[data-lane="p1"] [data-action="lane-phases"]'));
    await page.settle();
    assert.equal(page.all(".lane-phases").length, 0);
    assert.deepEqual((page.state() as { opened: string[] }).opened, []);
  } finally {
    await page.close();
  }
  // 作り直しても開いたまま
  const again = await openBoard(scene(), { state: { opened: [laneKey("", "p1")] } });
  try {
    assert.equal(again.all('.lane[data-lane="p1"] .lane-phases').length, 1);
  } finally {
    await again.close();
  }
});

test("CB-D1302 開いた親の行の「計画の流れを見る」は、図の中身（parents[].plans）を持つ親にだけ出し、押すと同じ図の部品を見るだけで開く（承認・編集の操作は無い）。Escと「閉じる」で閉じる", async () => {
  // 図の中身が無い親には出さない
  const bare = await openBoard(scene(), { state: { opened: [laneKey("", "p1")] } });
  try {
    assert.equal(bare.all('.lane[data-lane="p1"] .lane-phases').length, 1);
    assert.equal(bare.all('[data-action="plan-flow"]').length, 0);
  } finally {
    await bare.close();
  }
  const withPlans = scene();
  const plans = plansOf(["research", { type: "design", after: [1] }, { type: "implement", after: [2] }], undefined, [1]).map((plan) => ({ ...plan, ticket: "p1", source_sha: "" }));
  const data: BoardJson = { ...withPlans, parents: withPlans.parents.map((p) => ({ ...p, plans })) };
  const page = await openBoard(data, { state: { opened: [laneKey("", "p1")] }, measure: true });
  try {
    // 閉じた行には出さない（フェーズの一覧の中に置く）
    assert.equal(page.all('.lane[data-lane="p1"] .lane-phases [data-action="plan-flow"]').length, 1);
    const button = page.one('[data-action="plan-flow"]');
    assert.equal(button.textContent, "計画の流れを見る");
    page.click(button);
    await page.settle();
    const dialog = page.one(".plan-flow");
    assert.equal(dialog.getAttribute("role"), "dialog");
    assert.match(page.one("#plan-flow-title").textContent, /計画の流れ: p1/);
    // 承認のオーバーレイと同じ図の部品。点はステップとstart・end、子が承認された番号は固定の印
    assert.deepEqual(page.all(".plan-flow .plan-graph").map((g) => g.getAttribute("data-part")), ["plan"]);
    assert.equal(page.all('.plan-flow .plan-graph .react-flow__node').length, 5);
    assert.equal(page.one('.plan-flow .react-flow__node[data-id="n1"] .plan-node').getAttribute("data-locked"), "1");
    // 見るだけ: ボタンは「閉じる」（と図の拡大・縮小）だけで、承認・計画編集は無い。押しても拡張ホストへは送らない
    assert.deepEqual(page.all(".plan-flow .overlay-actions button").map((b) => b.getAttribute("data-action")), ["plan-flow-close"]);
    assert.equal(page.all('.plan-flow [data-action="edit-workflow"], .plan-flow [data-action^="approve"]').length, 0);
    assert.equal(page.posted.filter((m) => m.type !== "ready").length, 0);
    page.key("Escape");
    await page.settle();
    assert.equal(page.all(".plan-flow").length, 0);
    page.click(page.one('[data-action="plan-flow"]'));
    await page.settle();
    page.click(page.one('[data-action="plan-flow-close"]'));
    await page.settle();
    assert.equal(page.all(".plan-flow").length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D1297 親の動いた表示は行の見出しに出す", async () => {
  const page = await openBoard(scene());
  try {
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(scene()), moved: [{ id: "p2", to: "todo" }] } });
    const head = page.one('.lane[data-lane="p2"] > .card.lane-head');
    assert.ok(head.classList.contains("moved"));
    assert.equal(head.querySelector(".moved-mark")?.textContent, "新規起票");
  } finally {
    await page.close();
  }
});

test("CB-T1298 940px以下では親ごとの行を縦に積み、行の中で子を列の名前つきで並べる（子の無い列と畳んだ列のセルは出さない）", () => {
  const css = flatStyle(boardPage({ kind: "board", board: buildBoard(scene()) }));
  const narrow = css.slice(css.indexOf("@media (max-width: 940px)"));
  assert.match(narrow, /\.board \{ display: block;/);
  assert.match(narrow, /\.lane:not\(\.hidden\) \{ display: block; \}/);
  assert.match(narrow, /\.cell\.empty-cell, \.cell\.folded-cell \{ display: none; \}/);
  assert.match(narrow, /\.cell-label \{ display: flex;/);
  // 広い幅では列の名前は列の見出しにだけ出す
  assert.match(css, /\.cell-label \{ display: none; \}/);
});
