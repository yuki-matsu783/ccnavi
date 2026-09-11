import { test } from "node:test";
import assert from "node:assert/strict";
import { lockFromBoard, lockFromError } from "../src/core/lock.js";
import { fixture } from "./fixture.js";

test("CB-T34 提案が doing のチケットがあれば保存できない", () => {
  const lock = lockFromBoard(fixture());
  assert.equal(lock.locked, true);
  assert.deepEqual(lock.doing, ["i0001-02"]);
  assert.match(lock.reason, /i0001-02/);
});

test("CB-T35 doing が無ければ保存できる。todo や done は数えない", () => {
  const board = fixture();
  const tickets = board.tickets.map((t) =>
    t.proposal === null || t.proposal.state !== "doing"
      ? t
      : { ...t, proposal: { ...t.proposal, state: "done" as const } },
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
