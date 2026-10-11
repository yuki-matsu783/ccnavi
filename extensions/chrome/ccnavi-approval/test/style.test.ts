/**
 * 画面の CSS の入口。ボードと設定画面は「部品 1 つに CSS 1 本」で、入口の style.css が `@import` で束ねる。
 * `@import` を書き忘れた CSS は、置いてあるのにバンドル（dist/*.css）へ入らず、見た目だけが気づかないうちに抜ける。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { HERE } from "./helpers/python.js";

const SCREENS = ["board", "options"];

/** 画面の置き場の下の CSS（入口の style.css を除く）。置き場からの相対パス */
function parts(dir: string, sub = ""): string[] {
  return fs.readdirSync(path.join(dir, sub), { withFileTypes: true }).flatMap((e) => {
    const rel = sub === "" ? e.name : `${sub}/${e.name}`;
    if (e.isDirectory()) return parts(dir, rel);
    return e.name.endsWith(".css") && rel !== "style.css" ? [rel] : [];
  });
}

test("CX-T235 ボードと設定画面に置いた CSS は、入口の style.css から全部 @import されている", () => {
  for (const name of SCREENS) {
    const dir = path.join(HERE, "src", name);
    const entry = fs.readFileSync(path.join(dir, "style.css"), "utf8");
    const imported = new Set([...entry.matchAll(/@import\s+["']\.\/([^"']+)["']/g)].map((m) => m[1]));
    const found = parts(dir);
    assert.ok(found.length > 0, `${name} に部品の CSS が見つからない`);
    assert.deepEqual(
      found.filter((file) => !imported.has(file)),
      [],
      `${name}/style.css に @import が無い CSS がある。style.css に足す`,
    );
    // 置き場に無いファイルを読んでいない（消した部品の @import が残っていない）
    assert.deepEqual(
      [...imported].filter((file) => !fs.existsSync(path.join(dir, file))),
      [],
      `${name}/style.css の @import の行き先が無い`,
    );
  }
});
