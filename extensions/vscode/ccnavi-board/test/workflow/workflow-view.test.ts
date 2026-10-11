/**
 * ワークフロー編集タブの拡張ホストと画面の契約（`src/core/workflow-view.ts`）。実行ファイルの答えを画面の状態に
 * 整える純関数を見る。**番号は振り直さない**（振り直しの正は実行ファイル）。ここが組むのは、図で変えた線を
 * 元の番号（いま提案に書いてある番号）での直接の先行（`--order` の形）に戻すことと、見せる文の材料だけ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  asPlanOrder,
  asWorkflowMessage,
  changedOrder,
  editedOrder,
  isDirty,
  orderOf,
  planDiff,
  renumbered,
  saveBlocker,
  workflowTicketOf,
  workflowTitle,
} from "../../src/core/workflow-view.js";
import { planOrderSample } from "../helpers/plan-order.js";

test("CB-T343 答えの線を、元の番号での直接の先行（--order の形）に戻す。先行の無い番号も空のリストで並べる", () => {
  assert.deepEqual(orderOf(planOrderSample("open")), {
    plan: { "1": [], "2": [1], "3": [1], "4": [2, 3] },
    feedback: { "5": [], "6": [5] },
  });
  // 振り直した答え（2 番に元の 3、3 番に元の 2）は、元の番号に戻して読む
  assert.deepEqual(orderOf(planOrderSample("reverse")).plan, { "1": [], "2": [1, 3], "3": [1], "4": [2] });
});

test("CB-T344 図で線を足す・消すと、見えている番号を元の番号に直して --order を組む。提案と同じ計画は渡さない", () => {
  const open = planOrderSample("open");
  // 3 番が 2 番を待つ線を足す（見えている番号 = 元の番号）。フィードバック計画は変えていないので渡さない
  assert.deepEqual(editedOrder(open, { part: "plan", from: 2, to: 3, add: true }), { plan: { "1": [], "2": [1], "3": [1, 2], "4": [2, 3] } });
  // 1 → 3 の線を消す
  assert.deepEqual(editedOrder(open, { part: "plan", from: 1, to: 3, add: false }), { plan: { "1": [], "2": [1], "3": [], "4": [2, 3] } });
  // 振り直した答えの上では、見えている番号（2 番 = 元の 3）を元の番号に直す。3 番（元の 2）が 2 番（元の 3）を待つ線を消す
  const reverse = planOrderSample("reverse");
  assert.deepEqual(editedOrder(reverse, { part: "plan", from: 2, to: 3, add: false }), { plan: { "1": [], "2": [1], "3": [1], "4": [2] } });
  assert.deepEqual(editedOrder(open, { part: "feedback", from: 5, to: 6, add: false }), { feedback: { "5": [], "6": [] } });
  // 消した線を引き直して提案と同じになったら、何も渡さない（提案のまま）
  assert.deepEqual(editedOrder(planOrderSample("loose"), { part: "plan", from: 3, to: 4, add: true }), {});
  // 計画に無い番号は組まない
  assert.equal(editedOrder(open, { part: "plan", from: 9, to: 3, add: true }), undefined);
  assert.equal(editedOrder(open, { part: "steps", from: 1, to: 3, add: true }), undefined);
});

test("CB-T345 未保存は、答えの線が提案に書いてある先行と違うかで決める（画面は番号を数えない）", () => {
  assert.equal(isDirty(planOrderSample("open")), false);
  assert.equal(isDirty(planOrderSample("reverse")), true);
  assert.equal(isDirty(planOrderSample("loose")), true);
  // 保存と未保存の写しに渡すのは、提案と違う計画だけ
  assert.deepEqual(changedOrder(planOrderSample("reverse")), { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } });
  assert.deepEqual(changedOrder(planOrderSample("open")), {});
});

test("CB-T346 番号の変わる項を並べる（保存の前に見せる）。変わらない項は並べない", () => {
  assert.deepEqual(renumbered(planOrderSample("reverse")), [
    { part: "plan", title: "実装とテスト", from: 3, number: 2 },
    { part: "plan", title: "設計", from: 2, number: 3 },
  ]);
  assert.deepEqual(renumbered(planOrderSample("open")), []);
});

test("CB-T347 外で提案が変わったときの差分。番号ごとに、定義と待ちの前後を並べる。変わらない番号は並べない", () => {
  const before = planOrderSample("open").plans;
  const after = planOrderSample("reverse").plans;
  assert.deepEqual(planDiff(before, after), [
    { part: "plan", number: 2, before: "設計（design）: 待つ 1", after: "実装とテスト（implement）: 待つ 1" },
    { part: "plan", number: 3, before: "実装とテスト（implement）: 待つ 1", after: "設計（design）: 待つ 1, 2" },
    { part: "plan", number: 4, before: "docs（docs）: 待つ 2, 3", after: "docs（docs）: 待つ 3" },
  ]);
  // 項が増えた・減った（フィードバック計画が無くなった）
  const dropped = planDiff(before, [before[0]]);
  assert.deepEqual(dropped.map((row) => [row.part, row.number, row.after]), [
    ["feedback", 5, "（無い）"],
    ["feedback", 6, "（無い）"],
  ]);
  assert.deepEqual(planDiff(before, before), []);
});

test("CB-T348 保存できない理由。書けない答え（実行ファイルの writable）・未保存でない・確かめている途中・外で変わった・提案が無くなった", () => {
  const reverse = planOrderSample("reverse");
  const calm = { pending: false, changed: false, gone: false };
  assert.equal(saveBlocker(reverse, calm), "");
  assert.match(saveBlocker(planOrderSample("open"), calm), /変えていません/);
  assert.match(saveBlocker(planOrderSample("loose"), calm), /順序の検査/);
  assert.match(saveBlocker(reverse, { ...calm, pending: true }), /確かめ/);
  assert.match(saveBlocker(reverse, { ...calm, changed: true }), /外で変わった/);
  assert.match(saveBlocker(reverse, { ...calm, gone: true }), /承認待ち/);
  const blocked = { ...reverse, writable: false, child_problems: ["i0001-03-01 の付け替え先が承認済みチケットと重なる"] };
  assert.match(saveBlocker(blocked, calm), /子の提案/);
});

test("CB-T349 タブの題は未保存の間だけ頭に ● を付ける", () => {
  assert.equal(workflowTitle("i0001"), "ccnavi ワークフロー: i0001");
  assert.equal(workflowTitle("i0001", true), "● ccnavi ワークフロー: i0001");
});

test("CB-T350 画面から届いたものを確かめる。形が崩れていれば捨てる（保存の中身が読めないものを渡さない）", () => {
  const order = { plan: { "1": [], "2": [1] } };
  assert.deepEqual(asWorkflowMessage({ type: "ready" }), { type: "ready" });
  assert.deepEqual(asWorkflowMessage({ type: "adopt" }), { type: "adopt" });
  assert.deepEqual(asWorkflowMessage({ type: "reload", dirty: true }), { type: "reload", dirty: true });
  assert.deepEqual(asWorkflowMessage({ type: "dirty", dirty: false }), { type: "dirty", dirty: false });
  assert.equal(asWorkflowMessage({ type: "dirty", dirty: "no" }), undefined);
  assert.deepEqual(asWorkflowMessage({ type: "order", seq: 3, order }), { type: "order", seq: 3, order });
  assert.equal(asWorkflowMessage({ type: "order", seq: "3", order }), undefined);
  assert.deepEqual(asWorkflowMessage({ type: "save", order, expect: "abc" }), { type: "save", order, expect: "abc" });
  assert.equal(asWorkflowMessage({ type: "save", order }), undefined, "ハッシュが無ければ保存しない");
  assert.deepEqual(asWorkflowMessage({ type: "draft", order: null }), { type: "draft", order: null });
  assert.deepEqual(asWorkflowMessage({ type: "draft", order }), { type: "draft", order });
  assert.equal(asWorkflowMessage({ type: "write" }), undefined);
  assert.equal(asWorkflowMessage(null), undefined);
  // --order の形。計画は plan と feedback だけ、番号は数字の文字列、先行は 1 以上の整数のリスト
  assert.deepEqual(asPlanOrder({}), {});
  assert.deepEqual(asPlanOrder({ feedback: { "5": [] } }), { feedback: { "5": [] } });
  for (const bad of [[], { steps: {} }, { plan: [] }, { plan: { x: [] } }, { plan: { "2": 1 } }, { plan: { "2": ["1"] } }, { plan: { "2": [1.5] } }, { plan: { "2": [true] } }, { plan: { "2": [0] } }]) {
    assert.equal(asPlanOrder(bad), undefined, JSON.stringify(bad));
  }
});

test("CB-T351 開くコマンドに渡す親の識別子。識別子の形でなければ受けない", () => {
  assert.equal(workflowTicketOf("i0001"), "i0001");
  for (const bad of ["", " i0001", "../i0001", "i0001/x", 1, undefined, null]) {
    assert.equal(workflowTicketOf(bad), undefined, String(bad));
  }
});
