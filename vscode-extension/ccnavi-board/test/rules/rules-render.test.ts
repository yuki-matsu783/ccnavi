import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHooks } from "../../src/core/hooks.js";
import { readRules } from "../../src/core/rules-doc.js";
import { KNOWN_TOOLS, renderRulesPage, type RulesPage } from "../../src/core/rules-render.js";

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
    rulesPath: ".ccnavi/common/rules.yml",
    mode: "dry-run",
    model: readRules(RULES).model,
    hooks: parseHooks(
      JSON.stringify({ hooks: { PreToolUse: [{ matcher: "", hooks: [{ command: "ccnavi <exe>", timeout: 10 }] }] } }),
      "settings",
    ),
    hookFiles: { settings: true, settingsLocal: false },
    samplesPath: ".ccnavi/common/rule-samples.yml",
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

test("CB-T50 dry-run のときは止めないことを言い、enable と未設定（実行ファイルは enable と扱う）なら言わない", () => {
  assert.match(renderRulesPage(page(), { nonce: "n" }), /CCNAVI_MODE<\/code>: <strong>dry-run<\/strong>/);
  assert.doesNotMatch(renderRulesPage(page({ mode: "" }), { nonce: "n" }), /CCNAVI_MODE/);
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

test("CB-T69 タイプごとに畳むボタンを出す", () => {
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

test("CB-T120 一覧は 1 件 1 行で既定は畳み、絞り込み欄を持ち、開いた行を id で state に控える", () => {
  const html = renderRulesPage(page(), { nonce: "n" });
  assert.match(html, /<input id="find" type="search"/);
  assert.match(html, /<ul class="list" data-list="deny"><\/ul>/);
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  // 行の見出し（要約）と本体。開いた行だけ本体が出る。
  assert.match(body, /class: "row-head"/);
  assert.match(body, /class: "row-body"/);
  assert.match(body, /\.row\.open \.row-body|classList\.toggle\("open", on\)/);
  // コンテキストの 4 欄は details に畳み、値があるときだけ open。
  assert.match(body, /h\("details", \{ class: "more" \}/);
  assert.match(body, /if \(moreOpen\.has\(key\) \? moreOpen\.get\(key\) : hasContext\(rule\)\) \{ more\.setAttribute\("open", ""\); \}/);
  // 判定で当たった行の展開は控えに入れない。控えを書くのは利用者が押したときだけ。
  assert.match(body, /function unfoldRule\(el\) \{[\s\S]*?setOpen\(el, true, false\);/);
  assert.match(body, /setOpen\(li, opened\.has\(key\), false\)/);
  // 開いている行は絞り込みで隠さない。絞り込み中は畳んだタイプの中身も見せる
  assert.match(html, /\.row\.hidden-by-find:not\(\.open\) \{ display: none; \}/);
  assert.match(html, /\.finding \.rule-section\.folded \.list \{ display: block; \}/);
  assert.doesNotMatch(html, /\.list \{[^}]*overflow: hidden/);
  // 開いた行は id で控える（空 id は控えない）。
  assert.match(body, /if \(found && found\.rule\.id !== ""\) \{ ids\.push\(found\.rule\.id\); \}/);
  assert.match(body, /savedOpen\.has\(rule\.id\)/);
  // タブの控えが開いた行の控えを消さない。
  assert.doesNotMatch(body, /vscode\.setState\(\{ tab: name \}\)/);
  assert.match(html, /\.row\.open \.row-body \{ display: grid; \}/);
  // 札で選んだあとは要約を今の値で書き直す（チェックボックスの input は change より先に伝わるため）
  assert.match(body, /markDirty\(\);\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*wrap\.dispatchEvent\(new Event\("input", \{ bubbles: true \}\)\);/);
  // 絞り込み中は畳んだタイプの矢印も開いた向きにする
  assert.match(body, /function syncTwist\(el\) \{[\s\S]*?const shownAsOpen = finding \|\| !el\.classList\.contains\("folded"\);/);
});

test("CB-T124 欄名は日本語で、YAML のキー名は欄名の title に載せる", () => {
  const html = renderRulesPage(page(), { nonce: "n" });
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  assert.match(body, /if \(key\) \{ cap\.setAttribute\("title", "YAML のキー: " \+ key\); \}/);
  for (const [label, key] of [["ツール", "match"], ["文面", "message"], ["渡す文", "additionalContext"]]) {
    assert.match(body, new RegExp(`captioned\\("${label}", [^\\n]*"${key}"\\)`), label);
  }
  assert.match(body, /fileField\(rule, key, "初回だけ渡すファイル", "additionalContextOnceFile"/);
  assert.match(html, /title="--test の subject">対象 /);
});
