/**
 * テンプレート管理画面が子プロセスに渡す引数（`src/core/commands.ts` の `templates*Args`）を確かめる。
 * 引数は実行ファイルとの契約なので、期待する配列そのものと比べる。`--root` は `run()` が付けるので含めない。
 * 画面は一覧・検索・表示だけで、取り込み・保存・改名・移動・削除の引数は持たない（下書きの取り込みは取り込み待ち画面）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { templatesArgs, templatesDraftArgs, templatesShowArgs } from "../../src/core/commands.js";

test("CB-T546 一覧の引数は --templates --json --layer <名前>", () => {
  assert.deepEqual(templatesArgs("self"), ["--templates", "--json", "--layer", "self"]);
});

test("CB-T547 本文の引数は --templates --show <名前> --layer <名前> --json", () => {
  assert.deepEqual(templatesShowArgs("feature", "self"), [
    "--templates",
    "--show",
    "feature",
    "--layer",
    "self",
    "--json",
  ]);
});

test("CB-T1101 下書きを作る引数は --templates --draft <親> --json。書くだけで効力が無い", () => {
  assert.deepEqual(templatesDraftArgs("i0001"), ["--templates", "--draft", "i0001", "--json"]);
});

test("CB-T1102 テンプレート専用の取り込み・編集の引数と、user templates の呼び出しは、拡張のソースに残っていない", () => {
  const src = path.join(__dirname, "..", "..", "..", "src");
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (/\.(ts|tsx)$/.test(entry.name)) {
        files.push(full);
      }
    }
  };
  walk(src);
  assert.ok(files.length > 50, `ソースを数えられていない（${files.length} 件）`);
  // 実行ファイルから無くなったオプションとサブコマンド。コメントの説明に残っても困らないので、引数の配列として並べた形だけを見る
  const gone = [/"user",\s*"templates"/, /"--save-draft"/, /"--delete-draft"/, /"--rename-template"/, /"--move-template"/, /"--delete-template"/, /"--import"/, /"--replace"/, /"--new-name"/, /"--to-layer"/];
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    for (const pattern of gone) {
      assert.doesNotMatch(text, pattern, `${path.relative(src, file)} に ${String(pattern)} が残っている`);
    }
  }
});
