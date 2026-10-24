/**
 * 計画編集タブの帳面（`src/core/workflow-tabs.ts`）。タブ（`workflow-panel.ts`）が書き、ボード
 * （`board-panel.ts`）が読む。ボードの承認は、未保存のタブがある親を含む一覧を打たず、タブが最後に保存したときの
 * `source_sha` と承認の一覧の `source_sha` を比べて「保存したあとに提案が変わった」を出す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { forgetSaved, markUnsaved, onWorkflowTabs, rememberSaved, resetWorkflowTabs, staleSaved, workflowTabs } from "../../src/core/workflow-tabs.js";

test("CB-T381 未保存の親と保存したときのハッシュを持ち、変わったときだけ知らせる。閉じた・破棄した・保存したタブは未保存から外れる", () => {
  resetWorkflowTabs();
  let calls = 0;
  const stop = onWorkflowTabs(() => {
    calls += 1;
  });
  try {
    assert.deepEqual(workflowTabs(), { unsaved: [], saved: {} });
    markUnsaved("i0002", true);
    markUnsaved("i0001", true);
    assert.deepEqual(workflowTabs().unsaved, ["i0001", "i0002"], "識別子の順に並べる");
    assert.equal(calls, 2);
    // 同じ値を入れ直しても知らせない（ボードを描き直さない）
    markUnsaved("i0001", true);
    assert.equal(calls, 2);
    // 保存した（書けた）・破棄した・閉じた。どれもタブが未保存でなくなったと書く
    markUnsaved("i0001", false);
    assert.deepEqual(workflowTabs().unsaved, ["i0002"]);
    assert.equal(calls, 3);

    rememberSaved("i0001", "sha-1");
    rememberSaved("i0001", "sha-2");
    assert.deepEqual(workflowTabs().saved, { i0001: "sha-2" }, "最後に保存したときのハッシュ");
    assert.equal(calls, 5);
    rememberSaved("i0001", "sha-2");
    assert.equal(calls, 5);
    // 承認して承認待ちから無くなった親は忘れる（あとで出す改版の提案を、前の保存と比べない）
    forgetSaved(["i0001", "i0009"]);
    assert.deepEqual(workflowTabs().saved, {});
    assert.equal(calls, 6);
    forgetSaved(["i0001"]);
    assert.equal(calls, 6);

    // 渡したコピーを書き換えても帳面は変わらない
    const shot = workflowTabs();
    (shot.unsaved as string[]).push("i0003");
    assert.deepEqual(workflowTabs().unsaved, ["i0002"]);
  } finally {
    stop();
  }
  // 止めたあとは知らせない
  markUnsaved("i0004", true);
  assert.equal(calls, 6);
  resetWorkflowTabs();
});

test("CB-T390 ボードを再読み込みしたとき、承認待ちの親の提案にない親の保存のハッシュを忘れる（ターミナルや Chrome 拡張で承認した・取り下げた親）", () => {
  const saved = { i0001: "s1", i0002: "s2", i0003: "s3" };
  assert.deepEqual(staleSaved(saved, ["i0002", "i0009"]), ["i0001", "i0003"]);
  assert.deepEqual(staleSaved(saved, ["i0001", "i0002", "i0003"]), []);
  assert.deepEqual(staleSaved({}, []), []);
});
