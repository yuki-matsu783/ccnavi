import { test } from "node:test";
import assert from "node:assert/strict";

import { foldRejected } from "../../src/webview/board/text.js";

test("CB-T1245 承認対象外の提案は、理由が全文一致するものを1行に畳み、同じ文は1つにする", () => {
  const rejected = Array.from({ length: 50 }, (_, i) => ({
    ticket: `C${i}`,
    problems: [`error: C${i}: 長い理由`, `error: C${i}: 長い理由`],
  }));
  const { groups, more } = foldRejected(rejected);
  assert.equal(more, 0);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].tickets, ["C0", "C1", "C2"]);
  assert.equal(groups[0].more, 47);
  assert.deepEqual(groups[0].reasons, ["長い理由"]);
});

test("CB-T1263 理由が上限を超えたら「ほかN件」だけにする（承認画面に--lintの案内は出さない）", () => {
  const problems = Array.from({ length: 7 }, (_, i) => `error: C0: 理由${i}`);
  const { groups } = foldRejected([{ ticket: "C0", problems }]);
  assert.equal(groups[0].reasons.length, 6);
  assert.equal(groups[0].reasons[5], "ほか2件");
});
