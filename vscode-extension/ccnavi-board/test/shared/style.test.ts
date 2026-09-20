/**
 * 5 つの画面が同じ骨組みの CSS を 1 か所から持っていること。
 * 5 画面とも中身は画面（React）の中で組むが、CSS と body は拡張が入れるので同じに見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { PAGE_STYLE } from "../../src/core/styles.js";
import { buildProjectsPage } from "../../src/core/projects.js";
import { fixture } from "../helpers/fixture.js";
import { NONCE, boardPage } from "../helpers/board.js";
import { projectsHtml } from "../helpers/projects.js";
import { page as riskPage, riskHtml } from "../helpers/risk.js";
import { page as phasesPage, phasesHtml } from "../helpers/phases.js";
import { page as rulesPage, rulesHtml } from "../helpers/rules.js";

const OPTIONS = { nonce: NONCE };

/** 5 画面。中身は画面（React）が組み、CSS と body だけを拡張が入れる */
function reactPages(appearance?: "claude-dark"): string[] {
  const options = appearance === undefined ? {} : { appearance };
  const page = buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", ignored: true, rulesRels: {}, rulesExists: {}, hasClaudeDir: {}, selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false });
  return [
    boardPage({ kind: "board", board: buildBoard(fixture()) }, options),
    projectsHtml({ kind: "page", page }, options),
    riskHtml({ kind: "page", page: riskPage() }, options),
    phasesHtml({ kind: "page", page: phasesPage() }, options),
    rulesHtml({ kind: "page", page: rulesPage() }, options),
  ];
}

function board(appearance?: "claude-dark"): string {
  return reactPages(appearance)[0];
}

/** 一覧（設定 3 画面）の骨組みを持つ 1 枚。ルール設定画面で見る */
function rulesOnly(): string {
  return reactPages()[4];
}

test("CB-T127 5 つの画面は同じ骨組みの CSS（ツールバー・帯・欄・脚注）を 1 つの定数から持つ", () => {
  assert.match(PAGE_STYLE, /\.toolbar \{/);
  assert.match(PAGE_STYLE, /\.banner\.warn \{/);
  assert.match(PAGE_STYLE, /input\[type=text\], input\[type=search\], textarea, select \{/);
  for (const html of reactPages()) {
    assert.ok(html.includes(PAGE_STYLE));
    // 骨組みの定義は 1 度だけ（画面ごとの写しを残さない）
    assert.equal((html.match(/  \.toolbar \{ display: flex;/g) ?? []).length, 1);
    // 見た目を指定しなければ素の body。Claude の配色の CSS は常に持つ
    assert.ok(html.includes("\n<body>\n"));
    assert.ok(html.includes("body.ccnavi-claude-light:not("));
  }
  // 切り替えの受け口は 5 画面とも画面（React）の中にある。動かして見るのは各画面の dom のテスト
  // （ボードは CB-T142、プロジェクト管理は CB-D32、リスク管理は CB-D57、ルール設定は CB-D0b）
  for (const html of reactPages("claude-dark")) {
    assert.ok(html.includes('\n<body class="ccnavi-claude-dark">\n'));
  }
});

test("CB-T130 ハイコントラスト向けの縁は contrast の変数を使い、他のテーマでは効かない書き方になっている", () => {
  const html = board();
  // 一覧（設定 3 画面）の開いた行の縁は LIST_STYLE にあるので、ルール設定画面で見る
  const rules = rulesOnly();
  assert.match(rules, /\.row\.open > \.row-head, \.row\.open > \.row-body \{ box-shadow: inset 3px 0 0 var\(--vscode-contrastActiveBorder, var\(--vscode-focusBorder\)\); \}/);
  assert.match(html, /button\.action:disabled \{ border-color: var\(--vscode-contrastBorder, transparent\); border-style: dashed; \}/);
  // カードのホバーの点線は疑似要素で、他のテーマでは透明。焦点の輪（outline の実線）には触らない
  assert.match(html, /\.card:hover::after \{[^}]*border: 1px dashed var\(--vscode-contrastActiveBorder, transparent\); \}/);
  assert.match(html, /\.card:hover, \.card:focus \{\s*outline: 1px solid var\(--vscode-focusBorder\);/);
  assert.doesNotMatch(html, /\.card:hover \{ outline/);
  // ホバーの点線はボタンの焦点の輪を消さない。行の見出しにも点線
  assert.match(html, /button\.action:hover:not\(:disabled\):not\(:focus-visible\) \{ outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  assert.match(rules, /\.row-head:hover \{ background: var\(--vscode-list-hoverBackground\); outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
});
