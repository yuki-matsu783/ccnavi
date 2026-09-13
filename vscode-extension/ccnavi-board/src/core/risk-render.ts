/**
 * リスク管理画面を外部資源に依存しない 1 枚の HTML に組み立てる。
 *
 * ルール設定画面と同じ作り。編集の途中の状態を持つので、HTML を作り直して差し替えるのは
 * 開いたときと再読込のときだけで、保存の可否や失敗は Webview の中のスクリプトが
 * メッセージを受けて DOM を書き換える。項目の一覧も、埋め込んだ JSON からスクリプトが
 * 組み立てる（足す・消す・並べ替えを DOM の作り直しで済ませるため）。
 *
 * 画面は点を数えない。何点になるかは実行ファイルが子を閉じるときに出すもので、ここは
 * 配点を書く場所と、書いた配点が読めるか（`--lint`）を確かめる入口だけ。
 * 色は VS Code のテーマ変数だけを使う。文字列は全部実体参照にする。
 */
import type { Lock } from "./lock.js";
import { BUTTON_STYLE, escapeHtml } from "./render.js";
import { BUILTIN_LEVELS, KINDS, LEVEL_NAMES, type RiskModel } from "./risk-doc.js";

export interface RiskPage {
  readonly root: string;
  /** 配点のファイル（ワークスペースルートからの相対で見せる） */
  readonly riskPath: string;
  /** ファイルが在るか。無ければ組み込みの配点を見せ、「作る」だけができる */
  readonly exists: boolean;
  /** `.claude/settings.json` の env.CCNAVI_TICKET_CONTROL の読み。disable なら配点は使われない */
  readonly ticketControl: string;
  readonly model: RiskModel;
  readonly lock: Lock;
}

export interface RenderOptions {
  readonly nonce: string;
}

/** 当て方の説明。select の札と、値の欄の placeholder */
export const KIND_LABELS: Readonly<Record<(typeof KINDS)[number], { readonly label: string; readonly placeholder: string }>> = {
  lines_over: { label: "差分の行数が超えたら", placeholder: "300（追加と削除の合計がこれを超えたら加点）" },
  files_over: { label: "ファイル数が超えたら", placeholder: "10（変えたファイルの数がこれを超えたら加点）" },
  deleted_over: { label: "消したファイル数が超えたら", placeholder: "3（消したファイルの数がこれを超えたら加点）" },
  glob: { label: "当たったファイルごとに", placeholder: ".github/**（作業ツリーのルートからの相対。当たるごとに points を加点、max で上限）" },
  script: { label: "スクリプトが出す点", placeholder: ".claude/ccnavi/risk/xxx.sh（.claude/ccnavi/ か .claude/scripts/ の下。失敗は points を加点）" },
  judge: { label: "サブエージェントの判定", placeholder: "テストの無い振る舞いの変更を含むか（差分を読んで yes / no で答えられる問い。yes で加点）" },
};

export function renderRiskPage(page: RiskPage, options: RenderOptions): string {
  const { nonce } = options;
  const embedded = JSON.stringify({
    form: page.model.form,
    lock: page.lock,
    exists: page.exists,
    kinds: KINDS.map((k) => ({ kind: k, label: KIND_LABELS[k].label, placeholder: KIND_LABELS[k].placeholder })),
    builtinLevels: BUILTIN_LEVELS,
  }).replace(/</g, "\\u003c");
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi リスク管理</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
<body>
${renderTicketControlBanner(page.ticketControl)}<div id="changed" class="banner warn hidden">ファイルが外部で変更された。画面の内容は古い。<button type="button" class="action" data-action="reload">再読込</button></div>
<header class="toolbar">
  <div class="summary">
    <span class="path" title="${escapeHtml(page.root)}">${escapeHtml(page.riskPath)}</span>
    <span id="dirty" class="dirty hidden">未保存</span>
  </div>
  <div class="controls">
    <button type="button" class="action" data-action="open-risk"${page.exists ? "" : " disabled"}>エディタで開く</button>
    <button type="button" class="action" data-action="reload">再読込</button>
    <button type="button" class="action primary" id="save" data-action="save" disabled>保存</button>
  </div>
</header>
<p id="lock" class="lock${page.lock.locked ? "" : " hidden"}">${escapeHtml(page.lock.reason)}</p>
${renderProblems(page.model.problems)}${renderMissing(page)}<section class="block">
  <h2>段階の閾値</h2>
  <p class="hint">点がその値以上になると段階が上がる（LOW → MEDIUM → HIGH → CRITICAL）。<strong>HIGH 以上でフェーズのゲートが閉じ</strong>、宣言に関わらず人間レビューが要る扱いになる。medium ≤ high ≤ critical の順。空ならその段階は組み込みの値（${LEVEL_NAMES.map((n) => `${n} ${BUILTIN_LEVELS[n]}`).join(" / ")}）。</p>
  <div class="levels" id="levels"></div>
</section>
<section class="block">
  <h2>項目 <span class="count" id="factor-count">0</span>
    <button type="button" class="action small" data-action="add">＋ 項目を追加</button></h2>
  <p class="hint">子を閉じるとき、その子の差分（base_sha..HEAD）に当てて加点する。1 件につき当て方は 1 つ。点の合計で段階が決まり、フェーズの点は子の最大値。<code>script</code> の失敗と読めない出力は重い側に倒れて points がそのまま加点され、<code>judge</code> は判定が揃うまで子を閉じられない。</p>
  <ul class="factors" id="factors"></ul>
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

function renderTicketControlBanner(ticketControl: string): string {
  if (ticketControl !== "disable") {
    return "";
  }
  return `<div class="banner warn">このワークスペースはチケット制御が <code>disable</code>（<code>CCNAVI_TICKET_CONTROL</code>）。配点は子チケットを閉じるときにしか使われないので、いまは何にも効かない</div>\n`;
}

function renderMissing(page: RiskPage): string {
  if (page.exists) {
    return "";
  }
  return `<div class="banner missing"><span>${escapeHtml(page.riskPath)} が無い。実行ファイルは組み込みの配点で数えている（画面の値はその組み込み）。直すにはまずファイルを作る。</span><button type="button" class="action primary" data-action="create">組み込みの配点でファイルを作る</button></div>\n`;
}

function renderProblems(problems: readonly string[]): string {
  if (problems.length === 0) {
    return "";
  }
  const items = problems.map((p) => `  <li>${escapeHtml(p)}</li>`).join("\n");
  return `<ul class="problems">\n${items}\n</ul>\n`;
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
  .banner.missing { border-color: var(--vscode-editorInfo-foreground); }
  .banner.missing > span { flex: 1 1 320px; }
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
  .hint { margin: 0 0 10px; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .empty { color: var(--vscode-descriptionForeground); }
${BUTTON_STYLE}
  button.action.small { margin-left: auto; }
  input[type=text], select {
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 2px;
    padding: 3px 6px; font: inherit;
  }
  input[type=text]:focus, select:focus { outline: 1px solid var(--vscode-focusBorder); }
  select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border-color: var(--vscode-dropdown-border); }
  input:disabled, select:disabled { opacity: .6; }
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { margin: 0 0 8px; font-size: 1em; display: flex; gap: 8px; align-items: center; }
  .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .levels { display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: flex-end; }
  /* 欄名は欄の上に小さく常に出す。placeholder は説明で、入れると消えてよい。 */
  .field { display: flex; flex-direction: column; gap: 1px; }
  .field > .cap { font-size: .78em; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); }
  .field > input, .field > select { width: 100%; box-sizing: border-box; margin: 0; }
  .field.w-level input { width: 90px; }
  .level-name { font-weight: 700; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; }
  .level-name.medium { color: var(--vscode-editorWarning-foreground); }
  .level-name.high, .level-name.critical { color: var(--vscode-editorError-foreground); }
  .factors { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .factor {
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background);
  }
  .factor-row { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: flex-end; margin-bottom: 6px; }
  .factor-row:last-child { margin-bottom: 0; }
  .factor-row .grow { flex: 1 1 260px; }
  .factor-row .field.w-id { width: 170px; }
  .factor-row .field.w-num { width: 90px; }
  .factor-row .field.w-kind { width: 230px; }
  .factor-row .buttons { margin-left: auto; display: flex; gap: 4px; }
  .foot { margin-top: 12px; font-size: .85em; color: var(--vscode-descriptionForeground); min-height: 1.2em; }
  .foot.error { color: var(--vscode-editorError-foreground); }`;

// 中のスクリプトはテンプレート文字列に埋めるので、バッククォートと \${ を使わない。
const SCRIPT = `  const vscode = acquireVsCodeApi();
  const page = JSON.parse(document.getElementById("page").textContent);
  const LEVELS = ["medium", "high", "critical"];
  const NUMERIC = ["lines_over", "files_over", "deleted_over"];
  const form = page.form;
  let lock = page.lock;
  let dirty = false;
  let busy = false;
  let seq = 0;
  const keys = new Map();

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
  function keyOf(factor) {
    if (!keys.has(factor)) { seq += 1; keys.set(factor, "f" + seq); }
    return keys.get(factor);
  }
  function factorByKey(key) {
    for (const factor of form.factors) { if (keyOf(factor) === key) { return factor; } }
    return undefined;
  }
  function option(value, label, selected) {
    const el = h("option", { value: value, text: label });
    if (selected) { el.selected = true; }
    return el;
  }
  function field(target, name, className, placeholder) {
    const input = h("input", { type: "text", class: className, spellcheck: "false", placeholder: placeholder || "" });
    input.value = target[name];
    input.disabled = !page.exists;
    input.addEventListener("input", () => { target[name] = input.value; markDirty(); });
    return input;
  }
  // 欄名を欄の上に小さく出す。placeholder は説明なので、入れると消えてよい。
  function captioned(name, control, className) {
    return h("div", { class: "field " + (className || "") }, [h("span", { class: "cap", text: name }), control]);
  }
  function kindInfo(kind) {
    for (const k of page.kinds) { if (k.kind === kind) { return k; } }
    return page.kinds[0];
  }
  function renderLevels() {
    const box = document.getElementById("levels");
    box.textContent = "";
    for (const name of LEVELS) {
      const input = field(form.levels, name, "f-level", "既定 " + page.builtinLevels[name]);
      input.setAttribute("inputmode", "numeric");
      box.appendChild(captioned(name, input, "w-level"));
      box.appendChild(h("span", { class: "level-name " + name, text: name.toUpperCase() + " 以上" }));
    }
  }
  function renderFactor(factor) {
    const key = keyOf(factor);
    const info = kindInfo(factor.kind);
    const kindSelect = h("select", { class: "f-kind" }, page.kinds.map((k) => option(k.kind, k.kind + "（" + k.label + "）", k.kind === factor.kind)));
    kindSelect.disabled = !page.exists;
    kindSelect.addEventListener("change", () => {
      factor.kind = kindSelect.value;
      // 値は当て方ごとに意味が違うので持ち越さない。max は glob だけの欄。
      factor.value = "";
      if (factor.kind !== "glob") { factor.max = ""; }
      markDirty();
      renderAll();
    });
    const points = field(factor, "points", "f-points", "25");
    points.setAttribute("inputmode", "numeric");
    const value = field(factor, "value", "f-value", info.placeholder);
    if (NUMERIC.indexOf(factor.kind) >= 0) { value.setAttribute("inputmode", "numeric"); }
    const rows = [
      h("div", { class: "factor-row" }, [
        captioned("id", field(factor, "id", "f-id", "big-diff"), "w-id"),
        captioned("points", points, "w-num"),
        captioned("当て方", kindSelect, "w-kind"),
        h("span", { class: "buttons" }, [upButton(key), downButton(key), deleteButton(key)]),
      ]),
      h("div", { class: "factor-row" }, [
        captioned(factor.kind, value, "grow"),
        factor.kind === "glob" ? captioned("max", field(factor, "max", "f-max", "上限。空なら青天井"), "w-num") : null,
      ]),
      h("div", { class: "factor-row" }, [
        captioned("message", field(factor, "message", "f-message", "加点した理由として依頼文と閉じたときの出力に出る短い文。空なら id"), "grow"),
      ]),
    ];
    return h("li", { class: "factor", "data-key": key }, rows);
  }
  function upButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "↑", title: "上へ" });
    b.disabled = !page.exists;
    b.addEventListener("click", () => shift(key, -1));
    return b;
  }
  function downButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "↓", title: "下へ" });
    b.disabled = !page.exists;
    b.addEventListener("click", () => shift(key, 1));
    return b;
  }
  function deleteButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "削除" });
    b.disabled = !page.exists;
    b.addEventListener("click", () => remove(key));
    return b;
  }
  function renderAll() {
    renderLevels();
    const list = document.getElementById("factors");
    list.textContent = "";
    for (const factor of form.factors) { list.appendChild(renderFactor(factor)); }
    if (form.factors.length === 0) { list.appendChild(h("li", { class: "empty", text: "項目が無い。加点する項目が無ければ、どの子も LOW のまま閉じる" })); }
    document.getElementById("factor-count").textContent = String(form.factors.length);
    const add = document.querySelector("button[data-action=add]");
    if (add) { add.disabled = !page.exists; }
  }
  function markDirty() {
    dirty = true;
    document.getElementById("dirty").classList.remove("hidden");
    updateSave();
  }
  function updateSave() {
    const save = document.getElementById("save");
    save.disabled = !dirty || lock.locked || busy || !page.exists;
    const lockEl = document.getElementById("lock");
    lockEl.textContent = lock.reason;
    lockEl.classList.toggle("hidden", !lock.locked);
  }
  function shift(key, delta) {
    const list = form.factors.slice();
    const i = list.findIndex((f) => keyOf(f) === key);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= list.length) { return; }
    const moved = list[i];
    list[i] = list[j];
    list[j] = moved;
    form.factors = list;
    markDirty();
    renderAll();
  }
  function remove(key) {
    const factor = factorByKey(key);
    if (!factor) { return; }
    form.factors = form.factors.filter((f) => f !== factor);
    markDirty();
    renderAll();
  }
  function add() {
    form.factors = form.factors.concat([{ origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", message: "" }]);
    markDirty();
    renderAll();
    const items = document.querySelectorAll("#factors .factor");
    const last = items[items.length - 1];
    if (last) { last.querySelector("input.f-id").focus(); }
  }
  function status(text, isError) {
    const el = document.getElementById("status");
    el.textContent = text;
    el.parentElement.classList.toggle("error", !!isError);
  }
  // 保存の往復（lint）の間に入れた編集は、保存が通ると再描画で消える。その間は欄ごと止める。
  function setBusy(on, text) {
    busy = on;
    for (const b of document.querySelectorAll("button[data-action=reload], button[data-action=create]")) { b.disabled = on; }
    for (const el of document.querySelectorAll("#levels input, #factors input, #factors select, #factors button, button[data-action=add]")) { el.disabled = on || !page.exists; }
    updateSave();
    if (text) { status(text, false); }
  }
  document.body.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) { return; }
    const action = button.getAttribute("data-action");
    if (action === "add") { add(); }
    else if (action === "save") { setBusy(true, "検証して保存中…"); vscode.postMessage({ type: "save", form: form }); }
    else if (action === "reload") { vscode.postMessage({ type: "reload", dirty: dirty }); }
    else if (action === "create") { setBusy(true, "ファイルを作成中…"); vscode.postMessage({ type: "create" }); }
    else if (action === "open-risk") { vscode.postMessage({ type: "openFile" }); }
  });
  window.addEventListener("message", (event) => {
    const m = event.data || {};
    if (m.type === "failed") { setBusy(false, ""); status(m.message, true); }
    else if (m.type === "lock") { lock = m.lock; updateSave(); }
    else if (m.type === "changed") { document.getElementById("changed").classList.remove("hidden"); }
  });
  renderAll();
  updateSave();`;
