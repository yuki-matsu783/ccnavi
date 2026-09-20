import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { APPROVE_VERSION, parseApprovePreview, parseApproveResult, partialMessage } from "../../src/core/approvemodel.js";

/** Python 側のテスト（tests/ticket/test_approve_json.py）が書き出した、実行ファイルの出力そのもの */
function fixtureText(name: string): string {
  return fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", name), "utf8");
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
  // 本文の指紋。承認するときに --digest で返す。値はワークツリーの絶対パスに依るので、
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

test("CB-T106 版が違う・JSON でない答えは読まない", () => {
  const other = parseApprovePreview(JSON.stringify({ version: APPROVE_VERSION + 1, batch: [] }));
  assert.ok(!other.ok);
  assert.ok(!other.ok && other.error.includes("版が違う"));
  const broken = parseApproveResult("承認した。\n");
  assert.ok(!broken.ok);
  assert.ok("error" in broken);
});

test("CB-T158 途中で止まった承認を読む（置いたぶんを拾い、成功にはしない）", () => {
  const stopped = parseApproveResult(
    JSON.stringify({
      version: APPROVE_VERSION,
      partial: { placed: ["i0001"], ticket: "i0001-01", reason: "書けない (…)", lines: ["  i0001 のフェーズ 1 のマーカーを消した"] },
    }),
  );
  assert.ok(!stopped.ok, "置いたぶんがあっても成功にはしない");
  assert.ok("partial" in stopped);
  if ("partial" in stopped) {
    assert.deepEqual(stopped.partial.placed, ["i0001"]);
    assert.equal(stopped.partial.ticket, "i0001-01");
    assert.ok(stopped.partial.reason.includes("書けない"));
    assert.deepEqual(stopped.partial.lines, ["  i0001 のフェーズ 1 のマーカーを消した"]);
  }
  // 1 件も置かれなかった形
  const none = parseApproveResult(
    JSON.stringify({ version: APPROVE_VERSION, partial: { placed: [], ticket: "i0001", reason: "書けない", lines: [] } }),
  );
  assert.ok(!none.ok && "partial" in none && none.partial.placed.length === 0);
});

test("CB-T159 途中で止まったことを伝える文（置いた件数・後始末で止まった場合・進捗の行）", () => {
  const stopped = partialMessage({
    placed: ["i0001"],
    ticket: "i0001-01",
    reason: "書けない (…)",
    lines: ["  i0001 のフェーズ 1 のマーカーを消した"],
  });
  assert.ok(stopped.includes("i0001-01 で止まった"));
  assert.ok(stopped.includes("i0001 の 1 件は承認済みチケットに入っている"));
  assert.ok(stopped.includes("コミットと push は送っていない"));
  assert.ok(stopped.includes("マーカーを消した"), "端末に出ていた行も渡す");

  // 1 件も置かれなかったときは「一部だけ置かれた」と言わない
  const none = partialMessage({ placed: [], ticket: "i0001", reason: "書けない", lines: [] });
  assert.ok(none.includes("1 件も置かれていない"));
  assert.ok(!none.includes("入っている"));

  // 書けたあとの後始末（マーカーを置く）で落ちたときは、言い方を変える
  const after = partialMessage({
    placed: ["i0001"],
    ticket: "i0001",
    reason: "マーカーを置けない: 書けない",
    lines: [],
  });
  assert.ok(after.includes("i0001 の後始末で止まった"));
  assert.ok(!after.includes("i0001 で止まった"));
});
