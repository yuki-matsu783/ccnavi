import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { APPROVE_VERSION, parseApprovePreview, parseApproveResult } from "../src/core/approvemodel.js";

/** Python 側のテスト（tests/ticket/test_approve_json.py）が書き出した、実行ファイルの出力そのもの */
function fixtureText(name: string): string {
  return fs.readFileSync(path.join(__dirname, "..", "..", "test", "fixtures", name), "utf8");
}

test("CB-T104 承認の preview を読む（一覧・範囲の超過・本文・対象外・読めない提案）", () => {
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
      ["i0001-02", "i0001", 1, false],
    ],
  );
  // 種類の範囲を超える子は承認を止めず、一覧に載って超過を持つ（判定で止まる）。
  assert.deepEqual(preview.batch[0].overflow, []);
  assert.deepEqual(preview.batch[1].overflow, []);
  assert.equal(preview.batch[2].overflow.length, 1);
  assert.ok(preview.batch[2].overflow[0].includes("超えている"));
  assert.ok(preview.text.startsWith("Ticket 承認リクエスト: 3 件"));
  assert.ok(preview.text.includes("判定で止まるもの"));
  // 本文の指紋。承認するときに --digest で返す。値は作業ツリーの絶対パスに依るので、
  // フィクスチャでは伏せてある。
  assert.equal(preview.digest, "<digest>");
  // 対象にしないのは形の壊れた子（計画に無い番号）だけ。
  assert.equal(preview.rejected.length, 1);
  assert.equal(preview.rejected[0].ticket, "i0001-05");
  assert.ok(preview.rejected[0].problems[0].includes("計画に無い"));
  assert.deepEqual(preview.problems, []);
});

test("CB-T104b 超過の欄が無い古い答えは、空の並びとして読む", () => {
  const parsed = parseApprovePreview(
    JSON.stringify({
      version: APPROVE_VERSION,
      batch: [{ ticket: "i0001", title: "親", parent: null, phase: null, revision: false, tree: "", path: "" }],
    }),
  );
  assert.ok(parsed.ok);
  assert.ok(parsed.ok && parsed.value.batch[0].overflow.length === 0);
});

test("CB-T105 承認の答えを読む（承認した / 一覧が違った）", () => {
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
    assert.deepEqual(changed.mismatch.digest, { expected: "<digest>", current: "<digest>" });
  }
});

test("CB-T105b 指紋の欄の無い食い違い（古い実行ファイル）は digest を持たない。preview の指紋は空", () => {
  const changed = parseApproveResult(
    JSON.stringify({ version: APPROVE_VERSION, mismatch: { expected: ["i0001"], current: [] } }),
  );
  assert.ok(!changed.ok && "mismatch" in changed && changed.mismatch.digest === undefined);
  const old = parseApprovePreview(JSON.stringify({ version: APPROVE_VERSION, batch: [], text: "x" }));
  assert.ok(old.ok && old.value.digest === "");
});

test("CB-T106 版が違う・JSON でない答えは読まない", () => {
  const other = parseApprovePreview(JSON.stringify({ version: APPROVE_VERSION + 1, batch: [] }));
  assert.ok(!other.ok);
  assert.ok(!other.ok && other.error.includes("版が違う"));
  const broken = parseApproveResult("承認した。\n");
  assert.ok(!broken.ok);
  assert.ok("error" in broken);
});
