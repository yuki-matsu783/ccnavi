/**
 * ルール設定画面を外部資源に依存しない 1 枚の HTML に組み立てる。
 *
 * ボードと違い、ここは編集の途中の状態を持つ。だから HTML を作り直して差し替えるのは
 * 開いたときと再読込のときだけで、判定の結果や保存の可否は Webview の中のスクリプトが
 * メッセージを受けて DOM を書き換える。ルールの一覧も、埋め込んだ JSON からスクリプトが
 * 組み立てる（足す・消す・移すを DOM の作り直しで済ませるため）。
 *
 * 色は VS Code のテーマ変数だけを使う。文字列は全部実体参照にする。
 */
import type { HookEntry } from "./hooks.js";
import type { Lock } from "./lock.js";
import { escapeHtml } from "./render.js";
import type { RulesModel } from "./rules-doc.js";

/** 判定が対象を取り出せるツール。ccnavi の diagnose.KNOWN_TOOLS と同じ並び */
export const KNOWN_TOOLS = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Agent"] as const;

export interface RulesPage {
  readonly root: string;
  /** ルールファイル（ワークスペースルートからの相対で見せる） */
  readonly rulesPath: string;
  /** `.claude/settings.json` の env.CCNAVI_MODE。空なら未設定 */
  readonly mode: string;
  readonly model: RulesModel;
  readonly hooks: readonly HookEntry[];
  /** 読めた設定ファイル。無いものは一覧に「無い」と出す */
  readonly hookFiles: { readonly settings: boolean; readonly settingsLocal: boolean };
  readonly samplesPath: string;
  readonly lock: Lock;
}

export interface RenderOptions {
  readonly nonce: string;
}

export function renderRulesPage(page: RulesPage, options: RenderOptions): string {
  const { nonce } = options;
  const embedded = JSON.stringify({
    sections: page.model.sections,
    lock: page.lock,
    tools: KNOWN_TOOLS,
    samplesPath: page.samplesPath,
  }).replace(/</g, "\\u003c");
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi ルール設定</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
<body>
${renderModeBanner(page.mode)}<div id="changed" class="banner warn hidden">ファイルが外で変わった。画面の内容は古い。<button type="button" class="action" data-action="reload">再読込</button></div>
<header class="toolbar">
  <div class="summary">
    <span class="path" title="${escapeHtml(page.root)}">${escapeHtml(page.rulesPath)}</span>
    <span id="dirty" class="dirty hidden">未保存</span>
  </div>
  <div class="controls">
    <button type="button" class="action" data-action="open-rules">エディタで開く</button>
    <button type="button" class="action" data-action="reload">再読込</button>
    <button type="button" class="action primary" id="save" data-action="save" disabled>保存</button>
  </div>
</header>
<p id="lock" class="lock${page.lock.locked ? "" : " hidden"}">${escapeHtml(page.lock.reason)}</p>
${renderProblems(page.model.problems)}<nav class="tabs" role="tablist">
  <button type="button" class="tab active" data-tab="rules" role="tab">ルール</button>
  <button type="button" class="tab" data-tab="judge" role="tab">判定を試す</button>
  <button type="button" class="tab" data-tab="hooks" role="tab">hook</button>
</nav>
<section id="tab-rules" class="pane active">
  <p class="hint">判定は強い順に deny &gt; ask &gt; allow</p>
${(["deny", "ask", "allow"] as const).map(renderSectionShell).join("\n")}
</section>
<section id="tab-judge" class="pane">
  <p class="hint">判定は実行ファイルの <code>--test</code> を通る。編集中の内容で試すので、保存していなくてもよい。走っているセッションが dry-run でも、ここは enable の答えを返す。</p>
  <div class="judge-form">
    <label>ツール <select id="tool">${KNOWN_TOOLS.map((t) => `<option value="${t}">${t}</option>`).join("")}</select></label>
    <label class="grow">subject <input id="subject" type="text" placeholder="Bash ならコマンド、それ以外なら絶対パス" spellcheck="false"></label>
    <button type="button" class="action primary" data-action="judge">判定</button>
  </div>
  <div id="judge-result" class="result hidden"></div>
  <div class="samples-head">
    <button type="button" class="action" data-action="samples">見本を一括で流す</button>
    <span class="path">${escapeHtml(page.samplesPath)}</span>
    <button type="button" class="action" data-action="open-samples">エディタで開く</button>
  </div>
  <div id="samples-result" class="result hidden"></div>
</section>
<section id="tab-hooks" class="pane">
  <p class="hint">読むだけで書き換えない。載るのは <code>.claude/settings.json</code>${page.hookFiles.settingsLocal ? " と <code>.claude/settings.local.json</code>" : ""} の hooks。利用者ごとの設定（<code>~/.claude/settings.json</code>）は見ないのでここには載らない。</p>
${renderHooks(page.hooks, page.hookFiles)}
</section>
<footer class="foot"><span id="status"></span></footer>
<script nonce="${nonce}" type="application/json" id="page">${embedded}</script>
<script nonce="${nonce}">
${SCRIPT}
</script>
</body>
</html>
`;
}

function renderModeBanner(mode: string): string {
  if (mode === "enable") {
    return "";
  }
  const shown = mode === "" ? "未設定" : mode;
  return `<div class="banner warn">現在の <code>CCNAVI_MODE</code>: <strong>${escapeHtml(shown)}</strong>。deny, ask 判定に HIT しても tool_use は停止しない</div>\n`;
}

function renderProblems(problems: readonly string[]): string {
  if (problems.length === 0) {
    return "";
  }
  const items = problems.map((p) => `  <li>${escapeHtml(p)}</li>`).join("\n");
  return `<ul class="problems">\n${items}\n</ul>\n`;
}

const SECTION_LABELS = {
  deny: "止める",
  ask: "人に確認する",
  allow: "通す",
} as const;

function renderSectionShell(section: "deny" | "ask" | "allow"): string {
  return `  <section class="rule-section" data-section="${section}">
    <h2><button type="button" class="twist" data-action="fold-section" data-section="${section}" aria-expanded="true" title="このタイプを畳む／開く">▾</button> <span class="section-name ${section}">${section}</span> <span class="section-label">${SECTION_LABELS[section]}</span> <span class="count" data-count="${section}">0</span>
      <button type="button" class="action small" data-action="add" data-section="${section}">＋ ルールを足す</button></h2>
    <ul class="rules" data-list="${section}"></ul>
  </section>`;
}

function renderHooks(
  hooks: readonly HookEntry[],
  files: { readonly settings: boolean; readonly settingsLocal: boolean },
): string {
  if (!files.settings) {
    return `  <p class="empty">.claude/settings.json が無い</p>`;
  }
  if (hooks.length === 0) {
    return `  <p class="empty">hooks が 1 つも登録されていない</p>`;
  }
  const rows = hooks
    .map(
      (h) => `      <tr>
        <td>${escapeHtml(h.event)}</td>
        <td><code>${escapeHtml(h.matcher === "" ? "（全部）" : h.matcher)}</code></td>
        <td class="cmd"><code>${escapeHtml(h.command)}</code></td>
        <td class="num">${h.timeout === null ? "" : String(h.timeout)}</td>
        <td>${h.source === "settings" ? "settings.json" : "settings.local.json"}</td>
      </tr>`,
    )
    .join("\n");
  return `  <table class="hooks">
    <thead><tr><th>イベント</th><th>matcher</th><th>コマンド</th><th>timeout</th><th>出所</th></tr></thead>
    <tbody>
${rows}
    </tbody>
  </table>
  <p class="hint">「判定を試す」でツール名を入れると、そのツールで走る hook をここから絞って出す。matcher の意味は Claude Code のもの（空か <code>*</code> で全部、それ以外はツール名への正規表現）。</p>`;
}

const STYLE = `  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  code { font-family: var(--vscode-editor-font-family); font-size: .95em; }
  .hidden { display: none !important; }
  .banner {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-panel-border);
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  }
  .banner.warn { border-color: var(--vscode-editorWarning-foreground); color: var(--vscode-editorWarning-foreground); }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 0 4px 8px; }
  .summary { display: flex; gap: 12px; align-items: center; }
  .path { color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }
  .dirty { color: var(--vscode-editorWarning-foreground); font-weight: 600; }
  .controls { display: flex; gap: 8px; align-items: center; margin-left: auto; }
  .lock {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground);
  }
  .problems {
    margin: 0 0 12px; padding: 8px 8px 8px 24px;
    border: 1px solid var(--vscode-editorWarning-foreground); border-radius: 4px;
    color: var(--vscode-editorWarning-foreground);
  }
  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--vscode-panel-border); margin-bottom: 10px; }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent; color: var(--vscode-descriptionForeground);
    padding: 6px 12px; cursor: pointer; font: inherit;
  }
  .tab.active { color: var(--vscode-editor-foreground); border-bottom-color: var(--vscode-focusBorder); }
  .pane { display: none; }
  .pane.active { display: block; }
  .hint { margin: 0 0 10px; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .empty { color: var(--vscode-descriptionForeground); }
  button.action {
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: none; border-radius: 2px; padding: 3px 10px; cursor: pointer; font: inherit;
  }
  button.action:hover { background: var(--vscode-button-secondaryHoverBackground); }
  button.action.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  button.action.primary:hover { background: var(--vscode-button-hoverBackground); }
  button.action:disabled { opacity: .5; cursor: default; }
  button.action.small { padding: 1px 8px; font-size: .9em; margin-left: auto; }
  input[type=text], textarea, select {
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 2px;
    padding: 3px 6px; font: inherit;
  }
  input[type=text]:focus, textarea:focus, select:focus { outline: 1px solid var(--vscode-focusBorder); }
  select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border-color: var(--vscode-dropdown-border); }
  .rule-section { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .rule-section h2 { margin: 0 0 8px; font-size: 1em; display: flex; gap: 8px; align-items: center; }
  .section-name { font-weight: 700; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; }
  .section-name.deny { color: var(--vscode-editorError-foreground); }
  .section-name.ask { color: var(--vscode-editorWarning-foreground); }
  .section-name.allow { color: var(--vscode-charts-green); }
  .section-label, .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .rules { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .rule {
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background);
  }
  .rule.hit { outline: 2px solid var(--vscode-focusBorder); }
  /* 畳む印。タイプ（区画）と 1 件ずつのルールの両方に付く。 */
  .twist {
    background: none; border: none; color: var(--vscode-descriptionForeground);
    font: inherit; padding: 0 2px; cursor: pointer; line-height: 1;
  }
  .rule-section.folded .rules { display: none; }
  .rule-head { display: flex; gap: 8px; align-items: center; }
  .rule-head .buttons { margin-left: auto; display: flex; gap: 4px; }
  /* 開いているときは欄がそのまま出るので、要約は畳んだときだけ出す。 */
  .rule-sum { display: none; }
  .rule.folded .rule-sum { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; cursor: pointer; overflow-wrap: anywhere; }
  .rule.folded .rule-body { display: none; }
  .rule-sum .sum-id { font-weight: 600; }
  .rule-sum .sum-match, .rule-sum .sum-empty { color: var(--vscode-descriptionForeground); }
  .rule-sum code { font-family: var(--vscode-editor-font-family); }
  .rule-row { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: flex-end; margin-bottom: 6px; }
  .rule-row label { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
  .rule-row .grow { flex: 1 1 240px; }
  .rule-row .grow input { flex: 1; min-width: 120px; }
  .rule-row input.f-id { width: 160px; }
  .rule-row input.f-match { width: 220px; }
  .rule-row .buttons { margin-left: auto; display: flex; gap: 4px; }
  .rule textarea { width: 100%; min-height: 2.6em; resize: vertical; font-family: inherit; margin-bottom: 4px; }
  /* 欄名は欄の上に小さく常に出す。placeholder は説明で、入れると消えてよい。 */
  .field { display: flex; flex-direction: column; gap: 1px; }
  .field > .cap { font-size: .78em; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); }
  .field > input, .field > textarea { width: 100%; box-sizing: border-box; margin: 0; }
  .rule-row .field.w-id { width: 170px; }
  .rule-row .field.w-match { width: 230px; }
  .rule .field.block { margin-bottom: 6px; }
  /* 押すと札が出る欄。欄そのものは普通のテキスト入力で、手でも書ける。 */
  .picker { position: relative; }
  .picker > input { width: 100%; box-sizing: border-box; }
  .picker > .menu { display: none; }
  .picker.open > .menu {
    display: flex; flex-direction: column; gap: 2px; position: absolute; z-index: 5; top: 100%; left: 0; min-width: 100%;
    background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 3px; padding: 5px 7px; box-shadow: 0 2px 8px var(--vscode-widget-shadow, transparent);
  }
  .picker .tool { display: flex; gap: 5px; align-items: center; white-space: nowrap; cursor: pointer; }
  .picker .tool input { margin: 0; }
  .with-button { display: flex; gap: 6px; align-items: center; }
  .with-button > input { flex: 1; min-width: 120px; }
  .with-button > button { margin-left: 0; white-space: nowrap; }
  .rule .stale { margin: 0 0 6px; font-size: .9em; color: var(--vscode-editorError-foreground); overflow-wrap: anywhere; }
  .rule .stale code { margin: 0 6px; }
  .rule .pattern { font-family: var(--vscode-editor-font-family); }
  .judge-form { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; margin-bottom: 10px; }
  .judge-form label { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
  .judge-form .grow { flex: 1 1 320px; }
  .judge-form .grow input { flex: 1; }
  .result { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; }
  .verdict { font-weight: 700; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; }
  .verdict.deny { color: var(--vscode-editorError-foreground); }
  .verdict.ask { color: var(--vscode-editorWarning-foreground); }
  .verdict.allow { color: var(--vscode-charts-green); }
  .verdict.skip, .verdict.none { color: var(--vscode-descriptionForeground); }
  .kv { margin: 4px 0; display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; }
  .kv dt { color: var(--vscode-descriptionForeground); }
  .kv dd { margin: 0; overflow-wrap: anywhere; }
  pre.response { margin: 4px 0 0; padding: 6px 8px; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--vscode-textCodeBlock-background); border-radius: 4px; }
  table { border-collapse: collapse; width: 100%; font-size: .95em; }
  th, td { text-align: left; padding: 3px 8px; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  th { color: var(--vscode-descriptionForeground); font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.cmd { overflow-wrap: anywhere; }
  tr.ng td { color: var(--vscode-editorError-foreground); }
  tr.skipped td { color: var(--vscode-editorWarning-foreground); }
  .samples-head { display: flex; gap: 10px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
  .counts { display: flex; gap: 14px; margin-bottom: 6px; }
  .counts .ng { color: var(--vscode-editorError-foreground); font-weight: 600; }
  .foot { margin-top: 12px; font-size: .85em; color: var(--vscode-descriptionForeground); min-height: 1.2em; }
  .foot.error { color: var(--vscode-editorError-foreground); }`;

// 中のスクリプトはテンプレート文字列に埋めるので、バッククォートと \${ を使わない。
const SCRIPT = `  const vscode = acquireVsCodeApi();
  const page = JSON.parse(document.getElementById("page").textContent);
  const SECTIONS = ["deny", "ask", "allow"];
  let sections = page.sections;
  let lock = page.lock;
  let dirty = false;
  let busy = false;
  let seq = 0;
  const keys = new Map();
  // 畳んであるルールの鍵。鍵はルールごとなので、並べ替えても移しても畳んだままになる。
  const folded = new Set();

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    for (const name in attrs || {}) {
      if (name === "text") { el.textContent = attrs[name]; }
      else if (name === "class") { el.className = attrs[name]; }
      else { el.setAttribute(name, attrs[name]); }
    }
    for (const child of children || []) { if (child) { el.appendChild(child); } }
    return el;
  }
  function keyOf(rule) {
    if (!keys.has(rule)) { seq += 1; keys.set(rule, "r" + seq); }
    return keys.get(rule);
  }
  function ruleByKey(key) {
    for (const section of SECTIONS) {
      for (const rule of sections[section]) { if (keyOf(rule) === key) { return { section: section, rule: rule }; } }
    }
    return undefined;
  }
  function option(value, label, selected) {
    const el = h("option", { value: value, text: label });
    if (selected) { el.selected = true; }
    return el;
  }
  function field(rule, name, className, placeholder, width) {
    const input = h("input", { type: "text", class: className, spellcheck: "false", placeholder: placeholder || "" });
    input.value = rule[name];
    input.addEventListener("input", () => { rule[name] = input.value; markDirty(); });
    return input;
  }
  function area(rule, name, className, placeholder) {
    const textarea = h("textarea", { class: className, placeholder: placeholder });
    textarea.value = rule[name];
    textarea.addEventListener("input", () => { rule[name] = textarea.value; markDirty(); });
    return textarea;
  }
  // 欄名を欄の上に小さく出す。placeholder は説明なので、入れると消えてよい。
  function captioned(name, control, className) {
    return h("div", { class: "field " + (className || "") }, [h("span", { class: "cap", text: name }), control]);
  }
  // match はツール名を "|" で並べたもの。判定は名前をそのまま突き合わせるので、
  // 打ち間違えると黙って当たらなくなる。書かせずに選ばせる。ファイルに書いてある
  // 知らない名前（MCP のツールなど）も、消さずにそのまま札にして出す。
  function matchField(rule) {
    const input = field(rule, "match", "f-match", "Bash / Write|Edit");
    const menu = h("div", { class: "menu" });
    const boxes = [];
    const names = [];
    function tokens() {
      return rule.match.split("|").map((s) => s.trim()).filter((s) => s !== "");
    }
    // 札は「判定が対象を取り出せるツール」＋「いま欄に書いてある知らない名前」。
    // 知らない名前も並べるのは、開いただけで消えたように見えないようにするため。
    function fill() {
      const chosen = tokens();
      names.length = 0;
      boxes.length = 0;
      menu.textContent = "";
      for (const name of page.tools.concat(chosen.filter((t) => page.tools.indexOf(t) < 0))) {
        const box = h("input", { type: "checkbox", value: name });
        box.checked = chosen.indexOf(name) >= 0;
        box.addEventListener("change", () => {
          rule.match = names.filter((n, i) => boxes[i].checked).join("|");
          input.value = rule.match;
          markDirty();
        });
        names.push(name);
        boxes.push(box);
        menu.appendChild(h("label", { class: "tool" }, [box, document.createTextNode(name)]));
      }
    }
    // 手で書いた結果もチェックに映す。開くたびに作り直すので、書いた名前がそのまま札になる。
    input.addEventListener("input", () => { if (wrap.classList.contains("open")) { fill(); } });
    const wrap = h("div", { class: "picker" }, [input, menu]);
    input.addEventListener("focus", () => { fill(); openPicker(wrap); });
    input.addEventListener("click", () => { fill(); openPicker(wrap); });
    return captioned("match", wrap, "w-match");
  }
  // 開いているポップアップは 1 つだけ。外を押すか Esc で閉じる。
  function openPicker(wrap) {
    for (const other of document.querySelectorAll(".picker.open")) {
      if (other !== wrap) { other.classList.remove("open"); }
    }
    wrap.classList.add("open");
  }
  // ファイルを指す欄。手で書くほかに、VS Code のダイアログで選べる。選んだ結果は
  // 拡張側がルート相対にして "picked" で返す。
  function fileField(rule, key, name, className, placeholder) {
    const input = field(rule, name, className, placeholder);
    const pick = h("button", { type: "button", class: "action small", text: "選ぶ…", title: "ファイルを選ぶ" });
    pick.addEventListener("click", () => vscode.postMessage({ type: "pickFile", key: key, field: name }));
    return captioned(name, h("div", { class: "with-button" }, [input, pick]), "block");
  }
  function renderRule(section, rule) {
    const key = keyOf(rule);
    const sectionSelect = h("select", { class: "f-section" }, SECTIONS.map((s) => option(s, s, s === section)));
    sectionSelect.addEventListener("change", () => moveTo(key, sectionSelect.value));
    const kindSelect = h("select", { class: "f-kind" }, [option("glob", "glob", rule.kind === "glob"), option("regex", "regex", rule.kind === "regex")]);
    kindSelect.addEventListener("change", () => { rule.kind = kindSelect.value; markDirty(); renderAll(); });
    const pattern = field(rule, "pattern", "f-pattern pattern", rule.kind === "glob" ? "*git push*" : "\\\\bgit push\\\\b");
    // message は deny だけの欄。ask と allow には欄を出さない。文面が残っていれば
    // （lint が止めるので）そう言って、消すボタンだけ出す。
    let message;
    if (section === "deny") {
      message = captioned("message", area(rule, "message", "f-message", "なぜ止めるかと、代わりに何をすればよいか（止められたモデルに届く）"), "block");
    } else if (rule.message !== "") {
      const drop = h("button", { type: "button", class: "action small", text: "message を消す" });
      drop.addEventListener("click", () => { rule.message = ""; markDirty(); renderAll(); });
      message = h("p", { class: "stale" }, [
        document.createTextNode(section + " の message は" + (section === "ask" ? "人の確認ダイアログにしか出ない" : "どこにも届かない") + "ので lint が止める。モデルに渡す文は additionalContext に移す: "),
        h("code", { text: rule.message }),
        drop,
      ]);
    }
    const context = captioned("additionalContext", area(rule, "additionalContext", "f-context", "HIT したときにコンテキストに追加するプロンプト"), "block");
    const once = captioned("additionalContextOnce", area(rule, "additionalContextOnce", "f-once", "セッションで最初に HIT したときにコンテキストに追加するプロンプト"), "block");
    const contextFile = fileField(rule, key, "additionalContextFile", "f-context-file", "HIT したときにコンテキストに追加するファイル（先頭 4000 文字まで）");
    const onceFile = fileField(rule, key, "additionalContextOnceFile", "f-once-file", "セッションで最初に HIT したときにコンテキストに追加するファイル（同上）");
    const up = h("button", { type: "button", class: "action small", text: "↑", title: "上へ" });
    up.addEventListener("click", () => shift(key, -1));
    const down = h("button", { type: "button", class: "action small", text: "↓", title: "下へ" });
    down.addEventListener("click", () => shift(key, 1));
    const del = h("button", { type: "button", class: "action small", text: "削除" });
    del.addEventListener("click", () => remove(key));
    // 畳んだときは要約だけを出す。要約は欄を打つたびに書き直す（入力は上へ伝わる）。
    const twist = h("button", { type: "button", class: "twist", title: "このルールを畳む／開く" });
    const sum = h("span", { class: "rule-sum" });
    const li = h("li", { class: "rule", "data-key": key, "data-id": rule.id }, [
      h("div", { class: "rule-head" }, [twist, sum, h("span", { class: "buttons" }, [up, down, del])]),
      h("div", { class: "rule-body" }, [
        h("div", { class: "rule-row" }, [
          captioned("id", field(rule, "id", "f-id", "git-push"), "w-id"),
          matchField(rule),
          captioned("タイプ", sectionSelect),
        ]),
        h("div", { class: "rule-row" }, [
          captioned("形", kindSelect),
          captioned(rule.kind, pattern, "grow"),
        ]),
        message,
        context,
        contextFile,
        once,
        onceFile,
      ]),
    ]);
    function fillSummary() {
      sum.textContent = "";
      sum.appendChild(h("span", { class: "sum-id", text: rule.id === "" ? "（id 無し）" : rule.id }));
      sum.appendChild(h("span", { class: "sum-match", text: rule.match === "" ? "（全ツール）" : rule.match }));
      sum.appendChild(rule.pattern === ""
        ? h("span", { class: "sum-empty", text: "（" + rule.kind + " 空）" })
        : h("code", { text: rule.kind + " " + rule.pattern }));
    }
    function setFolded(on) {
      if (on) { folded.add(key); } else { folded.delete(key); }
      li.classList.toggle("folded", on);
      twist.textContent = on ? "▸" : "▾";
      twist.setAttribute("aria-expanded", on ? "false" : "true");
      if (on) { fillSummary(); }
    }
    twist.addEventListener("click", () => setFolded(!li.classList.contains("folded")));
    sum.addEventListener("click", () => setFolded(false));
    li.addEventListener("input", () => { if (li.classList.contains("folded")) { fillSummary(); } });
    fillSummary();
    setFolded(folded.has(key));
    return li;
  }
  // タイプごとの畳み。画面の見え方だけで、ルールの中身と並びには触らない。
  function foldSection(section, on) {
    const el = document.querySelector(".rule-section[data-section=" + section + "]");
    if (!el) { return; }
    const next = on === undefined ? !el.classList.contains("folded") : on;
    el.classList.toggle("folded", next);
    const twist = el.querySelector("h2 > .twist");
    twist.textContent = next ? "▸" : "▾";
    twist.setAttribute("aria-expanded", next ? "false" : "true");
  }
  // 判定に当たったルールは、畳んであっても開く。見えないところで光っても分からないので。
  function unfoldRule(el) {
    folded.delete(el.getAttribute("data-key"));
    const section = el.closest(".rule-section");
    if (section) { foldSection(section.getAttribute("data-section"), false); }
    if (!el.classList.contains("folded")) { return; }
    el.classList.remove("folded");
    const twist = el.querySelector(".rule-head > .twist");
    twist.textContent = "▾";
    twist.setAttribute("aria-expanded", "true");
  }
  function renderAll() {
    for (const section of SECTIONS) {
      const list = document.querySelector("[data-list=" + section + "]");
      list.textContent = "";
      for (const rule of sections[section]) { list.appendChild(renderRule(section, rule)); }
      document.querySelector("[data-count=" + section + "]").textContent = String(sections[section].length);
    }
    for (const el of document.querySelectorAll("input.f-id")) {
      el.addEventListener("input", () => { el.closest(".rule").setAttribute("data-id", el.value); });
    }
  }
  function markDirty() {
    dirty = true;
    document.getElementById("dirty").classList.remove("hidden");
    updateSave();
  }
  function updateSave() {
    const save = document.getElementById("save");
    save.disabled = !dirty || lock.locked || busy;
    const lockEl = document.getElementById("lock");
    lockEl.textContent = lock.reason;
    lockEl.classList.toggle("hidden", !lock.locked);
  }
  function moveTo(key, target) {
    const found = ruleByKey(key);
    if (!found || found.section === target) { return; }
    sections[found.section] = sections[found.section].filter((r) => r !== found.rule);
    sections[target] = sections[target].concat([found.rule]);
    markDirty();
    renderAll();
  }
  function shift(key, delta) {
    const found = ruleByKey(key);
    if (!found) { return; }
    const list = sections[found.section].slice();
    const i = list.indexOf(found.rule);
    const j = i + delta;
    if (j < 0 || j >= list.length) { return; }
    list[i] = list[j];
    list[j] = found.rule;
    sections[found.section] = list;
    markDirty();
    renderAll();
  }
  function remove(key) {
    const found = ruleByKey(key);
    if (!found) { return; }
    sections[found.section] = sections[found.section].filter((r) => r !== found.rule);
    markDirty();
    renderAll();
  }
  function add(section) {
    sections[section] = sections[section].concat([{ origin: null, id: "", match: "", kind: "glob", pattern: "", message: "", additionalContext: "", additionalContextOnce: "", additionalContextFile: "", additionalContextOnceFile: "" }]);
    markDirty();
    renderAll();
    foldSection(section, false);
    const items = document.querySelectorAll("[data-list=" + section + "] .rule");
    const last = items[items.length - 1];
    if (last) { last.querySelector("input.f-id").focus(); }
  }
  function status(text, isError) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.parentElement.classList.toggle("error", !!isError);
  }
  function setBusy(on, text) {
    busy = on;
    for (const b of document.querySelectorAll("button[data-action=judge], button[data-action=samples], button[data-action=reload]")) { b.disabled = on; }
    updateSave();
    if (text) { status(text, false); }
  }
  function verdictEl(verdict) {
    const v = verdict || "none";
    return h("span", { class: "verdict " + v, text: verdict || "（判定に入らない）" });
  }
  function dl(pairs) {
    const el = h("dl", { class: "kv" });
    for (const p of pairs) {
      if (p[1] === "" || p[1] === undefined || p[1] === null) { continue; }
      el.appendChild(h("dt", { text: p[0] }));
      el.appendChild(h("dd", { text: p[1] }));
    }
    return el;
  }
  function hitsTable(rules) {
    if (rules.length === 0) { return h("p", { class: "empty", text: "どのルールにも当たらなかった" }); }
    const body = h("tbody", {}, rules.map((r) => h("tr", {}, [
      h("td", {}, [r.section ? h("span", { class: "verdict " + r.section, text: r.section }) : null]),
      h("td", { text: r.id + (r.source === "outside" ? "（ルールファイルの外から来た根拠）" : "") }),
      h("td", {}, [r.kind ? h("code", { text: r.kind + " " + r.written }) : null]),
      h("td", {}, [r.pattern ? h("code", { text: r.pattern }) : null]),
    ])));
    return h("table", {}, [
      h("thead", {}, [h("tr", {}, [h("th", { text: "タイプ" }), h("th", { text: "id" }), h("th", { text: "書いたもの" }), h("th", { text: "翻訳後" })])]),
      body,
    ]);
  }
  function hooksTable(hooks) {
    if (hooks.length === 0) { return h("p", { class: "empty", text: "走る hook は無い" }); }
    return h("table", {}, [
      h("thead", {}, [h("tr", {}, [h("th", { text: "イベント" }), h("th", { text: "matcher" }), h("th", { text: "コマンド" })])]),
      h("tbody", {}, hooks.map((k) => h("tr", {}, [
        h("td", { text: k.event }),
        h("td", {}, [h("code", { text: k.matcher === "" ? "（全部）" : k.matcher })]),
        h("td", { class: "cmd" }, [h("code", { text: k.command })]),
      ]))),
    ]);
  }
  function showJudged(result, hooks) {
    const box = document.getElementById("judge-result");
    box.textContent = "";
    box.classList.remove("hidden");
    for (const el of document.querySelectorAll(".rule.hit")) { el.classList.remove("hit"); }
    if (!result.known) {
      box.appendChild(h("p", {}, [verdictEl(""), document.createTextNode(" " + result.tool + " は判定が対象を取り出せないツール。ルールを書いても当たらず、呼び出しはそのまま通る")]));
    } else {
      box.appendChild(h("p", {}, [verdictEl(result.verdict), document.createTextNode(result.code ? " " + result.code : "")]));
      box.appendChild(dl([["tool", result.tool], ["subject", result.subject], ["resolved", result.resolved], ["reason", result.reason], ["degraded", result.degraded ? result.degraded + "（生の文字列に当てた）" : ""], ["fallback", result.fallback ? result.fallback + "（組み込みの既定で判定した）" : ""]]));
      box.appendChild(h("h3", { text: "当たったルール" }));
      box.appendChild(hitsTable(result.rules));
      for (const r of result.rules) {
        for (const el of document.querySelectorAll(".rule[data-id]")) {
          if (el.getAttribute("data-id") === r.id) { el.classList.add("hit"); unfoldRule(el); }
        }
      }
      box.appendChild(h("h3", { text: "返る文面" }));
      box.appendChild(result.response ? h("pre", { class: "response", text: result.response }) : h("p", { class: "empty", text: "何も返さない" }));
    }
    box.appendChild(h("h3", { text: "このツールで走る hook" }));
    box.appendChild(hooksTable(hooks));
  }
  function showSamples(result) {
    const box = document.getElementById("samples-result");
    box.textContent = "";
    box.classList.remove("hidden");
    const counts = h("div", { class: "counts" });
    for (const section of SECTIONS) {
      const c = result.counts[section] || { ok: 0, total: 0 };
      counts.appendChild(h("span", { class: c.ok === c.total ? "" : "ng", text: section + " " + c.ok + "/" + c.total }));
    }
    counts.appendChild(h("span", { class: result.mismatches > 0 ? "ng" : "", text: "食い違い " + result.mismatches + " 件" }));
    counts.appendChild(h("span", { text: "判定に入らなかったもの " + result.skipped + " 件" }));
    box.appendChild(counts);
    const rows = result.samples.map((s) => h("tr", { class: s.ok ? (s.skipped ? "skipped" : "") : "ng" }, [
      h("td", {}, [h("span", { class: "verdict " + s.expected, text: s.expected })]),
      h("td", {}, [verdictEl(s.known ? s.verdict : "")]),
      h("td", { text: s.tool }),
      h("td", { class: "cmd" }, [h("code", { text: s.subject })]),
      h("td", { text: s.rules.filter((r) => r.source === "file").map((r) => r.section + ":" + r.id).join(", ") }),
      h("td", { text: s.why }),
    ]));
    box.appendChild(h("table", {}, [
      h("thead", {}, [h("tr", {}, [h("th", { text: "期待" }), h("th", { text: "判定" }), h("th", { text: "tool" }), h("th", { text: "subject" }), h("th", { text: "当たったルール" }), h("th", { text: "なぜ" })])]),
      h("tbody", {}, rows),
    ]));
  }
  function showTab(name) {
    for (const tab of document.querySelectorAll(".tab")) { tab.classList.toggle("active", tab.getAttribute("data-tab") === name); }
    for (const pane of document.querySelectorAll(".pane")) { pane.classList.toggle("active", pane.id === "tab-" + name); }
    vscode.setState({ tab: name });
  }
  document.body.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) { return; }
    const action = button.getAttribute("data-action");
    if (action === "add") { add(button.getAttribute("data-section")); }
    else if (action === "fold-section") { foldSection(button.getAttribute("data-section")); }
    else if (action === "save") { setBusy(true, "検証して保存している…"); vscode.postMessage({ type: "save", sections: sections }); }
    else if (action === "reload") { vscode.postMessage({ type: "reload", dirty: dirty }); }
    else if (action === "judge") {
      const tool = document.getElementById("tool").value;
      const subject = document.getElementById("subject").value;
      if (subject.trim() === "") { status("subject が空", true); return; }
      setBusy(true, "判定している…");
      vscode.postMessage({ type: "judge", sections: sections, tool: tool, subject: subject });
    }
    else if (action === "samples") { setBusy(true, "見本を流している…"); vscode.postMessage({ type: "samples", sections: sections }); }
    else if (action === "open-rules") { vscode.postMessage({ type: "openFile", which: "rules" }); }
    else if (action === "open-samples") { vscode.postMessage({ type: "openFile", which: "samples" }); }
  });
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showTab(tab.getAttribute("data-tab")));
  }
  document.getElementById("subject").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); document.querySelector("button[data-action=judge]").click(); }
  });
  window.addEventListener("message", (event) => {
    const m = event.data || {};
    if (m.type === "judged") { setBusy(false, ""); showJudged(m.result, m.hooks); showTab("judge"); }
    else if (m.type === "sampled") { setBusy(false, ""); showSamples(m.result); showTab("judge"); }
    else if (m.type === "failed") { setBusy(false, ""); status(m.message, true); }
    else if (m.type === "lock") { lock = m.lock; updateSave(); }
    else if (m.type === "changed") { document.getElementById("changed").classList.remove("hidden"); }
    else if (m.type === "picked") {
      const found = ruleByKey(m.key);
      if (!found) { return; }
      found.rule[m.field] = m.path;
      markDirty();
      const input = document.querySelector('.rule[data-key="' + m.key + '"] .' + (m.field === "additionalContextFile" ? "f-context-file" : "f-once-file"));
      if (input) { input.value = m.path; }
    }
  });
  // ポップアップは、その欄と札の外を押したとき、または Esc で閉じる。
  document.addEventListener("mousedown", (event) => {
    for (const open of document.querySelectorAll(".picker.open")) {
      if (!open.contains(event.target)) { open.classList.remove("open"); }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") { return; }
    for (const open of document.querySelectorAll(".picker.open")) { open.classList.remove("open"); }
  });
  const saved = vscode.getState();
  renderAll();
  updateSave();
  if (saved && saved.tab) { showTab(saved.tab); }`;
