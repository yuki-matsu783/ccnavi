/**
 * ボードを外部資源に依存しない 1 枚の HTML に組み立てる。
 * 色は VS Code のテーマ変数だけを使う。チケットの本文に何が書かれていても、表示を壊さない
 * ように全部の文字列を実体参照にする。
 */
import { APPEARANCE_SCRIPT, APPEARANCE_STYLE, type Appearance, bodyTag } from "./appearance.js";
import type { ApprovePreview } from "./approvemodel.js";
import type { Action, Board, BoardColumn, Card, ParentOption, PhaseChip } from "./board.js";

/**
 * 承認のオーバーレイの状態。拡張側（board-panel）が持ち、描くたびに渡す。Webview の中に
 * 持たないのは、監視の更新で HTML が作り直されてもオーバーレイが消えないようにするため。
 */
export type ApprovalOverlay =
  | { readonly kind: "loading" }
  | { readonly kind: "preview"; readonly preview: ApprovePreview; readonly notice?: string }
  | { readonly kind: "approving"; readonly preview: ApprovePreview }
  | { readonly kind: "error"; readonly error: string }
  /**
   * 承認できた。Claude Code に渡す文と、コピー / 新しいセッションで開く を出す。
   * `carried` は承認済みチケットを運ぶ sh を端末に送ったか。送ったときだけ、そう言う
   */
  | { readonly kind: "done"; readonly count: number; readonly prompt: string; readonly carried?: boolean };

export interface RenderOptions {
  readonly nonce: string;
  /** あれば、ボードの上に承認のオーバーレイを被せる */
  readonly approval?: ApprovalOverlay;
  /** 見た目。無ければ VS Code のテーマに従う */
  readonly appearance?: Appearance;
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
${bodyTag(options.appearance)}
<header class="toolbar">
  <div class="summary">
${approveCount > 0 ? `    <span class="pending warn">承認待ち ${approveCount} 件</span>\n` : ""}${board.issueCount > 0 ? `    <span class="issues warn">不備 ${board.issueCount} 件</span>\n` : ""}    <span class="counts">残り ${board.remainingCount} / 全 ${board.totalCount}</span>
  </div>
  <div class="controls">
${renderFilter(board.projects)}${renderParentFilter(board.parents)}    <button type="button" class="action" data-action="refresh">更新</button>
    <button type="button" class="action primary" data-action="approve"${approveCount === 0 ? " disabled" : ""}>承認待ち ${approveCount} 件を承認</button>
  </div>
</header>
${renderProblems(board.problems)}${board.totalCount === 0 ? '<p class="board-empty">チケット無し</p>\n' : ""}<div class="board">
${board.columns.map(renderColumn).join("\n")}
</div>
<footer class="foot">取得 ${escapeHtml(board.generatedAt)} / ${escapeHtml(board.root)}</footer>
${renderApproval(options.approval)}<script nonce="${nonce}">
${SCRIPT}
</script>
</body>
</html>
`;
}

/**
 * 承認のオーバーレイ。一覧の識別子の表、承認画面の本文（`<pre>`）、対象外の提案と読めない提案、
 * 「この N 件を承認する」「やめる」。本文は実行ファイルが組んだものをそのまま出し、項目には分けない。
 */
export function renderApproval(overlay: ApprovalOverlay | undefined): string {
  if (overlay === undefined) {
    return "";
  }
  const inner = (() => {
    switch (overlay.kind) {
      case "loading":
        return `<p class="approval-note">承認待ちの一覧を読み込んでいる…</p>\n<div class="approval-actions"><button type="button" class="action" data-action="approve-cancel">やめる</button></div>`;
      case "error":
        return `<p class="approval-note error">${escapeHtml(overlay.error)}</p>\n<div class="approval-actions"><button type="button" class="action" data-action="approve-cancel">閉じる</button></div>`;
      case "preview":
      case "approving":
        return renderApprovalBody(overlay.preview, overlay.kind === "approving", overlay.kind === "preview" ? overlay.notice : undefined);
      case "done":
        return `<h2 id="approval-title">${overlay.count} 件を承認した</h2>
${overlay.carried === true ? `<p class="approval-note">承認済みチケットのコミットと push を端末に送った。</p>\n` : ""}<p class="approval-note">Claude Code に伝える文を用意した。コピーして進行中のセッションに貼るか、新しいセッションで開く。送るときは自分で Enter を押す。</p>
<pre class="approval-text">${escapeHtml(overlay.prompt)}</pre>
<div class="approval-actions"><button type="button" class="action primary" data-action="prompt-copy">コピー</button><button type="button" class="action" data-action="prompt-open">新しいセッションで開く</button><button type="button" class="action" data-action="approve-cancel">閉じる</button></div>`;
    }
  })();
  return `<div class="approval-backdrop" data-approval="${overlay.kind}">
<section class="approval" role="dialog" aria-modal="true" aria-labelledby="approval-title">
${inner}
</section>
</div>
`;
}

function renderApprovalBody(preview: ApprovePreview, approving: boolean, notice: string | undefined): string {
  const count = preview.batch.length;
  const tickets = preview.batch.map((b) => b.ticket).join(",");
  const rows = preview.batch
    .map((b) => {
      const where = b.revision ? "親の改版" : b.parent === null ? "親" : `親 ${b.parent} / フェーズ ${b.phase ?? "?"}`;
      return `<tr><td class="approval-id">${escapeHtml(b.ticket)}</td><td>${escapeHtml(b.title)}</td><td>${escapeHtml(where)}</td></tr>`;
    })
    .join("\n");
  const rejected =
    preview.rejected.length === 0
      ? ""
      : `<h3>承認の対象にしない</h3>\n<ul class="approval-rejected">\n${preview.rejected
          .map((r) => `<li><span class="approval-id">${escapeHtml(r.ticket)}</span>${r.problems.map((p) => `<div>${escapeHtml(p)}</div>`).join("")}</li>`)
          .join("\n")}\n</ul>\n`;
  const problems =
    preview.problems.length === 0
      ? ""
      : `<h3>読めない提案・承認済みチケット</h3>\n<ul class="approval-problems">\n${preview.problems.map((p) => `<li>${escapeHtml(p)}</li>`).join("\n")}\n</ul>\n`;
  const confirm =
    count === 0
      ? ""
      : `<button type="button" class="action primary" data-action="approve-confirm" data-tickets="${escapeHtml(tickets)}"${approving ? " disabled" : ""}>${approving ? "承認中…" : `この ${count} 件を承認する`}</button>`;
  return `<h2 id="approval-title">Ticket 承認リクエスト: ${count} 件</h2>
${notice ? `<p class="approval-note warn">${escapeHtml(notice)}</p>\n` : ""}${
    count === 0 ? '<p class="approval-note">承認待ちのチケット無し</p>\n' : `<table class="approval-batch"><thead><tr><th>識別子</th><th>題</th><th>場所</th></tr></thead><tbody>\n${rows}\n</tbody></table>\n`
  }<pre class="approval-text">${escapeHtml(preview.text)}</pre>
${rejected}${problems}<div class="approval-actions">
${confirm}<button type="button" class="action" data-action="approve-cancel"${approving ? " disabled" : ""}>やめる</button>
</div>`;
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
      ? '    <p class="empty">チケット無し</p>'
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
  const facts = renderFacts(card);
  const phases = card.phases.length > 0 ? renderPhases(card.phases) : "";
  const issues = renderIssues(card.issues);
  const actions = renderActions(card.actions, card.id);
  const stage = card.stage ? `\n        <div class="stage">${escapeHtml(card.stage)}</div>` : "";
  const where = card.isParent ? "親" : `子 · 親 ${card.parent} / フェーズ ${card.phase ?? "?"}`;
  return `      <li class="${classes.join(" ")}" data-id="${escapeHtml(card.id)}" data-path="${escapeHtml(card.openPath)}" data-project="${escapeHtml(card.project)}" data-family="${escapeHtml(card.family)}" tabindex="0">
        <div class="card-head"><span class="num">${escapeHtml(card.id)}</span><span class="title">${escapeHtml(card.title)}</span><span class="where">${escapeHtml(where)}</span></div>${stage}${badges}${facts}${phases}${issues}${actions}
      </li>`;
}

/**
 * 枠付きの札は、人が動く必要がある状態だけ。未承認、ゲート閉、作業ツリーなし（閉じたチケットは除く）、
 * 実績のリスクが HIGH 以上、レビュー依頼済（人のレビュー待ち）、本物が決まらない写り。
 * 出す札が無ければ行ごと出さない。
 */
function renderBadges(card: Card): string {
  const badges: string[] = [];
  if (card.copyStatus === "none") {
    badges.push(badge("copy copy-none", COPY_LABELS.none));
  }
  if (card.gateClosed) {
    badges.push(badge("gate", "ゲート閉"));
  }
  if (!card.worktreeExists && card.copyStatus !== "closed") {
    badges.push(badge("worktree none", "作業ツリーなし"));
  }
  for (const mark of card.marks) {
    if (mark === "requested") {
      badges.push(badge("mark mark-requested", MARK_LABELS.requested));
    }
  }
  if (card.riskLevel === "HIGH" || card.riskLevel === "CRITICAL") {
    badges.push(badge(`risk risk-${card.riskLevel.toLowerCase()}`, riskText(card)));
  }
  // 写りがあること自体は普通なので数では出さない。どれが本物か決まらないときだけ言う。
  if (card.scattered.length > 0) {
    const where = card.scattered.map((s) => `${s.tree || "main"}:${s.state}`).join(", ");
    badges.push(badge("seen", `複数の場所にある（${card.scattered.length} か所）`, where));
  }
  if (badges.length === 0) {
    return "";
  }
  return `\n        <div class="badges">\n${badges.map((b) => `          ${b}`).join("\n")}\n        </div>`;
}

/**
 * 枠の無い薄い文字で 1 行に並べる属性。承認済／クローズ、人レビューの要否、作業ツリー、
 * マーカー（依頼済は札のほう）、Draft 解除済、締めた、リスク（MEDIUM 以下）、base、プロジェクト。
 */
function renderFacts(card: Card): string {
  const facts: string[] = [];
  if (card.copyStatus !== "none") {
    facts.push(fact(`copy-${card.copyStatus}`, COPY_LABELS[card.copyStatus]));
  }
  facts.push(fact("review", `人レビュー${card.reviewRequired ? "要" : "不要"}`, card.reviewReason));
  if (card.worktreeExists) {
    facts.push(fact("worktree", `作業ツリー ${worktreeName(card.worktreePath)}`, card.worktreePath));
  }
  for (const mark of card.marks) {
    if (mark !== "requested") {
      facts.push(fact(`mark mark-${mark}`, MARK_LABELS[mark] ?? mark));
    }
  }
  if (card.ready) {
    facts.push(fact("ready", "Draft 解除済"));
  }
  if (card.wrapped) {
    facts.push(fact("wrapped", "締めた"));
  }
  if (card.riskLevel !== "" && card.riskLevel !== "HIGH" && card.riskLevel !== "CRITICAL") {
    facts.push(fact(`risk risk-${card.riskLevel.toLowerCase()}`, riskText(card)));
  }
  if (card.baseSha !== "") {
    facts.push(fact("sha", `base ${card.baseSha.slice(0, 7)}`, card.baseSha));
  }
  if (card.project !== "") {
    facts.push(fact("project", `project ${card.project}`));
  }
  return `\n        <div class="facts">\n${facts.map((f) => `          ${f}`).join("\n")}\n        </div>`;
}

function riskText(card: Card): string {
  return card.riskPoints === null ? `リスク ${card.riskLevel}` : `リスク ${card.riskLevel}（${card.riskPoints} 点）`;
}

/** 作業ツリーの置き場の末尾（`.claude/worktrees/<名前>` の名前）。読めなければ「あり」 */
function worktreeName(path: string): string {
  const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  return name === "" ? "あり" : name;
}

function badge(kind: string, text: string, title = ""): string {
  const tip = title !== "" ? ` title="${escapeHtml(title)}"` : "";
  return `<span class="badge ${escapeHtml(kind)}"${tip}>${escapeHtml(text)}</span>`;
}

function fact(kind: string, text: string, title = ""): string {
  const tip = title !== "" ? ` title="${escapeHtml(title)}"` : "";
  return `<span class="fact ${escapeHtml(kind)}"${tip}>${escapeHtml(text)}</span>`;
}

/**
 * 親カードのフェーズ一覧。1 段階 1 行で、左の丸が段階（終了は塗り、進行中は青、未計画は空）。
 * 右には人が見るべきことだけを出す。ゲートが開いている、マーカーが無い、レビューが要らない、は
 * 普通の状態なので書かない。
 */
function renderPhases(phases: readonly PhaseChip[]): string {
  const rows = phases
    .map((p) => {
      const notes: string[] = [];
      if (p.gateClosed) {
        notes.push("ゲート閉");
      }
      for (const m of p.marks) {
        notes.push(MARK_LABELS[m] ?? m);
      }
      if (p.reviewRequired) {
        notes.push("レビュー要");
      }
      if (p.riskLine !== "") {
        notes.push(p.riskLine);
      }
      const status = [PHASE_STATE_LABELS[p.state], ...notes].join(" · ");
      const tickets = p.tickets.length > 0 ? `<span class="phase-tickets">${escapeHtml(p.tickets.join(", "))}</span>` : "";
      const actions = p.actions.map((a) => renderActionButton(a, `${p.parent}:${p.number}`)).join("");
      return `          <li class="phase phase-${escapeHtml(p.state)}${p.gateClosed ? " gate-closed" : ""}"><span class="phase-dot" aria-hidden="true"></span><span class="phase-name"><span class="phase-label">${escapeHtml(p.label)}</span>${tickets}</span><span class="phase-status">${escapeHtml(status)}${actions}</span></li>`;
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
      // このカードだけを承認の対象にする（`--approve --preview --json <識別子>`）。同じ親の承認待ちが他にあっても巻き込まない。
      return `<button type="button" class="action" data-action="approve-one" data-ticket="${escapeHtml(id)}" title="このチケットだけを承認する（ccnavi --approve ${escapeHtml(id)}）。まとめて承認するなら上部のボタン">この 1 件を承認</button>`;
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
 * 5 つの画面（ボード・ルール設定・リスク管理・フェーズ管理・プロジェクト管理）で同じ見た目のボタン。
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

/**
 * 5 つの画面で同じ骨組み。本文・見出し上のツールバー（左にパス、右にボタン）・帯・注意・欄・脚注。
 * 画面ごとの部品（ボードの列、設定 3 画面の一覧、プロジェクトのカード）は各画面が足す。
 */
export const PAGE_STYLE = `  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 12px;
    background: var(--vscode-editor-background);
    color: var(--vscode-editor-foreground);
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
  }
  code { font-family: var(--vscode-editor-font-family); font-size: .95em; }
  .hidden { display: none !important; }
  .mono { font-family: var(--vscode-editor-font-family); }
  .small { font-size: .85em; }
  .dim { color: var(--vscode-descriptionForeground); }
  .toolbar { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 0 4px 10px; }
  .summary { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; }
  .summary .warn { color: var(--vscode-editorWarning-foreground); font-weight: 600; }
  .path { color: var(--vscode-descriptionForeground); overflow-wrap: anywhere; }
  .dirty { color: var(--vscode-editorWarning-foreground); font-weight: 600; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-left: auto; }
  .banner {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-panel-border);
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  }
  .banner.warn { border-color: var(--vscode-editorWarning-foreground); color: var(--vscode-editorWarning-foreground); }
  .banner.error { border-color: var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground); }
  .banner.missing { border-color: var(--vscode-editorInfo-foreground); }
  .banner.missing > span { flex: 1 1 320px; }
  .lock {
    margin: 0 0 10px; padding: 6px 10px; border-radius: 4px;
    border: 1px solid var(--vscode-editorError-foreground); color: var(--vscode-editorError-foreground);
  }
  .problems {
    margin: 0 0 12px; padding: 8px 8px 8px 24px;
    border: 1px solid var(--vscode-editorWarning-foreground); border-radius: 4px;
    color: var(--vscode-editorWarning-foreground);
  }
  h2 { margin: 0 0 8px; font-size: 1em; }
  .count { color: var(--vscode-descriptionForeground); font-weight: 400; }
  .hint { margin: 0 0 10px; color: var(--vscode-descriptionForeground); font-size: .92em; }
  .empty { margin: 0; color: var(--vscode-descriptionForeground); font-size: .92em; }
  details.help { margin: 0 0 8px; font-size: .92em; }
  details.help > summary { cursor: pointer; color: var(--vscode-descriptionForeground); list-style: none; }
  details.help > summary::-webkit-details-marker { display: none; }
  details.help > summary::before { content: "▸ "; }
  details.help[open] > summary::before { content: "▾ "; }
  details.help > .hint { margin: 4px 0 0; }
${BUTTON_STYLE}
  input[type=text], input[type=search], textarea, select {
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 2px;
    min-height: 24px; padding: 2px 6px; font: inherit;
  }
  input[type=text]:focus, input[type=search]:focus, textarea:focus, select:focus { outline: 1px solid var(--vscode-focusBorder); outline-offset: -1px; }
  select { background: var(--vscode-dropdown-background); color: var(--vscode-dropdown-foreground); border-color: var(--vscode-dropdown-border); }
  input:disabled, select:disabled, textarea:disabled { opacity: .6; }
  .foot { margin-top: 12px; font-size: .85em; color: var(--vscode-descriptionForeground); min-height: 1.2em; overflow-wrap: anywhere; }
  .foot.error { color: var(--vscode-editorError-foreground); }
  /* ハイコントラストのテーマでは背景が変わらないので、VS Code の作法どおり点線の縁でホバーを見せ、
     無効なボタンは枠を消さず点線にする。contrast の変数は HC でしか定義されないので、他のテーマでは効かない */
  button.action:hover:not(:disabled):not(:focus-visible) { outline: 1px dashed var(--vscode-contrastActiveBorder, transparent); outline-offset: -1px; }
  button.action:disabled { border-color: var(--vscode-contrastBorder, transparent); border-style: dashed; }
${APPEARANCE_STYLE}`;

/**
 * 設定 3 画面（ルール設定・リスク管理・フェーズ管理）の一覧。1 件 1 行で、押した行だけ下に欄が開く。
 * 行の見出し（.row-head）の列の幅は画面ごとに決める。欄名は欄の左に置き、欄と欄名は親の格子に並ぶ
 * （.field は display: contents）。出番の少ない欄は details.more に畳み、値があるときだけ開いて出す。
 */
export const LIST_STYLE = `  .list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--vscode-panel-border); border-radius: 5px; }
  .list:empty { display: none; }
  .row { border-top: 1px solid var(--vscode-panel-border); }
  .row:first-child { border-top: 0; }
  .row:first-child > .row-head { border-radius: 4px 4px 0 0; }
  .row:last-child > .row-body, .row:last-child:not(.open) > .row-head { border-radius: 0 0 4px 4px; }
  /* 絞り込みで隠す。開いている行は打っている途中で消えないよう隠さない */
  .row.hidden-by-find:not(.open) { display: none; }
  .row-head { display: grid; gap: 10px; align-items: center; padding: 5px 8px; cursor: pointer; }
  .row-head:hover { background: var(--vscode-list-hoverBackground); outline: 1px dashed var(--vscode-contrastActiveBorder, transparent); outline-offset: -1px; }
  .row.open .row-head { background: var(--vscode-editorWidget-background); }
  /* 開いている行は左に縁を付ける。HC は contrastActiveBorder、他は focusBorder */
  .row.open > .row-head, .row.open > .row-body { box-shadow: inset 3px 0 0 var(--vscode-contrastActiveBorder, var(--vscode-focusBorder)); }
  .row.open > .row-body { border-top: 1px dashed var(--vscode-contrastBorder, transparent); }
  .twist {
    background: none; border: none; color: var(--vscode-descriptionForeground);
    font: inherit; padding: 0 2px; cursor: pointer; line-height: 1;
  }
  .sum { display: contents; }
  .sum .sum-id { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sum .dim { color: var(--vscode-descriptionForeground); }
  .sum .mono { font-family: var(--vscode-editor-font-family); font-size: .92em; }
  .sum .clip { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sum .sum-note { color: var(--vscode-descriptionForeground); margin-left: 10px; }
  .sum .sum-flag { width: 8px; height: 8px; border-radius: 50%; justify-self: center; }
  .sum .sum-flag.on { background: var(--vscode-charts-blue); }
  .sum .tag { font-size: .85em; padding: 0 7px; border-radius: 999px; border: 1px solid currentColor; font-family: var(--vscode-editor-font-family); justify-self: start; }
  .row-body {
    display: none; padding: 4px 10px 10px 36px; gap: 6px 12px; align-items: center;
    grid-template-columns: 150px minmax(0, 1fr); background: var(--vscode-editorWidget-background);
  }
  .row.open .row-body { display: grid; }
  .row-body .buttons { grid-column: 1 / -1; display: flex; gap: 4px; justify-content: flex-end; padding-top: 4px; }
  .row-body .buttons button { margin-left: 0; }
  .row-body .wide { grid-column: 1 / -1; }
  .row-body textarea { width: 100%; min-height: 2.6em; resize: vertical; font-family: inherit; }
  .field { display: contents; }
  .field > .cap { color: var(--vscode-descriptionForeground); text-align: right; font-size: .95em; }
  .field > input, .field > select, .field > textarea, .field > .inline, .field > .picker, .field > .with-button { width: 100%; box-sizing: border-box; margin: 0; min-width: 0; }
  .field > input.narrow, .field > select.narrow { max-width: 260px; }
  .field > input.num { max-width: 90px; }
  .inline { display: flex; gap: 6px; align-items: center; }
  .inline > input { flex: 1; min-width: 120px; }
  .inline > input.num { flex: 0 0 90px; min-width: 0; }
  .inline > .cap { color: var(--vscode-descriptionForeground); }
  details.more { grid-column: 1 / -1; }
  details.more > summary { cursor: pointer; color: var(--vscode-descriptionForeground); list-style: none; padding: 2px 0; }
  details.more > summary::-webkit-details-marker { display: none; }
  details.more > summary::before { content: "▸ "; }
  details.more[open] > summary::before { content: "▾ "; }
  details.more > summary b { font-weight: 500; color: var(--vscode-editor-foreground); }
  details.more > .sub { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 6px 12px; align-items: center; padding-top: 4px; }
  .find { display: flex; gap: 12px; align-items: center; margin: 0 0 10px; flex-wrap: wrap; }
  .find input { width: 320px; max-width: 100%; }
  .find .hint { margin: 0; }`;

const STYLE = `${PAGE_STYLE}
  .toolbar { padding-bottom: 12px; }
  .summary .counts { color: var(--vscode-descriptionForeground); }
  .filter { display: flex; gap: 6px; align-items: center; color: var(--vscode-descriptionForeground); }
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
    position: relative;
    border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 8px;
    background: var(--vscode-editorWidget-background); cursor: pointer;
  }
  .card.child { margin-left: 12px; }
  .card:hover, .card:focus {
    outline: 1px solid var(--vscode-focusBorder);
    background: var(--vscode-list-hoverBackground);
  }
  /* HC のホバーの点線。疑似要素に描き、上の outline（実線。焦点の輪でもある）には触らない。他のテーマでは透明 */
  .card:hover::after { content: ""; position: absolute; inset: 0; border-radius: 5px; pointer-events: none; border: 1px dashed var(--vscode-contrastActiveBorder, transparent); }
  .card.has-issue { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  .card.pending { border-left: 3px solid var(--vscode-charts-blue); }
  .card.gate-closed { border-left: 3px solid var(--vscode-editorError-foreground); }
  .card.hidden { display: none; }
  .card-head { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
  .num { font-weight: 600; font-variant-numeric: tabular-nums; }
  .title { overflow-wrap: anywhere; }
  .where { margin-left: auto; font-size: .85em; color: var(--vscode-descriptionForeground); white-space: nowrap; }
  .stage { margin-top: 2px; font-size: .9em; color: var(--vscode-descriptionForeground); }
  /* 札は人が動く必要がある状態だけ。属性は枠無しの薄い文字で 1 行に並べる */
  .badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .badge {
    font-size: .82em; padding: 0 6px; border-radius: 999px;
    border: 1px solid currentColor; color: var(--vscode-descriptionForeground);
  }
  .badge.copy-none { color: var(--vscode-charts-blue); }
  .badge.gate, .badge.seen, .badge.risk-high, .badge.risk-critical { color: var(--vscode-editorError-foreground); }
  .badge.mark-requested { color: var(--vscode-charts-yellow); }
  .badge.worktree.none { color: var(--vscode-editorWarning-foreground); }
  .facts { display: flex; flex-wrap: wrap; gap: 2px 10px; margin-top: 5px; font-size: .85em; color: var(--vscode-descriptionForeground); }
  .fact { white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
  .fact.copy-open::before, .fact.copy-closed::before, .fact.mark-reviewed::before { content: "✓ "; }
  .fact.sha { font-family: var(--vscode-editor-font-family); }
  /* 親のフェーズ一覧。1 段階 1 行。左の丸が段階で、右に人が見るべきことだけ */
  .phases { list-style: none; margin: 8px 0 0; padding: 6px 0 0; border-top: 1px solid var(--vscode-panel-border); font-size: .85em; display: flex; flex-direction: column; gap: 3px; }
  .phase { display: grid; grid-template-columns: 12px minmax(0, 1fr) minmax(0, auto); gap: 6px; align-items: baseline; color: var(--vscode-descriptionForeground); }
  .phase-dot { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--vscode-descriptionForeground); align-self: center; }
  .phase-ended .phase-dot { background: var(--vscode-charts-green); border-color: var(--vscode-charts-green); }
  .phase-active .phase-dot { border: 2.5px solid var(--vscode-charts-blue); }
  .phase-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .phase .phase-label { font-weight: 600; color: var(--vscode-editor-foreground); }
  .phase-tickets::before { content: "·"; margin: 0 5px; }
  .phase-status { text-align: right; overflow-wrap: anywhere; max-width: 55%; justify-self: end; }
  .phase-active .phase-status { color: var(--vscode-charts-blue); }
  .phase.gate-closed .phase-label, .phase.gate-closed .phase-status { color: var(--vscode-editorError-foreground); }
  .phase button.action { margin-left: 6px; min-height: 20px; padding: 0 8px; font-size: .95em; }
  .issues {
    list-style: none; margin: 6px 0 0; padding: 0;
    font-size: .82em; color: var(--vscode-editorWarning-foreground);
  }
  .issues li { overflow-wrap: anywhere; }
  .card-actions { display: flex; gap: 6px; margin-top: 8px; }
  .approval-backdrop {
    position: fixed; inset: 0; z-index: 10;
    background: color-mix(in srgb, var(--vscode-editor-background) 70%, transparent);
    display: flex; align-items: center; justify-content: center; padding: 16px;
  }
  .approval {
    width: min(920px, 100%); max-height: 100%; overflow: auto; box-sizing: border-box;
    padding: 14px 16px; border-radius: 6px;
    background: var(--vscode-editorWidget-background, var(--vscode-editor-background));
    border: 1px solid var(--vscode-editorWidget-border, var(--vscode-panel-border));
    box-shadow: 0 4px 16px rgba(0, 0, 0, .35);
  }
  .approval h2 { margin: 0 0 8px; font-size: 1.1em; }
  .approval h3 { margin: 12px 0 4px; font-size: .95em; color: var(--vscode-descriptionForeground); }
  .approval-batch { border-collapse: collapse; width: 100%; font-size: .9em; margin-bottom: 8px; }
  .approval-batch th, .approval-batch td { text-align: left; padding: 2px 8px 2px 0; border-bottom: 1px solid var(--vscode-panel-border); vertical-align: top; }
  .approval-id { font-family: var(--vscode-editor-font-family); }
  .approval-text {
    margin: 0; padding: 8px; max-height: 50vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
    font-family: var(--vscode-editor-font-family); font-size: var(--vscode-editor-font-size);
    background: var(--vscode-textCodeBlock-background); border: 1px solid var(--vscode-panel-border); border-radius: 4px;
  }
  .approval-rejected, .approval-problems { margin: 0; padding-left: 18px; font-size: .88em; color: var(--vscode-editorWarning-foreground); }
  .approval-rejected li, .approval-problems li { overflow-wrap: anywhere; }
  .approval-note { margin: 4px 0 8px; }
  .approval-note.warn { color: var(--vscode-editorWarning-foreground); }
  .approval-note.error { color: var(--vscode-editorError-foreground); white-space: pre-wrap; overflow-wrap: anywhere; }
  .approval-actions { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; }`;

const SCRIPT = `  const vscode = acquireVsCodeApi();
  // 絞り込みで見えている承認待ちの識別子と、絞り込み中かどうか。「絞り込み無し」は空の並びでは
  // なく filtered で言う。空を「全部」に読ませると、0 件のつもりが全部承認に化ける。
  function filtering() { return document.body.classList.contains("filtering"); }
  function visiblePending() {
    return [...document.querySelectorAll(".card.pending:not(.hidden)")].map((card) => card.getAttribute("data-id") || "");
  }
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
      else if (action === "approve") { vscode.postMessage({ type: "approve", tickets: visiblePending(), filtered: filtering() }); }
      else if (action === "approve-one") { vscode.postMessage({ type: "approve", tickets: [button.getAttribute("data-ticket") || ""], filtered: true }); }
      else if (action === "prompt-copy") { vscode.postMessage({ type: "promptCopy" }); }
      else if (action === "prompt-open") { vscode.postMessage({ type: "promptOpen" }); }
      else if (action === "approve-confirm") {
        const tickets = (button.getAttribute("data-tickets") || "").split(",").filter((t) => t !== "");
        vscode.postMessage({ type: "approveConfirm", tickets: tickets });
      }
      else if (action === "approve-cancel") { vscode.postMessage({ type: "approveCancel" }); }
      else if (action === "accept") {
        vscode.postMessage({ type: "accept", parent: button.getAttribute("data-parent"), phase: Number(button.getAttribute("data-phase")) });
      }
    });
  }
  // 承認のオーバーレイ。Esc でやめる。承認している最中は閉じない。開いたら「やめる」に焦点を置く
  //（承認したあとは「コピー」）。
  const approval = document.querySelector(".approval-backdrop");
  if (approval) {
    const cancel = approval.querySelector('button[data-action="prompt-copy"]') || approval.querySelector('button[data-action="approve-cancel"]');
    if (cancel && !cancel.disabled) { cancel.focus(); }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && approval.getAttribute("data-approval") !== "approving") {
        vscode.postMessage({ type: "approveCancel" });
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
    document.body.classList.toggle("filtering", project !== "*" || parent !== "*");
    // 列の件数は絞り込み後に見えているカードの数にする。上部の集計（残り・全・不備・承認待ち）は
    // 絞り込みに関係なくボード全体の数のまま。
    for (const column of document.querySelectorAll(".column")) {
      const count = column.querySelector(":scope > h2 > .count");
      if (count) { count.textContent = String(column.querySelectorAll(".card:not(.hidden)").length); }
    }
    // 「承認待ち N 件を承認」だけは、押したときに承認の対象になるもの（見えている承認待ち）の数にする。
    const approve = document.querySelector('.controls button[data-action="approve"]');
    if (approve) {
      const n = document.querySelectorAll(".card.pending:not(.hidden)").length;
      approve.textContent = "承認待ち " + n + " 件を承認";
      approve.disabled = n === 0;
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
  });
${APPEARANCE_SCRIPT}`;
