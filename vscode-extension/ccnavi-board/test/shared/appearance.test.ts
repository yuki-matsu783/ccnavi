import { test } from "node:test";
import assert from "node:assert/strict";
import { APPEARANCE_STYLE, appearanceClass, bodyTag, parseAppearance } from "../../src/core/appearance.js";

/** WCAG の相対輝度によるコントラスト比 */
function contrast(a: string, b: string): number {
  const lum = (hex: string): number => {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test("CB-T128 見た目の設定は 3 つの値だけを受け、知らない値と型違いは既定（VS Code のテーマ）に落とす", () => {
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
  // HC では効かせない書き方
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
