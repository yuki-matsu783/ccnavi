import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { SUGGEST_VERSION, parseSuggestJson } from "../../src/core/suggestmodel.js";

const FIXTURES = path.join(__dirname, "..", "..", "..", "test", "fixtures");

function read(name: string): string {
  return fs.readFileSync(path.join(FIXTURES, name), "utf8");
}

test("CB-T266 --suggest --json のフィクスチャ（実行ファイルの出力）を読める", () => {
  const parsed = parseSuggestJson(read("suggest.json"));
  assert.equal(parsed.ok, true);
  if (!parsed.ok) {
    return;
  }
  const s = parsed.value;
  assert.equal(s.version, SUGGEST_VERSION);
  assert.equal(s.dropped, 1);
  assert.deepEqual(
    s.candidates.map((c) => [c.kind, c.section, c.id]),
    [
      ["rule", "ask", "suggest-npm-install"],
      ["message", "deny", "git-push"],
    ],
  );
  assert.match(s.candidates[0].yaml, /^# /);
  assert.equal(s.candidates[1].samples[0].subject, "git push origin main");
});

test("CB-T267 版が違う・JSON でないときは理由を返し、allow の候補は並べない", () => {
  const other = read("suggest.json").replace(`"version": ${SUGGEST_VERSION}`, `"version": ${SUGGEST_VERSION + 1}`);
  const parsed = parseSuggestJson(other);
  assert.equal(parsed.ok, false);
  if (!parsed.ok) {
    assert.match(parsed.error, /版が違います/);
  }
  assert.equal(parseSuggestJson("nope").ok, false);
  const allow = parseSuggestJson(JSON.stringify({ version: SUGGEST_VERSION, candidates: [{ section: "allow", id: "x" }] }));
  assert.ok(allow.ok);
  if (allow.ok) {
    assert.deepEqual(allow.value.candidates, []);
  }
});
