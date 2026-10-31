/**
 * 取り込み待ち画面の入れ物（HTML）（`src/core/handoff-render.ts`）。中身は画面（React）が作るので、ここで見るのは
 * 保護（CSP）・埋め込む中身・バンドルした画面の流し込みだけ。画面の描き方は画面のテストで見る。
 * 画面の 1 本（バンドル）には頼らず、渡すスクリプトと CSS は見本の文字列にする。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderHandoffPage } from "../../src/core/handoff-render.js";
import type { HandoffData } from "../../src/core/handoff-view.js";

const SCRIPT = "console.log('handoff-screen');";
const STYLE = ".handoff-screen { color: red; }";

function render(data: HandoffData, nonce: string): string {
  return renderHandoffPage(data, { nonce, script: SCRIPT, style: STYLE });
}

test("CB-T1134 取り込み待ち画面の CSP は nonce のみで、外部資源を読まない。タイトルは ccnavi 取り込み待ち", () => {
  const rendered = render({ kind: "loading", text: "取り込み待ちを読み込み中…" }, "n0nce");
  assert.match(rendered, /<meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
  assert.match(rendered, /style-src 'nonce-n0nce'; script-src 'nonce-n0nce'/);
  assert.doesNotMatch(rendered, /https?:\/\//);
  assert.doesNotMatch(rendered, /<(?:script|img|iframe)[^>]*\ssrc=|<link\s/);
  assert.ok(rendered.includes("<title>ccnavi 取り込み待ち</title>"));
});

test("CB-T1135 埋め込みの JSON に data が入り、< は生で入らない。渡したスクリプトと CSS は nonce 付きの中に入る", () => {
  const hostile = "</script><b>x</b>";
  const rendered = render({ kind: "error", error: hostile }, "n0nce");
  const embedded = /<script type="application\/json" id="ccnavi-handoff-data">(.*?)<\/script>/s.exec(rendered);
  assert.ok(embedded !== null, "埋め込みの script がありません");
  assert.ok(!embedded[1].includes("<"), "埋め込みの中に < が生で入っている");
  const data = JSON.parse(embedded[1]) as { kind: string; error: string };
  assert.equal(data.kind, "error");
  assert.equal(data.error, hostile);
  assert.ok(rendered.includes(`<style nonce="n0nce">\n${STYLE}\n</style>`));
  assert.ok(rendered.includes(`<script nonce="n0nce">\n${SCRIPT}\n</script>`));
});

test("CB-T1136 呼ぶたびに渡された nonce が使われる", () => {
  const data: HandoffData = { kind: "error", error: "読めません" };
  const a = render(data, "AAAA");
  const b = render(data, "BBBB");
  assert.ok(a.includes('nonce="AAAA"') && !a.includes("BBBB"));
  assert.ok(b.includes('nonce="BBBB"') && !b.includes("AAAA"));
});
