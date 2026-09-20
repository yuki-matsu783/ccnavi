/**
 * 層まわりの描画のうち、文字列で組む画面のぶん。プロジェクト管理画面は React に移したので、
 * 同じ確かめは `projects.dom.test.ts`（CB-T113 / CB-T123 / CB-T133）が DOM で見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRulesPage } from "../../src/core/rules-render.js";

test("CB-T112 ルール設定画面は注意を上部に実体参照で出し、無ければ出さない", () => {
  const base = {
    root: "/ws",
    rulesPath: "projects/lib/.ccnavi/config/rules.yml",
    mode: "enable",
    model: readRules("deny: []\n").model,
    hooks: [],
    hookFiles: { settings: true, settingsLocal: false },
    samplesPath: ".ccnavi/common/rule-samples.yml",
    lock: { locked: false, reason: "", doing: [] },
  };
  const html = renderRulesPage({ ...base, notices: ["実行ファイルはこのファイルを読めない: <理由>"] }, { nonce: "n" });
  assert.match(html, /<div class="banner warn">実行ファイルはこのファイルを読めない: &lt;理由&gt;<\/div>/);
  // 「外で変わった」の帯（hidden 付き）は常にあるので、注意の帯だけを見る
  assert.doesNotMatch(renderRulesPage(base, { nonce: "n" }), /<div class="banner warn">/);
});
