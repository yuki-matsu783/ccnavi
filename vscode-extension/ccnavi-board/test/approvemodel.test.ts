import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { APPROVE_VERSION, parseApprovePreview, parseApproveResult } from "../src/core/approvemodel.js";

/** Python 側のテスト（tests/test_approve_json.py）が書き出した、実行ファイルの出力そのもの */
function fixtureText(name: string): string {
  return fs.readFileSync(path.join(__dirname, "..", "..", "test", "fixtures", name), "utf8");
}

test("CB-T70 承認の preview を読む（束・本文・対象外・読めない提案）", () => {
  const parsed = parseApprovePreview(fixtureText("approve-preview.json"));
  assert.ok(parsed.ok);
  if (!parsed.ok) {
    return;
  }
  const preview = parsed.value;
  assert.equal(preview.version, APPROVE_VERSION);
  assert.deepEqual(
    preview.batch.map((b) => [b.ticket, b.parent, b.phase, b.revision]),
    [
      ["i0001", null, null, false],
      ["i0001-01", "i0001", 1, false],
    ],
  );
  assert.ok(preview.text.startsWith("Ticket 承認リクエスト: 2 件"));
  assert.equal(preview.rejected.length, 1);
  assert.equal(preview.rejected[0].ticket, "i0001-02");
  assert.ok(preview.rejected[0].problems[0].includes("超えている"));
  assert.deepEqual(preview.problems, []);
});

test("CB-T71 承認の答えを読む（承認した / 束が違った）", () => {
  const done = parseApproveResult(fixtureText("approve-yes.json"));
  assert.ok(done.ok);
  if (done.ok) {
    assert.deepEqual(done.value.approved, ["i0001", "i0001-01"]);
    assert.equal(done.value.copies.length, 2);
    assert.ok(done.value.prompt.includes("i0001-01"));
    assert.ok(done.value.prompt.includes("ccnavi-ticket.sh start"));
    assert.ok(done.value.lines.length > 0);
  }
  const changed = parseApproveResult(fixtureText("approve-mismatch.json"));
  assert.ok(!changed.ok);
  assert.ok("mismatch" in changed);
  if ("mismatch" in changed) {
    assert.deepEqual(changed.mismatch.expected, ["i0001-09"]);
    assert.deepEqual(changed.mismatch.current, []);
  }
});

test("CB-T72 版が違う・JSON でない答えは読まない", () => {
  const other = parseApprovePreview(JSON.stringify({ version: APPROVE_VERSION + 1, batch: [] }));
  assert.ok(!other.ok);
  assert.ok(!other.ok && other.error.includes("版が違う"));
  const broken = parseApproveResult("承認した。\n");
  assert.ok(!broken.ok);
  assert.ok("error" in broken);
});
