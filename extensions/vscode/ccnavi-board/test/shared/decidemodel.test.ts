/**
 * 残った指摘の JSON（実行ファイルとの契約）の読み取り。`src/core/decidemodel.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { choicesProblem, parseDecidePreview, parseDecideResult } from "../../src/core/decidemodel.js";

const DIGEST = "v2.eyJiIjoiYiIsInQiOnsidTEiOiJ2In19";

function previewJson(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 2,
    parent: "i0001",
    phase: 2,
    mr: { number: 7, url: "https://example.com/mr/7" },
    can_issue: true,
    threads: [
      {
        key: "u1",
        url: "u1",
        path: "src/a.py",
        line: 3,
        body: "命名",
        version: "v".repeat(32),
        last_comment: { id: "c1", at: "2026-10-01T00:00:00Z", author: "me" },
      },
    ],
    digest: DIGEST,
    ...extra,
  });
}

test("CB-T206 残った指摘の一覧を読む。版・ダイジェストが合わなければ読まない", () => {
  const parsed = parseDecidePreview(previewJson());
  assert.ok(parsed.ok);
  assert.equal(parsed.ok && parsed.value.threads[0].key, "u1");
  assert.equal(parsed.ok && parsed.value.phase, 2);
  assert.equal(parsed.ok && parsed.value.threads[0].version, "v".repeat(32));
  assert.equal(parsed.ok && parsed.value.threads[0].last_comment.id, "c1");
  // 版 1 の JSON とダイジェスト（64 桁の 16 進）は読まない
  assert.equal(parseDecidePreview(previewJson({ version: 1 })).ok, false);
  assert.equal(parseDecidePreview(previewJson({ digest: "b".repeat(64) })).ok, false);
  assert.equal(parseDecidePreview(previewJson({ digest: "" })).ok, false);
  // どこから来た値かを文に入れる
  const badDigest = parseDecidePreview(previewJson({ digest: "xyz" }));
  assert.ok(!badDigest.ok && "error" in badDigest && badDigest.error.startsWith("ccnavi-review.sh decide --preview の digest が"));
  assert.equal(parseDecidePreview("not json").ok, false);
});

test("CB-T207 置いた答えを読む。食い違いは mismatch、ok が真でなければ置けたと読まない", () => {
  const ok = parseDecideResult(
    JSON.stringify({ version: 2, ok: true, parent: "i0001", phase: 2, reviewed: false, pending_extra: ["u9"], followup: "i0001-02-03", prompt: "文", issue_url: "", warning: "" }),
  );
  assert.ok(ok.ok);
  assert.equal(ok.ok && ok.value.followup, "i0001-02-03");
  assert.deepEqual(ok.ok && ok.value.pending_extra, ["u9"]);
  const mismatch = parseDecideResult(JSON.stringify({ version: 2, ok: false, mismatch: true }));
  assert.deepEqual(mismatch, { ok: false, mismatch: true });
  const unknown = parseDecideResult(JSON.stringify({ version: 2 }));
  assert.equal(unknown.ok, false);
  assert.ok(!unknown.ok && "error" in unknown);
});

test("CB-T208 対応方針は見せた指摘の全部に 1 つずつ。知らない対応方針は通さず、issue はいつでも通す", () => {
  const preview = parseDecidePreview(previewJson());
  assert.ok(preview.ok);
  if (!preview.ok) return;
  assert.equal(choicesProblem(preview.value, { u1: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, {}), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "keep", u2: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "later" }), undefined);
  // issue はいつでも選べる。古い版が false を返しても、選べるかの判定には使わない
  assert.equal(choicesProblem(preview.value, { u1: "issue" }), undefined);
  assert.equal(choicesProblem({ ...preview.value, can_issue: false }, { u1: "issue" }), undefined);
});
