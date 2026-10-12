/**
 * テンプレート管理画面が子プロセスに渡す引数（`src/core/commands.ts` の `templates*Args`）を確かめる。
 * 引数は実行ファイルとの契約なので、期待する配列そのものと比べる。`--root` は `run()` が付けるので含めない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  templatesArgs,
  templatesImportArgs,
  templatesImportYesArgs,
  templatesShowArgs,
} from "../../src/core/commands.js";
import type { ImportRequest } from "../../src/core/templates-view.js";

const COMMON: ImportRequest = { draft: "login", to: "common", layer: "", replace: false };
const PROJECT: ImportRequest = { draft: "login", to: "project", layer: "self", replace: false };
const PROJECT_REPLACE: ImportRequest = { draft: "login", to: "project", layer: "self", replace: true };

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

test("CB-T548 下見の引数は common・project・replace の 3 通り。--yes は付けない", () => {
  assert.deepEqual(templatesImportArgs(COMMON), ["--templates", "--import", "login", "--to", "common", "--json"]);
  assert.deepEqual(templatesImportArgs(PROJECT), [
    "--templates",
    "--import",
    "login",
    "--to",
    "project",
    "--layer",
    "self",
    "--json",
  ]);
  assert.deepEqual(templatesImportArgs(PROJECT_REPLACE), [
    "--templates",
    "--import",
    "login",
    "--to",
    "project",
    "--layer",
    "self",
    "--replace",
    "--json",
  ]);
});

test("CB-T549 実行の引数は --via board が --json の前、--yes と --expect が並ぶ", () => {
  assert.deepEqual(templatesImportYesArgs(COMMON, "ab12"), [
    "--templates",
    "--import",
    "login",
    "--to",
    "common",
    "--yes",
    "--expect",
    "ab12",
    "--via",
    "board",
    "--json",
  ]);
  assert.deepEqual(templatesImportYesArgs(PROJECT_REPLACE, "ab12"), [
    "--templates",
    "--import",
    "login",
    "--to",
    "project",
    "--layer",
    "self",
    "--replace",
    "--yes",
    "--expect",
    "ab12",
    "--via",
    "board",
    "--json",
  ]);
});
