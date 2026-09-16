/**
 * プロジェクト管理画面を外部資源に依存しない 1 枚の HTML に組み立てる。
 * 色は VS Code のテーマ変数だけを使う。文字列は全部実体参照にする。
 *
 * 画面が持つ状態は clone の入力欄だけで、一覧は読み直すたびに HTML ごと差し替える。
 * 入力欄の途中は Webview の state に控え、差し替え後に戻す。
 */
import type { LintProblem } from "./lintmodel.js";
import type { ProjectRow, ProjectsPage, Stray } from "./projects.js";
import { APPEARANCE_SCRIPT, type Appearance, bodyTag } from "./appearance.js";
import { PAGE_STYLE, escapeHtml } from "./render.js";

export interface RenderOptions {
  readonly nonce: string;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
}

export function renderProjectsPage(page: ProjectsPage, options: RenderOptions): string {
  const { nonce } = options;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi プロジェクト管理</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
${bodyTag(options.appearance)}
<header class="toolbar">
  <div class="summary">
    <span>プロジェクト ${page.rows.length} 件</span>
    <span class="path" title="${escapeHtml(page.projectsDir)}">置き場: ${escapeHtml(page.projectsRel === "" ? "（無効）" : `${page.projectsRel}/`)}</span>
  </div>
  <div class="controls">
    <button type="button" class="action" data-action="open-rules" data-name="" title="共通層のルール（どのツリーにも効く。既定 .ccnavi/common/rules.yml）を編集し、判定を試す">ルール管理</button>
${page.ticketsEnabled ? '    <button type="button" class="action" data-action="open-board" data-name="*">チケット管理</button>\n' : ""}    <button type="button" class="action" data-action="refresh">更新</button>
  </div>
</header>
${renderBanners(page)}<section class="clone">
  <h2>リポジトリを clone する</h2>
  <div class="clone-form">
    <label class="grow">URL <input id="url" type="text" placeholder="https://gitlab.example.com/group/repo.git" spellcheck="false"></label>
    <label>名前 <input id="name" type="text" placeholder="URL から自動で入る" spellcheck="false"></label>
    <button type="button" class="action primary" data-action="clone" title="git clone を「ccnavi」ターミナルで実行する。認証が要るならターミナルで入れる">clone</button>
  </div>
  <p id="status" class="status hidden"></p>
</section>
<section class="list">
  <h2>ワークスペース内のプロジェクト <span class="count">${page.rows.length}</span></h2>
${page.rows.length === 0 ? '  <p class="empty">プロジェクトはまだ無い。上の欄から clone するか、既存のリポジトリを置き場（無ければ作る）の直下へ移す</p>' : renderList(page)}
</section>
${renderStrays(page.strays)}<section class="workspace">
  <h2>ワークスペース本体</h2>
  <p class="hint"><span class="mono">${escapeHtml(page.root)}</span>（作業ツリー ${page.workspaceWorktrees.length} 件${page.workspaceWorktrees.length > 0 ? `: ${escapeHtml(page.workspaceWorktrees.join(", "))}` : ""}）</p>
${renderSelfRules(page)}</section>
<footer class="foot">最終更新 ${escapeHtml(page.generatedAt)}（${escapeHtml(page.root)}）</footer>
<script nonce="${nonce}">
${SCRIPT}
</script>
</body>
</html>
`;
}

function renderBanners(page: ProjectsPage): string {
  const banners: string[] = [];
  if (page.lintError !== "") {
    banners.push(`<div class="banner warn">検証結果を取得できなかったので、プロジェクトごとの検証結果は出せない。${escapeHtml(page.lintError)}</div>`);
  }
  if (page.projectsRel === "") {
    banners.push(`<div class="banner warn">置き場が無効（CCNAVI_PROJECTS が空）。clone してもプロジェクトとして扱われない</div>`);
    return `${banners.join("\n")}\n`;
  }
  if (!page.ignored) {
    banners.push(
      `<div class="banner warn"><code>.gitignore</code> に <code>/${escapeHtml(page.projectsRel)}/</code> が無い。各プロジェクトは自分の git リポジトリを持つので、ワークスペースの git からは除外する。<button type="button" class="action" data-action="fix-ignore">.gitignore に追加</button></div>`,
    );
  }
  for (const p of page.dirProblems) {
    // .gitignore の帯（直すボタン付き）と同じ事象は 2 度出さない
    if (!page.ignored && /無視されていない/.test(p.detail)) {
      continue;
    }
    banners.push(`<div class="banner ${p.severity}">${escapeHtml(p.severity)}: ${escapeHtml(p.detail)}</div>`);
  }
  return banners.length === 0 ? "" : `${banners.join("\n")}\n`;
}

/**
 * 一覧は表ではなく 1 プロジェクト 1 枚のカード。表だと列が 7 本になり、幅が足りないと検証や
 * チケットの列が 1 文字ずつ縦に潰れる。カードの中の項目は幅に合わせて 1〜3 段に組み替わる。
 */
function renderList(page: ProjectsPage): string {
  const items = page.rows.map((r) => renderProject(r, page.ticketsEnabled)).join("\n");
  return `  <ul class="projects">\n${items}\n  </ul>`;
}

/**
 * ワークスペース自身の層のルール。無いのは正常なので warn の色は使わない。
 * フェーズの種類の行は、チケット制御が disable なら出さない（種類はチケットにしか読まれない）。
 */
function renderSelfRules(page: ProjectsPage): string {
  const rel = `<span class="mono small">${escapeHtml(page.selfRulesRel)}</span>`;
  const state = page.selfRulesExists
    ? `<span class="ok">あり</span> ${rel}`
    : `<span class="dim">なし</span> ${rel} <button type="button" class="action small" data-action="create-self-rules" title="共通層の rules.yml を自身の層にコピーする。文面の sh のパスは {root} 付きに置き換える">共通層からコピー</button>`;
  const selfPhases = page.ticketsEnabled
    ? `  <div class="self-rules"><span>自身の層のフェーズの種類</span> <button type="button" class="action small" data-action="open-self-phases" title="ワークスペース自身のチケット（project: が空）の計画に、共通層に足して使う種類を編集する。無ければ画面から作れる">フェーズ管理</button></div>\n`
    : "";
  return `  <div class="self-rules"><span>自身の層のルール</span> ${state} <button type="button" class="action small" data-action="open-self-rules" ${page.selfRulesExists ? "" : "disabled "}title="ワークスペース自身のツリーへの書き込みと、全ツリーの Bash に足して当たるルールを編集し、判定を試す">ルール管理</button></div>
${selfPhases}`;
}

function renderRules(row: ProjectRow): string {
  return row.rulesRel === ""
      ? '<span class="dim">層として数えられていない（検証の error を見る）</span>'
      : row.rulesExists
        ? `<span class="ok">あり</span> <span class="mono small">${escapeHtml(row.rulesRel)}</span>`
        : `<span class="warn-text">なし</span> <span class="mono small dim">${escapeHtml(row.rulesRel)}</span> <button type="button" class="action small" data-action="create-rules" data-name="${escapeHtml(row.name)}" title="共通層の rules.yml をこのプロジェクトの層にコピーする。文面の sh のパスは {root} 付きに置き換える">共通層からコピー</button>`;
}

function renderProject(row: ProjectRow, ticketsEnabled: boolean): string {
  const rules = renderRules(row);
  const worktrees = row.worktrees.length === 0 ? '<span class="dim">なし</span>' : `${row.worktrees.length} 件 <span class="small dim">${escapeHtml(row.worktrees.join(", "))}</span>`;
  const tickets = ticketsEnabled
    ? `\n          <div class="field"><dt>チケット</dt><dd>${row.tickets} 件${row.doing > 0 ? `<span class="dim">、作業中 ${row.doing} 件</span>` : ""}</dd></div>`
    : "";
  // .claude/ があることは説明付きの 1 行で言う。lint の同じ指摘（ccnavi/lint.py の文面「.claude/ を持つ。…」）は
  // 重ねない。".claude/settings.json を読めない" のような別の指摘まで消さないよう、文面の先頭で当てる。
  const problems = [
    ...(row.hasClaudeDir ? [{ severity: "warn" as const, where: "", detail: `.claude/ がある。Claude Code はそこにあるスキルを読み込み、cd するとそこが別のワークスペースルートに見える。プロジェクトの設定は ${settingsDir(row)}/ に置く` }] : []),
    ...row.problems.filter((p) => !(row.hasClaudeDir && p.detail.startsWith(".claude/ を持つ"))),
  ];
  const lint = problems.length === 0 ? '<span class="ok">問題なし</span>' : renderProblems(problems);
  const origin = row.origin === "" ? '<span class="dim">不明</span>' : `<span class="mono small" title="${escapeHtml(row.origin)}">${escapeHtml(row.origin)}</span>`;
  const board = ticketsEnabled
    ? `\n          <button type="button" class="action" data-action="open-board" data-name="${escapeHtml(row.name)}" title="このプロジェクトに絞ってチケット管理を開く">チケット管理</button>`
    : "";
  // フェーズの種類は親チケットの計画と子の範囲にしか読まれない。チケット制御が disable の間は
  // 何も動かさないので、開く側（phases-panel）と揃えて入口を出さない。
  const phases = ticketsEnabled
    ? `\n          <button type="button" class="action" data-action="open-phases" data-name="${escapeHtml(row.name)}" ${row.rulesRel === "" ? "disabled " : ""}title="このプロジェクトのチケットの計画に、共通層に足して使うフェーズの種類を編集する。無ければ画面から作れる">フェーズ管理</button>`
    : "";
  const flags = [
    row.doing > 0 ? `<span class="badge doing">作業中 ${row.doing}</span>` : "",
    problems.some((p) => p.severity === "error") ? '<span class="badge error">error</span>' : "",
    problems.some((p) => p.severity === "warn") ? '<span class="badge warn">warn</span>' : "",
  ].filter((f) => f !== "");
  return `    <li class="project${problems.length > 0 ? " has-problem" : ""}" data-name="${escapeHtml(row.name)}">
      <div class="project-head">
        <span class="name mono">${escapeHtml(row.name)}</span>
        <span class="rel small dim">${escapeHtml(row.rel)}</span>${flags.length > 0 ? `\n        <span class="flags">${flags.join(" ")}</span>` : ""}
      </div>
      <dl class="fields">
          <div class="field"><dt>origin</dt><dd>${origin}</dd></div>
          <div class="field"><dt>ルール</dt><dd>${rules}</dd></div>
          <div class="field"><dt>作業ツリー</dt><dd>${worktrees}</dd></div>${tickets}
          <div class="field wide"><dt>検証</dt><dd>${lint}</dd></div>
      </dl>
      <div class="ops">
        <details class="menu">
          <summary class="action">開く ▾</summary>
          <div class="menu-items">
          <button type="button" class="action" data-action="open-rules" data-name="${escapeHtml(row.name)}" ${row.rulesExists ? "" : "disabled "}title="このプロジェクトの ${escapeHtml(row.rulesRel === "" ? "層のルール" : row.rulesRel)} を編集し、判定を試す">ルール管理</button>${phases}${board}
          </div>
        </details>
        <details class="menu">
          <summary class="action">git ▾</summary>
          <div class="menu-items">
          <button type="button" class="action" data-action="fetch" data-name="${escapeHtml(row.name)}" title="git fetch をターミナルで実行する">fetch</button>
          <button type="button" class="action" data-action="pull" data-name="${escapeHtml(row.name)}" title="git pull をターミナルで実行する。衝突があれば git が止める">pull</button>
          </div>
        </details>
      </div>
    </li>`;
}

/** 層の設定の置き場（プロジェクトのルートからの相対）。層のルールの置き場から逆算し、無ければ既定 */
function settingsDir(row: ProjectRow): string {
  const prefix = `${row.rel}/`;
  const inside = row.rulesRel.startsWith(prefix) ? row.rulesRel.slice(prefix.length) : "";
  const cut = inside.lastIndexOf("/");
  return cut < 0 ? ".ccnavi/config" : inside.slice(0, cut);
}

function renderProblems(problems: readonly LintProblem[]): string {
  const items = problems
    .map((p) => `<li class="${p.severity}">${escapeHtml(p.severity)}: ${escapeHtml(p.detail)}</li>`)
    .join("");
  return `<ul class="lint">${items}</ul>`;
}

function renderStrays(strays: readonly Stray[]): string {
  if (strays.length === 0) {
    return "";
  }
  const items = strays
    .map((s) => `    <li><span class="mono">${escapeHtml(s.path)}</span> <span class="dim">${escapeHtml(s.reason)}</span></li>`)
    .join("\n");
  return `<section class="strays">
  <h2>プロジェクトとして認識されない git リポジトリ <span class="count">${strays.length}</span></h2>
  <p class="hint">ワークスペース直下から 2 階層までを探して見つかったもの（node_modules、.venv、.claude の中は探さない）。プロジェクトとして扱うには <code>projects/</code> の直下へ移す。この画面からは操作できない。</p>
  <ul class="stray-list">
${items}
  </ul>
</section>
`;
}

const STYLE = `${PAGE_STYLE}
  section { margin-bottom: 18px; }
  .summary { font-weight: 600; }
  .summary .path { font-weight: 400; }
  .ok { color: var(--vscode-charts-green); }
  .warn-text { color: var(--vscode-editorWarning-foreground); }
  .clone-form input[type=text] { width: 100%; }
  .clone-form { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
  .clone-form label { display: flex; flex-direction: column; gap: 2px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  .clone-form label.grow { flex: 1 1 320px; }
  .clone-form label:not(.grow) { flex: 0 1 200px; }
  .status { margin: 8px 0 0; padding: 6px 10px; border-radius: 4px; border: 1px solid var(--vscode-panel-border); overflow-wrap: anywhere; }
  .status.failed { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .projects { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .project {
    border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px 10px;
    background: var(--vscode-editorWidget-background);
  }
  .project.has-problem { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  .project-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; margin-bottom: 6px; }
  .project-head .name { font-weight: 600; font-size: 1.05em; overflow-wrap: anywhere; }
  .project-head .rel { overflow-wrap: anywhere; }
  .project-head .flags { display: flex; flex-wrap: wrap; gap: 4px; margin-left: auto; }
  /* 項目は 1 つ 240px を目安に、幅に合わせて段数が変わる。検証は長くなるので常に 1 段まるごと使う */
  .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 6px 16px; margin: 0; }
  .field { min-width: 0; }
  .field.wide { grid-column: 1 / -1; }
  .field dt { font-size: .85em; color: var(--vscode-descriptionForeground); }
  .field dd { margin: 0; overflow-wrap: anywhere; }
  .field dd button.action { vertical-align: middle; }
  .ops { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--vscode-panel-border); }
  /* 行末のボタンは 2 つのメニューにまとめる。開いたメニューは外を押すか Esc で閉じる */
  .menu { position: relative; }
  .menu > summary {
    list-style: none; display: inline-flex; align-items: center; min-height: 24px; padding: 2px 12px; line-height: 1.3;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-button-border, rgba(128, 128, 128, .4)); border-radius: 3px;
    box-shadow: 0 1px 1px rgba(0, 0, 0, .25); cursor: pointer; user-select: none;
  }
  .menu > summary:hover, .menu[open] > summary { background: var(--vscode-button-secondaryHoverBackground); border-color: var(--vscode-focusBorder); }
  .menu > summary:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
  .menu > summary::-webkit-details-marker { display: none; }
  .menu > .menu-items {
    position: absolute; z-index: 5; top: 100%; left: 0; margin-top: 2px; min-width: 100%;
    display: flex; flex-direction: column; gap: 2px; padding: 4px;
    background: var(--vscode-editorWidget-background); border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 3px; box-shadow: 0 2px 8px var(--vscode-widget-shadow, rgba(0, 0, 0, .3));
  }
  .menu > .menu-items > button.action { justify-content: flex-start; border-color: var(--vscode-contrastBorder, transparent); box-shadow: none; background: none; }
  .menu > .menu-items > button.action:hover:not(:disabled) { background: var(--vscode-list-hoverBackground); }
  /* HC では押せない項目も点線の枠で「在るが押せない」と分かるようにする */
  .menu > .menu-items > button.action:disabled { border-style: dashed; }
  .badge { font-size: .82em; padding: 0 6px; border-radius: 999px; border: 1px solid var(--vscode-panel-border); white-space: nowrap; }
  .badge.doing { color: var(--vscode-charts-yellow); border-color: var(--vscode-charts-yellow); }
  .badge.warn { color: var(--vscode-editorWarning-foreground); border-color: var(--vscode-editorWarning-foreground); }
  .badge.error { color: var(--vscode-editorError-foreground); border-color: var(--vscode-editorError-foreground); }
  .lint { list-style: none; margin: 0; padding: 0; font-size: .88em; }
  .lint li { overflow-wrap: anywhere; }
  .lint li.warn { color: var(--vscode-editorWarning-foreground); }
  .lint li.error { color: var(--vscode-editorError-foreground); }
  .self-rules { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; overflow-wrap: anywhere; }
  .stray-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
`;

const SCRIPT = `  const vscode = acquireVsCodeApi();
  const url = document.getElementById("url");
  const name = document.getElementById("name");
  const status = document.getElementById("status");
  let nameTouched = false;
  const saved = vscode.getState();
  if (saved && typeof saved.url === "string") { url.value = saved.url; }
  if (saved && typeof saved.name === "string") { name.value = saved.name; nameTouched = saved.nameTouched === true; }
  function remember() { vscode.setState({ url: url.value, name: name.value, nameTouched: nameTouched }); }
  function guessName(text) {
    const trimmed = text.trim().replace(/\\/+$/, "").replace(/\\.git$/i, "");
    const tail = trimmed.split(/[\\/:]/).pop() || "";
    return tail;
  }
  url.addEventListener("input", () => {
    if (!nameTouched) { name.value = guessName(url.value); }
    remember();
  });
  name.addEventListener("input", () => { nameTouched = name.value !== ""; remember(); });
  function show(kind, message) {
    status.textContent = message;
    status.classList.remove("hidden", "failed");
    if (kind === "failed") { status.classList.add("failed"); }
  }
  for (const button of document.querySelectorAll("button[data-action]")) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const action = button.getAttribute("data-action");
      const target = button.getAttribute("data-name") || "";
      if (action === "refresh") { vscode.postMessage({ type: "refresh" }); }
      else if (action === "clone") { vscode.postMessage({ type: "clone", url: url.value, name: name.value }); }
      else if (action === "fix-ignore") { vscode.postMessage({ type: "fixIgnore" }); }
      else if (action === "create-rules") { vscode.postMessage({ type: "createRules", name: target }); }
      else if (action === "open-rules") { vscode.postMessage({ type: "openRules", name: target }); }
      else if (action === "create-self-rules") { vscode.postMessage({ type: "createSelfRules" }); }
      else if (action === "open-self-rules") { vscode.postMessage({ type: "openSelfRules" }); }
      else if (action === "open-phases") { vscode.postMessage({ type: "openPhases", name: target }); }
      else if (action === "open-self-phases") { vscode.postMessage({ type: "openSelfPhases" }); }
      else if (action === "open-board") { vscode.postMessage({ type: "openBoard", name: target }); }
      else if (action === "fetch") { vscode.postMessage({ type: "fetch", name: target }); }
      else if (action === "pull") { vscode.postMessage({ type: "pull", name: target }); }
    });
  }
  // メニューは 1 つだけ開く。項目を押したら閉じ、外を押すか Esc でも閉じる
  function closeMenus(except) {
    for (const menu of document.querySelectorAll("details.menu[open]")) { if (menu !== except) { menu.removeAttribute("open"); } }
  }
  for (const menu of document.querySelectorAll("details.menu")) {
    menu.addEventListener("toggle", () => { if (menu.open) { closeMenus(menu); } });
    for (const button of menu.querySelectorAll("button")) { button.addEventListener("click", () => closeMenus()); }
  }
  document.addEventListener("mousedown", (event) => {
    const inside = event.target.closest ? event.target.closest("details.menu") : null;
    closeMenus(inside);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeMenus(); } });
  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "failed") { show("failed", String(data.message || "")); }
    else if (data.type === "info") { show("info", String(data.message || "")); }
    else if (data.type === "cloned") { url.value = ""; name.value = ""; nameTouched = false; remember(); show("info", String(data.message || "")); }
  });
${APPEARANCE_SCRIPT}`;
