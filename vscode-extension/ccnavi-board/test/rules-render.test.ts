import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHooks } from "../src/core/hooks.js";
import { readRules } from "../src/core/rules-doc.js";
import { KNOWN_TOOLS, renderRulesPage, type RulesPage } from "../src/core/rules-render.js";

const RULES = `version: 3
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: "<b>push</b> は人が行う"
allow: []
`;

function page(overrides: Partial<RulesPage> = {}): RulesPage {
  return {
    root: "/ws",
    rulesPath: ".claude/ccnavi/rules.yml",
    mode: "dry-run",
    model: readRules(RULES).model,
    hooks: parseHooks(
      JSON.stringify({ hooks: { PreToolUse: [{ matcher: "", hooks: [{ command: "ccnavi <exe>", timeout: 10 }] }] } }),
      "settings",
    ),
    hookFiles: { settings: true, settingsLocal: false },
    samplesPath: "testdata/rule-samples.yml",
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

test("CB-T48 ルール設定画面は外部資源を読まず、nonce で自分のスタイルとスクリプトだけを許す", () => {
  const html = renderRulesPage(page(), { nonce: "N0NCE" });
  assert.match(html, /default-src 'none'/);
  assert.match(html, /style-src 'nonce-N0NCE'; script-src 'nonce-N0NCE'/);
  assert.doesNotMatch(html, /https?:\/\//);
  assert.match(html, /<script nonce="N0NCE" type="application\/json" id="page">/);
});

test("CB-T49 埋め込むルールは JSON で、文面の < は実体にして script を閉じさせない", () => {
  const html = renderRulesPage(page(), { nonce: "n" });
  assert.match(html, /\\u003cb>push\\u003c\/b>/);
  assert.doesNotMatch(html, /<b>push<\/b>/);
  // hook のコマンドも実体参照
  assert.match(html, /ccnavi &lt;exe&gt;/);
});

test("CB-T50 dry-run のときは enable でないことを言い、enable なら言わない", () => {
  assert.match(renderRulesPage(page(), { nonce: "n" }), /CCNAVI_MODE<\/code>: <strong>dry-run<\/strong>/);
  assert.match(renderRulesPage(page({ mode: "" }), { nonce: "n" }), /<strong>未設定<\/strong>/);
  assert.doesNotMatch(renderRulesPage(page({ mode: "enable" }), { nonce: "n" }), /CCNAVI_MODE/);
});

test("CB-T51 保存できない理由と読み込みの苦情を出す", () => {
  const locked = renderRulesPage(
    page({ lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] } }),
    { nonce: "n" },
  );
  assert.match(locked, /<p id="lock" class="lock">作業中のチケットがある（i0001-02）<\/p>/);
  const open = renderRulesPage(page(), { nonce: "n" });
  assert.match(open, /<p id="lock" class="lock hidden"><\/p>/);
  const broken = renderRulesPage(page({ model: readRules("version: 3\ndeny: nope\n").model }), { nonce: "n" });
  assert.match(broken, /<ul class="problems">/);
});

test("CB-T52 settings.json が無ければ hook の表にそう書く", () => {
  const html = renderRulesPage(page({ hooks: [], hookFiles: { settings: false, settingsLocal: false } }), { nonce: "n" });
  assert.match(html, /settings\.json が無い/);
});

test("CB-T69 タイプごとに畳む印を出す", () => {
  const html = renderRulesPage(page(), { nonce: "n" });
  for (const section of ["deny", "ask", "allow"]) {
    assert.match(html, new RegExp(`data-action="fold-section" data-section="${section}"`));
  }
});

test("CB-T71 match の候補と判定の試し打ちは、権限ルールの名前（括弧の中を除いたもの）で並ぶ", () => {
  // 判定が対象を取り出せるツールだけ。WebSearch は取り出せないので載せない。
  assert.deepEqual([...KNOWN_TOOLS], [
    "Bash", "PowerShell", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Skill", "Agent", "WebFetch",
  ]);
  const html = renderRulesPage(page(), { nonce: "n" });
  for (const tool of ["PowerShell", "Grep", "Glob", "Skill", "WebFetch"]) {
    assert.match(html, new RegExp(`<option value="${tool}">${tool}</option>`));
  }
  assert.doesNotMatch(html, /<option value="WebSearch">/);
});

// 画面の中のスクリプトは文字列なので、tsc は見ない。壊れても画面が黙って動かなくなるだけ。
test("CB-T70 画面に埋める script は構文として通る", () => {
  const html = renderRulesPage(page(), { nonce: "n" });
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  assert.doesNotThrow(() => new Function(body));
});
