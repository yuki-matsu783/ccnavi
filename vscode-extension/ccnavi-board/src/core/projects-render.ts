/**
 * プロジェクト管理画面を外部資源に依存しない 1 枚の HTML に組み立てる。
 * 色は VS Code のテーマ変数だけを使う。文字列は全部実体参照にする。
 *
 * 画面が持つ状態は clone の入力欄だけで、一覧は読み直すたびに HTML ごと差し替える。
 * 入力欄の途中は Webview の state に控え、差し替え後に戻す。
 */
import type { LintProblem } from "./lintmodel.js";
import type { ProjectRow, ProjectsPage, Stray } from "./projects.js";
import { BUTTON_STYLE, escapeHtml } from "./render.js";

export interface RenderOptions {
  readonly nonce: string;
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
<body>
<header class="toolbar">
  <div class="summary">
    <span>プロジェクト ${page.rows.length} 件</span>
    <span class="path" title="${escapeHtml(page.projectsDir)}">置き場 ${escapeHtml(page.projectsRel === "" ? "（無効）" : `${page.projectsRel}/`)}</span>
  </div>
  <div class="controls">
    <button type="button" class="action" data-action="open-rules" data-name="" title="ワークスペースのルール（.claude/ccnavi/rules.yml）を直す">ルール管理</button>
${page.ticketsEnabled ? '    <button type="button" class="action" data-action="open-board" data-name="*">チケット管理</button>\n' : ""}    <button type="button" class="action" data-action="refresh">更新</button>
  </div>
</header>
${renderBanners(page)}<section class="clone">
  <h2>git プロジェクトを clone する</h2>
  <div class="clone-form">
    <label class="grow">URL <input id="url" type="text" placeholder="https://gitlab.example.com/group/repo.git" spellcheck="false"></label>
    <label>名前 <input id="name" type="text" placeholder="URL の末尾" spellcheck="false"></label>
    <button type="button" class="action primary" data-action="clone" title="git clone を「ccnavi」ターミナルへ送る。認証の対話はそこで">clone</button>
  </div>
  <p id="status" class="status hidden"></p>
</section>
<section class="list">
  <h2>ワークスペース内のプロジェクト <span class="count">${page.rows.length}</span></h2>
${page.rows.length === 0 ? '  <p class="empty">まだ無い。上の欄から clone するか、既存のリポジトリを置き場の直下へ移す</p>' : renderList(page)}
</section>
${renderStrays(page.strays)}<section class="workspace">
  <h2>ワークスペース自身</h2>
  <p class="hint"><span class="mono">${escapeHtml(page.root)}</span> / 作業ツリー ${page.workspaceWorktrees.length} 件${page.workspaceWorktrees.length > 0 ? `（${escapeHtml(page.workspaceWorktrees.join(", "))}）` : ""}</p>
</section>
<footer class="foot">取得 ${escapeHtml(page.generatedAt)} / ${escapeHtml(page.root)}</footer>
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
    banners.push(`<div class="banner warn"><code>--lint --json</code> の結果を読めなかったので、プロジェクトごとの検証結果は出せない: ${escapeHtml(page.lintError)}</div>`);
  }
  if (page.projectsRel === "") {
    banners.push(`<div class="banner warn">置き場が無効（CCNAVI_PROJECTS が空）。clone してもプロジェクトとして扱われない</div>`);
    return `${banners.join("\n")}\n`;
  }
  if (!page.projectsDirExists) {
    banners.push(
      `<div class="banner"><code>${escapeHtml(page.projectsRel)}/</code> がまだ無い。<button type="button" class="action" data-action="create-dir">作る</button> clone すれば git が作るので、無くても clone はできる</div>`,
    );
  }
  if (!page.ignored) {
    banners.push(
      `<div class="banner warn"><code>.gitignore</code> に <code>/${escapeHtml(page.projectsRel)}/</code> が無い。プロジェクトは自分の git を持つので、ワークスペースの git では無視する。<button type="button" class="action" data-action="fix-ignore">.gitignore に足す</button></div>`,
    );
  }
  for (const p of page.dirProblems) {
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

function renderProject(row: ProjectRow, ticketsEnabled: boolean): string {
  const rules = row.rulesExists
    ? `<span class="ok">あり</span> <span class="mono small">${escapeHtml(row.rulesRel)}</span>`
    : `<span class="warn-text">無い</span> <button type="button" class="action small" data-action="create-rules" data-name="${escapeHtml(row.name)}" title="ワークスペースの rules.yml を写す。文面の sh の綴りを {root} 付きに置き換える">ワークスペースから写す</button>`;
  const worktrees = row.worktrees.length === 0 ? '<span class="dim">無し</span>' : `${row.worktrees.length} 件 <span class="small dim">${escapeHtml(row.worktrees.join(", "))}</span>`;
  const tickets = ticketsEnabled
    ? `\n          <div class="field"><dt>チケット</dt><dd>${row.tickets} 件${row.doing > 0 ? `<span class="dim">、作業中 ${row.doing} 件</span>` : ""}</dd></div>`
    : "";
  const problems = [
    ...(row.hasClaudeDir ? [{ severity: "warn" as const, where: "", detail: ".claude/ を持つ。Claude Code がそこのスキルを読み、cd 1 回で別のルートに見える" }] : []),
    ...row.problems,
  ];
  const lint = problems.length === 0 ? '<span class="ok">問題なし</span>' : renderProblems(problems);
  const origin = row.origin === "" ? '<span class="dim">読めない</span>' : `<span class="mono small" title="${escapeHtml(row.origin)}">${escapeHtml(row.origin)}</span>`;
  const board = ticketsEnabled
    ? `\n          <button type="button" class="action" data-action="open-board" data-name="${escapeHtml(row.name)}" title="チケット管理をこのプロジェクトで絞って開く">チケット管理</button>`
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
          <button type="button" class="action" data-action="open-rules" data-name="${escapeHtml(row.name)}" ${row.rulesExists ? "" : "disabled "}title="このプロジェクトの config/rules.yml を直し、判定を試す">ルール管理</button>${board}
          <button type="button" class="action" data-action="fetch" data-name="${escapeHtml(row.name)}" title="git fetch をターミナルへ送る">fetch</button>
          <button type="button" class="action" data-action="pull" data-name="${escapeHtml(row.name)}" title="git pull をターミナルへ送る。衝突すれば git が止める">pull</button>
      </div>
    </li>`;
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
  <h2>プロジェクトになっていない .git <span class="count">${strays.length}</span></h2>
  <p class="hint">ワークスペース直下を深さ 2 まで歩いて見つけたもの（node_modules、.venv、.claude の中は歩かない）。プロジェクトにするには <code>projects/</code> の直下へ移す。ここからは操作できない。</p>
  <ul class="stray-list">
${items}
  </ul>
</section>
`;
}

const STYLE = `  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  h2 { margin: 0 0 8px; font-size: 1em; }
  section { margin-bottom: 18px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 0 4px 12px; }
  .summary { display: flex; flex-wrap: wrap; gap: 4px 16px; font-weight: 600; }
  .summary .path { font-weight: 400; color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-left: auto; }
  .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .hint { margin: 0 0 8px; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .empty { margin: 0; color: var(--vscode-descriptionForeground); }
  .mono { font-family: var(--vscode-editor-font-family); }
  .small { font-size: .85em; }
  .dim { color: var(--vscode-descriptionForeground); }
  .ok { color: var(--vscode-charts-green); }
  .warn-text { color: var(--vscode-editorWarning-foreground); }
  code { font-family: var(--vscode-editor-font-family); }
${BUTTON_STYLE}
  input[type=text] {
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 3px;
    min-height: 24px; padding: 2px 6px; font: inherit; width: 100%;
  }
  input[type=text]:focus { outline: 1px solid var(--vscode-focusBorder); outline-offset: -1px; }
  .banner {
    margin: 0 0 8px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-panel-border); display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  }
  .banner.warn { border-color: var(--vscode-editorWarning-foreground); color: var(--vscode-editorWarning-foreground); }
  .banner.error { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .clone-form { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
  .clone-form label { display: flex; flex-direction: column; gap: 2px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  .clone-form label.grow { flex: 1 1 320px; }
  .clone-form label:not(.grow) { flex: 0 1 200px; }
  .status { margin: 8px 0 0; padding: 6px 10px; border-radius: 4px; border: 1px solid var(--vscode-panel-border); overflow-wrap: anywhere; }
  .status.failed { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .hidden { display: none; }
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
  .badge { font-size: .82em; padding: 0 6px; border-radius: 999px; border: 1px solid var(--vscode-panel-border); white-space: nowrap; }
  .badge.doing { color: var(--vscode-charts-yellow); border-color: var(--vscode-charts-yellow); }
  .badge.warn { color: var(--vscode-editorWarning-foreground); border-color: var(--vscode-editorWarning-foreground); }
  .badge.error { color: var(--vscode-editorError-foreground); border-color: var(--vscode-editorError-foreground); }
  .lint { list-style: none; margin: 0; padding: 0; font-size: .88em; }
  .lint li { overflow-wrap: anywhere; }
  .lint li.warn { color: var(--vscode-editorWarning-foreground); }
  .lint li.error { color: var(--vscode-editorError-foreground); }
  .stray-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .foot { margin-top: 12px; font-size: .82em; color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }`;

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
      else if (action === "create-dir") { vscode.postMessage({ type: "createDir" }); }
      else if (action === "fix-ignore") { vscode.postMessage({ type: "fixIgnore" }); }
      else if (action === "create-rules") { vscode.postMessage({ type: "createRules", name: target }); }
      else if (action === "open-rules") { vscode.postMessage({ type: "openRules", name: target }); }
      else if (action === "open-board") { vscode.postMessage({ type: "openBoard", name: target }); }
      else if (action === "fetch") { vscode.postMessage({ type: "fetch", name: target }); }
      else if (action === "pull") { vscode.postMessage({ type: "pull", name: target }); }
    });
  }
  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "failed") { show("failed", String(data.message || "")); }
    else if (data.type === "info") { show("info", String(data.message || "")); }
    else if (data.type === "cloned") { url.value = ""; name.value = ""; nameTouched = false; remember(); show("info", String(data.message || "")); }
  });`;
