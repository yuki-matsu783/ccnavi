import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { parseApprovePreview, type ApprovePreview } from "../../src/core/approvemodel.js";
import { buildBoard } from "../../src/core/board.js";
import { PAGE_STYLE, escapeHtml, renderBoard } from "../../src/core/render.js";
import { renderRulesPage } from "../../src/core/rules-render.js";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRiskPage } from "../../src/core/risk-render.js";
import { readRisk, BUILTIN_RISK_TEXT } from "../../src/core/risk-doc.js";
import { renderPhasesPage } from "../../src/core/phases-render.js";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import { renderProjectsPage } from "../../src/core/projects-render.js";
import { buildProjectsPage } from "../../src/core/projects.js";
import type { TicketJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";

const OPTIONS = { nonce: "TEST-NONCE-123" };

function approvePreview(): ApprovePreview {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", "approve-preview.json"), "utf8");
  const parsed = parseApprovePreview(text);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}

test("CB-T107 承認のオーバーレイに一覧・本文・対象外を出し、見せた識別子を承認ボタンに持たせる", () => {
  const preview = approvePreview();
  const html = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "preview", preview } });
  assert.ok(html.includes('class="approval-backdrop" data-approval="preview"'));
  // 種類の範囲を超える子（i0001-02）は承認を止めないので一覧に載り、超過は本文の見出しに出る。
  assert.ok(html.includes("Ticket 承認リクエスト: 3 件"));
  assert.ok(html.includes('data-action="approve-confirm" data-tickets="i0001,i0001-01,i0001-02"'));
  assert.ok(html.includes("この 3 件を承認する"));
  assert.ok(html.includes('data-action="approve-cancel"'));
  assert.ok(html.includes('<pre class="approval-text">Ticket 承認リクエスト'));
  assert.ok(html.includes("判定で止まるもの"));
  assert.ok(html.includes("超えている"));
  // 対象にしないのは形の壊れた子（計画に無い番号）。
  assert.ok(html.includes("承認の対象にしない"));
  assert.ok(html.includes("i0001-05"));
  assert.ok(html.includes("計画に無い"));
  assert.ok(!html.includes("読めない提案・承認済みチケット"));
  // 本文は実体参照にする。
  const spiked = { ...preview, text: "<script>alert(1)</script>" };
  const escaped = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "preview", preview: spiked } });
  assert.ok(!escaped.includes("<script>alert(1)</script>"));
  assert.ok(escaped.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
});

test("CB-T108 承認の対象が空なら承認ボタンを出さず、承認中はボタンを押せず、食い違いの注意を出す", () => {
  const preview = approvePreview();
  const empty = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "preview", preview: { ...preview, batch: [], text: "承認待ちのチケットは無い。" } },
  });
  assert.ok(empty.includes("承認待ちのチケットは無い"));
  assert.ok(!empty.includes('data-action="approve-confirm"'));
  const approving = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "approving", preview } });
  assert.ok(approving.includes('data-approval="approving"'));
  assert.ok(approving.includes("承認中…"));
  assert.ok(/data-action="approve-confirm"[^>]*disabled/.test(approving));
  const noticed = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "preview", preview, notice: "見せた一覧と今の一覧が違った" },
  });
  assert.ok(noticed.includes('class="approval-note warn">見せた一覧と今の一覧が違った'));
  const failed = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "error", error: "実行ファイルが無い" } });
  assert.ok(failed.includes('class="approval-note error">実行ファイルが無い'));
});

test("CB-T108b 承認したら同じオーバーレイに文とコピー・新しいセッションで開く・閉じるを出す", () => {
  const html = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "done", count: 1, prompt: "i0001-03 を承認した <b>" },
  });
  assert.ok(html.includes('class="approval-backdrop" data-approval="done"'));
  assert.ok(html.includes("1 件を承認した</h2>"));
  assert.ok(html.includes('<pre class="approval-text">i0001-03 を承認した &lt;b&gt;</pre>'), "文は実体参照にして見せる");
  assert.ok(html.includes('data-action="prompt-copy"'));
  assert.ok(html.includes('data-action="prompt-open"'));
  assert.ok(html.includes('data-action="approve-cancel"'));
  // 文は Webview から送らせない。拡張が持っている文を使う。
  assert.ok(html.includes('vscode.postMessage({ type: "promptCopy" })'));
  assert.ok(html.includes('vscode.postMessage({ type: "promptOpen" })'));
  // 運ぶ sh を端末に送ったときだけ、そう言う。
  assert.ok(!html.includes("端末に送った"));
  const carried = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "done", count: 1, prompt: "i0001-03 を承認した", carried: true },
  });
  assert.ok(carried.includes("承認済みチケットのコミットと push を端末に送った。"));
});

test("CB-T108c カードの承認はそのカードの識別子だけを絞りとして送る", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  const pending = fixture().pending_approval[0];
  assert.ok(html.includes(`data-action="approve-one" data-ticket="${pending}"`));
  assert.ok(html.includes("この 1 件を承認"));
  assert.ok(
    html.includes('vscode.postMessage({ type: "approve", tickets: [button.getAttribute("data-ticket") || ""], filtered: true })'),
    "1 件だけを絞りとして送る",
  );
  // 上部のボタンは今までどおり見えている承認待ち全部。
  assert.ok(html.includes('<button type="button" class="action primary" data-action="approve"'));
});

test("CB-T109 オーバーレイを渡さなければ出ない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // スタイルとスクリプトには名前が残るので、要素そのものが無いことを見る。
  assert.ok(!html.includes('class="approval-backdrop"'));
  assert.ok(!html.includes('data-action="approve-confirm"'));
  assert.ok(html.includes('data-action="approve"'));
});

test("CB-T12 4 列と件数と承認ボタンを出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  for (const label of ["未着手", "作業中", "完了", "取り消し"]) {
    assert.ok(html.includes(label), label);
  }
  assert.equal((html.match(/class="column"/g) ?? []).length, 4);
  assert.ok(html.includes("残り 3 / 全 4"));
  assert.ok(html.includes('<span class="pending warn">承認待ち 1 件</span>'));
  // 0 件のものは見出しに出さない
  assert.ok(!html.includes("不備 0 件"));
  assert.ok(html.includes("承認待ち 1 件を承認"));
  assert.ok(!html.includes('data-action="approve" disabled'));
  assert.ok(html.includes(`nonce="${OPTIONS.nonce}"`));
});

test("CB-T12b 列ごとに畳むボタンを出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  for (const state of ["todo", "doing", "done", "cancelled"]) {
    assert.ok(html.includes(`data-fold="${state}" aria-expanded="true"`), state);
  }
  assert.equal((html.match(/class="fold"/g) ?? []).length, 4);
});

test("CB-T12c 絞り込み後の件数は見えているカードで数え、畳んだ列は固定幅に縛られない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 絞り込みのたびに列の見出しの .count を .hidden でないカードの数で書き直す
  assert.ok(html.includes('column.querySelector(":scope > h2 > .count")'), "列の見出しの件数を拾う");
  assert.ok(html.includes('column.querySelectorAll(".card:not(.hidden)").length'), "見えているカードで数える");
  // ドラッグで付けたインラインの width より畳んだ状態を優先する
  assert.ok(/\.column\.folded \{[^}]*width: auto !important/.test(html), "畳んだ列は固定幅より優先");
});

test("CB-T12d 承認ボタンは見えている承認待ちの数を出し、その識別子を --approve に渡す。上部の集計は絞らない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 上部の集計は描いたときの全体の数のままで、script は触らない
  assert.ok(html.includes("承認待ち 1 件</span>"), "集計は全体の数");
  // ボタンの数と disabled は見えている承認待ちで決め、押すとその識別子を送る
  assert.ok(html.includes(`document.querySelector('.controls button[data-action="approve"]')`), "上部のボタンだけを書き換える");
  assert.ok(html.includes('document.querySelectorAll(".card.pending:not(.hidden)").length'), "見えている承認待ちで数える");
  // 識別子と「絞り込み中か」を別々に送る。空の並びを「全部」に読ませない
  assert.ok(html.includes('vscode.postMessage({ type: "approve", tickets: visiblePending(), filtered: filtering() })'), "識別子と絞り込みの有無を送る");
});

test("CB-T13 カードにバッジ・フェーズ・操作を出す。札は人が動く状態だけで、属性は枠無しの行に出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 人が動く状態は枠付きの札
  assert.ok(html.includes('<span class="badge copy copy-none">未承認</span>'));
  assert.ok(html.includes('<span class="badge worktree none">作業ツリーなし</span>'));
  // 属性は枠無しの fact。承認済・レビューの要否・作業ツリーの名前・base
  assert.ok(html.includes('<span class="fact copy-open">承認済</span>'));
  assert.ok(html.includes('<span class="fact copy-closed">クローズ</span>'));
  assert.ok(/<span class="fact review" title="[^"]*">人レビュー要<\/span>/.test(html));
  assert.ok(/<span class="fact worktree" title="[^"]*">作業ツリー i0001<\/span>/.test(html));
  assert.ok(/<span class="fact sha" title="[0-9a-f]+">base [0-9a-f]{7}<\/span>/.test(html));
  assert.ok(html.includes('<span class="fact risk risk-low">リスク LOW（0 点）</span>'));
  // 属性は列からはみ出さず、フェーズ行の右側は折り返す
  assert.match(html, /\.fact \{ white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; \}/);
  assert.match(html, /\.phase-status \{ text-align: right; overflow-wrap: anywhere; max-width: 55%; justify-self: end; \}/);
  assert.match(html, /\.phase \{ display: grid; grid-template-columns: 12px minmax\(0, 1fr\) minmax\(0, auto\);/);
  assert.ok(!html.includes('class="badge copy copy-open"'));
  assert.ok(!html.includes('class="badge review"'));
  // 写りは子の作業ツリーに普通に入るので、正常な場面ではバッジを出さない
  assert.ok(!html.includes("複数の場所にある"));
  assert.ok(html.includes('<span class="where">子 · 親 i0001 / フェーズ 2</span>'));
  assert.ok(html.includes('class="phases"'));
  // 親のフェーズは 1 段階 1 行。ゲート開・マーカーなし・レビュー不要は普通の状態なので書かない
  assert.ok(html.includes('<li class="phase phase-ended"><span class="phase-dot" aria-hidden="true"></span><span class="phase-name"><span class="phase-label">1（調査）</span><span class="phase-tickets">i0001-01</span></span><span class="phase-status">終了 · リスク: 0 (LOW)</span></li>'));
  assert.ok(html.includes('<span class="phase-status">進行中 · レビュー要</span>'));
  assert.ok(!html.includes("ゲート開"));
  assert.ok(!html.includes("マーカーなし"));
  assert.doesNotMatch(html, /class="phase-status">[^<]*レビュー不要/);
  // ゲート閉のフェーズ行は段階名も右の状態も赤
  assert.match(html, /\.phase\.gate-closed \.phase-label, \.phase\.gate-closed \.phase-status \{ color: var\(--vscode-editorError-foreground\); \}/);
  // ゲート閉の左線は承認待ちの左線より後に書き、勝つ
  assert.ok(html.indexOf(".card.pending { border-left") < html.indexOf(".card.gate-closed { border-left"));
  // 締める（wrapup）のボタンは出さない
  assert.ok(!html.includes('data-action="wrapup"'));
  assert.ok(!html.includes("締める"));
});

test("CB-T13b 親の絞り込みを出し、カードに家族を付ける", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(html.includes('id="parent-filter"'));
  assert.ok(/<option value="i0001">i0001 [^<]+<\/option>/.test(html));
  assert.equal((html.match(/data-family="i0001"/g) ?? []).length, 4);
  const empty = { ...fixture(), tickets: [], parents: [], pending_approval: [] };
  assert.ok(!renderBoard(buildBoard(empty), OPTIONS).includes('id="parent-filter"'));
});

test("CB-T14 0 件のときは空の表示と無効な承認ボタン", () => {
  const empty = { ...fixture(), tickets: [], parents: [], pending_approval: [] };
  const html = renderBoard(buildBoard(empty), OPTIONS);
  assert.ok(html.includes("チケットは無い"));
  assert.equal((html.match(/class="empty"/g) ?? []).length, 4);
  assert.ok(html.includes('data-action="approve" disabled'));
});

test("CB-T15 問題とプロジェクトの絞り込みを出す", () => {
  const json = { ...fixture(), problems: ["承認済みチケット x を読めない"], projects: ["lib", "app"] };
  const html = renderBoard(buildBoard(json), OPTIONS);
  assert.ok(html.includes('class="problems"'));
  assert.ok(html.includes("承認済みチケット x を読めない"));
  assert.ok(html.includes('id="project-filter"'));
  assert.ok(html.includes('<option value="lib">lib</option>'));
  const without = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(!without.includes('id="project-filter"'));
});

test("CB-T16 本文の文字列で表示を壊さない", () => {
  const base = fixture();
  const evil = { ...base.tickets[0], title: `<script>alert("x")</script>` };
  const html = renderBoard(buildBoard({ ...base, tickets: [evil, ...base.tickets.slice(1)] }), OPTIONS);
  assert.ok(!html.includes(`<script>alert("x")</script>`));
  assert.ok(html.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"));
  assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("CB-T118 本物が決まらない写りだけをバッジにし、場所を tooltip に出す", () => {
  const base = fixture();
  const child = base.tickets.find((t) => t.ticket === "i0001-03")!;
  const where = [
    { tree: "", state: "todo", path: "/x/wip/tickets/todo/i0001-03.md" },
    { tree: "i0001-02", state: "todo", path: "/x/w/i0001-02/wip/tickets/todo/i0001-03.md" },
  ];
  const homeless: TicketJson = { ...child, seen_in: where, scattered: where };
  const html = renderBoard(buildBoard({ ...base, tickets: [homeless] }), OPTIONS);
  assert.ok(html.includes("複数の場所にある（2 か所）"));
  assert.ok(html.includes('title="main:todo, i0001-02:todo"'));
});

test("CB-T127 5 つの画面は同じ骨組みの CSS（ツールバー・帯・欄・脚注）を 1 つの定数から持つ", () => {
  const lock = { locked: false, reason: "", doing: [] };
  const pages = [
    renderBoard(buildBoard(fixture()), OPTIONS),
    renderRulesPage({ root: "/ws", rulesPath: "r.yml", mode: "enable", model: readRules("deny: []\n").model, hooks: [], hookFiles: { settings: true, settingsLocal: false }, samplesPath: "s.yml", lock }, OPTIONS),
    renderRiskPage({ root: "/ws", riskPath: "risks.yml", exists: true, ticketControl: "enable", model: readRisk(BUILTIN_RISK_TEXT).model, lock }, OPTIONS),
    renderPhasesPage({ root: "/ws", phasesPath: "phases.yml", exists: true, ticketControl: "enable", model: readPhases(TEMPLATE_PHASES_TEXT).model, lock }, OPTIONS),
    renderProjectsPage(buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", projectsDirExists: true, ignored: true, rulesRels: {}, rulesExists: {}, hasClaudeDir: {}, selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false }), OPTIONS),
  ];
  assert.match(PAGE_STYLE, /\.toolbar \{/);
  assert.match(PAGE_STYLE, /\.banner\.warn \{/);
  assert.match(PAGE_STYLE, /input\[type=text\], input\[type=search\], textarea, select \{/);
  for (const html of pages) {
    assert.ok(html.includes(PAGE_STYLE));
    // 骨組みの定義は 1 度だけ（画面ごとの写しを残さない）
    assert.equal((html.match(/  \.toolbar \{ display: flex;/g) ?? []).length, 1);
    // 見た目を指定しなければ素の body。Claude の配色の CSS と、切り替えの受け口は常に持つ
    assert.ok(html.includes("\n<body>\n"));
    assert.ok(html.includes("body.ccnavi-claude-light {"));
    assert.ok(html.includes('if (data.type === "appearance") { applyAppearance(data.value); }'));
  }
  const themed = [
    renderBoard(buildBoard(fixture()), { ...OPTIONS, appearance: "claude-dark" }),
    renderRulesPage({ root: "/ws", rulesPath: "r.yml", mode: "enable", model: readRules("deny: []\n").model, hooks: [], hookFiles: { settings: true, settingsLocal: false }, samplesPath: "s.yml", lock }, { ...OPTIONS, appearance: "claude-dark" }),
    renderRiskPage({ root: "/ws", riskPath: "risks.yml", exists: true, ticketControl: "enable", model: readRisk(BUILTIN_RISK_TEXT).model, lock }, { ...OPTIONS, appearance: "claude-dark" }),
    renderPhasesPage({ root: "/ws", phasesPath: "phases.yml", exists: true, ticketControl: "enable", model: readPhases(TEMPLATE_PHASES_TEXT).model, lock }, { ...OPTIONS, appearance: "claude-dark" }),
    renderProjectsPage(buildProjectsPage({ board: fixture(), lint: undefined, lintError: "", origins: {}, strays: [], projectsRel: "projects", projectsDirExists: true, ignored: true, rulesRels: {}, rulesExists: {}, hasClaudeDir: {}, selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false }), { ...OPTIONS, appearance: "claude-dark" }),
  ];
  for (const html of themed) {
    assert.ok(html.includes('\n<body class="ccnavi-claude-dark">\n'));
  }
});

test("CB-T130 ハイコントラスト向けの縁は contrast の変数を使い、他のテーマでは効かない書き方になっている", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 一覧（設定 3 画面）の開いた行の縁は LIST_STYLE にあるので、ルール設定画面で見る
  const rules = renderRulesPage({ root: "/ws", rulesPath: "r.yml", mode: "enable", model: readRules("deny: []\n").model, hooks: [], hookFiles: { settings: true, settingsLocal: false }, samplesPath: "s.yml", lock: { locked: false, reason: "", doing: [] } }, OPTIONS);
  assert.match(rules, /\.row\.open > \.row-head, \.row\.open > \.row-body \{ box-shadow: inset 3px 0 0 var\(--vscode-contrastActiveBorder, var\(--vscode-focusBorder\)\); \}/);
  assert.match(html, /button\.action:disabled \{ border-color: var\(--vscode-contrastBorder, transparent\); border-style: dashed; \}/);
  assert.match(html, /\.card:hover \{ outline: 1px dashed var\(--vscode-contrastActiveBorder, var\(--vscode-focusBorder\)\); \}/);
});
