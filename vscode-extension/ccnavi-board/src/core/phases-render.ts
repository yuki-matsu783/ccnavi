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
import { APPEARANCE_SCRIPT, type Appearance, bodyTag } from "./appearance.js";
import { LIST_STYLE, PAGE_STYLE } from "./styles.js";
import { escapeHtml } from "./html.js";

export interface PhasesPage {
  readonly root: string;
  /** 種類の定義のファイル（ワークスペースルートからの相対で見せる） */
  readonly phasesPath: string;
  /** ファイルが在るか。無ければ空の画面を見せ、「雛形で作る」だけができる */
  readonly exists: boolean;
  readonly model: PhasesModel;
  readonly lock: Lock;
  /**
   * 層（自身の層かプロジェクト）の種類か。層はファイルが無くても編集でき、最初の保存でファイルを作る。
   * 雛形は置かない（雛形の id は共通層の種類と重なりやすい）
   */
  readonly layer?: boolean;
  /** 上部に出す注意（実行ファイルがこの層を読めていない、など） */
  readonly notices?: readonly string[];
}

export interface RenderOptions {
  readonly nonce: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/** 区分の説明。select の札 */
export const KIND_LABELS: Readonly<Record<(typeof PHASE_KINDS)[number], string>> = {
  work: "work（全体計画 plan: に並べる種類）",
  feedback: "feedback（フィードバック計画 feedback: に並べる種類。レビューは mr 固定）",
};

/** レビューの既定の説明。select の札 */
export const REVIEW_LABELS: Readonly<Record<(typeof REVIEWS)[number], string>> = {
  none: "none（既定。レビューを求めない。ただし実績のリスクが HIGH 以上なら要る）",
  mr: "mr（マージリクエストのレビューを受ける）",
};

export function renderPhasesPage(page: PhasesPage, options: RenderOptions): string {
  const { nonce } = options;
  const embedded = JSON.stringify({
    form: page.model.form,
    lock: page.lock,
    exists: page.exists,
    // 欄を触れるか。共通層はファイルが無ければ「雛形で作る」まで触れない。層は無くても足して保存できる。
    editable: page.exists || page.layer === true,
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
${bodyTag(options.appearance)}
${renderNotices(page.notices ?? [])}<div id="changed" class="banner warn hidden">ファイルが外で変更されたので、画面の内容は古い。<button type="button" class="action" data-action="reload">再読込</button></div>
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
  <div class="find">
    <input id="find" type="search" placeholder="id・title・scope・when で絞り込む" spellcheck="false">
    <span class="hint">行を押すと開く</span>
  </div>
  <details class="help"><summary>この画面の説明</summary><p class="hint">親チケットの <code>plan:</code> に <code>work</code> の種類を順に並べたものが全体計画で、<code>--approve</code> が通ることが合意になる。レビューのあとは <code>feedback:</code> に <code>feedback</code> の種類を並べて改版を出す。<code>id</code> と <code>title</code> はどちらも一意。<code>scope</code> は子チケットの範囲の上限（ワークツリーのルートからの glob。<code>inherit</code> なら親の範囲そのまま）、<code>deliverables</code> は閉じる前に存在し、git に追跡されているべきもの。<code>overlap</code> は並行してよい種類（対称）、<code>requires</code> は計画に置くなら一緒に要る種類。<code>agent</code> と <code>when</code> は案内にだけ使う。並びの欄は <code>,</code> で区切る。</p></details>
  <ul class="list" id="phases"></ul>
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

function renderNotices(notices: readonly string[]): string {
  return notices.map((n) => `<div class="banner warn">${escapeHtml(n)}</div>\n`).join("");
}

function renderMissing(page: PhasesPage): string {
  if (page.exists) {
    return "";
  }
  if (page.layer === true) {
    return `<div class="banner missing"><span>${escapeHtml(page.phasesPath)} が無い。無い層は空で、共通層の種類だけが使われる。この層に種類を足すなら、下で足して保存する（最初の保存でファイルが作られる）。雛形は置かない。雛形の id は共通層の種類と重なりやすく、中身が違えばこの層が空として扱われるため。</span></div>\n`;
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

const STYLE = `${PAGE_STYLE}
  button.action.small { margin-left: auto; }
  input.duplicate { border-color: var(--vscode-editorError-foreground); }
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { display: flex; gap: 8px; align-items: center; }
${LIST_STYLE}
  /* 1 行 = 開閉、id、title、kind、review、scope */
  .phase .row-head { grid-template-columns: 18px minmax(110px, 160px) minmax(90px, 160px) max-content max-content minmax(0, 1fr); }
  .sum .tag.work { color: var(--vscode-descriptionForeground); }
  .sum .tag.feedback { color: var(--vscode-editorInfo-foreground); }
  .field > select.f-kind, .field > select.f-review { max-width: 420px; }
`;

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
  // 開いている種類の鍵。既定は全部畳む。Webview の state には id で控え、再読込のあとも同じ種類が開く。
  const opened = new Set();
  // 「関係と案内」の開閉。最初は値の有無で決め、以後は利用者の操作を鍵で覚える。
  const moreOpen = new Map();
  const savedOpen = new Set((vscode.getState() || {}).open || []);
  function persistOpen() {
    const ids = [];
    for (const key of opened) {
      const phase = phaseByKey(key);
      if (phase && phase.id !== "") { ids.push(phase.id); }
    }
    vscode.setState(Object.assign({}, vscode.getState() || {}, { open: ids }));
  }

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
    input.disabled = !page.editable;
    input.addEventListener("input", () => { target[name] = input.value; markDirty(); });
    return input;
  }
  // 並びの欄は 1 つの欄に "," 区切りで出し、入力のたびに配列へ戻す。
  function listField(target, name, className, placeholder) {
    const input = h("input", { type: "text", class: className, spellcheck: "false", placeholder: placeholder || "" });
    input.value = target[name].join(", ");
    input.disabled = !page.editable;
    input.addEventListener("input", () => { target[name] = splitList(input.value); markDirty(); });
    return input;
  }
  function splitList(text) {
    return text.split(",").map((s) => s.trim()).filter((s) => s !== "");
  }
  // 欄名は日本語で欄の左に出す。YAML のキー名は欄名の title（ツールチップ）に載せる。
  function captioned(name, control, className, key) {
    const cap = h("span", { class: "cap", text: name });
    if (key) { cap.setAttribute("title", "YAML のキー: " + key); }
    return h("div", { class: "field " + (className || "") }, [cap, control]);
  }
  function selectField(target, name, choices, className, onChange) {
    const select = h("select", { class: className }, choices.map((c) => option(c.value, c.label, c.value === target[name])));
    select.disabled = !page.editable;
    select.addEventListener("change", () => { target[name] = select.value; markDirty(); if (onChange) { onChange(); } });
    return select;
  }
  function hasRelations(phase) {
    return phase.overlap.length > 0 || phase.requires.length > 0 || phase.agent !== "" || phase.when !== "";
  }
  function renderPhase(phase) {
    const key = keyOf(phase);
    const idInput = field(phase, "id", "f-id narrow", "implement（英数字で始まり、使えるのは英数字と . _ -）");
    idInput.addEventListener("input", () => updateSave());
    const kindSelect = selectField(phase, "kind", page.kinds, "f-kind", () => renderAll());
    const reviewSelect = selectField(phase, "review", page.reviews, "f-review");
    const scopeSelect = h("select", { class: "f-scope" }, [
      option("inherit", "inherit（親の範囲そのまま）", phase.inherit),
      option("globs", "上限を書く（glob の並び）", !phase.inherit),
    ]);
    scopeSelect.disabled = !page.editable;
    scopeSelect.addEventListener("change", () => { phase.inherit = scopeSelect.value === "inherit"; markDirty(); renderAll(); });
    // 関係と案内の 4 欄は出番が少ないので見出し 1 行に畳む。値がある種類だけ最初から開く。
    const moreSummary = h("summary", {});
    const more = h("details", { class: "more" }, [
      moreSummary,
      h("div", { class: "sub" }, [
        captioned("並行できる種類", listField(phase, "overlap", "f-overlap", "並行してよい種類の id"), "", "overlap"),
        captioned("一緒に要る種類", listField(phase, "requires", "f-requires", "計画に置くなら一緒に要る種類の id"), "", "requires"),
        captioned("エージェント", field(phase, "agent", "f-agent narrow", "案内に出すサブエージェントの名前"), "", "agent"),
        captioned("置く目安", field(phase, "when", "f-when", "この種類を計画に置く目安。案内にだけ使う"), "", "when"),
      ]),
    ]);
    if (moreOpen.has(key) ? moreOpen.get(key) : hasRelations(phase)) { more.setAttribute("open", ""); }
    more.addEventListener("toggle", () => moreOpen.set(key, more.open));
    const twist = h("button", { type: "button", class: "twist", title: "この種類を開く／畳む" });
    const sum = h("span", { class: "sum" });
    const head = h("div", { class: "row-head" }, [twist, sum]);
    const li = h("li", { class: "row phase " + phase.kind, "data-key": key }, [
      head,
      h("div", { class: "row-body" }, [
        captioned("id", idInput, "", "id"),
        captioned("題", field(phase, "title", "f-title narrow", "実装とテスト（空なら id をそのまま使う）"), "", "title"),
        captioned("区分", kindSelect, "", "kind"),
        captioned("レビュー", reviewSelect, "", "review"),
        captioned("範囲", h("div", { class: "inline" }, [
          scopeSelect,
          phase.inherit ? null : listField(phase, "scope", "f-scope-globs", "src/*, tests/*（ワークツリーのルートからの相対。子チケットの範囲はこの中に収める）"),
        ]), "", "scope"),
        captioned("成果物", listField(phase, "deliverables", "f-deliverables", "wip/design/*.md（閉じる前に存在し、git に追跡されているべきもの）"), "", "deliverables"),
        more,
        h("span", { class: "buttons" }, [upButton(key), downButton(key), deleteButton(key)]),
      ]),
    ]);
    function fillSummary() {
      sum.textContent = "";
      sum.appendChild(h("span", { class: "sum-id" + (phase.id === "" ? " dim" : ""), text: phase.id === "" ? "（id 未設定）" : phase.id }));
      sum.appendChild(h("span", { class: "clip", text: phase.title === "" ? "" : phase.title, title: phase.title }));
      sum.appendChild(h("span", { class: "tag " + phase.kind, text: phase.kind }));
      sum.appendChild(h("span", { class: "dim", text: "レビュー " + phase.review, title: "review" }));
      sum.appendChild(h("span", { class: "clip dim mono", text: phase.inherit ? "inherit" : phase.scope.length === 0 ? "（scope 未設定）" : phase.scope.join(", "), title: phase.inherit ? "親の範囲そのまま" : phase.scope.join(", ") }));
      li.setAttribute("data-find", (phase.id + " " + phase.title + " " + phase.scope.join(" ") + " " + phase.deliverables.join(" ") + " " + phase.when).toLowerCase());
      moreSummary.textContent = "";
      moreSummary.appendChild(h("b", { text: "関係と案内" }));
      moreSummary.appendChild(document.createTextNode(hasRelations(phase) ? "（設定あり）" : "（未設定）— 並行できる種類・一緒に要る種類・エージェント・置く目安"));
    }
    head.addEventListener("click", () => {
      if (window.getSelection && String(window.getSelection()) !== "") { return; }
      setOpen(li, !li.classList.contains("open"), true);
    });
    li.addEventListener("input", () => { fillSummary(); applyFind(); });
    fillSummary();
    setOpen(li, opened.has(key), false);
    return li;
  }
  // 行を開く／畳む。persist が真なら控えも書く（利用者の操作だけ。描画のやり直しでは書かない）。
  function setOpen(li, on, persist) {
    const key = li.getAttribute("data-key");
    if (persist) { if (on) { opened.add(key); } else { opened.delete(key); } }
    li.classList.toggle("open", on);
    const twist = li.querySelector(".row-head > .twist");
    twist.textContent = on ? "▾" : "▸";
    twist.setAttribute("aria-expanded", on ? "true" : "false");
    if (persist) { persistOpen(); }
  }
  // 絞り込み。要約に含む文字で行を隠すだけで、種類の中身と並びには触らない。開いている行は隠さない。
  function applyFind() {
    const q = document.getElementById("find").value.trim().toLowerCase();
    for (const li of document.querySelectorAll("#phases .phase")) {
      li.classList.toggle("hidden-by-find", q !== "" && (li.getAttribute("data-find") || "").indexOf(q) < 0);
    }
    const shown = document.querySelectorAll("#phases .phase:not(.hidden-by-find)").length;
    const kept = document.querySelectorAll("#phases .phase.hidden-by-find.open").length;
    document.getElementById("phase-count").textContent = q === "" ? String(form.phases.length) : shown + " / " + form.phases.length + (kept > 0 ? "（開いたまま " + kept + "）" : "");
  }
  function upButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "↑", title: "上へ" });
    b.disabled = !page.editable;
    b.addEventListener("click", () => shift(key, -1));
    return b;
  }
  function downButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "↓", title: "下へ" });
    b.disabled = !page.editable;
    b.addEventListener("click", () => shift(key, 1));
    return b;
  }
  function deleteButton(key) {
    const b = h("button", { type: "button", class: "action small", text: "削除" });
    b.disabled = !page.editable;
    b.addEventListener("click", () => remove(key));
    return b;
  }
  function renderAll() {
    // 前に開いていた種類は id で覚えている。最初の描画でだけ鍵に写す。
    if (savedOpen.size > 0) {
      for (const phase of form.phases) { if (savedOpen.has(phase.id)) { opened.add(keyOf(phase)); } }
      savedOpen.clear();
    }
    const list = document.getElementById("phases");
    list.textContent = "";
    for (const phase of form.phases) { list.appendChild(renderPhase(phase)); }
    if (form.phases.length === 0) { list.appendChild(h("li", { class: "empty", text: page.exists ? "種類が無い。種類が 1 つも無いファイルは実行ファイルが読めないので、保存する前に足す" : page.editable ? "ファイルが無い（無い層は空で、共通層の種類だけが使われる）。種類を足して保存すると、ファイルが作られる" : "ファイルが無い。上の「雛形でファイルを作る」で作ってから直す" })); }
    document.getElementById("phase-count").textContent = String(form.phases.length);
    const add = document.querySelector("button[data-action=add]");
    if (add) { add.disabled = !page.editable; }
    applyFind();
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
    save.disabled = !dirty || lock.locked || busy || !page.editable || dup.size > 0;
    const lockEl = document.getElementById("lock");
    lockEl.textContent = lock.reason;
    lockEl.classList.toggle("hidden", !lock.locked);
    if (dup.size > 0) { status("id が重なっている（" + Array.from(dup).map((d) => d === "" ? "空" : d).join(", ") + "）。1 つにするまで保存できない", true); dupShown = true; }
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
    // 既定は inherit。scope: [] （何も書けない）の種類を、glob を埋め忘れただけで作らないため。
    const phase = { origin: null, id: "", title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], overlap: [], requires: [], agent: "", when: "" };
    form.phases = form.phases.concat([phase]);
    // 足した種類は開いて出す。畳んだままでは何を足したか分からない。
    opened.add(keyOf(phase));
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
  // 保存の往復（lint）の間に入れた編集は、保存が通ると再描画で消える。その間は欄ごと止める。
  function setBusy(on, text) {
    busy = on;
    for (const b of document.querySelectorAll("button[data-action=reload], button[data-action=create]")) { b.disabled = on; }
    for (const el of document.querySelectorAll("#phases .row-body input, #phases .row-body select, #phases .row-body button, button[data-action=add]")) { el.disabled = on || !page.editable; }
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
  document.getElementById("find").addEventListener("input", applyFind);
  renderAll();
${APPEARANCE_SCRIPT}`;
