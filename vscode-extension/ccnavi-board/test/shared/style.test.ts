/**
 * 5 つの画面の CSS。置き場は画面（React）と同じ `src/webview/<名前>/` で、部品 1 つに CSS 1 本。
 * 束ねる（`scripts/bundle-webview.js`）と画面 1 つにつき 1 本になり、拡張がそれを `<style nonce>` に
 * 流し込む（ADR-0066）。
 *
 * ここで見るのは 3 つ。骨組み（`styles/page.css`）が 5 画面とも 1 か所から来ていること、
 * ハイコントラストのテーマ向けの書き方が残っていること、そして**置いた CSS が束ねから漏れて
 * いないこと**（`@import` を書き忘れると、見た目だけが黙って抜ける）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { buildBoard } from "../../src/core/board.js";
import { buildProjectsPage } from "../../src/core/projects.js";
import { fixture } from "../helpers/fixture.js";
import { screenStyle, WEBVIEW_SRC } from "../helpers/bundle.js";
import { boardPage } from "../helpers/board.js";
import { projectsHtml } from "../helpers/projects.js";
import { page as riskPage, riskHtml } from "../helpers/risk.js";
import { page as phasesPage, phasesHtml } from "../helpers/phases.js";
import { page as rulesPage, rulesHtml } from "../helpers/rules.js";

/** 画面の名前と、その 1 枚。中身は画面（React）が組み、CSS と body だけを拡張が入れる */
function reactPages(appearance?: "claude-dark"): [string, string][] {
  const options = appearance === undefined ? {} : { appearance };
  const page = buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", ignored: true, rulesRels: {}, rulesExists: {}, hasClaudeDir: {}, selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false });
  return [
    ["board", boardPage({ kind: "board", board: buildBoard(fixture()) }, options)],
    ["projects", projectsHtml({ kind: "page", page }, options)],
    ["risk", riskHtml({ kind: "page", page: riskPage() }, options)],
    ["phases", phasesHtml({ kind: "page", page: phasesPage() }, options)],
    ["rules", rulesHtml({ kind: "page", page: rulesPage() }, options)],
  ];
}

function board(): string {
  return reactPages()[0][1];
}

/** 一覧（設定 3 画面）の骨組みを持つ 1 枚。ルール設定画面で見る */
function rulesOnly(): string {
  return reactPages()[4][1];
}

/** `src/webview/` の下の CSS 全部（リポジトリのルートからの綴り） */
function cssFiles(dir: string = WEBVIEW_SRC): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return cssFiles(full);
    }
    return entry.isFile() && entry.name.endsWith(".css") ? [full] : [];
  });
}

/** `@import "./x.css";` の行き先 */
function importsOf(file: string): string[] {
  const text = fs.readFileSync(file, "utf8");
  return [...text.matchAll(/@import\s+"([^"]+)"/g)].map((match) => path.resolve(path.dirname(file), match[1]));
}

/** 束ねに入る CSS 全部（入口から `@import` で辿れるもの。入口自身も含む） */
function reachable(entries: string[]): Set<string> {
  const seen = new Set<string>();
  const stack = [...entries];
  while (stack.length > 0) {
    const file = stack.pop() as string;
    if (seen.has(file)) continue;
    seen.add(file);
    stack.push(...importsOf(file));
  }
  return seen;
}

/**
 * その CSS が最初に当てる選択子。束ねに入っているかを、綴りではなく中身で見る。
 * `@import` を並べるだけの入口（`style.css`）は当てるものを持たないので undefined
 */
function firstSelector(file: string): string | undefined {
  const text = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  const found = text.split("\n").find((line) => line.trim().endsWith("{"));
  return found?.trim().replace(/\s*\{$/, "");
}

test("CB-T127 5 つの画面は同じ骨組みの CSS（ツールバー・帯・欄・脚注）を 1 か所から持つ", () => {
  for (const [name, html] of reactPages()) {
    // 拡張が入れるのは、束ねた 1 本（`out/webview/<名前>.css`）そのもの
    assert.ok(html.includes(screenStyle(name)), name);
    const style = screenStyle(name);
    assert.match(style, /^\.toolbar \{$/m);
    assert.match(style, /^\.banner\.warn \{$/m);
    assert.match(style, /^input\[type=text\],\ninput\[type=search\],\ntextarea,\nselect \{$/m);
    // 骨組みの定義は 1 度だけ（画面ごとの写しを残さない）
    assert.equal((style.match(/^\.toolbar \{\n  display: flex;/gm) ?? []).length, 1);
    // 見た目を指定しなければ素の body。Claude の配色の CSS は常に持つ
    assert.ok(html.includes("\n<body>\n"));
    assert.ok(style.includes("body.ccnavi-claude-light:not("));
  }
  // 切り替えの受け口は 5 画面とも画面（React）の中にある。動かして見るのは各画面の dom のテスト
  // （ボードは CB-T142、プロジェクト管理は CB-D32、リスク管理は CB-D57、ルール設定は CB-D0b）
  for (const [, html] of reactPages("claude-dark")) {
    assert.ok(html.includes('\n<body class="ccnavi-claude-dark">\n'));
  }
});

test("CB-T130 ハイコントラスト向けの縁は contrast の変数を使い、他のテーマでは効かない書き方になっている", () => {
  const html = board();
  // 一覧（設定 3 画面）の開いた行の縁は styles/list.css にあるので、ルール設定画面で見る
  const rules = rulesOnly();
  assert.match(rules, /\.row\.open > \.row-head,\n\.row\.open > \.row-body \{\n  box-shadow: inset 3px 0 0 var\(--vscode-contrastActiveBorder, var\(--vscode-focusBorder\)\);\n\}/);
  assert.match(html, /button\.action:disabled \{\n  border-color: var\(--vscode-contrastBorder, transparent\);\n  border-style: dashed;\n\}/);
  // カードのホバーの点線は疑似要素で、他のテーマでは透明。焦点の輪（outline の実線）には触らない
  assert.match(html, /\.card:hover::after \{[^}]*border: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  assert.match(html, /\.card:hover,\n\.card:focus \{\n  outline: 1px solid var\(--vscode-focusBorder\);/);
  assert.doesNotMatch(html, /\.card:hover \{\n  outline/);
  // ホバーの点線はボタンの焦点の輪を消さない。行の見出しにも点線
  assert.match(html, /button\.action:hover:not\(:disabled\):not\(:focus-visible\) \{\n  outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  assert.match(rules, /\.row-head:hover \{\n  background: var\(--vscode-list-hoverBackground\);\n  outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
});

test("CB-T166 画面ごとに CSS の入口があり、置いた CSS は必ずその束ねに入る", () => {
  const names = reactPages().map(([name]) => name);
  const entries = names.map((name) => path.join(WEBVIEW_SRC, name, "style.css"));
  for (const entry of entries) {
    assert.ok(fs.existsSync(entry), `画面の CSS の入口が無い: ${entry}`);
  }
  // 置いてあるのに、どの画面の束ねにも入らない CSS が無い（`@import` の書き忘れ）
  const found = reachable(entries);
  const orphans = cssFiles()
    .filter((file) => !found.has(file))
    .map((file) => path.relative(WEBVIEW_SRC, file).split(path.sep).join("/"));
  assert.deepEqual(orphans, [], "どの画面の束ねにも入らない CSS がある。画面の style.css に @import を足す");
  // 綴りだけでなく、束ねた 1 本に中身が入っていることも見る
  for (const name of names) {
    const style = screenStyle(name);
    for (const file of reachable([path.join(WEBVIEW_SRC, name, "style.css")])) {
      const selector = firstSelector(file);
      if (selector === undefined) continue; // `@import` を並べるだけの入口
      assert.ok(style.includes(`${selector} {`), `${name} の束ねに ${path.basename(file)} の ${selector} が入っていない`);
    }
  }
});
