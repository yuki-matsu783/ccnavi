import { test } from "node:test";
import assert from "node:assert/strict";
import { APPEARANCE_SCRIPT, APPEARANCE_STYLE, appearanceClass, bodyTag, parseAppearance } from "../../src/core/appearance.js";

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
  assert.match(APPEARANCE_STYLE, /body\.ccnavi-claude-dark \{\s*--vscode-editor-background: #262624;/);
  assert.match(APPEARANCE_STYLE, /body\.ccnavi-claude-light \{\s*--vscode-editor-background: #FAF9F5;/);
  // 意味の色（error / warning）はダークでは触らない
  assert.doesNotMatch(APPEARANCE_STYLE.split("body.ccnavi-claude-light")[0], /editorError-foreground/);
  // 画面のスクリプトはテンプレートに埋めるので、バッククォートと ${ を含まない
  assert.doesNotMatch(APPEARANCE_SCRIPT, /`|\$\{/);
  assert.doesNotThrow(() => new Function(APPEARANCE_SCRIPT));
});
