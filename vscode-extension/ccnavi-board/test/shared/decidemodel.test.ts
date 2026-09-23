/**
 * 残った指摘の JSON（実行ファイルとの契約）の読み取り。`src/core/decidemodel.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { choicesProblem, parseDecidePreview, parseDecideResult } from "../../src/core/decidemodel.js";

const DIGEST = "b".repeat(64);

function previewJson(extra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 1,
    parent: "i0001",
    phase: 2,
    mr: { number: 7, url: "https://example.com/mr/7" },
    can_issue: false,
    threads: [{ key: "u1", url: "u1", path: "src/a.py", line: 3, body: "命名" }],
    digest: DIGEST,
    ...extra,
  });
}

test("CB-T206 残った指摘の一覧を読む。版・指紋が合わなければ読まない", () => {
  const parsed = parseDecidePreview(previewJson());
  assert.ok(parsed.ok);
  assert.equal(parsed.ok && parsed.value.threads[0].key, "u1");
  assert.equal(parsed.ok && parsed.value.phase, 2);
  assert.equal(parseDecidePreview(previewJson({ version: 2 })).ok, false);
  assert.equal(parseDecidePreview(previewJson({ digest: "" })).ok, false);
  assert.equal(parseDecidePreview("not json").ok, false);
});

test("CB-T207 置いた答えを読む。食い違いは mismatch、ok が真でなければ置けたと読まない", () => {
  const ok = parseDecideResult(
    JSON.stringify({ version: 1, ok: true, parent: "i0001", phase: 2, reviewed: false, followup: "i0001-03", prompt: "文", issue_url: "", warning: "" }),
  );
  assert.ok(ok.ok);
  assert.equal(ok.ok && ok.value.followup, "i0001-03");
  const mismatch = parseDecideResult(JSON.stringify({ version: 1, ok: false, mismatch: true }));
  assert.deepEqual(mismatch, { ok: false, mismatch: true });
  const unknown = parseDecideResult(JSON.stringify({ version: 1 }));
  assert.equal(unknown.ok, false);
  assert.ok(!unknown.ok && "error" in unknown);
});

test("CB-T208 行き先は見せた指摘の全部に 1 つずつ。知らない行き先と、回せない issue は通さない", () => {
  const preview = parseDecidePreview(previewJson());
  assert.ok(preview.ok);
  if (!preview.ok) return;
  assert.equal(choicesProblem(preview.value, { u1: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, {}), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "keep", u2: "keep" }), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "later" }), undefined);
  assert.notEqual(choicesProblem(preview.value, { u1: "issue" }), undefined);
  assert.equal(choicesProblem({ ...preview.value, can_issue: true }, { u1: "issue" }), undefined);
});
