/**
 * 書き込みの保護（`src/core/guard.ts`）。書く先のパスが置き場の下かを、先頭の文字列だけで決めない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_PLACES, isPlainRel, underPlaces, writeRefusal } from "../src/core/guard.js";

test("CX-T214 置き場の下のふつうの相対パスは書ける", () => {
  for (const p of ["wip/proposals/todo/i0001.md", ".ccnavi/approved/doing/i0001-01-01.md", ".ccnavi/approved/flows/i0001-01-01.yml"]) {
    assert.equal(isPlainRel(p), true, p);
    assert.equal(underPlaces(p, DEFAULT_PLACES), true, p);
  }
  assert.equal(writeRefusal("i0001", "main", ["wip/proposals/todo/i0001.md"], DEFAULT_PLACES), "");
});

test("CX-T215 置き場で始まっても、外へ出る形（.. や . の要素・空の要素・絶対パス・バックスラッシュ）は断る", () => {
  const bad = [
    "wip/proposals/../../src/x.py",
    ".ccnavi/approved/../../.claude/settings.json",
    "wip/proposals/./todo/i0001.md",
    "wip/proposals//todo/i0001.md",
    "wip/proposals/todo/..",
    "/wip/proposals/todo/i0001.md",
    "~/wip/proposals/todo/i0001.md",
    "C:/wip/proposals/todo/i0001.md",
    "wip/proposals/todo\\..\\..\\x.md",
    "\\wip/proposals/todo/i0001.md",
    "",
  ];
  for (const p of bad) {
    assert.equal(isPlainRel(p), false, p);
    assert.equal(underPlaces(p, DEFAULT_PLACES), false, p);
  }
  const why = writeRefusal("i0001", "main", ["wip/proposals/todo/i0001.md", "wip/proposals/../../src/x.py"], DEFAULT_PLACES);
  assert.match(why, /置き場/);
  assert.match(why, /wip\/proposals\/\.\.\/\.\.\/src\/x\.py/);
});
