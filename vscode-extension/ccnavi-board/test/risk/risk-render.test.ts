/**
 * リスク管理画面の入れ物（HTML）。中身は画面（React）が作るので、ここで見るのは
 * 守り（CSP）・埋め込む中身・束ねた画面の流し込みだけ。描くものは risk.dom.test.ts。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRisk } from "../../src/core/risk-doc.js";
import { NONCE, page, riskHtml } from "../helpers/risk.js";
import { screenScript } from "../helpers/bundle.js";

const RISK = `version: 1
factors:
  - id: ci
    points: 35
    glob: ".github/**"
    message: "<b>CI</b> に触った"
`;

function html(): string {
  return riskHtml({ kind: "page", page: page({ model: readRisk(RISK).model }) }, { nonce: "N0NCE" });
}

/** 束ねた画面を除いた入れ物。外を読んでいないことは、拡張が書いたところだけを見て確かめる */
function shell(rendered: string): string {
  return rendered.split(screenScript("risk")).join("（束ねた画面）");
}

test("CB-T80 リスク管理画面は外部資源を読まず、nonce で自分のスタイルとスクリプトだけを許す", () => {
  const rendered = html();
  assert.match(rendered, /default-src 'none'/);
  assert.match(rendered, /style-src 'nonce-N0NCE'; script-src 'nonce-N0NCE'/);
  // 外の資源を指す口が無い（束ねた画面の中の文字列は、読みに行く綴りではないので除く）
  assert.doesNotMatch(shell(rendered), /https?:\/\//);
  assert.doesNotMatch(shell(rendered), /<(?:script|img|iframe)[^>]*\ssrc=|<link\s/);
  assert.match(rendered, /<script type="application\/json" id="ccnavi-risk-data">/);
  assert.match(rendered, /<title>ccnavi リスク管理<\/title>/);
});

test("CB-T81 埋め込む中身は JSON で、文面の < は実体にして script を閉じさせない", () => {
  const rendered = html();
  assert.match(rendered, /\\u003cb>CI\\u003c\/b>/);
  assert.doesNotMatch(rendered, /<b>CI<\/b>/);
  // 当て方のラベルも組み込みの閾値も埋め込まない。画面が契約（risk-view）から持つ
  assert.doesNotMatch(rendered, /"builtinLevels"/);
  assert.match(rendered, /"exists":true/);
});

// 画面のスクリプトは束ねた 1 本を流し込む（ファイルとしては読ませない）。
// 中身の型は tsconfig.webview.json が見るので、ここで見るのは入れ方だけ。
test("CB-T85 束ねた画面を nonce 付きの script に流し込み、資源としては読ませない", () => {
  const rendered = riskHtml({ kind: "page", page: page() });
  assert.ok(rendered.includes(`<script nonce="${NONCE}">\n${screenScript("risk")}\n</script>`));
  assert.match(rendered, /<div id="root"><\/div>/);
  assert.doesNotMatch(shell(rendered), /<script[^>]*\ssrc=/);
});

test("CB-T155 読み直せなかったときは一覧の代わりに理由を渡す", () => {
  const rendered = riskHtml({ kind: "error", error: "配点のファイルを読めない" });
  assert.match(rendered, /"kind":"error"/);
  assert.match(rendered, /配点のファイルを読めない/);
});
