/**
 * ボードを外部資源に依存しない 1 枚の HTML に組み立てる。
 * 色は VS Code のテーマ変数だけを使う。チケットの本文に何が書かれていても、表示を壊さない
 * ように全部の文字列を実体参照にする。
 */
import type { Action, Board, BoardColumn, Card, ParentOption, PhaseChip } from "./board.js";

export interface RenderOptions {
  readonly nonce: string;
}

const COPY_LABELS = { none: "未承認", open: "承認済", closed: "クローズ" } as const;
const MARK_LABELS: Readonly<Record<string, string>> = {
  pending: "終了を通知",
  skipped: "レビュー省略",
  requested: "レビュー依頼済",
  reviewed: "レビュー済",
};
const PHASE_STATE_LABELS = { planned: "未計画", active: "進行中", ended: "終了" } as const;

export function renderBoard(board: Board, options: RenderOptions): string {
  const { nonce } = options;
  const approveCount = board.pendingApproval.length;
  return `<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ccnavi ボード</title>
<style nonce="${nonce}">
${STYLE}
</style>
</head>
<body>
<header class="toolbar">
  <div class="summary">
    <span class="remaining">残り ${board.remainingCount} 件</span>
    <span class="total">全 ${board.totalCount} 件</span>
    <span class="issues${board.issueCount > 0 ? " warn" : ""}">不備 ${board.issueCount} 件</span>
    <span class="pending${approveCount > 0 ? " warn" : ""}">承認待ち ${approveCount} 件</span>
  </div>
  <div class="controls">
${renderFilter(board.projects)}${renderParentFilter(board.parents)}    <button type="button" class="action" data-action="refresh">更新</button>
    <button type="button" class="action primary" data-action="approve"${approveCount === 0 ? " disabled" : ""}>承認待ち ${approveCount} 件を承認</button>
  </div>
</header>
${renderProblems(board.problems)}${board.totalCount === 0 ? '<p class="board-empty">チケットはありません</p>\n' : ""}<div class="board">
${board.columns.map(renderColumn).join("\n")}
</div>
<footer class="foot">取得 ${escapeHtml(board.generatedAt)} / ${escapeHtml(board.root)}</footer>
<script nonce="${nonce}">
${SCRIPT}
</script>
</body>
</html>
`;
}

function renderFilter(projects: readonly string[]): string {
  if (projects.length === 0) {
    return "";
  }
  const options = projects
    .map((p) => `      <option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`)
    .join("\n");
  return `    <label class="filter">プロジェクト
      <select id="project-filter">
      <option value="*">すべて</option>
      <option value="">ワークスペース本体</option>
${options}
      </select>
    </label>
`;
}

/** 親の絞り込み。並行して進めている親が複数あるとき、1 つの家族（親とその子）だけを見る */
function renderParentFilter(parents: readonly ParentOption[]): string {
  if (parents.length === 0) {
    return "";
  }
  const options = parents
    .map((p) => {
      const label = p.title === "" ? p.id : `${p.id} ${p.title}`;
      return `      <option value="${escapeHtml(p.id)}">${escapeHtml(label)}</option>`;
    })
    .join("\n");
  return `    <label class="filter">親
      <select id="parent-filter">
      <option value="*">すべて</option>
${options}
      </select>
    </label>
`;
}

function renderProblems(problems: readonly string[]): string {
  if (problems.length === 0) {
    return "";
  }
  const items = problems.map((p) => `  <li>${escapeHtml(p)}</li>`).join("\n");
  return `<ul class="problems">\n${items}\n</ul>\n`;
}

function renderColumn(column: BoardColumn): string {
  const body =
    column.count === 0
      ? '    <p class="empty">チケットはありません</p>'
      : `    <ul class="cards">\n${column.cards.map(renderCard).join("\n")}\n    </ul>`;
  return `  <section class="column" data-state="${escapeHtml(column.state)}">
    <h2>
      <button type="button" class="fold" data-fold="${escapeHtml(column.state)}" aria-expanded="true" title="列を折りたたむ／広げる"><span class="fold-mark" aria-hidden="true"></span><span class="label">${escapeHtml(column.label)}</span></button>
      <span class="count">${column.count}</span>
    </h2>
${body}
    <div class="resizer" data-resize="${escapeHtml(column.state)}" title="ドラッグで幅を変える／ダブルクリックで戻す"></div>
  </section>`;
}

function renderCard(card: Card): string {
  const classes = ["card", card.isParent ? "parent" : "child"];
  if (card.issues.length > 0) {
    classes.push("has-issue");
  }
  if (card.gateClosed) {
    classes.push("gate-closed");
  }
  if (card.pendingApproval) {
    classes.push("pending");
  }
  const badges = renderBadges(card);
  const phases = card.phases.length > 0 ? renderPhases(card.phases) : "";
  const issues = renderIssues(card.issues);
  const actions = renderActions(card.actions, card.id);
  const stage = card.stage ? `\n        <div class="stage">${escapeHtml(card.stage)}</div>` : "";
  return `      <li class="${classes.join(" ")}" data-id="${escapeHtml(card.id)}" data-path="${escapeHtml(card.openPath)}" data-project="${escapeHtml(card.project)}" data-family="${escapeHtml(card.family)}" tabindex="0">
        <div class="card-head"><span class="num">${escapeHtml(card.id)}</span><span class="title">${escapeHtml(card.title)}</span></div>${stage}
        <div class="badges">
${badges}
        </div>${phases}${issues}${actions}
      </li>`;
}

function renderBadges(card: Card): string {
  const badges: string[] = [];
  if (!card.isParent) {
    badges.push(badge("phase", `親 ${card.parent} / フェーズ ${card.phase ?? "?"}`));
  }
  if (card.project !== "") {
    badges.push(badge("project", `project ${card.project}`));
  }
  badges.push(badge(`copy copy-${card.copyStatus}`, COPY_LABELS[card.copyStatus]));
  badges.push(
    badge("review", `人レビュー ${card.reviewRequired ? "要" : "不要"}`, card.reviewReason),
  );
  badges.push(
    card.worktreeExists
      ? badge("worktree", "作業ツリーあり", card.worktreePath)
      : badge("worktree none", "作業ツリーなし"),
  );
  for (const mark of card.marks) {
    badges.push(badge(`mark mark-${mark}`, MARK_LABELS[mark] ?? mark));
  }
  if (card.gateClosed) {
    badges.push(badge("gate", "ゲート閉"));
  }
  if (card.ready) {
    badges.push(badge("ready", "Draft 解除済"));
  }
  if (card.wrapped) {
    badges.push(badge("wrapped", "締めた"));
  }
  if (card.riskLevel !== "") {
    badges.push(
      badge(
        `risk risk-${card.riskLevel.toLowerCase()}`,
        `リスク ${card.riskPoints ?? ""} ${card.riskLevel}`.replace(/\s+/g, " "),
      ),
    );
  }
  if (card.baseSha !== "") {
    badges.push(badge("sha", `base ${card.baseSha.slice(0, 7)}`, card.baseSha));
  }
  if (card.seenIn.length > 1) {
    const where = card.seenIn.map((s) => `${s.tree || "main"}:${s.state}`).join(", ");
    badges.push(badge("seen", `${card.seenIn.length} か所にコピーあり`, where));
  }
  return badges.map((b) => `          ${b}`).join("\n");
}

function badge(kind: string, text: string, title = ""): string {
  const tip = title !== "" ? ` title="${escapeHtml(title)}"` : "";
  return `<span class="badge ${escapeHtml(kind)}"${tip}>${escapeHtml(text)}</span>`;
}

function renderPhases(phases: readonly PhaseChip[]): string {
  const rows = phases
    .map((p) => {
      const marks = p.marks.map((m) => MARK_LABELS[m] ?? m).join("・") || "印なし";
      const gate = p.gateClosed ? "ゲート閉" : "ゲート開";
      const review = p.reviewRequired ? "レビュー要" : "レビュー不要";
      const risk = p.riskLine !== "" ? ` / ${p.riskLine}` : "";
      const tickets = p.tickets.length > 0 ? ` / ${p.tickets.join(", ")}` : "";
      const actions = p.actions.map((a) => renderActionButton(a, `${p.parent}:${p.number}`)).join("");
      return `          <li class="phase phase-${escapeHtml(p.state)}${p.gateClosed ? " gate-closed" : ""}"><span class="phase-label">${escapeHtml(p.label)}</span> ${escapeHtml(PHASE_STATE_LABELS[p.state])} / ${escapeHtml(marks)} / ${escapeHtml(gate)} / ${escapeHtml(review)}${escapeHtml(risk)}${escapeHtml(tickets)}${actions}</li>`;
    })
    .join("\n");
  return `\n        <ul class="phases">\n${rows}\n        </ul>`;
}

function renderIssues(issues: readonly string[]): string {
  if (issues.length === 0) {
    return "";
  }
  const items = issues.map((i) => `          <li>${escapeHtml(i)}</li>`).join("\n");
  return `\n        <ul class="issues">\n${items}\n        </ul>`;
}

function renderActions(actions: readonly Action[], id: string): string {
  if (actions.length === 0) {
    return "";
  }
  return `\n        <div class="card-actions">${actions.map((a) => renderActionButton(a, id)).join("")}</div>`;
}

function renderActionButton(action: Action, id: string): string {
  switch (action.kind) {
    case "approve":
      return `<button type="button" class="action" data-action="approve" title="束で承認する（ccnavi --approve）。${escapeHtml(id)} だけを承認することはできない">承認</button>`;
    case "accept":
      return `<button type="button" class="action" data-action="accept" data-parent="${escapeHtml(action.parent)}" data-phase="${action.phase}" title="未解決のレビューを受け入れて進む（ccnavi-review.sh accept ${action.phase}）">受け入れ</button>`;
  }
}

/** HTML の特殊文字を実体参照にする。& を最初に変換して二重変換を避ける */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * 3 つの画面（ボード・ルール設定・プロジェクト管理）で同じ見た目のボタン。
 * 縁と薄い影で「押せる」と分かるようにし、押した瞬間に 1px 沈む。primary は VS Code の主ボタンの色。
 */
export const BUTTON_STYLE = `  button.action {
    display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    min-height: 24px; padding: 2px 12px; line-height: 1.3; white-space: nowrap;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-button-border, rgba(128, 128, 128, .4)); border-radius: 3px;
    box-shadow: 0 1px 1px rgba(0, 0, 0, .25);
    cursor: pointer; font: inherit;
  }
  button.action:hover { background: var(--vscode-button-secondaryHoverBackground); border-color: var(--vscode-focusBorder); }
  button.action:active { transform: translateY(1px); box-shadow: none; }
  button.action:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 1px; }
  button.action.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border-color: transparent; }
  button.action.primary:hover { background: var(--vscode-button-hoverBackground); }
  button.action.small { min-height: 20px; padding: 0 8px; font-size: .9em; }
  button.action:disabled { opacity: .5; cursor: not-allowed; transform: none; box-shadow: none; border-color: transparent; }`;

const STYLE = `  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 0 4px 12px; }
  .summary { display: flex; gap: 16px; font-weight: 600; }
  .summary .warn { color: var(--vscode-editorWarning-foreground); }
  .controls { display: flex; gap: 8px; align-items: center; margin-left: auto; }
  .filter { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
  select {
    background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground);
    border: 1px solid var(--vscode-dropdown-border); border-radius: 2px; padding: 2px 4px;
  }
${BUTTON_STYLE}
  .problems {
    margin: 0 0 12px; padding: 8px 8px 8px 24px;
    border: 1px solid var(--vscode-editorWarning-foreground); border-radius: 4px;
    color: var(--vscode-editorWarning-foreground);
  }
  .board-empty { padding: 4px; color: var(--vscode-descriptionForeground); }
  /* 列は空きに合わせて伸び縮みする。1 列 220px を割るところまで狭まったら横スクロールに逃がす。
     右端の取っ手をドラッグした列は幅が px で固定され（.sized）、ダブルクリックで元の伸び縮みに戻る。
     畳んだ列は見出し 1 行ぶんの幅に縮む。縦書きにはしない */
  .board { display: flex; gap: 12px; align-items: flex-start; overflow-x: auto; }
  .column {
    position: relative;
    flex: 1 1 0; min-width: 220px;
    border: 1px solid var(--vscode-panel-border); border-radius: 6px; padding: 8px;
  }
  .column.sized { flex: 0 0 auto; }
  .resizer { position: absolute; top: 0; bottom: 0; right: -7px; width: 12px; cursor: col-resize; z-index: 1; }
  .resizer::after {
    content: ""; position: absolute; top: 8px; bottom: 8px; left: 5px; width: 2px;
    border-radius: 1px; background: var(--vscode-focusBorder); opacity: 0;
  }
  .resizer:hover::after, .column.resizing .resizer::after { opacity: 1; }
  body.resizing, body.resizing * { cursor: col-resize; user-select: none; }
  .column h2 { margin: 0 0 8px; font-size: 1em; display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .column .count { color: var(--vscode-descriptionForeground); }
  button.fold {
    display: flex; align-items: center; gap: 4px; min-width: 0;
    background: none; border: none; padding: 0; margin: 0; cursor: pointer;
    font: inherit; font-weight: inherit; color: inherit; text-align: left;
  }
  button.fold:hover .label { text-decoration: underline; }
  button.fold:focus-visible { outline: 1px solid var(--vscode-focusBorder); outline-offset: 2px; }
  .fold-mark {
    display: inline-block; width: 0; height: 0;
    border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid currentColor;
  }
  .column.folded .fold-mark {
    border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-left: 6px solid currentColor; border-right: 0;
  }
  /* ドラッグで固定した px 幅（インラインの style）より畳んだ状態を優先する。広げたときは固定幅に戻る */
  .column.folded { flex: 0 0 auto; min-width: 0; width: auto !important; }
  .column.folded h2 { margin: 0; white-space: nowrap; }
  .column.folded .cards, .column.folded .empty, .column.folded .resizer { display: none; }
  .empty { margin: 0; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .cards { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .card {
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background); cursor: pointer;
  }
  .card.child { margin-left: 12px; }
  .card:hover, .card:focus {
    outline: 1px solid var(--vscode-focusBorder);
    background: var(--vscode-list-hoverBackground);
  }
  .card.has-issue { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  .card.gate-closed { border-left: 3px solid var(--vscode-editorError-foreground); }
  .card.pending { border-left: 3px solid var(--vscode-charts-blue); }
  .card.hidden { display: none; }
  .card-head { display: flex; gap: 6px; align-items: baseline; }
  .num { color: var(--vscode-descriptionForeground); font-variant-numeric: tabular-nums; }
  .title { overflow-wrap: anywhere; }
  .stage { margin-top: 4px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  .badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .badge {
    font-size: .82em; padding: 0 6px; border-radius: 999px;
    border: 1px solid var(--vscode-panel-border);
    color: var(--vscode-descriptionForeground);
  }
  .badge.copy-open { color: var(--vscode-charts-green); border-color: var(--vscode-charts-green); }
  .badge.copy-none { color: var(--vscode-charts-blue); border-color: var(--vscode-charts-blue); }
  .badge.gate, .badge.risk-high, .badge.risk-critical { color: var(--vscode-editorError-foreground); border-color: var(--vscode-editorError-foreground); }
  .badge.mark-reviewed { color: var(--vscode-charts-green); }
  .badge.mark-requested { color: var(--vscode-charts-yellow); }
  .badge.seen, .badge.worktree.none { color: var(--vscode-editorWarning-foreground); }
  .phases { list-style: none; margin: 6px 0 0; padding: 0; font-size: .85em; display: flex; flex-direction: column; gap: 2px; }
  .phase { color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }
  .phase .phase-label { font-weight: 600; color: var(--vscode-editor-foreground); }
  .phase.gate-closed { color: var(--vscode-editorError-foreground); }
  .phase button.action { margin-left: 6px; min-height: 20px; padding: 0 8px; font-size: .95em; }
  .issues {
    list-style: none; margin: 6px 0 0; padding: 0;
    font-size: .82em; color: var(--vscode-editorWarning-foreground);
  }
  .issues li { overflow-wrap: anywhere; }
  .card-actions { display: flex; gap: 6px; margin-top: 8px; }
  .foot { margin-top: 12px; font-size: .82em; color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }`;

const SCRIPT = `  const vscode = acquireVsCodeApi();
  function open(card) {
    const filePath = card.getAttribute("data-path");
    if (filePath) { vscode.postMessage({ type: "open", filePath: filePath }); }
  }
  for (const card of document.querySelectorAll(".card")) {
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) { return; }
      open(card);
    });
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.target.closest("button")) { event.preventDefault(); open(card); }
    });
  }
  for (const button of document.querySelectorAll("button[data-action]")) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const action = button.getAttribute("data-action");
      if (action === "refresh") { vscode.postMessage({ type: "refresh" }); }
      else if (action === "approve") { vscode.postMessage({ type: "approve" }); }
      else if (action === "accept") {
        vscode.postMessage({ type: "accept", parent: button.getAttribute("data-parent"), phase: Number(button.getAttribute("data-phase")) });
      }
    });
  }
  // 絞り込み（プロジェクト・親）・畳んだ列・列の幅は、更新で HTML が作り直されても残るように webview の状態に置く。
  const saved = vscode.getState() || {};
  const savedWidths = saved.widths && typeof saved.widths === "object" ? saved.widths : {};
  const state = {
    project: typeof saved.project === "string" ? saved.project : "*",
    parent: typeof saved.parent === "string" ? saved.parent : "*",
    folded: Array.isArray(saved.folded) ? saved.folded.filter((f) => typeof f === "string") : [],
    widths: Object.fromEntries(Object.entries(savedWidths).filter(([, w]) => typeof w === "number" && w > 0)),
  };
  function save() { vscode.setState({ project: state.project, parent: state.parent, folded: state.folded, widths: state.widths }); }

  function setFolded(column, folded) {
    column.classList.toggle("folded", folded);
    const button = column.querySelector("button.fold");
    if (button) { button.setAttribute("aria-expanded", folded ? "false" : "true"); }
  }
  for (const button of document.querySelectorAll("button.fold")) {
    const column = button.closest(".column");
    const key = button.getAttribute("data-fold") || "";
    setFolded(column, state.folded.includes(key));
    button.addEventListener("click", () => {
      const folded = !column.classList.contains("folded");
      setFolded(column, folded);
      state.folded = folded ? state.folded.concat([key]) : state.folded.filter((f) => f !== key);
      save();
    });
  }

  // 列の幅。取っ手をドラッグした列だけ px で固定し、ダブルクリックで元の伸び縮みに戻す。
  const MIN_WIDTH = 220;
  function setWidth(column, width) {
    column.classList.toggle("sized", width !== undefined);
    column.style.width = width === undefined ? "" : width + "px";
  }
  for (const handle of document.querySelectorAll(".resizer")) {
    const column = handle.closest(".column");
    const key = handle.getAttribute("data-resize") || "";
    setWidth(column, state.widths[key]);
    handle.addEventListener("dblclick", () => {
      delete state.widths[key];
      setWidth(column, undefined);
      save();
    });
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) { return; }
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = column.getBoundingClientRect().width;
      column.classList.add("resizing");
      document.body.classList.add("resizing");
      handle.setPointerCapture(event.pointerId);
      const move = (e) => {
        const width = Math.max(MIN_WIDTH, Math.round(startWidth + e.clientX - startX));
        setWidth(column, width);
        state.widths[key] = width;
      };
      const finish = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", finish);
        handle.removeEventListener("pointercancel", finish);
        column.classList.remove("resizing");
        document.body.classList.remove("resizing");
        save();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", finish);
      handle.addEventListener("pointercancel", finish);
    });
  }

  // 絞り込みはプロジェクトと親の両方を満たすカードだけを出す。覚えていた値が候補に無ければ
  //（その親が消えた等）「すべて」のまま。
  const filter = document.getElementById("project-filter");
  const parentFilter = document.getElementById("parent-filter");
  function applyFilter() {
    const project = filter ? filter.value : "*";
    const parent = parentFilter ? parentFilter.value : "*";
    for (const card of document.querySelectorAll(".card")) {
      const ownProject = card.getAttribute("data-project") || "";
      const ownFamily = card.getAttribute("data-family") || "";
      const hidden = (project !== "*" && ownProject !== project) || (parent !== "*" && ownFamily !== parent);
      card.classList.toggle("hidden", hidden);
    }
    // 列の件数は絞り込み後に見えているカードの数にする。
    for (const column of document.querySelectorAll(".column")) {
      const count = column.querySelector(".count");
      if (count) { count.textContent = String(column.querySelectorAll(".card:not(.hidden)").length); }
    }
    state.project = project;
    state.parent = parent;
    save();
  }
  function select(element, value) {
    if (!element) { return; }
    if ([...element.options].some((o) => o.value === value)) { element.value = value; applyFilter(); }
  }
  function selectProject(value) { select(filter, value); }
  for (const element of [filter, parentFilter]) {
    if (element) { element.addEventListener("change", applyFilter); }
  }
  selectProject(state.project);
  select(parentFilter, state.parent);
  applyFilter();
  // プロジェクト管理画面から「このプロジェクトで絞って開く」で来たとき。
  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "filter" && typeof data.project === "string") { selectProject(data.project); }
  });`;
