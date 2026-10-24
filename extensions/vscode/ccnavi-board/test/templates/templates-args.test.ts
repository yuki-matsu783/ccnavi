/**
 * テンプレート管理画面が子プロセスに渡す引数（`src/core/commands.ts` の `templates*Args`）を確かめる。
 * 引数は実行ファイルとの契約なので、期待する配列そのものと比べる。`--root` は `run()` が付けるので含めない。
 * 画面は一覧・検索・表示だけで、取り込み・保存・改名・移動・削除の引数は持たない（下書きの取り込みは取り込み待ち画面）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
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
