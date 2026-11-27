import { test } from "node:test";
import assert from "node:assert/strict";
import { dirtyTitle, editSaveBlocker, saveTitle } from "../../src/core/save-gate.js";

test("CB-T1279 編集する画面のタブのタイトルは、未保存の間だけ頭に「● 」を付ける", () => {
  assert.equal(dirtyTitle("ccnaviルール管理", false), "ccnaviルール管理");
  assert.equal(dirtyTitle("ccnaviルール管理", true), "● ccnaviルール管理");
});

const OPEN = { locked: false, reason: "" };

test("CB-T1275 保存を押せない理由は重い順に1つだけ返し、押せるときは空。ツールチップは理由かCtrl+Sの案内", () => {
  assert.equal(editSaveBlocker({ busy: false, dirty: true, lock: OPEN }), "");
  assert.match(editSaveBlocker({ busy: true, dirty: true, lock: { locked: true, reason: "作業中" } }), /処理中/);
  assert.equal(editSaveBlocker({ busy: false, dirty: true, lock: OPEN, readOnly: "読み取り専用", invalid: "重複" }), "読み取り専用");
  assert.equal(editSaveBlocker({ busy: false, dirty: true, lock: { locked: true, reason: "作業中" }, invalid: "重複" }), "作業中");
  assert.match(editSaveBlocker({ busy: false, dirty: true, lock: { locked: true, reason: "" } }), /確認できていない/);
  assert.equal(editSaveBlocker({ busy: false, dirty: false, lock: OPEN, invalid: "重複" }), "重複");
  assert.match(editSaveBlocker({ busy: false, dirty: false, lock: OPEN }), /変更が無い/);
  assert.equal(saveTitle(""), "保存します（Ctrl+S）");
  assert.equal(saveTitle("作業中"), "作業中");
});
