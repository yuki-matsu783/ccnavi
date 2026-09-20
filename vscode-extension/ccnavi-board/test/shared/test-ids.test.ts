/**
 * テストの名前に付ける ID（`CB-T…` / `CB-D…`）の決まりを、テストで守る。
 *
 * ID は「落ちたテストを名指しする」ためのもので、README やチケットの記録から参照する。
 * **同じ番号が 2 つあると、どちらの主題か読めない**（issue #88）。画面ごとに別のファイルで
 * 採番すると起きるので、人の注意ではなくここで止める。
 *
 * 枝番（`CB-T19b`）は、既にある ID に後から足した確認。番号そのものは同じでよく、
 * 枝番まで込みで一意であればよい。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const TEST_DIR = path.join(__dirname, "..", "..", "..", "test");

/** `test("CB-T12 …"` の ID（枝番まで）と、書いてある場所 */
const DEFINITION = /^\s*test\(\s*"(CB-[TD][0-9]+[a-z]*)\s/;

interface Named {
  readonly id: string;
  readonly where: string;
}

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return walk(full);
    }
    return entry.isFile() && entry.name.endsWith(".test.ts") ? [full] : [];
  });
}

function names(): Named[] {
  const found: Named[] = [];
  for (const file of walk(TEST_DIR)) {
    const rel = path.relative(TEST_DIR, file).split(path.sep).join("/");
    fs.readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, index) => {
        const match = DEFINITION.exec(line);
        if (match !== null) {
          found.push({ id: match[1], where: `${rel}:${index + 1}` });
        }
      });
  }
  return found;
}

test("CB-T163 テストの ID は重複しない。足すときは最後尾の次を採る", () => {
  const found = names();
  assert.ok(found.length > 100, `テストを数えられていない（${found.length} 件）。置き場の綴りが変わった？`);
  const seen = new Map<string, string[]>();
  for (const { id, where } of found) {
    seen.set(id, [...(seen.get(id) ?? []), where]);
  }
  const duplicated = [...seen].filter(([, wheres]) => wheres.length > 1);
  assert.deepEqual(
    duplicated.map(([id, wheres]) => `${id}: ${wheres.join(" / ")}`),
    [],
    "同じ ID が 2 つある。後から付けたほうを最後尾の次へ振り直す（README の「テストの ID」）",
  );
});

test("CB-T164 テストの名前は ID から始まる（名指しできない名前を作らない）", () => {
  const files = walk(TEST_DIR);
  const headless: string[] = [];
  for (const file of files) {
    const rel = path.relative(TEST_DIR, file).split(path.sep).join("/");
    fs.readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, index) => {
        if (/^\s*test\(\s*"/.test(line) && DEFINITION.exec(line) === null) {
          headless.push(`${rel}:${index + 1}`);
        }
      });
  }
  assert.deepEqual(headless, [], "ID の無いテストがある。`test(\"CB-T<番号> …\"` の形にする");
});
