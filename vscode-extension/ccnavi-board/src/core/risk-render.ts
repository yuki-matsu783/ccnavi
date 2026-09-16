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
import { APPEARANCE_SCRIPT, type Appearance, bodyTag } from "./appearance.js";
import { LIST_STYLE, PAGE_STYLE, escapeHtml } from "./render.js";
import { BUILTIN_LEVELS, KINDS, LEVEL_NAMES, type RiskModel } from "./risk-doc.js";

export interface RiskPage {
  readonly root: string;
  /** 配点のファイル（ワークスペースルートからの相対で見せる） */
  readonly riskPath: string;
  /** ファイルが在るか。無ければ組み込みの配点を見せ、「作る」だけができる */
  readonly exists: boolean;
  readonly model: RiskModel;
  readonly lock: Lock;
}

export interface RenderOptions {
  readonly nonce: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

/** 当て方の説明。select の札と、値の欄の placeholder */
export const KIND_LABELS: Readonly<Record<(typeof KINDS)[number], { readonly label: string; readonly placeholder: string }>> = {
  lines_over: { label: "差分の行数がしきい値を超えたら加点", placeholder: "300（追加と削除の合計がこれを超えたら加点）" },
  files_over: { label: "変えたファイル数がしきい値を超えたら加点", placeholder: "10（変えたファイルの数がこれを超えたら加点）" },
  deleted_over: { label: "消したファイル数がしきい値を超えたら加点", placeholder: "3（消したファイルの数がこれを超えたら加点）" },
  glob: { label: "glob にヒットしたファイルが 1 つあるごとに加点", placeholder: ".github/**（作業ツリーのルートからの相対。ヒットしたファイル 1 つごとに points を加点し、max が上限）" },
  script: { label: "スクリプトが出した点を加点", placeholder: ".ccnavi/common/scripts/xxx.sh（.ccnavi/common/scripts/ の下だけ。スクリプトが出した点を加点し、失敗や読めない出力なら points を加点）" },
  judge: { label: "サブエージェントの答えが yes だったら加点", placeholder: "テストの無い振る舞いの変更を含むか（差分を読んで yes / no で答えられる問い。yes で加点）" },
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
${bodyTag(options.appearance)}
<div id="changed" class="banner warn hidden">ファイルが外で変更されたので、画面の内容は古い。<button type="button" class="action" data-action="reload">再読込</button></div>
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
  <h2>段階の閾値 <span class="count">点がこの値以上になると段階が上がる。HIGH 以上でゲートが閉じる</span></h2>
  <details class="help"><summary>この欄の説明</summary><p class="hint">点がその値以上になると段階が上がる（LOW → MEDIUM → HIGH → CRITICAL）。<strong>HIGH 以上でフェーズのゲートが閉じ</strong>、宣言に関わらず人間レビューが要る扱いになる。medium ≤ high ≤ critical の順。空ならその段階は組み込みの値（${LEVEL_NAMES.map((n) => `${n} ${BUILTIN_LEVELS[n]}`).join(" / ")}）。</p></details>
  <div class="levels" id="levels"></div>
</section>
<section class="block">
  <h2>項目 <span class="count" id="factor-count">0</span>
    <button type="button" class="action small" data-action="add">＋ 項目を追加</button></h2>
  <div class="find">
    <input id="find" type="search" placeholder="id・当て方・値・文面で絞り込む" spellcheck="false">
    <span class="hint">行を押すと開く</span>
  </div>
  <details class="help"><summary>この欄の説明</summary><p class="hint">子を閉じるとき、その子の差分（base_sha..HEAD）に当てて加点する。1 件につき当て方は 1 つ。点の合計で段階が決まり、フェーズの点は子の最大値。<code>script</code> が失敗したときと出力が読めないときは安全側に倒して points をそのまま加点し、<code>judge</code> は判定が揃うまで子を閉じられない。</p></details>
  <ul class="list" id="factors"></ul>
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

function renderMissing(page: RiskPage): string {
  if (page.exists) {
    return "";
  }
  return `<div class="banner missing"><span>${escapeHtml(page.riskPath)} が無い。実行ファイルは組み込みの配点で数えている（画面の値はその組み込みの配点）。直すにはまずファイルを作る。</span><button type="button" class="action primary" data-action="create">組み込みの配点でファイルを作る</button></div>\n`;
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
  .block { border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; }
  .block h2 { display: flex; gap: 8px; align-items: center; }
  .levels { display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; }
  .levels .field { display: flex; flex-direction: row; gap: 6px; align-items: center; }
  .levels .field > .cap { text-align: left; }
  .levels .field > input { width: 90px; }
${LIST_STYLE}
  /* 1 行 = 開閉、id、points、当て方と値と文面 */
  .factor .row-head { grid-template-columns: 18px minmax(110px, 160px) 60px minmax(0, 1fr); }
  .sum .sum-points { text-align: right; font-variant-numeric: tabular-nums; }
  .field > select.f-kind { max-width: 300px; }
`;

// 中のスクリプトはテンプレート文字列に埋めるので、バッククォートと \${ を使わない。
const SCRIPT = `  const vscode = acquireVsCodeApi();
  const page = JSON.parse(document.getElementById("page").textContent);
  const LEVELS = ["medium", "high", "critical"];
  const NUMERIC = ["lines_over", "files_over", "deleted_over"];
  const KINDS_KEYS = page.kinds.map((k) => k.kind).join(" / ");
  const form = page.form;
  let lock = page.lock;
  let dirty = false;
  let busy = false;
  let seq = 0;
  const keys = new Map();
  // 開いている項目の鍵。既定は全部畳む。Webview の state には id で控え、再読込のあとも同じ項目が開く。
  const opened = new Set();
  const savedOpen = new Set((vscode.getState() || {}).open || []);
  function persistOpen() {
    const ids = [];
    for (const key of opened) {
      const factor = factorByKey(key);
      if (factor && factor.id !== "") { ids.push(factor.id); }
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
  // 欄名は日本語で欄の左に出す。YAML のキー名は欄名の title（ツールチップ）に載せる。
  function captioned(name, control, className, key) {
    const cap = h("span", { class: "cap", text: name });
    if (key) { cap.setAttribute("title", "YAML のキー: " + key); }
    return h("div", { class: "field " + (className || "") }, [cap, control]);
  }
  // 要約の行に出す、当て方と値をつないだ文。読んで意味が通る語順にする
  function describe(factor) {
    const v = factor.value;
    const code = (t) => h("code", { text: t });
    const dim = (t) => h("span", { class: "dim", text: t });
    if (v === "") { return [dim("（" + valueLabel(factor.kind) + " 未設定）")]; }
    if (factor.kind === "lines_over") { return [dim("差分が "), code(v), dim(" 行を超えたら加点")]; }
    if (factor.kind === "files_over") { return [dim("変えたファイルが "), code(v), dim(" 件を超えたら加点")]; }
    if (factor.kind === "deleted_over") { return [dim("消したファイルが "), code(v), dim(" 件を超えたら加点")]; }
    if (factor.kind === "glob") { return [code(v), dim(" にヒットしたファイルが 1 つあるごとに加点" + (factor.max !== "" ? "（上限 " + factor.max + " 点）" : ""))]; }
    if (factor.kind === "script") { return [dim("スクリプト "), code(v), dim(" が出した点を加点（測れなければ " + (factor.points === "" ? "points" : factor.points + " 点") + "）")]; }
    return [dim("問い「"), code(v), dim("」に yes だったら加点")];
  }
  // 値の欄の名前。当て方で意味が変わる
  function valueLabel(kind) {
    if (kind === "glob") { return "glob"; }
    if (kind === "script") { return "スクリプト"; }
    if (kind === "judge") { return "問い"; }
    return "しきい値";
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
      input.setAttribute("title", name.toUpperCase() + " 以上になる点");
      box.appendChild(captioned(name.toUpperCase(), input, "", "levels." + name));
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
    const points = field(factor, "points", "f-points num", "25");
    points.setAttribute("inputmode", "numeric");
    const value = field(factor, "value", "f-value", info.placeholder);
    if (NUMERIC.indexOf(factor.kind) >= 0) { value.setAttribute("inputmode", "numeric"); }
    const twist = h("button", { type: "button", class: "twist", title: "この項目を開く／畳む" });
    const sum = h("span", { class: "sum" });
    const head = h("div", { class: "row-head" }, [twist, sum]);
    const li = h("li", { class: "row factor", "data-key": key }, [
      head,
      h("div", { class: "row-body" }, [
        captioned("id", field(factor, "id", "f-id narrow", "big-diff"), "", "id"),
        captioned("点", points, "", "points"),
        captioned("当て方", kindSelect, "", KINDS_KEYS),
        captioned(valueLabel(factor.kind), factor.kind === "glob"
          ? h("div", { class: "inline" }, [value, h("span", { class: "cap", text: "上限", title: "YAML のキー: max" }), field(factor, "max", "f-max num", "上限。空なら上限なし")])
          : value, "", factor.kind),
        captioned("文面", field(factor, "message", "f-message", "加点の理由として依頼文と閉じたときの出力に出る短い文。空なら id をそのまま使う"), "", "message"),
        h("span", { class: "buttons" }, [upButton(key), downButton(key), deleteButton(key)]),
      ]),
    ]);
    function fillSummary() {
      sum.textContent = "";
      sum.appendChild(h("span", { class: "sum-id" + (factor.id === "" ? " dim" : ""), text: factor.id === "" ? "（id 未設定）" : factor.id }));
      sum.appendChild(h("span", { class: "sum-points" + (factor.points === "" ? " dim" : ""), text: factor.points === "" ? "—" : factor.points + " 点", title: "points" }));
      sum.appendChild(h("span", { class: "clip", title: kindInfo(factor.kind).label + (factor.value === "" ? "" : ": " + factor.value) }, describe(factor).concat([
        factor.message === "" ? null : h("span", { class: "sum-note", text: factor.message }),
      ])));
      // 画面に出ている語（当て方の札と要約の文）でも、キーの綴り（lines_over など）でも当たる
      li.setAttribute("data-find", (factor.id + " " + factor.kind + " " + kindInfo(factor.kind).label + " " + sum.textContent).toLowerCase());
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
  // 絞り込み。要約に含む文字で行を隠すだけで、項目の中身と並びには触らない。開いている行は隠さない。
  function applyFind() {
    const q = document.getElementById("find").value.trim().toLowerCase();
    for (const li of document.querySelectorAll("#factors .factor")) {
      li.classList.toggle("hidden-by-find", q !== "" && (li.getAttribute("data-find") || "").indexOf(q) < 0);
    }
    const shown = document.querySelectorAll("#factors .factor:not(.hidden-by-find)").length;
    const kept = document.querySelectorAll("#factors .factor.hidden-by-find.open").length;
    document.getElementById("factor-count").textContent = q === "" ? String(form.factors.length) : shown + " / " + form.factors.length + (kept > 0 ? "（開いたまま " + kept + "）" : "");
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
    // 前に開いていた項目は id で覚えている。最初の描画でだけ鍵に写す。
    if (savedOpen.size > 0) {
      for (const factor of form.factors) { if (savedOpen.has(factor.id)) { opened.add(keyOf(factor)); } }
      savedOpen.clear();
    }
    const list = document.getElementById("factors");
    list.textContent = "";
    for (const factor of form.factors) { list.appendChild(renderFactor(factor)); }
    if (form.factors.length === 0) { list.appendChild(h("li", { class: "empty", text: "項目が無い。加点する項目が無ければ、どの子も LOW のまま閉じる" })); }
    document.getElementById("factor-count").textContent = String(form.factors.length);
    const add = document.querySelector("button[data-action=add]");
    if (add) { add.disabled = !page.exists; }
    applyFind();
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
    const factor = { origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", message: "" };
    form.factors = form.factors.concat([factor]);
    // 足した項目は開いて出す。畳んだままでは何を足したか分からない。
    opened.add(keyOf(factor));
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
    for (const el of document.querySelectorAll("#levels input, #factors .row-body input, #factors .row-body select, #factors .row-body button, button[data-action=add]")) { el.disabled = on || !page.exists; }
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
  document.getElementById("find").addEventListener("input", applyFind);
  renderAll();
  updateSave();
${APPEARANCE_SCRIPT}`;
