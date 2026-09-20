/**
 * 5 つの画面が同じ骨組みの CSS を 1 か所から持っていること。
 * React に移した 2 画面（ボード・プロジェクト管理）は中身を画面の中で組むが、
 * CSS と body は拡張が入れるので同じに見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { PAGE_STYLE } from "../../src/core/styles.js";
import { renderRulesPage } from "../../src/core/rules-render.js";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRiskPage } from "../../src/core/risk-render.js";
import { readRisk, BUILTIN_RISK_TEXT } from "../../src/core/risk-doc.js";
import { renderPhasesPage } from "../../src/core/phases-render.js";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import { buildProjectsPage } from "../../src/core/projects.js";
import { fixture } from "../helpers/fixture.js";
import { NONCE, boardPage } from "../helpers/board.js";
import { projectsHtml } from "../helpers/projects.js";

const OPTIONS = { nonce: NONCE };
const lock = { locked: false, reason: "", doing: [] };

/** まだ文字列で中身を組む 3 画面（ルール設定・リスク管理・フェーズ管理） */
function others(appearance?: "claude-dark"): string[] {
  const options = appearance === undefined ? OPTIONS : { ...OPTIONS, appearance };
  return [
    renderRulesPage(
      { root: "/ws", rulesPath: "r.yml", mode: "enable", model: readRules("deny: []\n").model, hooks: [], hookFiles: { settings: true, settingsLocal: false }, samplesPath: "s.yml", lock },
      options,
    ),
    renderRiskPage({ root: "/ws", riskPath: "risks.yml", exists: true, model: readRisk(BUILTIN_RISK_TEXT).model, lock }, options),
    renderPhasesPage({ root: "/ws", phasesPath: "phases.yml", exists: true, model: readPhases(TEMPLATE_PHASES_TEXT).model, lock }, options),
  ];
}

/** React に移した 2 画面。中身は画面が組み、CSS と body だけを拡張が入れる */
function reactPages(appearance?: "claude-dark"): string[] {
  const options = appearance === undefined ? {} : { appearance };
  const page = buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", ignored: true, rulesRels: {}, rulesExists: {}, hasClaudeDir: {}, selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false });
  return [boardPage({ kind: "board", board: buildBoard(fixture()) }, options), projectsHtml({ kind: "page", page }, options)];
}

function board(appearance?: "claude-dark"): string {
  return reactPages(appearance)[0];
}

test("CB-T127 5 つの画面は同じ骨組みの CSS（ツールバー・帯・欄・脚注）を 1 つの定数から持つ", () => {
  assert.match(PAGE_STYLE, /\.toolbar \{/);
  assert.match(PAGE_STYLE, /\.banner\.warn \{/);
  assert.match(PAGE_STYLE, /input\[type=text\], input\[type=search\], textarea, select \{/);
  for (const html of [...reactPages(), ...others()]) {
    assert.ok(html.includes(PAGE_STYLE));
    // 骨組みの定義は 1 度だけ（画面ごとの写しを残さない）
    assert.equal((html.match(/  \.toolbar \{ display: flex;/g) ?? []).length, 1);
    // 見た目を指定しなければ素の body。Claude の配色の CSS は常に持つ
    assert.ok(html.includes("\n<body>\n"));
    assert.ok(html.includes("body.ccnavi-claude-light:not("));
  }
  // 切り替えの受け口。文字列で組む 3 画面は埋め込みのスクリプト、React の 2 画面は画面の中
  // （ボードは CB-T142、プロジェクト管理は CB-D32 が動かして見る）
  for (const html of others()) {
    assert.ok(html.includes('if (data.type === "appearance") { applyAppearance(data.value); }'));
  }
  for (const html of [...reactPages("claude-dark"), ...others("claude-dark")]) {
    assert.ok(html.includes('\n<body class="ccnavi-claude-dark">\n'));
  }
});

test("CB-T130 ハイコントラスト向けの縁は contrast の変数を使い、他のテーマでは効かない書き方になっている", () => {
  const html = board();
  // 一覧（設定 3 画面）の開いた行の縁は LIST_STYLE にあるので、ルール設定画面で見る
  const rules = others()[0];
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
