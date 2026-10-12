/**
 * テンプレート管理画面が読む実行ファイルの答えの読み取り（`src/core/templatesmodel.ts`）を確かめる。見本は
 * `tests/fixtures/templates-json.json`（`ccnavi --templates --json` の答えの実例）。読むだけで判定しないので、
 * 期待値は見本の値と、手で書いた答えの形から決める。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseImport,
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

test("CB-T533 捨てた理由を読める。共通の broken は invalid、self の feature は duplicate、self の research は conflict", () => {
  const data = fixture();
  const broken = data.templates.find((t) => t.name === "broken" && t.layer === "common");
  assert.equal(broken?.dropped?.reason, "invalid");
  const feature = data.templates.find((t) => t.name === "feature" && t.layer === "self");
  assert.equal(feature?.dropped?.reason, "duplicate");
  const research = data.templates.find((t) => t.name === "research" && t.layer === "self");
  assert.equal(research?.dropped?.reason, "conflict");
  const common = data.templates.find((t) => t.name === "feature" && t.layer === "common");
  assert.equal(common?.dropped, null, "捨てていなければ null");
});

test("CB-T534 下書きの login は importable・checked_with・exists_in・also_in を型どおり読む", () => {
  const data = fixture();
  const login = data.drafts.find((draft) => draft.name === "login");
  assert.deepEqual(
    {
      importable: login?.importable,
      checked_with: login?.checked_with,
      exists_in: login?.exists_in,
      also_in: login?.also_in,
    },
    { importable: true, checked_with: "self", exists_in: [], also_in: ["lib"] },
  );
  const broken = data.drafts.find((draft) => draft.name === "broken");
  assert.equal(broken?.importable, false, "読めない下書きは取り込めない");
});

test("CB-T535 drafts 欄が無い答えは、古い実行ファイルの文で失敗する", () => {
  const text = fixtureWith((raw) => {
    delete raw.drafts;
  });
  assert.deepEqual(parseTemplates(text), {
    ok: false,
    error: "drafts 欄がありません。ccnavi が古い可能性があります。実行ファイルを更新してください",
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

test("CB-T539 --show の失敗の答えは error をそのまま返す。template 欄や text が無いときも失敗する", () => {
  assert.deepEqual(parseTemplateShow('{"version":1,"ok":false,"error":"x"}'), { ok: false, error: "x" });
  assert.equal(parseTemplateShow('{"version":1,"root":"r","layer":"self"}').ok, false);
  assert.equal(parseTemplateShow('{"version":1,"root":"r","layer":"self","template":{"name":"a"}}').ok, false);
});

test("CB-T540 下見の答え（ok・draft あり・diff と new_text）を読む", () => {
  const text = JSON.stringify({
    version: 1,
    ok: true,
    draft: { name: "login", tree: "", path: "wip/proposals/templates/login.yml", sha: "ab12" },
    to: "project",
    layer: "self",
    target: ".ccnavi/config/project/templates/login.yml",
    exists: false,
    replace: false,
    errors: [],
    infos: [],
    problems: [{ severity: "warn", detail: "TODO が残っている" }],
    diff: ["+ name: login", "+ description: x"],
    new_text: "name: login\n",
    written: false,
    removed: false,
    kept: "",
  });
  const answer = value(parseImport(text));
  assert.equal(answer.ok, true);
  assert.equal(answer.draft?.sha, "ab12");
  assert.equal(answer.layer, "self");
  assert.deepEqual(answer.diff, ["+ name: login", "+ description: x"]);
  assert.equal(answer.new_text, "name: login\n");
  assert.deepEqual(answer.problems, [{ severity: "warn", detail: "TODO が残っている" }]);
});

test("CB-T541 下見でエラーを返した答え（ok が偽・draft あり）は、ok:false の値として読む", () => {
  const text = JSON.stringify({
    version: 1,
    ok: false,
    draft: null,
    to: "common",
    layer: "",
    target: "",
    exists: false,
    replace: false,
    errors: ["login は既に在る"],
    infos: [],
    problems: [{ severity: "error", detail: "login は既に在る" }],
    diff: [],
    new_text: "",
    written: false,
    removed: false,
    kept: "",
  });
  const parsed = parseImport(text);
  assert.equal(parsed.ok, true, "draft 欄があれば、ok が偽でも値として読む");
  const answer = value(parsed);
  assert.equal(answer.ok, false);
  assert.equal(answer.draft, null);
  assert.deepEqual(answer.errors, ["login は既に在る"]);
});

test("CB-T542 draft 欄の無い失敗の答えは、error を返す", () => {
  assert.deepEqual(parseImport('{"version":1,"ok":false,"error":"y"}'), { ok: false, error: "y" });
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
