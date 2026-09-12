/**
 * フェーズ管理画面を外部資源に依存しない 1 枚の HTML に組み立てる。
 *
 * リスク管理画面と同じ作り。編集の途中の状態を持つので、HTML を作り直して差し替えるのは
 * 開いたときと再読込のときだけで、保存の可否や失敗は Webview の中のスクリプトが
 * メッセージを受けて DOM を書き換える。種類の一覧も、埋め込んだ JSON からスクリプトが
 * 組み立てる（足す・消す・並べ替えを DOM の作り直しで済ませるため）。
 *
 * 画面は種類の意味を判定しない。子の範囲が上限に収まるか、レビューが要るかは実行ファイルが
 * 承認と着手のときに出すもので、ここは種類を書く場所と、書いた種類が読めるか（`--lint`）を
 * 確かめる入口だけ。色は VS Code のテーマ変数だけを使う。文字列は全部実体参照にする。
 */
import type { Lock } from "./lock.js";
import { PHASE_KINDS, REVIEWS, type PhasesModel } from "./phases-doc.js";
import { BUTTON_STYLE, escapeHtml } from "./render.js";

export interface PhasesPage {
  readonly root: string;
  /** 種類の定義のファイル（ワークスペースルートからの相対で見せる） */
  readonly phasesPath: string;
  /** ファイルが在るか。無ければ空の画面を見せ、「雛形で作る」だけができる */
  readonly exists: boolean;
  /** `.claude/settings.json` の env.CCNAVI_TICKET_CONTROL の読み。disable なら種類は使われない */
  readonly ticketControl: string;
  readonly model: PhasesModel;
  readonly lock: Lock;
}

export interface RenderOptions {
  readonly nonce: string;
}

/** 区分の説明。select の札 */
export const KIND_LABELS: Readonly<Record<(typeof PHASE_KINDS)[number], string>> = {
  work: "work（全体計画 plan: に置く）",
  feedback: "feedback（フィードバック計画 feedback: に置く。review は mr 固定）",
};

/** レビューの既定の説明。select の札 */
export const REVIEW_LABELS: Readonly<Record<(typeof REVIEWS)[number], string>> = {
  none: "none（レビューを求めない既定。上限ではなく、実績のリスクが HIGH 以上なら要る）",
  mr: "mr（マージリクエストのレビューを受ける）",
};

export function renderPhasesPage(page: PhasesPage, options: RenderOptions): string {
  const { nonce } = options;
  const embedded = JSON.stringify({
    form: page.model.form,
    lock: page.lock,
    exists: page.exists,
    kinds: PHASE_KINDS.map((k) => ({ value: k, label: KIND_LABELS[k] })),
    reviews: REVIEWS.map((r) => ({ value: r, label: REVIEW_LABELS[r] })),
  }).replace(/</g, "\\u003c");
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi フェーズ管理</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
<body>
${renderTicketControlBanner(page.ticketControl)}<div id="changed" class="banner warn hidden">ファイルが外部で変更された。画面の内容は古い。<button type="button" class="action" data-action="reload">再読込</button></div>
<header class="toolbar">
  <div class="summary">
    <span class="path" title="${escapeHtml(page.root)}">${escapeHtml(page.phasesPath)}</span>
    <span id="dirty" class="dirty hidden">未保存</span>
  </div>
  <div class="controls">
    <button type="button" class="action" data-action="open-phases"${page.exists ? "" : " disabled"}>エディタで開く</button>
    <button type="button" class="action" data-action="reload">再読込</button>
    <button type="button" class="action primary" id="save" data-action="save" disabled>保存</button>
  </div>
</header>
<p id="lock" class="lock${page.lock.locked ? "" : " hidden"}">${escapeHtml(page.lock.reason)}</p>
${renderProblems(page.model.problems)}${renderMissing(page)}<section class="block">
  <h2>フェーズの種類 <span class="count" id="phase-count">0</span>
    <button type="button" class="action small" data-action="add">＋ 種類を追加</button></h2>
  <p class="hint">親チケットの <code>plan:</code> に <code>work</code> の種類を順に並べたものが全体計画で、<code>--approve</code> が通ることが合意になる。レビューのあとは <code>feedback:</code> に <code>feedback</code> の種類を並べて改版を出す。<code>id</code> と <code>title</code> はどちらも一意。<code>scope</code> は子チケットの範囲の上限（作業ツリーのルートからの glob。<code>inherit</code> なら親の範囲そのまま）、<code>deliverables</code> は閉じる前に在って追跡されているべきもの。<code>overlap</code> は並行してよい種類（対称）、<code>requires</code> は計画に置くなら一緒に要る種類。<code>agent</code> と <code>when</code> は案内にだけ使う。並びの欄は <code>,</code> で区切る。</p>
  <ul class="phases" id="phases"></ul>
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
  return `<div class="banner warn">このワークスペースはチケット制御が <code>disable</code>（<code>CCNAVI_TICKET_CONTROL</code>）。フェーズの種類は親チケットの計画と子の範囲にしか使われないので、いまは何にも効かない</div>\n`;
}

function renderMissing(page: PhasesPage): string {
  if (page.exists) {
    return "";
  }
  return `<div class="banner missing"><span>${escapeHtml(page.phasesPath)} が無い。実行ファイルはフェーズを番号だけで扱っていて、親チケットの <code>plan:</code> も読めない。種類を使うにはまずファイルを作る。雛形は README の例で、<code>scope</code> の綴りは作ったあとにこのプロジェクトの置き場へ直す。</span><button type="button" class="action primary" data-action="create">雛形でファイルを作る</button></div>\n`;
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
  input.duplicate { border-color: var(--vscode-editorError-foreground); }
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { margin: 0 0 8px; font-size: 1em; display: flex; gap: 8px; align-items: center; }
  .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  /* 欄名は欄の上に小さく常に出す。placeholder は説明で、入れると消えてよい。 */
  .field { display: flex; flex-direction: column; gap: 1px; }
  .field > .cap { font-size: .78em; color: var(--vscode-descriptionForeground); font-family: var(--vscode-editor-font-family); }
  .field > input, .field > select { width: 100%; box-sizing: border-box; margin: 0; }
  .phases { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .phase {
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background);
  }
  .phase.feedback { border-left: 3px solid var(--vscode-editorInfo-foreground); }
  .phase-row { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: flex-end; margin-bottom: 6px; }
  .phase-row:last-child { margin-bottom: 0; }
  .phase-row .grow { flex: 1 1 260px; }
  .phase-row .field.w-id { width: 170px; }
  .phase-row .field.w-title { width: 200px; }
  .phase-row .field.w-kind { width: 300px; }
  .phase-row .field.w-review { width: 300px; }
  .phase-row .field.w-scope { width: 220px; }
  .phase-row .field.w-agent { width: 170px; }
  .phase-row .buttons { margin-left: auto; display: flex; gap: 4px; }
  .foot { margin-top: 12px; font-size: .85em; color: var(--vscode-descriptionForeground); min-height: 1.2em; }
  .foot.error { color: var(--vscode-editorError-foreground); }`;

// 中のスクリプトはテンプレート文字列に埋めるので、バッククォートと \${ を使わない。
const SCRIPT = `  const vscode = acquireVsCodeApi();
  const page = JSON.parse(document.getElementById("page").textContent);
  const form = page.form;
  let lock = page.lock;
  let dirty = false;
  let busy = false;
  let dupShown = false;
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
  function keyOf(phase) {
    if (!keys.has(phase)) { seq += 1; keys.set(phase, "p" + seq); }
    return keys.get(phase);
  }
  function phaseByKey(key) {
    for (const phase of form.phases) { if (keyOf(phase) === key) { return phase; } }
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
  // 並びの欄は 1 つの欄に "," 区切りで出し、入力のたびに配列へ戻す。
  function listField(target, name, className, placeholder) {
    const input = h("input", { type: "text", class: className, spellcheck: "false", placeholder: placeholder || "" });
    input.value = target[name].join(", ");
    input.disabled = !page.exists;
    input.addEventListener("input", () => { target[name] = splitList(input.value); markDirty(); });
    return input;
  }
  function splitList(text) {
    return text.split(",").map((s) => s.trim()).filter((s) => s !== "");
  }
  // 欄名を欄の上に小さく出す。placeholder は説明なので、入れると消えてよい。
  function captioned(name, control, className) {
    return h("div", { class: "field " + (className || "") }, [h("span", { class: "cap", text: name }), control]);
  }
  function selectField(target, name, choices, className, onChange) {
    const select = h("select", { class: className }, choices.map((c) => option(c.value, c.label, c.value === target[name])));
    select.disabled = !page.exists;
    select.addEventListener("change", () => { target[name] = select.value; markDirty(); if (onChange) { onChange(); } });
    return select;
  }
  function renderPhase(phase) {
    const key = keyOf(phase);
    const idInput = field(phase, "id", "f-id", "implement（英数字で始まり、英数字と . _ - だけ）");
    idInput.addEventListener("input", () => updateSave());
    const kindSelect = selectField(phase, "kind", page.kinds, "f-kind", () => renderAll());
    const reviewSelect = selectField(phase, "review", page.reviews, "f-review");
    const scopeSelect = h("select", { class: "f-scope" }, [
      option("inherit", "inherit（親の範囲そのまま）", phase.inherit),
      option("globs", "上限を書く（glob の並び）", !phase.inherit),
    ]);
    scopeSelect.disabled = !page.exists;
    scopeSelect.addEventListener("change", () => { phase.inherit = scopeSelect.value === "inherit"; markDirty(); renderAll(); });
    const rows = [
      h("div", { class: "phase-row" }, [
        captioned("id", idInput, "w-id"),
        captioned("title", field(phase, "title", "f-title", "実装とテスト（空なら id）"), "w-title"),
        captioned("kind", kindSelect, "w-kind"),
        captioned("review", reviewSelect, "w-review"),
        h("span", { class: "buttons" }, [upButton(key), downButton(key), deleteButton(key)]),
      ]),
      h("div", { class: "phase-row" }, [
        captioned("scope", scopeSelect, "w-scope"),
        phase.inherit ? null : captioned("scope の glob", listField(phase, "scope", "f-scope-globs", "src/*, tests/*（作業ツリーのルートからの相対。子の範囲はこの中に収まる）"), "grow"),
      ]),
      h("div", { class: "phase-row" }, [
        captioned("deliverables", listField(phase, "deliverables", "f-deliverables", "wip/design/*.md（閉じる前に在って追跡されているべきもの）"), "grow"),
        captioned("overlap", listField(phase, "overlap", "f-overlap", "並行してよい種類の id"), "grow"),
        captioned("requires", listField(phase, "requires", "f-requires", "計画に置くなら一緒に要る種類の id"), "grow"),
      ]),
      h("div", { class: "phase-row" }, [
        captioned("agent", field(phase, "agent", "f-agent", "案内に出すサブエージェント名"), "w-agent"),
        captioned("when", field(phase, "when", "f-when", "この種類を計画に置く目安。案内にだけ使う"), "grow"),
      ]),
    ];
    return h("li", { class: "phase " + phase.kind, "data-key": key }, rows);
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
    const list = document.getElementById("phases");
    list.textContent = "";
    for (const phase of form.phases) { list.appendChild(renderPhase(phase)); }
    if (form.phases.length === 0) { list.appendChild(h("li", { class: "empty", text: "種類が無い。1 つも無いファイルは実行ファイルが読めないので、保存する前に足す" })); }
    document.getElementById("phase-count").textContent = String(form.phases.length);
    const add = document.querySelector("button[data-action=add]");
    if (add) { add.disabled = !page.exists; }
    updateSave();
  }
  function markDirty() {
    dirty = true;
    document.getElementById("dirty").classList.remove("hidden");
    updateSave();
  }
  // 同じ id が 2 つあると実行ファイルが後ろで黙って上書きするので、画面で止める。
  function duplicates() {
    const seen = new Set();
    const dup = new Set();
    for (const phase of form.phases) {
      const id = phase.id.trim();
      if (seen.has(id)) { dup.add(id); }
      seen.add(id);
    }
    return dup;
  }
  function updateSave() {
    const dup = duplicates();
    for (const li of document.querySelectorAll("#phases .phase")) {
      const phase = phaseByKey(li.getAttribute("data-key"));
      const input = li.querySelector("input.f-id");
      if (phase && input) { input.classList.toggle("duplicate", dup.has(phase.id.trim())); }
    }
    const save = document.getElementById("save");
    save.disabled = !dirty || lock.locked || busy || !page.exists || dup.size > 0;
    const lockEl = document.getElementById("lock");
    lockEl.textContent = lock.reason;
    lockEl.classList.toggle("hidden", !lock.locked);
    if (dup.size > 0) { status("id が重なっている（" + Array.from(dup).join(", ") + "）。1 つにするまで保存できない", true); dupShown = true; }
    else if (dupShown) { status("", false); dupShown = false; }
  }
  function shift(key, delta) {
    const list = form.phases.slice();
    const i = list.findIndex((p) => keyOf(p) === key);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= list.length) { return; }
    const moved = list[i];
    list[i] = list[j];
    list[j] = moved;
    form.phases = list;
    markDirty();
    renderAll();
  }
  function remove(key) {
    const phase = phaseByKey(key);
    if (!phase) { return; }
    form.phases = form.phases.filter((p) => p !== phase);
    markDirty();
    renderAll();
  }
  function add() {
    form.phases = form.phases.concat([{ origin: null, id: "", title: "", kind: "work", review: "mr", inherit: false, scope: [], deliverables: [], overlap: [], requires: [], agent: "", when: "" }]);
    markDirty();
    renderAll();
    const items = document.querySelectorAll("#phases .phase");
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
    for (const b of document.querySelectorAll("button[data-action=reload], button[data-action=create]")) { b.disabled = on; }
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
    else if (action === "open-phases") { vscode.postMessage({ type: "openFile" }); }
  });
  window.addEventListener("message", (event) => {
    const m = event.data || {};
    if (m.type === "failed") { setBusy(false, ""); status(m.message, true); }
    else if (m.type === "lock") { lock = m.lock; updateSave(); }
    else if (m.type === "changed") { document.getElementById("changed").classList.remove("hidden"); }
  });
  renderAll();`;
