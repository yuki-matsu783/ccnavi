/**
 * ルール設定画面の入れ物（HTML）。中身は画面（React）が作るので、ここで見るのは
 * 守り（CSP）・埋め込む中身・束ねた画面の流し込みだけ。描くものは rules.dom.test.ts。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../../src/core/rules-doc.js";
import { NONCE, page, rulesHtml } from "../helpers/rules.js";
import { screenScript } from "../helpers/bundle.js";

const RULES = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: "<b>push</b> は人が行う"
allow: []
`;

function html(): string {
  return rulesHtml({ kind: "page", page: page({ model: readRules(RULES).model }) }, { nonce: "N0NCE" });
}

/** 束ねた画面を除いた入れ物。外を読んでいないことは、拡張が書いたところだけを見て確かめる */
function shell(rendered: string): string {
  return rendered.split(screenScript("rules")).join("（束ねた画面）");
}

test("CB-T48 ルール設定画面は外部資源を読まず、nonce で自分のスタイルとスクリプトだけを許す", () => {
  const rendered = html();
  assert.match(rendered, /default-src 'none'/);
  assert.match(rendered, /style-src 'nonce-N0NCE'; script-src 'nonce-N0NCE'/);
  // 外の資源を指す口が無い（束ねた画面の中の文字列は、読みに行く綴りではないので除く）
  assert.doesNotMatch(shell(rendered), /https?:\/\//);
  assert.doesNotMatch(shell(rendered), /<(?:script|img|iframe)[^>]*\ssrc=|<link\s/);
  assert.match(rendered, /<script type="application\/json" id="ccnavi-rules-data">/);
  assert.match(rendered, /<title>ccnavi ルール設定<\/title>/);
});

test("CB-T49 埋め込む中身は JSON で、文面の < は実体にして script を閉じさせない", () => {
  const rendered = html();
  assert.match(rendered, /\\u003cb>push\\u003c\/b>/);
  assert.doesNotMatch(rendered, /<b>push<\/b>/);
  // ツールの一覧もタイプの読みも埋め込まない。画面が契約（rules-view）から持つ
  assert.doesNotMatch(rendered, /"tools"/);
  assert.match(rendered, /"rulesPath":"\.ccnavi\/common\/rules\.yml"/);
});

// 画面のスクリプトは束ねた 1 本を流し込む（ファイルとしては読ませない）。
// 中身の型は tsconfig.webview.json が見るので、ここで見るのは入れ方だけ。
test("CB-T70 束ねた画面を nonce 付きの script に流し込み、資源としては読ませない", () => {
  const rendered = rulesHtml({ kind: "page", page: page() });
  assert.ok(rendered.includes(`<script nonce="${NONCE}">\n${screenScript("rules")}\n</script>`));
  assert.match(rendered, /<div id="root"><p class="empty" id="ccnavi-loading">ルールを読み込み中\.\.\.<\/p><\/div>/);
  assert.doesNotMatch(shell(rendered), /<script[^>]*\ssrc=/);
});

test("CB-T165 読み直せなかったときは一覧の代わりに理由を渡す", () => {
  const rendered = rulesHtml({ kind: "error", error: "ルールファイルを読めない" });
  assert.match(rendered, /"kind":"error"/);
  assert.match(rendered, /ルールファイルを読めない/);
});
