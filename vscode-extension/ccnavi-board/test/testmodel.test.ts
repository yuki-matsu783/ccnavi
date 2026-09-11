import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { TEST_VERSION, parseSamplesJson, parseTestJson } from "../src/core/testmodel.js";

const FIXTURES = path.join(__dirname, "..", "..", "test", "fixtures");

function read(name: string): string {
  return fs.readFileSync(path.join(FIXTURES, name), "utf8");
}

test("CB-T37 --test --json のフィクスチャ（実行ファイルの出力）を読める", () => {
  const parsed = parseTestJson(read("test.json"));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  const t = parsed.value;
  assert.equal(t.version, TEST_VERSION);
  assert.equal(t.known, true);
  assert.equal(t.verdict, "deny");
  assert.equal(t.code, "DENY_COMMAND_PATTERN");
  assert.equal(t.rules.length, 1);
  assert.deepEqual(
    [t.rules[0].id, t.rules[0].section, t.rules[0].kind, t.rules[0].source, t.rules[0].written],
    ["git-push", "deny", "glob", "file", "*git push*"],
  );
  assert.notEqual(t.rules[0].pattern, "");
  assert.match(t.response, /push/);
});

test("CB-T38 --test-samples --json のフィクスチャを読める", () => {
  const parsed = parseSamplesJson(read("samples.json"));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  const s = parsed.value;
  assert.equal(s.mismatches, 1);
  assert.equal(s.skipped, 1);
  assert.deepEqual(s.counts.allow, { ok: 2, total: 3 });
  const wrong = s.samples.find((x) => !x.ok);
  assert.ok(wrong);
  assert.deepEqual([wrong.expected, wrong.verdict, wrong.rules[0].id], ["allow", "deny", "git-push"]);
  const skipped = s.samples.find((x) => x.skipped);
  assert.ok(skipped);
  assert.equal(skipped.ok, true);
});

test("CB-T39 版が違う・JSON でない・オブジェクトでないときは理由を返す", () => {
  const other = read("test.json").replace(`"version": ${TEST_VERSION}`, `"version": ${TEST_VERSION + 1}`);
  const parsed = parseTestJson(other);
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /版が違う/);
  }
  const broken = parseSamplesJson("nope");
  assert.equal(broken.ok, false);
  const notObject = parseTestJson("[]");
  assert.equal(notObject.ok, false);
});

test("CB-T40 欠けた項目は既定値で埋め、全体を捨てない", () => {
  const parsed = parseTestJson(JSON.stringify({ version: TEST_VERSION, tool: "Bash" }));
  assert.equal(parsed.ok, true);
  if (parsed.ok) {
    assert.equal(parsed.value.known, false);
    assert.deepEqual(parsed.value.rules, []);
    assert.equal(parsed.value.response, "");
  }
});
