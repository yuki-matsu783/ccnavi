/**
 * 開いたばかりのタブに入れる「読み込み中」の 1 枚（`core/loading-render.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadingText, renderLoadingPage } from "../../src/core/loading-render.js";
import { renderPhasesPage } from "../../src/core/phases-render.js";
import { renderProjectsPage } from "../../src/core/projects-render.js";
import { renderBoardPage } from "../../src/core/render.js";
import { renderRiskPage } from "../../src/core/risk-render.js";
import { renderRulesPage } from "../../src/core/rules-render.js";

test("CB-T200 読み込み中の 1 枚はスクリプトを持たず、名前を逃がして出し、見た目のクラスと画面の CSS を持つ", () => {
  const html = renderLoadingPage("ccnavi ルール設定: <a&b>", "<a&b> のルール", { nonce: "N1", style: ".empty { color: red; }", appearance: "claude-dark" });
  // スクリプトは走らせない。CSP も script を通さない（`ready` を送らないので、段取りは入れ物をまだ入れていないつもりのまま）
  assert.doesNotMatch(html, /<script/);
  assert.doesNotMatch(html, /script-src/);
  assert.match(html, /default-src 'none'/);
  assert.match(html, /<style nonce="N1">\n\.empty \{ color: red; \}\n<\/style>/);
  assert.match(html, /<body class="ccnavi-claude-dark">/);
  assert.match(html, /<title>ccnavi ルール設定: &lt;a&amp;b&gt;<\/title>/);
  assert.match(html, /<p class="empty" id="ccnavi-loading">&lt;a&amp;b&gt; のルールを読み込み中...<\/p>/);
  // 見た目を渡さなければ VS Code のテーマに従う
  assert.match(renderLoadingPage("x", "x", { nonce: "N2", style: "" }), /<body>/);
});

test("CB-T200b 読み込み中の一言は「<何>を読み込み中...」で、5 画面の入れ物も束ねた画面が組み上がるまで同じ一言を持つ", () => {
  assert.equal(loadingText("チケット"), "チケットを読み込み中...");
  const options = { nonce: "N", script: "", style: "" };
  const error = { kind: "error", error: "x" } as const;
  const pages: ReadonlyArray<readonly [string, string]> = [
    ["チケット", renderBoardPage(error, options)],
    ["プロジェクト", renderProjectsPage(error, options)],
    ["ルール", renderRulesPage(error, options)],
    ["フェーズ", renderPhasesPage(error, options)],
    ["リスク", renderRiskPage(error, options)],
  ];
  for (const [what, html] of pages) {
    assert.match(html, new RegExp(`<div id="root"><p class="empty" id="ccnavi-loading">${what}を読み込み中\\.\\.\\.</p></div>`), what);
  }
});
