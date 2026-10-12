/**
 * テンプレート管理画面の入れ物（HTML）（`src/core/templates-render.ts`）。中身は画面（React）が作るので、ここで見るのは
 * 保護（CSP）・埋め込む中身・バンドルした画面の流し込みだけ。画面の描き方は画面のテストで見る。
 * 画面の 1 本（バンドル）には頼らず、渡すスクリプトと CSS は見本の文字列にする。
 */
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderTemplatesPage } from "../../src/core/templates-render.js";
import type { TemplatesData } from "../../src/core/templates-view.js";

const SCRIPT = "console.log('templates-screen');";
const STYLE = ".templates-screen { color: red; }";

function render(data: TemplatesData, nonce: string): string {
  return renderTemplatesPage(data, { nonce, script: SCRIPT, style: STYLE });
}

test("CB-T550 テンプレート管理画面の CSP は nonce のみで、外部資源を読まない", () => {
  const rendered = render({ kind: "loading", text: "テンプレートを読み込み中…" }, "n0nce");
  assert.match(rendered, /<meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
  assert.match(rendered, /style-src 'nonce-n0nce'; script-src 'nonce-n0nce'/);
  assert.doesNotMatch(rendered, /https?:\/\//);
  assert.doesNotMatch(rendered, /<(?:script|img|iframe)[^>]*\ssrc=|<link\s/);
});

test("CB-T551 タイトルは ccnavi テンプレート管理", () => {
  const rendered = render({ kind: "loading", text: "テンプレートを読み込み中…" }, "n0nce");
  assert.ok(rendered.includes("<title>ccnavi テンプレート管理</title>"));
});

test("CB-T552 埋め込みの JSON に data が入り、< は生で入らない", () => {
  const hostile = "</script><b>x</b>";
  const rendered = render({ kind: "error", error: hostile }, "n0nce");
  const embedded = /<script type="application\/json" id="ccnavi-templates-data">(.*?)<\/script>/s.exec(rendered);
  assert.ok(embedded !== null, "埋め込みの script が無い");
  assert.ok(!embedded[1].includes("<"), "埋め込みの中に < が生で入っている");
  const data = JSON.parse(embedded[1]) as { kind: string; error: string };
  assert.equal(data.kind, "error");
  assert.equal(data.error, hostile);
});

test("CB-T553 渡したスクリプトと CSS は nonce 付きの script・style の中に入る", () => {
  const rendered = render({ kind: "loading", text: "テンプレートを読み込み中…" }, "n0nce");
  assert.ok(rendered.includes(`<style nonce="n0nce">\n${STYLE}\n</style>`));
  assert.ok(rendered.includes(`<script nonce="n0nce">\n${SCRIPT}\n</script>`));
});

test("CB-T554 呼ぶたびに渡された nonce が使われ、2 回の入れ物の nonce は一致しない", () => {
  const data: TemplatesData = { kind: "loading", text: "テンプレートを読み込み中…" };
  const first = randomBytes(16).toString("base64");
  const second = randomBytes(16).toString("base64");
  assert.notEqual(first, second);
  const a = render(data, first);
  const b = render(data, second);
  assert.ok(a.includes(`nonce="${first}"`));
  assert.ok(!a.includes(second));
  assert.ok(b.includes(`nonce="${second}"`));
  assert.ok(!b.includes(first));
});
