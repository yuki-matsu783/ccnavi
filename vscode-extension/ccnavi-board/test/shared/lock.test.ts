import { test } from "node:test";
import assert from "node:assert/strict";
import { lockFromBoard, lockFromError } from "../../src/core/lock.js";
import { fixture } from "../helpers/fixture.js";

test("CB-T34 着手済みのチケット（approved/doing/ にあり started_at を持つ）があれば保存できない", () => {
  const lock = lockFromBoard(fixture());
  assert.equal(lock.locked, true);
  // 親 i0001 は doing/ にあるが着手していない。レビュー待ち・閉じた子・取り消した子も数えない
  assert.deepEqual(lock.doing, ["i0001-02"]);
  assert.match(lock.reason, /i0001-02/);
});

test("CB-T35 着手済みが無ければ保存できる。承認待ち・未着手・レビュー待ち・完了は数えない", () => {
  const board = fixture();
  const tickets = board.tickets.map((t) =>
    t.ticket === "i0001-02" ? { ...t, started_at: "" } : t,
  );
  const lock = lockFromBoard({ ...board, tickets });
  assert.equal(lock.locked, false);
  assert.equal(lock.reason, "");
});

test("CB-T36 ボードが読めなければ閉じる側に倒す", () => {
  const lock = lockFromError("実行ファイルが無い");
  assert.equal(lock.locked, true);
  assert.match(lock.reason, /確かめられない/);
});
