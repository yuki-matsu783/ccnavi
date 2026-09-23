/**
 * 開いたばかりのタブに入れる「読み込み中」の 1 枚（`core/loading-render.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderLoadingPage } from "../../src/core/loading-render.js";

test("CB-T200 読み込み中の 1 枚はスクリプトを持たず、名前を逃がして出し、見た目のクラスと画面の CSS を持つ", () => {
  const html = renderLoadingPage("ccnavi ルール設定: <a&b>", { nonce: "N1", style: ".empty { color: red; }", appearance: "claude-dark" });
  // スクリプトは走らせない。CSP も script を通さない（`ready` を送らないので、段取りは入れ物をまだ入れていないつもりのまま）
  assert.doesNotMatch(html, /<script/);
  assert.doesNotMatch(html, /script-src/);
  assert.match(html, /default-src 'none'/);
  assert.match(html, /<style nonce="N1">\n\.empty \{ color: red; \}\n<\/style>/);
  assert.match(html, /<body class="ccnavi-claude-dark">/);
  assert.match(html, /<title>ccnavi ルール設定: &lt;a&amp;b&gt;<\/title>/);
  assert.match(html, /<p class="empty" id="ccnavi-loading">ccnavi ルール設定: &lt;a&amp;b&gt;を読み込んでいる…<\/p>/);
  // 見た目を渡さなければ VS Code のテーマに従う
  assert.match(renderLoadingPage("x", { nonce: "N2", style: "" }), /<body>/);
});
