import { test } from "node:test";
import assert from "node:assert/strict";
import { lockFromBoard, lockFromError } from "../../src/core/lock.js";
import { fixture } from "../helpers/fixture.js";

test("CB-T34 実行ファイルが作業中と答えたチケットがあれば保存できない", () => {
  const lock = lockFromBoard(fixture());
  assert.equal(lock.locked, true);
  // 作業中かは実行ファイルの答え（in_progress）だけで決まる。レビュー待ち・閉じた子・取り消した子は偽で来る
  assert.deepEqual(lock.doing, ["i0001-02-02"]);
  assert.match(lock.reason, /i0001-02-02/);
});

test("CB-T35 作業中がなければ保存できる。started_at があっても実行ファイルが偽と答えれば数えない", () => {
  const board = fixture();
  const tickets = board.tickets.map((t) =>
    t.ticket === "i0001-02-02" ? { ...t, in_progress: false } : t,
  );
  // 拡張が started_at から組み直していないことを見るため、着手の時刻は残したままにする
  assert.notEqual(tickets.find((t) => t.ticket === "i0001-02-02")?.started_at, "");
  const lock = lockFromBoard({ ...board, tickets });
  assert.equal(lock.locked, false);
  assert.equal(lock.reason, "");
});

test("CB-T36 ボードが読めなければ閉じる側にする", () => {
  const lock = lockFromError("実行ファイルがありません");
  assert.equal(lock.locked, true);
  // 読めなかった理由をそのまま載せる。何が読めなかったかをユーザが読める
  assert.equal(lock.reason, "作業中のチケット: 実行ファイルがありません");
});

test("CB-T391 作業中かを答えない古い実行ファイルの答えでは、確認できないので保存できない", () => {
  const board = fixture();
  const tickets = board.tickets.map((t) => ({ ...t, in_progress: null }));
  const lock = lockFromBoard({ ...board, tickets });
  assert.equal(lock.locked, true);
  assert.match(lock.reason, /作業中かを示しません/);
});

test("CB-T392 プロジェクトを渡したときは、そのプロジェクトのチケットの答えだけを見る", () => {
  const board = fixture();
  const tickets = board.tickets.map((t) => ({ ...t, project: t.ticket === "i0001-02-02" ? "lib" : "" }));
  assert.equal(lockFromBoard({ ...board, tickets }, "lib").locked, true);
  assert.equal(lockFromBoard({ ...board, tickets }, "other").locked, false);
});
