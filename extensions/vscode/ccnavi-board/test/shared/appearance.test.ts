import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { appearanceClass, bodyTag, parseAppearance, parseZoom, sendAppearance } from "../../src/core/appearance.js";
import { retainedHost, screenHost, type Surface } from "../../src/core/screen-host.js";
import type { ToBoard } from "../../src/core/board-view.js";
import type { ToFlow } from "../../src/core/flow-view.js";
import type { ToHandoff } from "../../src/core/handoff-view.js";
import type { ToWorkflow } from "../../src/core/workflow-view.js";
import type { ToPhases } from "../../src/core/phases-view.js";
import type { ToProjects } from "../../src/core/projects-view.js";
import type { ToRisk } from "../../src/core/risk-view.js";
import type { ToRules } from "../../src/core/rules-view.js";
import type { ToTemplates } from "../../src/core/templates-view.js";
import type { AppearanceMessage } from "../../src/core/appearance.js";
import { WEBVIEW_SRC } from "../helpers/bundle.js";

/** Claude の配色そのもの。画面の側の CSS にある（拡張が持つのは body のクラスだけ） */
const APPEARANCE_STYLE = fs.readFileSync(path.join(WEBVIEW_SRC, "styles", "appearance.css"), "utf8");

/** WCAG の相対輝度によるコントラスト比 */
function contrast(a: string, b: string): number {
  const lum = (hex: string): number => {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test("CB-T128 見た目の設定は 3 つの値だけを受け、知らない値と型違いは既定（VS Code のテーマ）にする", () => {
  assert.equal(parseAppearance("claude-light"), "claude-light");
  assert.equal(parseAppearance("claude-dark"), "claude-dark");
  assert.equal(parseAppearance("vscode"), "vscode");
  assert.equal(parseAppearance("Claude-Light"), "vscode");
  assert.equal(parseAppearance(undefined), "vscode");
  assert.equal(parseAppearance(3), "vscode");
});

test("CB-T129 body のクラスは Claude の配色のときだけ付き、CSS はそのクラスの下でテーマ変数を上書きする", () => {
  assert.equal(bodyTag(undefined), "<body>");
  assert.equal(bodyTag("vscode"), "<body>");
  assert.equal(bodyTag("claude-light"), '<body class="ccnavi-claude-light">');
  assert.equal(appearanceClass("claude-dark"), "ccnavi-claude-dark");
  // HC では適用しない書き方
  assert.match(APPEARANCE_STYLE, /body\.ccnavi-claude-dark:not\(\.vscode-high-contrast\):not\(\.vscode-high-contrast-light\) \{\s*--vscode-editor-background: #262624;/);
  assert.match(APPEARANCE_STYLE, /body\.ccnavi-claude-light:not\(\.vscode-high-contrast\):not\(\.vscode-high-contrast-light\) \{\s*--vscode-editor-background: #FAF9F5;/);
  // 意味の色は両方で地に合わせて置き換える（ライトのテーマからダークを選んでも読める）
  const [dark, light] = APPEARANCE_STYLE.split("body.ccnavi-claude-light");
  for (const part of [dark, light]) {
    for (const name of ["editorWarning-foreground", "editorError-foreground", "charts-green", "charts-yellow", "descriptionForeground", "button-foreground"]) {
      assert.match(part, new RegExp(`--vscode-${name}: #[0-9A-Fa-f]{6}`), name);
    }
  }
  // 部品の色を直接書かない（テーマ変数の上書きだけ）。placeholder は使う CSS が無いので上書きしない
  assert.doesNotMatch(APPEARANCE_STYLE, /\.summary|\.badge|\.card|placeholderForeground/);
  // 文字の色は地に対して 4.5:1 以上
  const pairs: [string, string][] = [["#E8E6DF", "#262624"], ["#A8A69E", "#30302E"], ["#1F1E1D", "#D97757"], ["#E0B75A", "#262624"], ["#F2707A", "#262624"], ["#7BC275", "#262624"], ["#141413", "#FAF9F5"], ["#6B6A64", "#F0EEE6"], ["#ffffff", "#B0532F"], ["#7F5500", "#F0EEE6"], ["#B3261E", "#F0EEE6"], ["#366D24", "#F0EEE6"]];
  for (const [fg, bg] of pairs) {
    assert.ok(contrast(fg, bg) >= 4.5, `${fg} on ${bg} = ${contrast(fg, bg).toFixed(2)}`);
  }
});

/** Chrome 拡張の画面が読む配色（`extensions/shared/styles/browser-theme.css`）。appearance.css のコピー */
const BROWSER_THEME = fs.readFileSync(path.join(WEBVIEW_SRC, "..", "..", "..", "..", "shared", "styles", "browser-theme.css"), "utf8");

/** CSS の `--vscode-*` の宣言を、名前 -> 値で取る（コメントは除き、値は大文字小文字と空白の差を無視する） */
function themeVariables(css: string): Map<string, string> {
  const found = new Map<string, string>();
  for (const match of css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--vscode-[\w-]+)\s*:\s*([^;}]+)[;}]/g)) {
    found.set(match[1], match[2].trim().replace(/\s+/g, " ").toLowerCase());
  }
  return found;
}

/** 名前と値が食い違う変数の説明。空なら一致 */
function themeDiff(expected: Map<string, string>, actual: Map<string, string>, allowedExtra: ReadonlySet<string>): string[] {
  const diffs: string[] = [];
  for (const [name, value] of expected) {
    if (!actual.has(name)) {
      diffs.push(`${name} が browser-theme.css にありません`);
    } else if (actual.get(name) !== value) {
      diffs.push(`${name}: appearance.css は ${value}、browser-theme.css は ${actual.get(name)}`);
    }
  }
  for (const name of actual.keys()) {
    if (!expected.has(name) && !allowedExtra.has(name)) {
      diffs.push(`${name} が appearance.css にありません`);
    }
  }
  return diffs;
}

test("CB-T393 browser-theme.css（Chrome 拡張）の配色は appearance.css の Claude の配色と、ライト・ダークとも同じ名前・同じ値", () => {
  // browser-theme.css だけが持ってよいのは字体の 3 変数（VS Code の Webview では VS Code が渡す値）
  const fontVariables = new Set(["--vscode-font-family", "--vscode-font-size", "--vscode-editor-font-family"]);
  const [beforeDark, afterDark] = BROWSER_THEME.split("@media (prefers-color-scheme: dark)");
  assert.notEqual(afterDark, undefined, "browser-theme.css にダークの @media がありません");
  const light = themeVariables(beforeDark);
  const dark = themeVariables(afterDark);
  const [darkPart, lightPart] = APPEARANCE_STYLE.split("body.ccnavi-claude-light");
  const expectedDark = themeVariables(darkPart);
  const expectedLight = themeVariables(lightPart);
  // 比べる元が空だと、何も比べないまま通る
  assert.ok(expectedLight.size >= 30 && expectedDark.size >= 30, `appearance.css の変数が少ない: light=${expectedLight.size} dark=${expectedDark.size}`);
  assert.deepEqual(themeDiff(expectedLight, light, fontVariables), [], "ライト");
  // ダークは字体を持たない（ライトの :root だけが持つ）。持つなら appearance.css に無いものとして落とす
  assert.deepEqual(themeDiff(expectedDark, dark, new Set()), [], "ダーク");
  for (const name of fontVariables) {
    assert.ok(light.has(name), `${name} が browser-theme.css のライトにありません`);
    assert.ok(!expectedLight.has(name) && !expectedDark.has(name), `${name} は appearance.css に置かない`);
  }
});

/** 段取りの下に置く Webview の代わり。何が送られたかだけを見る */
function surface(visible = true): Surface & { visible: boolean; readonly pages: string[]; readonly posted: unknown[] } {
  const pages: string[] = [];
  const posted: unknown[] = [];
  return {
    visible,
    pages,
    posted,
    html(text) {
      pages.push(text);
    },
    post(message) {
      posted.push(message);
    },
  };
}

test("CB-T182 見た目は画面に中身を渡す段取りを通る。組み上がっていない画面と破棄された画面には送らない", () => {
  const spy = surface();
  const host = screenHost<string>(spy, (data) => data);

  // 1 枚も入れていない。入れ物を入れる経路はまだ通っていないので送り先が無い
  assert.equal(sendAppearance(host, "claude-dark"), false);
  assert.deepEqual(spy.posted, []);

  // 入れ物は入ったが、まだ組み上がっていない（受け取る側が無い）
  host.send("あ");
  assert.equal(sendAppearance(host, "claude-dark"), false);
  assert.deepEqual(spy.posted, [], "失われるものを送ると、送ったつもりの切り替えが残る");

  // 組み上がった
  host.ready();
  assert.equal(sendAppearance(host, "claude-dark"), true);
  assert.deepEqual(spy.posted, [{ type: "appearance", value: "claude-dark" }]);

  // 非表示にする。画面は破棄されているので送らない。再表示すると入れ物から作り直され、
  // 組み上がった（`ready`）ところで呼ぶ側が送り直す
  spy.visible = false;
  host.hidden();
  assert.equal(sendAppearance(host, "vscode"), false);
  assert.deepEqual(spy.posted, [{ type: "appearance", value: "claude-dark" }]);
});

test("CB-T182b 保持する画面は非表示でも送る。1 枚目を読み込んでいる間だけ送れない", () => {
  const spy = surface();
  const host = retainedHost<string>(spy, (data) => data);

  host.send("あ");
  // 入れ物を入れてから組み上がるまでは受け取る側が無い
  assert.equal(sendAppearance(host, "claude-light"), false);
  host.ready();
  assert.equal(sendAppearance(host, "claude-light"), true);

  // 保持する画面は非表示になっても破棄されない。表示の有無を見ないので送れる
  // （届くかどうかは VS Code 次第なので、再表示されたところで呼ぶ側が送り直す）
  host.hidden();
  assert.equal(sendAppearance(host, "vscode"), true);
  assert.deepEqual(spy.posted, [
    { type: "appearance", value: "claude-light" },
    { type: "appearance", value: "vscode" },
  ]);
  assert.deepEqual(spy.pages, ["あ"], "入れ直すと画面が作り直され、打ちかけの編集が飛ぶ");
});

/**
 * 見た目の配線を、ソースを読んで見張る（CB-T157 と同じ手）。
 *
 * 退行そのもの（`followAppearance` が `webview.postMessage` を直に呼ぶ、送り直しを省く）は
 * `src/appearance.ts` と 8 つのパネルで起きるが、そこは `vscode` を import するので単体では
 * 動かせない。上の 2 本（CB-T182 / CB-T182b）が見ているのは段取りの側で、配線を戻してもテストは通ったまま。
 * 名前で見るだけなので表記を変えて呼ぶ方法までは防げないが、うっかり外すことは防げる。
 */
const EXT_SRC = path.join(WEBVIEW_SRC, "..");

/**
 * コードだけを返す。コメントを外すのが肝で、外さないと「`webview.postMessage` は呼ばない」と
 * 書いた説明そのものが「呼んでいる」としてヒットする。
 */
function source(name: string): string {
  return fs
    .readFileSync(path.join(EXT_SRC, name), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

/** 保持しない画面（再表示されると作り直される）と、保持する画面（作り直されない） */
const REBUILT_PANELS = ["board-panel.ts", "projects-panel.ts"];
const RETAINED_PANELS = ["rules-panel.ts", "risk-panel.ts", "phases-panel.ts", "templates-panel.ts", "flow-panel.ts", "workflow-panel.ts", "handoff-panel.ts"];

test("CB-T183 見た目は段取りを通る。直に postMessage を呼ぶ道と、自前の送り直しを持たない", () => {
  const follow = source("appearance.ts");
  assert.doesNotMatch(follow, /webview\s*\.\s*postMessage/, "見た目が段取り（ScreenHost）を迂回している");
  assert.doesNotMatch(follow, /onDidChangeViewState/, "再表示されたときの送り直しを 2 か所で持っている");
  assert.match(follow, /export function followAppearance\(panel: vscode\.WebviewPanel, host: AppearanceSink\)/);
});

test("CB-T183b 9 画面とも段取りに繋ぎ、組み上がったところで送り直す", () => {
  for (const name of [...REBUILT_PANELS, ...RETAINED_PANELS]) {
    const text = source(name);
    assert.match(text, /followAppearance\(panel, current\.host\)/, `${name} が段取りに繋いでいない`);
    // `ready` を受けたところ。入れてある HTML の body のクラスは古いことがある
    assert.match(text, /current\.host\.ready\(\);[\s\S]{0,600}?postAppearance\(current\.host\)/, `${name} の ready で送り直していない`);
  }
});

test("CB-T183c 保持する画面は、再表示されたところでも送り直す（作り直されないので ready が来ない）", () => {
  for (const name of RETAINED_PANELS) {
    const raw = source(name);
    // ルール・リスク・フェーズの再表示の処理は、3 画面が共通で使う段取り（settings-panel.ts）にある
    const text = /settingsPanel</.test(raw) ? source("settings-panel.ts") : raw;
    const found = /panel\.onDidChangeViewState\(\(\) => \{([\s\S]*?)\n\s*\}\);/.exec(text);
    assert.ok(found !== null, `${name} に再表示されたときの送り直しがありません`);
    assert.match(found[1], /postAppearance\(current\.host\)/, `${name} が再表示されても見た目を送り直していない`);
  }
  for (const name of REBUILT_PANELS) {
    // 保持しない画面は作り直されるので、ここで送ると「破棄された画面へ送る」になる
    assert.doesNotMatch(source(name), /onDidChangeViewState\([\s\S]{0,400}?postAppearance/, `${name} は ready で足りる`);
  }
});

/**
 * 見た目のメッセージが、9 画面すべての契約（`To*`）に入っていること。tsc が確かめる。
 *
 * 古いコードは呼び出しのたびに `{ type: "appearance", value } satisfies ToBoard` と書いていて、
 * その画面の契約に入っていることをコンパイラが確かめていた。`postAppearance(host)` にそろえたときに
 * その検査が消えた（`ScreenHost<D>.post` は `unknown` を取るので、契約から外しても通ってしまう）。
 * ここで 1 か所にまとめて確かめ直す。画面を足す人は、この配列に 1 行足せば同じ検査が有効になる。
 */
const APPEARANCE: AppearanceMessage = { type: "appearance", value: "claude-dark" };
const IN_EVERY_CONTRACT: readonly [ToBoard, ToProjects, ToRisk, ToRules, ToPhases, ToTemplates, ToFlow, ToWorkflow, ToHandoff] = [
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
  APPEARANCE,
];

test("CB-T184 見た目のメッセージは 9 画面すべての契約に入っている（外すとコンパイルが通らない）", () => {
  assert.equal(IN_EVERY_CONTRACT.length, 9);
  for (const message of IN_EVERY_CONTRACT) {
    assert.deepEqual(message, { type: "appearance", value: "claude-dark" });
  }
});

test("CB-T1243 拡大率の設定は 0.8〜2 に収め、数でない値は等倍にする。等倍以外のときだけメッセージに乗る", () => {
  assert.equal(parseZoom(1.5), 1.5);
  assert.equal(parseZoom(5), 2);
  assert.equal(parseZoom(0.1), 0.8);
  assert.equal(parseZoom("1.5"), 1);
  assert.equal(parseZoom(undefined), 1);
  assert.equal(parseZoom(Number.NaN), 1);
  assert.equal(parseZoom(Number.POSITIVE_INFINITY), 1);
  assert.equal(parseZoom(0.8), 0.8);
  assert.equal(parseZoom(2), 2);
  const sent: unknown[] = [];
  sendAppearance({ post: (m) => (sent.push(m), true) }, "vscode", 1.5);
  sendAppearance({ post: (m) => (sent.push(m), true) }, "vscode");
  assert.deepEqual(sent, [{ type: "appearance", value: "vscode", zoom: 1.5 }, { type: "appearance", value: "vscode" }]);
});
