/**
 * 計画編集タブの拡張ホストと画面の契約（`src/core/workflow-view.ts`）。実行ファイルの答えを画面の状態に
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
  goneReasonOf,
  restoreAsked,
  savedShaOf,
  workflowTicketOf,
  workflowTitle,
} from "../../src/core/workflow-view.js";
import { planOrderSample } from "../helpers/plan-order.js";

test("CB-T369 答えの線を、元の番号での直接の先行（--order の形）に戻す。先行のない番号も空のリストで並べる", () => {
  assert.deepEqual(orderOf(planOrderSample("open")), {
    plan: { "1": [], "2": [1], "3": [1], "4": [2, 3] },
    feedback: { "5": [], "6": [5] },
  });
  // 振り直した答え（2 番に元の 3、3 番に元の 2）は、元の番号に戻して読む
  assert.deepEqual(orderOf(planOrderSample("reverse")).plan, { "1": [], "2": [1, 3], "3": [1], "4": [2] });
});

test("CB-T370 図で線を追加する・削除すると、見えている番号を元の番号に直して --order を組む。提案と同じ計画は渡さない", () => {
  const open = planOrderSample("open");
  // 2 番が 3 番の先行になる線を足す（見えている番号 = 元の番号）。フィードバック計画は変えていないので渡さない
  assert.deepEqual(editedOrder(open, { part: "plan", from: 2, to: 3, add: true }), { plan: { "1": [], "2": [1], "3": [1, 2], "4": [2, 3] } });
  // 1 → 3 の線を消す
  assert.deepEqual(editedOrder(open, { part: "plan", from: 1, to: 3, add: false }), { plan: { "1": [], "2": [1], "3": [], "4": [2, 3] } });
  // 振り直した答えの上では、見えている番号（2 番 = 元の 3）を元の番号に直す。2 番（元の 3）が 3 番（元の 2）の先行である線を消す
  const reverse = planOrderSample("reverse");
  assert.deepEqual(editedOrder(reverse, { part: "plan", from: 2, to: 3, add: false }), { plan: { "1": [], "2": [1], "3": [1], "4": [2] } });
  assert.deepEqual(editedOrder(open, { part: "feedback", from: 5, to: 6, add: false }), { feedback: { "5": [], "6": [] } });
  // 消した線を引き直して提案と同じになったら、何も渡さない（提案のまま）
  assert.deepEqual(editedOrder(planOrderSample("loose"), { part: "plan", from: 3, to: 4, add: true }), {});
  // 計画に無い番号は組まない
  assert.equal(editedOrder(open, { part: "plan", from: 9, to: 3, add: true }), undefined);
  assert.equal(editedOrder(open, { part: "steps", from: 1, to: 3, add: true }), undefined);
});

test("CB-T371 未保存は、答えの線が提案に書いてある先行と違うかで決める（画面は番号を数えない）", () => {
  assert.equal(isDirty(planOrderSample("open")), false);
  assert.equal(isDirty(planOrderSample("reverse")), true);
  assert.equal(isDirty(planOrderSample("loose")), true);
  // 保存と未保存のコピーに渡すのは、提案と違う計画だけ
  assert.deepEqual(changedOrder(planOrderSample("reverse")), { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } });
  assert.deepEqual(changedOrder(planOrderSample("open")), {});
});

test("CB-T372 番号の変わるステップを並べる（保存の前に見せる）。変わらないステップは並べない", () => {
  assert.deepEqual(renumbered(planOrderSample("reverse")), [
    { part: "plan", title: "実装とテスト", from: 3, number: 2 },
    { part: "plan", title: "設計", from: 2, number: 3 },
  ]);
  assert.deepEqual(renumbered(planOrderSample("open")), []);
});

test("CB-T373 外で提案が変わったときの差分。番号ごとに、定義と先行の前後を並べる。変わらない番号は並べない", () => {
  const before = planOrderSample("open").plans;
  const after = planOrderSample("reverse").plans;
  assert.deepEqual(planDiff(before, after), [
    { part: "plan", number: 2, before: "設計（design）: 先行 1", after: "実装とテスト（implement）: 先行 1" },
    { part: "plan", number: 3, before: "実装とテスト（implement）: 先行 1", after: "設計（design）: 先行 1, 2" },
    { part: "plan", number: 4, before: "docs（docs）: 先行 2, 3", after: "docs（docs）: 先行 3" },
  ]);
  // ステップが増えた・減った（フィードバック計画が無くなった）
  const dropped = planDiff(before, [before[0]]);
  assert.deepEqual(dropped.map((row) => [row.part, row.number, row.after]), [
    ["feedback", 5, "（なし）"],
    ["feedback", 6, "（なし）"],
  ]);
  assert.deepEqual(planDiff(before, before), []);
});

test("CB-T374 保存できない理由。書けない答え（実行ファイルの writable）・未保存でない・確認している途中・外で変わった・提案が無くなった", () => {
  const reverse = planOrderSample("reverse");
  const calm = { pending: false, changed: false, gone: false };
  assert.equal(saveBlocker(reverse, calm), "");
  assert.match(saveBlocker(planOrderSample("open"), calm), /変えていません/);
  assert.match(saveBlocker(planOrderSample("loose"), calm), /順序の検査/);
  assert.match(saveBlocker(reverse, { ...calm, pending: true }), /確認/);
  assert.match(saveBlocker(reverse, { ...calm, changed: true }), /外で変わった/);
  assert.match(saveBlocker(reverse, { ...calm, gone: true }), /承認待ち/);
  const blocked = { ...reverse, writable: false, child_problems: ["i0001-03-01 の付け替え先が承認済みチケットと重なる"] };
  assert.match(saveBlocker(blocked, calm), /子の提案/);
});

test("CB-T375 タブの題は未保存の間だけ頭に ● を付ける", () => {
  assert.equal(workflowTitle("i0001"), "ccnavi 計画編集: i0001");
  assert.equal(workflowTitle("i0001", true), "● ccnavi 計画編集: i0001");
});

test("CB-T376 画面から届いたものを確認する。形が崩れていれば破棄する（保存の中身が読み込めないものを渡さない）", () => {
  const order = { plan: { "1": [], "2": [1] } };
  assert.deepEqual(asWorkflowMessage({ type: "ready" }), { type: "ready" });
  assert.deepEqual(asWorkflowMessage({ type: "adopt" }), { type: "adopt" });
  assert.deepEqual(asWorkflowMessage({ type: "reload", dirty: true }), { type: "reload", dirty: true });
  assert.deepEqual(asWorkflowMessage({ type: "dirty", dirty: false }), { type: "dirty", dirty: false, checking: false });
  assert.equal(asWorkflowMessage({ type: "dirty", dirty: "no" }), undefined);
  assert.deepEqual(asWorkflowMessage({ type: "order", seq: 3, order }), { type: "order", seq: 3, order });
  assert.equal(asWorkflowMessage({ type: "order", seq: "3", order }), undefined);
  assert.deepEqual(asWorkflowMessage({ type: "save", order, expect: "abc" }), { type: "save", order, expect: "abc" });
  assert.equal(asWorkflowMessage({ type: "save", order }), undefined, "ハッシュがなければ保存しない");
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

test("CB-T377 開くコマンドに渡す親の識別子。識別子の形でなければ受けない", () => {
  assert.equal(workflowTicketOf("i0001"), "i0001");
  for (const bad of ["", " i0001", "../i0001", "i0001/x", 1, undefined, null]) {
    assert.equal(workflowTicketOf(bad), undefined, String(bad));
  }
});

test("CB-T382 案内を閉じた（tourDone）は形を確認して受ける", () => {
  assert.deepEqual(asWorkflowMessage({ type: "tourDone" }), { type: "tourDone" });
  assert.deepEqual(asWorkflowMessage({ type: "tourDone", extra: 1 }), { type: "tourDone" });
});

test("CB-T386 未保存の知らせは、線の答えを待っている（checking）ことも運ぶ。真偽でなければ偽", () => {
  assert.deepEqual(asWorkflowMessage({ type: "dirty", dirty: false, checking: true }), { type: "dirty", dirty: false, checking: true });
  assert.deepEqual(asWorkflowMessage({ type: "dirty", dirty: true, checking: "yes" }), { type: "dirty", dirty: true, checking: false });
});

test("CB-T387 提案が承認待ちから無くなった理由。新規の親は承認済みか取り下げかを分け、改版は分けられないので言い切らない。まだ承認待ちか、ボードにない・確認できないときは何も言わない", () => {
  assert.equal(goneReasonOf("i0001", false, { proposal: "todo", approved: false }), undefined);
  assert.equal(goneReasonOf("i0001", true, { proposal: "todo", approved: true }), undefined);
  assert.match(goneReasonOf("i0001", false, { proposal: null, approved: true }) ?? "", /^i0001 は承認済みです/);
  assert.match(goneReasonOf("i0001", false, undefined) ?? "", /取り下げ・削除/);
  assert.match(goneReasonOf("i0001", false, { proposal: null, approved: false }) ?? "", /取り下げ・削除/);
  // 改版は承認済みの親に対して出すので、承認済みチケットがあっても承認されたとは限らない
  const revision = goneReasonOf("i0001", true, { proposal: null, approved: true }) ?? "";
  assert.match(revision, /改版の提案が承認待ちから無くなりました（承認されたか、取り下げ・削除されました）/);
  assert.doesNotMatch(revision, /承認済みです/);
  assert.match(goneReasonOf("i0001", true, { proposal: "review", approved: true }) ?? "", /承認されたか/);
});

test("CB-T388 閉じた後に戻すかを聞くのは、未保存でコピーがあり、読み込めていて、保存の途中でないときだけ", () => {
  const base = { dirty: true, draft: true, loaded: true, saving: false };
  assert.equal(restoreAsked(base), true);
  assert.equal(restoreAsked({ ...base, saving: true }), false, "保存を押してすぐ閉じた（書き込みは通る）");
  assert.equal(restoreAsked({ ...base, dirty: false }), false);
  assert.equal(restoreAsked({ ...base, draft: false }), false);
  assert.equal(restoreAsked({ ...base, loaded: false }), false);
});

test("CB-T389 保存で覚えるハッシュは、書けたときの答え（書いたあとに再読み込みしたもの）だけ", () => {
  const written = planOrderSample("written", "after-write");
  assert.equal(savedShaOf({ kind: "written", answer: written }), "after-write");
  for (const kind of ["unchanged", "changed"] as const) {
    assert.equal(savedShaOf({ kind, answer: written }), undefined, kind);
  }
  assert.equal(savedShaOf({ kind: "refused", answer: written, reason: "r" }), undefined);
  assert.equal(savedShaOf({ kind: "error", error: "e" }), undefined);
});
