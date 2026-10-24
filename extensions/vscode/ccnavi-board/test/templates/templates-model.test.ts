/**
 * テンプレート管理画面が読む実行ファイルの答えの読み取り（`src/core/templatesmodel.ts`）を確かめる。見本は
 * `tests/fixtures/templates-json.json`（`ccnavi --templates --json` の答えの実例）。読むだけで判定しないので、
 * 期待値は見本の値と、手で書いた答えの形から決める。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseTemplates,
  parseTemplateShow,
  revealControls,
  TEMPLATES_VERSION,
  templatesLayerOf,
} from "../../src/core/templatesmodel.js";
import type { TemplatesJson } from "../../src/core/templates-view.js";
import { templatesFixtureText } from "../helpers/templates.js";

/** 読めた値を返す。読めなければテストを失敗させる */
function value<T>(parsed: { readonly ok: true; readonly value: T } | { readonly ok: false; readonly error: string }): T {
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}

function fixture(): TemplatesJson {
  return value(parseTemplates(templatesFixtureText()));
}

/** 見本の JSON を、欄を書き換えて文字列にする */
function fixtureWith(edit: (raw: Record<string, unknown>) => void): string {
  const raw = JSON.parse(templatesFixtureText()) as Record<string, unknown>;
  edit(raw);
  return JSON.stringify(raw);
}

/** 答えを読んで、最初の下書きを返す */
function fixtureDraft(text: string): TemplatesJson["drafts"][number] {
  return value(parseTemplates(text)).drafts[0];
}

test("CB-T532 見本の一覧を読める。テンプレートの件数、drafts の名前、errors / warns / infos の数", () => {
  const data = fixture();
  assert.equal(data.version, 1);
  assert.equal(data.templates.length, 7);
  assert.deepEqual(
    data.drafts.map((draft) => draft.name),
    ["broken", "login", "research"],
  );
  assert.deepEqual([data.errors, data.warns, data.infos], [3, 2, 1]);
});

test("CB-T533 破棄した理由を読める。共通の broken は invalid、self の feature は duplicate、self の research は conflict", () => {
  const data = fixture();
  const broken = data.templates.find((t) => t.name === "broken" && t.layer === "common");
  assert.equal(broken?.dropped?.reason, "invalid");
  const feature = data.templates.find((t) => t.name === "feature" && t.layer === "self");
  assert.equal(feature?.dropped?.reason, "duplicate");
  const research = data.templates.find((t) => t.name === "research" && t.layer === "self");
  assert.equal(research?.dropped?.reason, "conflict");
  const common = data.templates.find((t) => t.name === "feature" && t.layer === "common");
  assert.equal(common?.dropped, null, "破棄していなければ null");
});

test("CB-T558 下書きは読むだけの欄（名前・置かれたツリー・パス・読めたか・指摘）を読む。取り込みのための欄は持たない", () => {
  const data = fixture();
  const login = data.drafts.find((draft) => draft.name === "login");
  assert.equal(login?.valid, true);
  assert.equal(login?.tree, "");
  assert.ok((login?.path ?? "") !== "");
  assert.ok(login?.problems.some((problem) => problem.detail.includes("TODO")), "TODO の指摘を読む");
  const broken = data.drafts.find((draft) => draft.name === "broken");
  assert.equal(broken?.valid, false, "読み込めない下書きは valid が偽");
  // 取り込みの判断は取り込み待ち画面（実行ファイルの user handoff）が受け持つので、読み取りの型に持ち込まない
  assert.deepEqual(
    Object.keys(login ?? {}).filter((key) => ["sha", "text", "importable", "checked_with", "exists_in", "also_in"].includes(key)),
    [],
  );
});

test("CB-T1103 下書きの欄が古い形（sha・importable・text・exists_in を含む答え）でも読める。余分な欄は破棄する", () => {
  const text = fixtureWith((raw) => {
    const drafts = raw.drafts as Record<string, unknown>[];
    drafts[0] = { ...drafts[0], sha: "x", importable: true, text: "t", exists_in: ["common"], also_in: ["lib"], checked_with: "self" };
  });
  const draft = fixtureDraft(text);
  assert.equal(Object.keys(draft).includes("importable"), false);
});

test("CB-T535 drafts 欄がない答えは、配列でない欄の文で失敗する", () => {
  const text = fixtureWith((raw) => {
    delete raw.drafts;
  });
  assert.deepEqual(parseTemplates(text), {
    ok: false,
    error: "テンプレートの JSON の drafts 欄が配列ではありません",
  });
});

test("CB-T536 版が違う答えは、版の文で失敗する", () => {
  const text = fixtureWith((raw) => {
    raw.version = TEMPLATES_VERSION + 1;
  });
  assert.deepEqual(parseTemplates(text), {
    ok: false,
    error: `テンプレートの JSON の形が違います（拡張は ${TEMPLATES_VERSION}、実行ファイルは ${TEMPLATES_VERSION + 1}）`,
  });
});

test("CB-T537 JSON でない文字列と、最上位がオブジェクトでない JSON は失敗する", () => {
  assert.equal(parseTemplates("これは JSON ではない").ok, false);
  assert.deepEqual(parseTemplates("[1, 2]"), { ok: false, error: "JSON の最上位がオブジェクトではありません" });
});

test("CB-T538 --show の答えを読む。本文はそのまま text に入り、欄は一覧と同じに読む", () => {
  const raw = JSON.parse(templatesFixtureText()) as { templates: Record<string, unknown>[] };
  const feature = raw.templates.find((t) => t.name === "feature" && t.layer === "self");
  const text = JSON.stringify({
    version: 1,
    root: "r",
    layer: "self",
    template: { ...feature, text: "a\nb" },
  });
  const detail = value(parseTemplateShow(text));
  assert.equal(detail.name, "feature");
  assert.equal(detail.text, "a\nb");
  assert.equal(detail.dropped?.reason, "duplicate");
  assert.equal(detail.plan[1].after[0], 1);
});

test("CB-T539 --show の失敗の答えは error をそのまま返す。template 欄や text がないときも失敗する", () => {
  assert.deepEqual(parseTemplateShow('{"version":1,"ok":false,"error":"x"}'), { ok: false, error: "x" });
  assert.equal(parseTemplateShow('{"version":1,"root":"r","layer":"self"}').ok, false);
  assert.equal(parseTemplateShow('{"version":1,"root":"r","layer":"self","template":{"name":"a"}}').ok, false);
});

test("CB-T543 読み違えを誘う文字（書式・幅ゼロ）を \\u で見せ、改行とタブは残す", () => {
  assert.equal(revealControls("a‮b​c\nd\te"), "a\\u202eb\\u200bc\nd\te");
});

test("CB-T544 BMP の外の書式文字は \\U で見せ、通常の日本語と絵文字は変えない", () => {
  assert.equal(revealControls("\u{e0001}"), "\\U000e0001");
  assert.equal(revealControls("日本語の本文 😀"), "日本語の本文 😀");
});

test("CB-T545 本文を読む層は、project なら名前、それ以外は self", () => {
  assert.equal(templatesLayerOf({ kind: "project", name: "lib" }), "lib");
  assert.equal(templatesLayerOf({ kind: "workspace", name: "" }), "self");
  assert.equal(templatesLayerOf({ kind: "self", name: "self" }), "self");
});
