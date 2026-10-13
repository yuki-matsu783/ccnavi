import * as fs from "node:fs";
import * as path from "node:path";

import type { TargetOption } from "../../src/core/targets.js";
import { parseTemplates, type TemplatesParse } from "../../src/core/templatesmodel.js";
import { renderTemplatesPage, type RenderOptions } from "../../src/core/templates-render.js";
import type { EditAnswer, ImportAnswer, TemplatesData, TemplatesJson, TemplatesPage } from "../../src/core/templates-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage } from "./dom.js";

/**
 * 実行ファイルの見本（リポジトリルートの `tests/fixtures/templates-json.json`）。`expect` が `ccnavi --templates --json` の答えで、
 * `about` に欄の説明がある。拡張のテストはこの見本を読んで、読み取りの期待値を決める。
 */
export const TEMPLATES_FIXTURE_PATH = path.join(__dirname, "..", "..", "..", "..", "..", "..", "tests", "fixtures", "templates-json.json");

/** 見本の `expect`（`ccnavi --templates --json` の答え）を、実行ファイルの標準出力と同じ文字列にして返す */
export function templatesFixtureText(): string {
  const all = JSON.parse(fs.readFileSync(TEMPLATES_FIXTURE_PATH, "utf8")) as { expect: unknown };
  return JSON.stringify(all.expect);
}

export const NONCE = "TEST-NONCE-123";

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function templatesHtml(data: TemplatesData, options: Partial<RenderOptions> = {}): string {
  return renderTemplatesPage(data, { nonce: NONCE, script: screenScript("templates"), style: screenStyle("templates"), ...options });
}

/** 見本（`expect`）の答えを読んだもの。読めなければ失敗させる */
export function fixtureJson(): TemplatesJson {
  const parsed: TemplatesParse = parseTemplates(templatesFixtureText());
  if (!parsed.ok) {
    throw new Error(`見本を読み込めない: ${parsed.error}`);
  }
  return parsed.value;
}

/** 見本の中身。差し替えたいところだけ渡す */
export function page(overrides: Partial<TemplatesPage> = {}): TemplatesPage {
  return {
    root: "/ws",
    generatedAt: "2026-10-12T00:00:00+0900",
    target: { kind: "self", name: "" },
    targets: [
      { kind: "workspace", name: "", label: "共通の設定" },
      { kind: "self", name: "", label: "ワークスペース" },
    ],
    data: fixtureJson(),
    lock: { locked: false, reason: "", doing: [] },
    projectLocks: {},
    importLayers: ["self", "lib"],
    edit: { enabled: true, reason: "" },
    notices: [],
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: TemplatesData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(templatesHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本の画面を開く。差し替えたいところだけ渡す */
export async function openTemplates(overrides: Partial<TemplatesPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}

/** 取り込みの下見の答えの見本（書く先に同名が無い、問題の無い答え）。差し替えたいところだけ渡す */
export function answer(overrides: Partial<ImportAnswer> = {}): ImportAnswer {
  return {
    ok: true,
    draft: { name: "login", tree: "", path: "login.yml", sha: "a".repeat(64) },
    to: "common",
    layer: "",
    target: ".ccnavi/config/common/templates/login.yml",
    exists: false,
    replace: false,
    errors: [],
    infos: [],
    problems: [],
    diff: [],
    new_text: "name: login\n",
    written: false,
    removed: false,
    kept: "",
    ...overrides,
  };
}

/** 編集の下見と実行の答えの見本（書いていない、問題の無い答え）。差し替えたいところだけ渡す */
export function editAnswer(overrides: Partial<EditAnswer> = {}): EditAnswer {
  return {
    ok: true,
    op: "save",
    name: "login",
    on: "draft",
    layer: "",
    new_name: "",
    to: "",
    to_layer: "",
    source: "wip/proposals/templates/login.yml",
    target: "wip/proposals/templates/login.yml",
    sha: "c".repeat(64),
    errors: [],
    infos: [],
    problems: [],
    diff: [],
    new_text: "",
    written: false,
    removed: false,
    new_sha: "",
    ...overrides,
  };
}

/** 対象の一覧の 1 件（テストで対象の選択欄を見るとき） */
export function targetOption(kind: string, name: string, label: string): TargetOption {
  return { kind, name, label };
}
