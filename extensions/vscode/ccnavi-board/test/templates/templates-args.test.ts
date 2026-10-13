/**
 * テンプレート管理画面が子プロセスに渡す引数（`src/core/commands.ts` の `templates*Args`）を確かめる。
 * 引数は実行ファイルとの契約なので、期待する配列そのものと比べる。`--root` は `run()` が付けるので含めない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  templatesArgs,
  templatesDraftArgs,
  templatesEditArgs,
  templatesEditYesArgs,
  templatesImportArgs,
  templatesImportYesArgs,
  templatesShowArgs,
} from "../../src/core/commands.js";
import type { EditRequest, ImportRequest } from "../../src/core/templates-view.js";

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

test("CB-T548 プレビューの引数は common・project・replace の 3 通り。--yes は付けない", () => {
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

const EDIT_BASE: EditRequest = { op: "save", name: "login", on: "draft", layer: "", newName: "", to: "", toLayer: "", body: "" };

test("CB-T559 プレビューの引数（保存）は --save-draft と --body-file。--yes は付けない", () => {
  assert.deepEqual(templatesEditArgs({ ...EDIT_BASE, op: "save" }, "/tmp/body.yml"), [
    "--templates",
    "--save-draft",
    "login",
    "--body-file",
    "/tmp/body.yml",
    "--json",
  ]);
});

test("CB-T560 実行の引数（保存）は --yes --expect --via board が --json の前に並ぶ", () => {
  assert.deepEqual(templatesEditYesArgs({ ...EDIT_BASE, op: "save" }, "/tmp/body.yml", "ab12"), [
    "--templates",
    "--save-draft",
    "login",
    "--body-file",
    "/tmp/body.yml",
    "--yes",
    "--expect",
    "ab12",
    "--via",
    "board",
    "--json",
  ]);
});

test("CB-T561 改名は project のとき --layer が --new-name の前に並ぶ", () => {
  assert.deepEqual(
    templatesEditArgs({ ...EDIT_BASE, op: "rename", name: "feature", on: "project", layer: "self", newName: "feature2" }, ""),
    ["--templates", "--rename-template", "feature", "--on", "project", "--layer", "self", "--new-name", "feature2", "--json"],
  );
});

test("CB-T562 移動は common から project へ。--to project の後ろに --to-layer が並ぶ", () => {
  assert.deepEqual(
    templatesEditArgs({ ...EDIT_BASE, op: "move", name: "feature", on: "common", to: "project", toLayer: "self" }, ""),
    ["--templates", "--move-template", "feature", "--on", "common", "--to", "project", "--to-layer", "self", "--json"],
  );
});

test("CB-T563 削除は common のとき --layer を付けず、--on common を並べる", () => {
  assert.deepEqual(templatesEditArgs({ ...EDIT_BASE, op: "delete", name: "feature", on: "common" }, ""), [
    "--templates",
    "--delete-template",
    "feature",
    "--on",
    "common",
    "--json",
  ]);
});

test("CB-T564 下書きの削除は --delete-draft <名前> だけ。実行は --yes --expect を続ける", () => {
  assert.deepEqual(templatesEditArgs({ ...EDIT_BASE, op: "delete-draft" }, ""), ["--templates", "--delete-draft", "login", "--json"]);
  assert.deepEqual(templatesEditYesArgs({ ...EDIT_BASE, op: "delete-draft" }, "", "ab12"), [
    "--templates",
    "--delete-draft",
    "login",
    "--yes",
    "--expect",
    "ab12",
    "--via",
    "board",
    "--json",
  ]);
});

test("CB-T571 下書きの引数は --templates --draft <親> --json（完了した親から下書きを作る）", () => {
  assert.deepEqual(templatesDraftArgs("i0001"), ["--templates", "--draft", "i0001", "--json"]);
});
