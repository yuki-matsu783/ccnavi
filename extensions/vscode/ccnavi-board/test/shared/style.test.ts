/**
 * 6 つの画面の CSS。置き場は画面（React）と同じ `src/webview/<名前>/` で、部品 1 つに CSS 1 本。
 * バンドルする（`scripts/bundle-webview.js`）と画面 1 つにつき 1 本になり、拡張がそれを `<style nonce>` に
 * 流し込む。
 *
 * ここで見るのは 3 つ。骨組み（共通の部品 `extensions/shared/styles/page.css`）が 6 画面とも 1 か所から来ていること、
 * ハイコントラストのテーマ向けの書き方が残っていること、そして置いた CSS がすべてバンドルに入って
 * いること（`@import` を書き忘れると、見た目だけが知らないうちに欠ける）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { buildBoard } from "../../src/core/board.js";
import { buildProjectsPage } from "../../src/core/projects.js";
import { fixture } from "../helpers/fixture.js";
import { flatStyle, screenNames, screenStyle, WEBVIEW_SRC } from "../helpers/bundle.js";
import { boardPage } from "../helpers/board.js";
import { projectsHtml } from "../helpers/projects.js";
import { page as riskPage, riskHtml } from "../helpers/risk.js";
import { page as phasesPage, phasesHtml } from "../helpers/phases.js";
import { page as rulesPage, rulesHtml } from "../helpers/rules.js";
import { flowHtml, page as flowPage } from "../helpers/flow.js";
import { workflowHtml, workflowPage } from "../helpers/workflow.js";
import { page as templatesPage, templatesHtml } from "../helpers/templates.js";
import { handoffHtml, page as handoffPage } from "../helpers/handoff.js";

/** 6 画面が共有する骨組みの CSS（重なり順の値もここ） */
const PAGE_CSS = path.join(WEBVIEW_SRC, "..", "..", "..", "..", "shared", "styles", "page.css");

/** 画面の名前と、その 1 枚。中身は画面（React）が組み、CSS と body だけを拡張が入れる */
function reactPages(appearance?: "claude-dark"): [string, string][] {
  const options = appearance === undefined ? {} : { appearance };
  const page = buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", ignored: true, rulesRels: {}, rulesExists: {}, selfRulesRel: ".ccnavi/config/project/rules.yml", selfRulesExists: false });
  return [
    ["board", boardPage({ kind: "board", board: buildBoard(fixture()) }, options)],
    ["projects", projectsHtml({ kind: "page", page }, options)],
    ["risk", riskHtml({ kind: "page", page: riskPage() }, options)],
    ["phases", phasesHtml({ kind: "page", page: phasesPage() }, options)],
    ["rules", rulesHtml({ kind: "page", page: rulesPage() }, options)],
    ["flow", flowHtml({ kind: "page", page: flowPage() }, options)],
    ["workflow", workflowHtml({ kind: "page", page: workflowPage() }, options)],
    ["templates", templatesHtml({ kind: "page", page: templatesPage() }, options)],
    ["handoff", handoffHtml({ kind: "page", page: handoffPage() }, options)],
  ];
}

function board(): string {
  return reactPages()[0][1];
}

/** 一覧（設定 3 画面）の骨組みを持つ 1 枚。ルール管理画面で見る */
function rulesOnly(): string {
  return reactPages()[4][1];
}

/** 共通の部品（`@ccnavi/shared`）の CSS の置き場。骨組み・ボタン・一覧はここにあり、各画面の style.css が読む */
const SHARED_STYLES = path.join(WEBVIEW_SRC, "..", "..", "..", "..", "shared", "styles");

/** `src/webview/` の下の CSS 全部（リポジトリのルートからのパス） */
function cssFiles(dir: string = WEBVIEW_SRC): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return cssFiles(full);
    }
    return entry.isFile() && entry.name.endsWith(".css") ? [full] : [];
  });
}

/**
 * `@import` の行き先。`"./x.css"` はそのファイルからの相対で、`"@xyflow/react/dist/style.css"` の
 * ように `.` で始まらないものは node_modules から解く（esbuild がバンドルするときと同じ解き方）。
 *
 * 外から来る CSS を入れているのは図の 1 本だけ（React Flow の CSS）。ここで解けないと、このテストは
 * 失敗するのではなく `readFileSync` の ENOENT で例外になるので、行き先を間違えたのか置き忘れたのかが
 * 読めなくなる。解けないパスは名指しで失敗させる。
 */
function importsOf(file: string): string[] {
  const text = fs.readFileSync(file, "utf8");
  return [...text.matchAll(/@import\s+"([^"]+)"/g)].map((match) => {
    const spec = match[1];
    if (spec.startsWith(".") || path.isAbsolute(spec)) {
      return path.resolve(path.dirname(file), spec);
    }
    try {
      return require.resolve(spec, { paths: [path.dirname(file)] });
    } catch {
      assert.fail(`@import の対応方針が解けない: ${spec}（${file}）`);
    }
  });
}

/** バンドルに入る CSS 全部（入口から `@import` で辿れるもの。入口自身も含む） */
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
 * その CSS が最初に適用する選択子。バンドルに入っているかを、パスではなく中身で見る。
 * `@import` を並べるだけの入口（`style.css`）は適用するものを持たないので undefined
 */
function firstSelector(file: string): string | undefined {
  const text = fs
    .readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/@import[^;]*;/g, "");
  // 最初の `{` の手前が、最初に適用する選択子。1 行で書いたファイル（`.a { x: 1; }`）も同じに読む
  const found = /([^{}]+)\{/.exec(text);
  return found === null ? undefined : found[1].trim().replace(/\s*\n\s*/g, " ");
}

test("CB-T127 6 つの画面は同じ骨組みの CSS（ツールバー・帯・欄・脚注）を 1 か所から持つ", () => {
  // 画面を足したら、その 1 枚を reactPages に足す（足さないと、ここから下の検査に入らない）
  assert.deepEqual(reactPages().map(([name]) => name).sort(), screenNames(), "reactPages にない画面がある");
  for (const [name, html] of reactPages()) {
    // 拡張が入れるのは、バンドルした 1 本（`out/webview/<名前>.css`）そのもの
    assert.ok(html.includes(screenStyle(name)), name);
    // 規則は 1 行にまとめて見る（esbuild の並べ方が変わっても、適用するものと宣言が同じなら通す）
    const style = flatStyle(html);
    assert.match(style, /\.toolbar \{ display: flex;/);
    assert.match(style, /\.banner\.warn \{ border-color:/);
    assert.match(style, /input\[type=text\], input\[type=search\], textarea, select \{ background:/);
    // 骨組みの定義は 1 度だけ（画面ごとの複製を残さない）
    assert.equal((style.match(/\.toolbar \{ display: flex;/g) ?? []).length, 1);
    // 見た目を指定しなければ素の body。Claude の配色の CSS は常に持つ
    assert.ok(html.includes("\n<body>\n"));
    assert.ok(style.includes("body.ccnavi-claude-light:not("));
  }
  // 切り替えを受け取る側は 6 画面とも画面（React）の中にある。動かして見るのは各画面の dom のテスト
  // （ボードは CB-T142、プロジェクト管理は CB-D32、リスク管理は CB-D57、ルール管理は CB-D0b）
  for (const [, html] of reactPages("claude-dark")) {
    assert.ok(html.includes('\n<body class="ccnavi-claude-dark">\n'));
  }
});

test("CB-T130 ハイコントラスト向けの縁は contrast の変数を使い、他のテーマでは効かない書き方になっている", () => {
  const html = flatStyle(board());
  // 一覧（設定 3 画面）の開いた行の縁は styles/list.css にあるので、ルール管理画面で見る
  const rules = flatStyle(rulesOnly());
  assert.match(rules, /\.row\.open > \.row-head, \.row\.open > \.row-body \{ box-shadow: inset 3px 0 0 var\(--vscode-contrastActiveBorder, var\(--vscode-focusBorder\)\); \}/);
  assert.match(html, /button\.action:disabled \{ border-color: var\(--vscode-contrastBorder, transparent\); border-style: dashed; \}/);
  // カードのホバーの点線は疑似要素で、他のテーマでは透明。焦点の輪（outline の実線）には触らない
  assert.match(html, /\.card:hover::after \{[^}]*border: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  assert.match(html, /\.card:hover, \.card:focus \{ outline: 1px solid var\(--vscode-focusBorder\);/);
  assert.doesNotMatch(html, /\.card:hover \{ outline/);
  // ホバーの点線はボタンの焦点の輪を消さない。行の見出しにも点線
  assert.match(html, /button\.action:hover:not\(:disabled\):not\(:focus-visible\) \{ outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  assert.match(rules, /\.row-head:hover \{ background: var\(--vscode-list-hoverBackground\); outline: 1px dashed var\(--vscode-contrastActiveBorder, transparent\);/);
  // 行末のボタンは、見出しの「＋ 追加」向けの margin-left: auto を打ち消す。詳細度で採られるようにしてあるので、
  // バンドルの順序（@import の順）が変わっても入れ替わらない
  assert.match(rules, /\.row-body \.buttons button\.action \{ margin-left: 0; \}/);
  // 動いたカードの輪も contrast の変数を使い、他のテーマでは緑のまま。左の縁（不備・承認待ち・
  // レビュー待ち）は上書きしない（別の channel）
  assert.match(html, /\.card\.moved \{ box-shadow: 0 0 0 1px var\(--vscode-contrastActiveBorder, var\(--vscode-charts-green\)\);/);
  assert.doesNotMatch(html, /\.card\.moved \{[^}]*border-left/);
});

test("CB-T193 動いた表示は、光らせない設定を尊び、色だけに頼らない", () => {
  const html = flatStyle(board());
  // 光るのは既定のときだけ。`prefers-reduced-motion` では輪だけが残る（`styles/button.css` の
  // 回り記号と同じ書き方）。ここを外すと、動きを嫌うユーザに 2 秒の脈動が出る
  assert.match(html, /@media \(prefers-reduced-motion: reduce\) \{ \.card\.moved \{ animation: none; \} \}/);
  // どこからどこへ動いたかは帯の文で言う（色が見分けられなくても読める）。中身は Card.tsx の movedLabel
  assert.match(html, /\.moved-mark \{[^}]*color: var\(--vscode-charts-green\);/);
});

test("CB-T166 画面ごとに CSS の入口があり、置いた CSS は必ずそのバンドルに入る", () => {
  const names = screenNames();
  const entries = names.map((name) => path.join(WEBVIEW_SRC, name, "style.css"));
  for (const entry of entries) {
    assert.ok(fs.existsSync(entry), `画面の CSS の入口がない: ${entry}`);
  }
  // 置いてあるのに、どの画面のバンドルにも入らない CSS が無い（`@import` の書き忘れ）
  const found = reachable(entries);
  const orphans = cssFiles()
    .filter((file) => !found.has(file))
    .map((file) => path.relative(WEBVIEW_SRC, file).split(path.sep).join("/"));
  assert.deepEqual(orphans, [], "どの画面のバンドルにも入らない CSS がある。画面の style.css に @import を追加する");
  // パスだけでなく、バンドルした 1 本に中身が入っていることも見る
  const skipped: string[] = [];
  for (const name of names) {
    const style = flatStyle(`<style nonce="x">\n${screenStyle(name)}\n</style>`);
    for (const file of reachable([path.join(WEBVIEW_SRC, name, "style.css")])) {
      const selector = firstSelector(file);
      if (selector === undefined) {
        // `@import` を並べるだけの入口。それ以外が来たら、適用するものを持たない CSS を置いている
        assert.equal(path.basename(file), "style.css", `適用するものが 1 つもない CSS: ${file}`);
        skipped.push(file);
        continue;
      }
      assert.ok(style.includes(`${selector} {`), `${name} のバンドルに ${path.basename(file)} の ${selector} が入っていない`);
    }
  }
  assert.equal(skipped.length, names.length, "入口（style.css）以外が中身の検査から外れている");
});

test("CB-T168 部品の CSS には、同じ名前の部品がある（部品を削除したら CSS も削除する）", () => {
  // 大文字で始まる CSS は部品のもの（`Card.css` は `Card.tsx`）。共通の骨組み（`styles/page.css`）と
  // 画面の入口（`style.css`）は小文字で始まるので、この決まりから外れる
  const missing = cssFiles()
    .filter((file) => /^[A-Z]/.test(path.basename(file)))
    .filter((file) => !fs.existsSync(file.replace(/\.css$/, ".tsx")))
    .map((file) => path.relative(WEBVIEW_SRC, file).split(path.sep).join("/"));
  assert.deepEqual(missing, [], "部品がないのに CSS だけ残っている。CSS と style.css の @import を削除");
});

test("CB-T216 案内の ? は案内のある画面（プロジェクト管理画面と取り込み待ち画面のほか全部）でヘッダの右上に同じ形で固定し、右端はその幅だけ空ける", () => {
  // プロジェクト管理画面と、案内を持たない取り込み待ち画面は、この検査から除く
  for (const name of screenNames().filter((n) => n !== "projects" && n !== "handoff")) {
    const style = flatStyle(`<style nonce="x">\n${screenStyle(name)}\n</style>`);
    assert.match(style, /\.toolbar > \.tour-button \{[^}]*position: absolute;[^}]*top: 8px;[^}]*right: 4px;[^}]*width: 22px;/, `${name} の ? が右上に固定されていない`);
    assert.match(style, /\.toolbar \{ padding-right: 34px; \}/, `${name} のツールバーが ? の幅を空けていない`);
  }
});

test("CB-T584 図の枠の z-index は枠の内側だけで有効で、ツールバーから開く案内の一覧が拡大ボタンの下に隠れない", () => {
  const css = fs.readFileSync(path.join(WEBVIEW_SRC, "shared", "Maximize.css"), "utf8");
  assert.match(css, /\.graph-max \{[^}]*isolation: isolate;/, ".graph-max が isolation: isolate を持たない");
  assert.match(fs.readFileSync(PAGE_CSS, "utf8"), /\.toolbar \{[^}]*z-index: var\(--z-toolbar\);/, "ツールバーが重なりの単位（z-index）を持たない");
});

test("CB-T1221 作業中の帯の情報色が page.css にある。.lock の赤は残す", () => {
  const page = fs.readFileSync(PAGE_CSS, "utf8");
  assert.match(page, /\.lock\.info \{[^}]*var\(--vscode-editorInfo-foreground\);[^}]*var\(--info-text\)/);
  assert.match(page, /\.lock \{[^}]*var\(--vscode-editorError-foreground\)/);
});

test("CB-T585 z-index・角丸・文字サイズ・すき間は page.css の変数で書き、画面の CSS に数値を直書きしない。z-index の値の大小は決めたとおり", () => {
  const page = fs.readFileSync(PAGE_CSS, "utf8");
  const level = (name: string): number => Number(new RegExp(`--z-${name}: (\\d+);`).exec(page)?.[1] ?? Number.NaN);
  // 画面全体で比べる値は下から foot < toolbar < overlay < graph-max < tour
  const order = ["foot", "toolbar", "overlay", "graph-max", "tour"].map(level);
  assert.ok(order.every((n, i) => !Number.isNaN(n) && (i === 0 || order[i - 1] < n)), `値の大小が崩れている: ${order.join(",")}`);
  // 部品の中の値は、脚注より上、ツールバーより下（本文の一覧がスクロールでツールバーの上に出ないように）
  assert.ok(level("inline") < level("foot") && level("foot") < level("popover") && level("popover") < level("toolbar"), "inline < foot < popover < toolbar が崩れている");
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".css")) files.push(full);
    }
  };
  walk(WEBVIEW_SRC);
  walk(path.join(path.dirname(PAGE_CSS), "..", "..", "chrome", "ccnavi-approval", "src"));
  // 画面と共通の部品（ボタン・一覧）。配色の変数だけを定める appearance.css と、値を定める page.css の :root は対象外
  files.push(path.join(path.dirname(PAGE_CSS), "button.css"), path.join(path.dirname(PAGE_CSS), "list.css"), PAGE_CSS);
  // 数値で書いてよいのは 0・1em（等倍）・px の文字（アイコンの大きさ）だけ
  const raw: [RegExp, string][] = [
    [/z-index:\s*-?\d/, "z-index（--z-*）"],
    [/border-radius:[^;}]*(?:\b[1-9]\d*px|\d*\.\d+em)/, "border-radius（--radius-*）"],
    [/font-size:\s*(?!1em\b)\d*\.?\d+em/, "font-size（--font-*）"],
    [/(?<![\w-])(?:row-|column-)?gap:\s*[\d.]/, "gap（--gap-*）"],
    [/box-shadow:\s*[^;]*rgba\(0, 0, 0, \.35\)/, "ポップアップの影（--shadow-popup）"],
  ];
  assert.ok(files.length > 50, `検査する CSS が少なすぎる（${files.length} 本）。置き場が動いて空振りしていないか`);
  const defined = new Set([...page.matchAll(/(--(?:z|radius|font|gap|shadow)-[a-z0-9-]+):/g)].map((m) => m[1]));
  for (const file of files) {
    if (file.endsWith("appearance.css")) continue;
    // 使っている変数は page.css の :root に定義がある（未定義だと値が無効になり、気づかないうちに既定の見た目に戻る）
    for (const used of fs.readFileSync(file, "utf8").matchAll(/var\((--(?:z|radius|font|gap|shadow)-[a-z0-9-]+)/g)) {
      assert.ok(defined.has(used[1]), `${path.basename(file)} が定義の無い ${used[1]} を使っている`);
    }
    let bare = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    if (file === PAGE_CSS) bare = bare.replace(/:root \{[^}]*\}/, "");
    for (const [pattern, what] of raw) {
      assert.doesNotMatch(bare, pattern, `${path.basename(file)} が ${what} を数値で直書きしている`);
    }
  }
});

/** WCAG の相対輝度によるコントラスト比（0〜255 の 3 つ組どうし） */
function contrastRatio(a: readonly number[], b: readonly number[]): number {
  const lum = (rgb: readonly number[]): number => {
    const c = rgb.map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function rgb(hex: string): number[] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

test("CB-T290 意味の色を文字に使うときは前景色へ混ぜ、Light Modern と Dark Modern のどの地にも 4.5:1 を超える", () => {
  const style = flatStyle(rulesOnly());
  // VS Code の既定のテーマ。地は本文・開いた行（editorWidget）・ホバーの 3 つ
  const themes: Record<string, { fg: string; bgs: string[]; colors: Record<string, string> }> = {
    light: { fg: "#3b3b3b", bgs: ["#ffffff", "#f8f8f8", "#f2f2f2"], colors: { editorWarning: "#bf8803", "charts-yellow": "#bf8803", editorInfo: "#1a85ff", "charts-green": "#388a34", editorError: "#e51400" } },
    dark: { fg: "#cccccc", bgs: ["#1f1f1f", "#202020", "#2a2d2e"], colors: { editorWarning: "#cca700", "charts-yellow": "#cca700", editorInfo: "#3794ff", "charts-green": "#89d185", editorError: "#f14c4c" } },
  };
  const defs = [...style.matchAll(/--([a-z]+-text): color-mix\(in srgb, var\(--vscode-([A-Za-z-]+?)(?:-foreground)?\) (\d+)%, var\(--vscode-foreground\)\);/g)];
  assert.deepEqual(defs.map((d) => d[1]).sort(), ["doing-text", "error-text", "info-text", "ok-text", "warn-text"]);
  for (const [, name, color, percent] of defs) {
    for (const [theme, t] of Object.entries(themes)) {
      const base = rgb(t.colors[color]);
      const fg = rgb(t.fg);
      const mixed = base.map((v, i) => (v * Number(percent) + fg[i] * (100 - Number(percent))) / 100);
      for (const bg of t.bgs) {
        const ratio = contrastRatio(mixed, rgb(bg));
        assert.ok(ratio >= 4.5, `${theme} の --${name} が ${bg} に対して ${ratio.toFixed(2)}`);
      }
    }
  }
  // ボード以外の画面の CSS は、意味の色を文字の色に直接使わない（枠・縁・記号には使ってよい）。
  // ボード（board/）と配色（appearance.css）・ボタン（共通の部品の button.css）はここでは見ない。
  // 共通の部品の CSS（骨組み・一覧）も見る。Chrome 拡張向けの変数の定義（browser-theme.css）は配色なので外す
  const skip = new Set([
    path.join(WEBVIEW_SRC, "styles", "appearance.css"),
    path.join(SHARED_STYLES, "button.css"),
    path.join(SHARED_STYLES, "browser-theme.css"),
  ]);
  const files = [...cssFiles(), ...cssFiles(SHARED_STYLES)];
  assert.ok(files.includes(path.join(SHARED_STYLES, "page.css")), `共通の部品の CSS が見つからない: ${SHARED_STYLES}`);
  for (const file of files.filter((f) => !f.startsWith(path.join(WEBVIEW_SRC, "board")) && !skip.has(f))) {
    const text = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.doesNotMatch(text, /(?:^|[\s;{])color: var\(--vscode-(?:editorWarning-foreground|editorInfo-foreground|editorError-foreground|charts-green|charts-yellow)/m, file);
  }
});

test("CB-T291 狭い幅（520px 以下）では一覧の欄を 1 列にして行の見出しを段に分け、ルール管理の表は入れ物の中で横に送る", () => {
  const rules = flatStyle(rulesOnly());
  assert.match(rules, /@media \(max-width: 520px\) \{ \.row-body \{ padding-left: 14px; grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(rules, /\.rule \.sum > \.clip:not\(\.mono\) \{ grid-row: 2; grid-column: 2 \/ -1; \}/);
  assert.match(rules, /\.table-scroll \{ overflow-x: auto; \}/);
  assert.match(rules, /td\.cmd \{ min-width: 12em; \}/);
  const risk = flatStyle(riskHtml({ kind: "page", page: riskPage() }));
  assert.match(risk, /\.factor \.sum > \.clip \{ grid-row: 2; grid-column: 2 \/ -1; \}/);
  assert.match(risk, /\.block h2 \{ display: flex; flex-wrap: wrap;/);
  const phases = flatStyle(phasesHtml({ kind: "page", page: phasesPage() }));
  assert.match(phases, /\.phase \.sum > \.clip\.mono \{ grid-row: 3; grid-column: 2 \/ -1; \}/);
  // 全体計画の先行関係の選択は外したので、その欄の規則も無い
  assert.doesNotMatch(phases, /\.order select/);
});

test("CB-T397 畳んだ部品箱と右の欄は、中身のボタンの display に負けずに隠れる", () => {
  const css = screenStyle("flow");
  assert.match(css, /\.flow-palette\[data-folded="1"\] > :not\(\.flow-fold\)[^{]*\{\s*display: none !important;?\s*\}/);
});

test("CB-T439 欄の幅の上限と基準幅は page.css で定めた名前（--w-*）を使い、画面の CSS に 320px・360px を直書きしない", () => {
  const page = fs.readFileSync(path.join(SHARED_STYLES, "page.css"), "utf8");
  assert.match(page, /:root \{ --w-field: 320px; --w-field-wide: 360px; --w-grow: 320px; \}/);
  const CHROME_SRC = path.join(SHARED_STYLES, "..", "..", "chrome", "ccnavi-approval", "src");
  const files = [...cssFiles(), ...cssFiles(CHROME_SRC), ...fs.readdirSync(SHARED_STYLES).filter((n) => n.endsWith(".css")).map((n) => path.join(SHARED_STYLES, n))];
  const loose: string[] = [];
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    // コメントは行数を保ったまま空にし、名前を定義する行も除く。width 系と flex 系の値に 320px・360px が残っていないかを見る
    const code = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, "")).split("\n").filter((l) => !l.includes("--w-field: 320px"));
    code.forEach((line, i) => {
      if (/(?<![-\w])(?:width|max-width|min-width|flex|flex-basis)\s*:[^;}]*\b(?:320|360)px/.test(line)) {
        loose.push(`${path.relative(WEBVIEW_SRC, file)}:${i + 1} ${line.trim()}`);
      }
    });
  }
  // ツアーの吹き出しの幅は画面固有なので対象外
  assert.deepEqual(loose.filter((l) => !l.startsWith("Tour.css")), []);
});
