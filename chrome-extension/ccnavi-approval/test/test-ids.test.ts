/**
 * 試験の名前の ID（`CX-T…`）が重ならないこと。VS Code 拡張の `CB-T…` と同じ決まり。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { HERE } from "./helpers/python.js";

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    return e.isDirectory() ? walk(full) : e.name.endsWith(".test.ts") ? [full] : [];
  });
}

test("CX-T099 試験の ID は重ならない", () => {
  const seen = new Map<string, string>();
  const dup: string[] = [];
  for (const file of walk(path.join(HERE, "test"))) {
    for (const m of fs.readFileSync(file, "utf8").matchAll(/^\s*test\(\s*"(CX-T\d+[a-z]*)\s/gm)) {
      if (seen.has(m[1])) dup.push(`${m[1]}: ${seen.get(m[1])} と ${file}`);
      seen.set(m[1], file);
    }
  }
  assert.ok(seen.size >= 20, `数えられた ID: ${seen.size}`);
  assert.deepEqual(dup, []);
});
