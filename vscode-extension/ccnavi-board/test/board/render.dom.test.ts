/**
 * ボード画面が何を描くか。React の画面を happy-dom で動かし、出来上がった DOM を見る。
 * 操作の続き（畳む・絞り込み・承認の送り先）は board.dom.test.ts。
 *
 * CSS は画面の中に文字列で入っているので、規則そのものを見たいところは HTML を見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { parseApprovePreview, type ApprovePreview } from "../../src/core/approvemodel.js";
import { buildBoard } from "../../src/core/board.js";
import { escapeHtml } from "../../src/core/render.js";
import type { ParentJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";
import { NONCE, boardPage, openBoard, openPage } from "../helpers/board.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

function approvePreview(): ApprovePreview {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", "approve-preview.json"), "utf8");
  const parsed = parseApprovePreview(text);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}

/** 見本のボードの HTML。CSS と nonce を文字列で見るときに使う */
function html(): string {
  return boardPage({ kind: "board", board: buildBoard(fixture()) });
}

function text(page: DomPage, selector: string): string {
  return page.one(selector).textContent;
}

function texts(page: DomPage, selector: string): string[] {
  return page.all(selector).map((element) => element.textContent);
}

test("CB-T107 承認のオーバーレイに一覧・本文・対象外を出し、見せた識別子を承認ボタンに持たせる", async () => {
  const preview = approvePreview();
  const page = await openBoard(fixture(), { approval: { kind: "preview", preview } });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "preview");
    // 種類の範囲を超える子（i0001-02）は承認を止めないので一覧に載り、超過は本文の見出しに出る。
    assert.equal(text(page, "#approval-title"), "Ticket 承認リクエスト: 3 件");
    assert.deepEqual(texts(page, ".approval-batch td.approval-id"), ["i0001", "i0001-01", "i0001-02"]);
    const confirm = page.one('button[data-action="approve-confirm"]');
    assert.equal(confirm.getAttribute("data-tickets"), "i0001,i0001-01,i0001-02");
    assert.equal(confirm.textContent, "この 3 件を承認する");
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    const body = text(page, "pre.approval-text");
    assert.ok(body.startsWith("Ticket 承認リクエスト"));
    assert.ok(body.includes("判定で止まるもの"));
    assert.ok(body.includes("超えている"));
    // 対象にしないのは形の壊れた子（計画に無い番号）。
    assert.deepEqual(texts(page, ".approval h3"), ["承認の対象にしない"]);
    assert.ok(text(page, ".approval-rejected").includes("i0001-05"));
    assert.ok(text(page, ".approval-rejected").includes("計画に無い"));
    assert.equal(page.all(".approval-problems").length, 0);
  } finally {
    await page.close();
  }
  // 本文に何が書かれていても、文字として出すだけ。
  const spiked = await openBoard(fixture(), { approval: { kind: "preview", preview: { ...preview, text: "<script>alert(1)</script>" } } });
  try {
    assert.equal(text(spiked, "pre.approval-text"), "<script>alert(1)</script>");
    assert.equal(spiked.all("pre.approval-text script").length, 0);
  } finally {
    await spiked.close();
  }
});

test("CB-T108 承認の対象が空なら承認ボタンを出さず、承認中はボタンを押せず、食い違いの注意を出す", async () => {
  const preview = approvePreview();
  const empty = await openBoard(fixture(), {
    approval: { kind: "preview", preview: { ...preview, batch: [], text: "承認待ちのチケットは無い。" } },
  });
  try {
    assert.ok(texts(empty, ".approval-note").includes("承認待ちのチケット無し"));
    // 実行ファイルの本文（文末に句点が付く文）はそのまま出す。画面のラベルとは別物。
    assert.equal(text(empty, "pre.approval-text"), "承認待ちのチケットは無い。");
    assert.equal(empty.all('button[data-action="approve-confirm"]').length, 0);
  } finally {
    await empty.close();
  }
  const approving = await openBoard(fixture(), { approval: { kind: "approving", preview } });
  try {
    assert.equal(approving.one(".approval-backdrop").getAttribute("data-approval"), "approving");
    const confirm = approving.one<HTMLButtonElement>('button[data-action="approve-confirm"]');
    assert.equal(confirm.textContent, "承認中…");
    assert.equal(confirm.disabled, true);
    assert.equal(approving.one<HTMLButtonElement>('button[data-action="approve-cancel"]').disabled, true);
  } finally {
    await approving.close();
  }
  const noticed = await openBoard(fixture(), { approval: { kind: "preview", preview, notice: "見せた一覧と今の一覧が違った" } });
  try {
    assert.equal(text(noticed, ".approval-note.warn"), "見せた一覧と今の一覧が違った");
  } finally {
    await noticed.close();
  }
  const failed = await openBoard(fixture(), { approval: { kind: "error", error: "実行ファイルが無い" } });
  try {
    assert.equal(text(failed, ".approval-note.error"), "実行ファイルが無い");
  } finally {
    await failed.close();
  }
});

test("CB-T108b 承認したら同じオーバーレイに文とコピー・新しいセッションで開く・閉じるを出す", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "done", count: 1, prompt: "i0001-03 を承認した <b>" } });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "done");
    assert.equal(text(page, "#approval-title"), "1 件を承認した");
    // 文は文字として出す。<b> がタグにならない
    assert.equal(text(page, "pre.approval-text"), "i0001-03 を承認した <b>");
    assert.equal(page.all("pre.approval-text b").length, 0);
    // 文は Webview から送らせない。拡張が持っている文を使うので、押したことだけを伝える
    page.click(page.one('button[data-action="prompt-copy"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptCopy" });
    page.click(page.one('button[data-action="prompt-open"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptOpen" });
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    // 運ぶ sh を端末に送ったときだけ、そう言う。
    assert.ok(!texts(page, ".approval-note").some((note) => note.includes("端末に送った")));
  } finally {
    await page.close();
  }
  const carried = await openBoard(fixture(), { approval: { kind: "done", count: 1, prompt: "i0001-03 を承認した", carried: true } });
  try {
    assert.ok(texts(carried, ".approval-note").includes("承認済みチケットのコミットと push を端末に送った。"));
  } finally {
    await carried.close();
  }
});

test("CB-T108c カードの承認はそのカードの識別子だけを絞りとして送る", async () => {
  const pending = fixture().pending_approval[0];
  const page = await openBoard();
  try {
    const one = page.one(`button[data-action="approve-one"][data-ticket="${pending}"]`);
    assert.equal(one.textContent, "この 1 件を承認");
    page.click(one);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: [pending], filtered: true });
    // 上部のボタンは今までどおり見えている承認待ち全部。
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: [pending], filtered: false });
  } finally {
    await page.close();
  }
});

test("CB-T109 オーバーレイを渡さなければ出ない", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all(".approval-backdrop").length, 0);
    assert.equal(page.all('button[data-action="approve-confirm"]').length, 0);
    assert.equal(page.all('.controls button[data-action="approve"]').length, 1);
  } finally {
    await page.close();
  }
});

test("CB-T12 4 列と件数と承認ボタンを出す", async () => {
  const page = await openBoard();
  try {
    assert.deepEqual(texts(page, ".column button.fold .label"), ["未着手", "作業中", "完了", "取り消し"]);
    assert.equal(page.all(".column").length, 4);
    assert.equal(text(page, ".summary .counts"), "残り 4 / 全 6");
    assert.equal(text(page, ".summary .pending"), "承認待ち 1 件");
    // 0 件のものは見出しに出さない
    assert.equal(page.all(".summary .issues").length, 0);
    const approve = page.one<HTMLButtonElement>('.controls button[data-action="approve"]');
    assert.equal(approve.textContent, "承認待ち 1 件を承認");
    assert.equal(approve.disabled, false);
  } finally {
    await page.close();
  }
  // nonce は style と script の両方に付く（CSP が nonce だけを通す）
  assert.ok(html().includes(`<style nonce="${NONCE}">`));
  assert.ok(html().includes(`<script nonce="${NONCE}">`));
});

test("CB-T12b 列ごとに畳むボタンを出す", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all("button.fold").length, 4);
    for (const state of ["todo", "doing", "done", "cancelled"]) {
      assert.equal(page.one(`button.fold[data-fold="${state}"]`).getAttribute("aria-expanded"), "true", state);
    }
  } finally {
    await page.close();
  }
});

test("CB-T12c 列の件数は見えているカードの数。畳んだ列は固定幅に縛られない", async () => {
  const page = await openBoard();
  try {
    for (const column of page.all(".column")) {
      const visible = column.querySelectorAll(".card:not(.hidden)").length;
      assert.equal(column.querySelector(":scope > h2 > .count")?.textContent, String(visible), column.getAttribute("data-state") ?? "");
    }
  } finally {
    await page.close();
  }
  // ドラッグで付けたインラインの width より畳んだ状態を優先する
  assert.match(html(), /\.column\.folded \{[^}]*width: auto !important/);
});

test("CB-T12d 承認ボタンは見えている承認待ちの数を出し、その識別子を送る。上部の集計は絞らない", async () => {
  const page = await openBoard();
  try {
    // 上部の集計は絞り込みに関わらずボード全体の数
    assert.equal(text(page, ".summary .pending"), "承認待ち 1 件");
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    // 識別子と「絞り込み中か」を別々に送る。空の並びを「全部」に読ませない
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: false });
  } finally {
    await page.close();
  }
});

test("CB-T13c フェーズ行の要約は札と同じ条件（レビュー準備中／レビュー待ち・HIGH 以上）だけ。マーカーの経過と MEDIUM 以下のリスクは全文にだけ出る", async () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson => {
      if (p.number === 1) {
        // 依頼して済んだレビューと HIGH のリスク。要約はリスクだけ（止まらなくなれば段の名前は出ない）、
        // 全文には点と理由とマーカーが残る
        return {
          ...p,
          review_required: true,
          marks: { requested: { at: "t" }, reviewed: { at: "t" } },
          risk: { ...(p.risk ?? {}), points: 40, level: "HIGH" },
          risk_line: "リスク: 40 (HIGH) — 行数が多い（6509 行 > 300）",
        };
      }
      // 依頼済みで止まったまま（判定は review_waiting で言う）。要約に「レビュー待ち」と
      // 受け入れボタンが並ぶ
      return { ...p, state: "ended", gate_closed: true, review_waiting: true, marks: { requested: { at: "t" } } };
    }),
  };
  const page = await openBoard({ ...base, parents: [parent] });
  try {
    const rows = page.all(".card.parent .phase");
    assert.equal(rows[0].querySelector(".phase-brief")?.textContent, "リスク HIGH");
    assert.equal(rows[0].querySelector(".phase-full")?.textContent, "終了 · レビュー依頼済 · レビュー済 · レビュー要 · リスク: 40 (HIGH) — 行数が多い（6509 行 > 300）");
    assert.equal(rows[1].querySelector(".phase-brief")?.textContent, "レビュー待ち");
    assert.equal(rows[1].querySelector(".phase-full")?.textContent, "終了 · レビュー待ち · レビュー依頼済 · レビュー要");
    assert.equal(rows[1].querySelectorAll('button[data-action="accept"]').length, 1);
  } finally {
    await page.close();
  }
  // MEDIUM は要約に出ない
  const medium: ParentJson = {
    ...parent,
    phases: parent.phases.map((p): PhaseJson => (p.number === 1 ? { ...p, risk: { ...(p.risk ?? {}), level: "MEDIUM" }, risk_line: "リスク: 25 (MEDIUM)" } : p)),
  };
  const page2 = await openBoard({ ...base, parents: [medium] });
  try {
    const row = page2.all(".card.parent .phase")[0];
    assert.equal(row.querySelector(".phase-brief")?.textContent, "");
    assert.equal(row.querySelector(".phase-full")?.textContent, "終了 · レビュー依頼済 · レビュー済 · レビュー要 · リスク: 25 (MEDIUM)");
  } finally {
    await page2.close();
  }
  // 依頼を出していないフェーズは「レビュー準備中」。受け入れボタンも出ない
  const unasked: ParentJson = {
    ...parent,
    phases: parent.phases.map((p): PhaseJson => (p.number === 2 ? { ...p, marks: {}, review_waiting: false } : p)),
  };
  const page3 = await openBoard({ ...base, parents: [unasked] });
  try {
    const row = page3.all(".card.parent .phase")[1];
    assert.equal(row.querySelector(".phase-brief")?.textContent, "レビュー準備中");
    assert.equal(row.querySelector(".phase-full")?.textContent, "終了 · レビュー準備中 · レビュー要");
    assert.equal(row.querySelectorAll("button").length, 0);
  } finally {
    await page3.close();
  }
  // 狭いとき全文は画面の外に置くだけで、読み上げには残す。要約は見た目だけ
  assert.match(html(), /\.phase-full \{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset\(50%\); white-space: nowrap; \}/);
  assert.doesNotMatch(html(), /\.phase-full \{ display: none/);
});

test("CB-T13 カードにバッジ・フェーズ・操作を出す。札は人が動く状態だけで、属性は枠無しの行に出す", async () => {
  const page = await openBoard();
  try {
    // 人が動く状態は枠付きの札
    assert.equal(text(page, ".badge.copy.copy-none"), "未承認");
    assert.equal(text(page, ".badge.worktree.none"), "ワークツリーなし");
    // 属性は枠無しの fact。承認済・レビューの要否・ワークツリーの名前・base
    assert.ok(texts(page, ".fact.copy-open").includes("承認済"));
    // レビュー待ちは列ではなく属性。カードは作業中の列にある
    assert.equal(text(page, '.column[data-state="doing"] .card[data-id="i0001-04"] .fact.copy-review'), "レビュー待ち");
    assert.ok(texts(page, ".fact.copy-closed").includes("クローズ"));
    // 取り消しは列で分かるので、カードには重ねて書かない
    assert.equal(page.all('.column[data-state="cancelled"] .card[data-id="i0001-05"]').length, 1);
    assert.equal(page.all(".fact.cancelled").length, 0);
    // 取り消した子にはワークツリーが無いが、閉じているので「ワークツリーなし」の札は出ない
    assert.equal(page.all(".badge.worktree.none").length, 1);
    assert.ok(page.one(".fact.review").getAttribute("title") !== "");
    assert.equal(text(page, ".fact.review"), "人レビュー要");
    assert.equal(text(page, ".fact.worktree:not(.none)"), "ワークツリー i0001");
    assert.match(text(page, ".fact.sha"), /^base [0-9a-f]{7}$/);
    assert.match(page.one(".fact.sha").getAttribute("title") ?? "", /^[0-9a-f]+$/);
    assert.ok(texts(page, ".fact.risk.risk-low").includes("リスク LOW（0 点）"));
    // 承認済みとレビューの要否は札にしない
    assert.equal(page.all(".badge.copy.copy-open").length, 0);
    assert.equal(page.all(".badge.review").length, 0);
    // 写りは子のワークツリーに普通に入るので、正常な場面ではバッジを出さない
    assert.equal(page.all(".badge.seen").length, 0);
    assert.ok(texts(page, ".card .where").includes("子 · 親 i0001 / フェーズ 2"));
    // 親のフェーズは 1 段階 1 行。状態は要約と全文を持ち、全文は行の title にも置く。
    // 順調に終わった段階（LOW のリスク）も、レビューが要るだけの進行中の段階も、要約は空。
    const rows = page.all(".card.parent .phase");
    assert.equal(rows[0].getAttribute("class"), "phase phase-ended");
    assert.equal(rows[0].getAttribute("title"), "終了 · リスク: 0 (LOW)");
    assert.equal(rows[0].querySelector(".phase-label")?.textContent, "1（調査）");
    assert.equal(rows[0].querySelector(".phase-tickets")?.textContent, "i0001-01");
    assert.equal(rows[0].querySelector(".phase-dot")?.getAttribute("aria-hidden"), "true");
    assert.equal(rows[0].querySelector(".phase-brief")?.textContent, "");
    assert.equal(rows[0].querySelector(".phase-brief")?.getAttribute("aria-hidden"), "true");
    assert.equal(rows[0].querySelector(".phase-full")?.textContent, "終了 · リスク: 0 (LOW)");
    assert.ok(page.all(".phase-full").some((full) => full.textContent === "進行中 · レビュー要"));
    // 止めていない・マーカーなし・レビュー不要は普通の状態なので書かない
    assert.ok(!texts(page, ".phase-full").some((full) => full.includes("レビュー不要")));
    // 締める（wrapup）のボタンは出さない
    assert.equal(page.all('button[data-action="wrapup"]').length, 0);
  } finally {
    await page.close();
  }
  // 属性は列からはみ出さない
  assert.match(html(), /\.fact \{ white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; \}/);
  // フェーズ行は 1 段階 1 行。右に auto の列を置くと状態の 1 行分の幅が行を占め、段階名の列が
  // 0 になって消えるので、状態の列は 55% で止める。幅を測るのはフェーズ一覧自身
  assert.match(html(), /\.phases \{[^}]*container-type: inline-size; \}/);
  assert.match(html(), /\.phase \{ display: grid; grid-template-columns: 12px minmax\(0, 1fr\) fit-content\(55%\);/);
  assert.match(html(), /\.phase-name \{ min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}/);
  assert.doesNotMatch(html(), /\.phase \{[^}]*minmax\(0, auto\)/);
  // 狭いときは要約だけを見せ、480px 以上で全文に替わる
  const wide = html().match(/@container \(min-width: 480px\) \{[^@]*?\n  \}/);
  assert.ok(wide, "@container の塊がある");
  assert.match(wide[0], /\.phase-brief \{ display: none; \}/);
  assert.match(wide[0], /\.phase-full \{ position: static;[^}]*clip-path: none;/);
  // 止めているフェーズ行は段階名も右の状態も赤
  assert.match(html(), /\.phase\.review-hold \.phase-label, \.phase\.review-hold \.phase-status \{ color: var\(--vscode-editorError-foreground\); \}/);
  // 止めているカードの左線は承認待ちの左線より後に書き、勝つ
  assert.ok(html().indexOf(".card.pending { border-left") < html().indexOf(".card.review-hold { border-left"));
});

test("CB-T13a 止めている間だけ段の名前を札に出す。レビューが済んで止まらなくなった子には出さない", async () => {
  const base = fixture();
  // 判定が出す形に揃える。止まるのはレビュー要のときで、レビュー待ちは「依頼済 かつ 止まっている」を判定が言う
  const withMarks = (marks: Record<string, Record<string, unknown>>, gateClosed: boolean) => ({
    ...base,
    parents: base.parents.map((parent) => ({
      ...parent,
      phases: parent.phases.map((p) =>
        p.number === 1
          ? { ...p, marks, review_required: true, gate_closed: gateClosed, review_waiting: gateClosed && "requested" in marks }
          : p,
      ),
    })),
  });
  const brief = (page: DomPage): string => page.all(".card.parent .phase")[0].querySelector(".phase-brief")?.textContent ?? "";
  const full = (page: DomPage): string => page.all(".card.parent .phase")[0].querySelector(".phase-full")?.textContent ?? "";

  // クローズ・レビュー済・止まっていない子（完了列の i0001-01）。札は出さず、レビュー済は枠無しの行に出る。
  // 親カードのフェーズ行の要約にも出ない。全文には経過として「レビュー依頼済 · レビュー済」が残る
  const done = await openBoard(withMarks({ requested: { at: "t" }, reviewed: { at: "t" } }, false));
  try {
    assert.equal(done.all(".badge.hold").length, 0);
    assert.ok(texts(done, ".fact.mark.mark-reviewed").includes("レビュー済"));
    assert.equal(brief(done), "");
    assert.equal(full(done), "終了 · レビュー依頼済 · レビュー済 · レビュー要 · リスク: 0 (LOW)");
  } finally {
    await done.close();
  }
  // 依頼済のマーカーだけで止まっていない（判定が待ちと言わない）子にも、札と要約は出ない
  const reopened = await openBoard(withMarks({ requested: { at: "t" } }, false));
  try {
    assert.equal(reopened.all(".badge.hold").length, 0);
    assert.equal(brief(reopened), "");
  } finally {
    await reopened.close();
  }
  // 依頼を出したのに止まったままの子には札が出て、親のフェーズ行の要約にも出る。reviewed の有無では分岐しない
  const waiting = await openBoard(withMarks({ requested: { at: "t" } }, true));
  try {
    assert.ok(texts(waiting, ".badge.hold").includes("レビュー待ち"));
    assert.equal(brief(waiting), "レビュー待ち");
    assert.equal(full(waiting), "終了 · レビュー待ち · レビュー依頼済 · レビュー要 · リスク: 0 (LOW)");
  } finally {
    await waiting.close();
  }
  const stillClosed = await openBoard(withMarks({ requested: { at: "t" }, reviewed: { at: "t" } }, true));
  try {
    assert.ok(texts(stillClosed, ".badge.hold").includes("レビュー待ち"));
  } finally {
    await stillClosed.close();
  }
  // 依頼を出していない子の札は「レビュー準備中」で、依頼済とは出ない
  const notRequested = await openBoard(withMarks({}, true));
  try {
    assert.ok(texts(notRequested, ".badge.hold").includes("レビュー準備中"));
    assert.equal(brief(notRequested), "レビュー準備中");
    assert.ok(!notRequested.document.body.textContent.includes("レビュー依頼済"));
  } finally {
    await notRequested.close();
  }
});

test("CB-T13b 親の絞り込みを出し、カードに家族を付ける", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.all("#parent-filter").length, 1);
    assert.ok(texts(page, "#parent-filter option").some((option) => /^i0001 .+/.test(option)));
    assert.equal(page.all('.card[data-family="i0001"]').length, 6);
  } finally {
    await page.close();
  }
  const empty = await openBoard({ ...fixture(), tickets: [], parents: [], pending_approval: [] });
  try {
    assert.equal(empty.all("#parent-filter").length, 0);
  } finally {
    await empty.close();
  }
});

test("CB-T14 0 件のときは空の表示と無効な承認ボタン", async () => {
  const page = await openBoard({ ...fixture(), tickets: [], parents: [], pending_approval: [] });
  try {
    assert.equal(text(page, ".board-empty"), "チケット無し");
    assert.equal(page.all(".column .empty").length, 4);
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').disabled, true);
  } finally {
    await page.close();
  }
});

test("CB-T15 問題とプロジェクトの絞り込みを出す", async () => {
  const page = await openBoard({ ...fixture(), problems: ["承認済みチケット x を読めない"], projects: ["lib", "app"] });
  try {
    assert.deepEqual(texts(page, ".problems li"), ["承認済みチケット x を読めない"]);
    assert.deepEqual(texts(page, "#project-filter option"), ["すべて", "ワークスペース本体", "lib", "app"]);
  } finally {
    await page.close();
  }
  const without = await openBoard();
  try {
    assert.equal(without.all("#project-filter").length, 0);
    assert.equal(without.all(".problems").length, 0);
  } finally {
    await without.close();
  }
});

test("CB-T16 本文の文字列で表示を壊さない", async () => {
  const base = fixture();
  const evil = { ...base.tickets[0], title: `<script>alert("x")</script>` };
  const page = await openBoard({ ...base, tickets: [evil, ...base.tickets.slice(1)] });
  try {
    assert.equal(text(page, `.card[data-id="${evil.ticket}"] .title`), `<script>alert("x")</script>`);
    // 画面の中に script は 1 本（束ねた画面）だけ。中身から生えない
    assert.equal(page.all(".card script").length, 0);
  } finally {
    await page.close();
  }
  // 設定 3 画面とプロジェクト管理は今も文字列で組み立てるので、逃がし方は変えていない
  assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("CB-T118 本物が決まらない写りだけをバッジにし、場所を tooltip に出す", async () => {
  const base = fixture();
  const child = base.tickets.find((t) => t.ticket === "i0001-03")!;
  const where = [
    { tree: "", state: "todo", path: "/x/wip/proposals/todo/i0001-03.md" },
    { tree: "i0001-02", state: "todo", path: "/x/w/i0001-02/wip/proposals/todo/i0001-03.md" },
  ];
  const homeless: TicketJson = { ...child, seen_in: where, scattered: where };
  const page = await openBoard({ ...base, tickets: [homeless] });
  try {
    assert.equal(text(page, ".badge.seen"), "複数の場所にある（2 か所）");
    assert.equal(page.one(".badge.seen").getAttribute("title"), "main:todo, i0001-02:todo");
  } finally {
    await page.close();
  }
});

/** フェーズ 2 を人のレビュー待ちにし、依頼のマーカーに MR を持たせる */
function waitingWithMr(url: string) {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson =>
      p.number === 2
        ? {
            ...p,
            state: "ended",
            gate_closed: true,
            review_required: true,
            review_waiting: true,
            marks: { requested: { head: "abc", mr: 18, url, host: "github", since: "t", at: "t" } },
          }
        : p,
    ),
  };
  return { ...base, parents: [parent] };
}

test("CB-T131r レビュー待ちのフェーズ行に「レビュー済み連絡」と依頼へのリンク、親カードに MR へのリンクを出す。http(s) 以外はリンクにしない", async () => {
  const page = await openBoard(waitingWithMr("https://example.com/o/r/pull/18#issuecomment-5"));
  try {
    // 受け入れの隣に連絡のボタン。マーカーを置く操作ではないと title で言う
    const accept = page.one('button[data-action="accept"]');
    assert.equal(accept.getAttribute("data-parent"), "i0001");
    assert.equal(accept.getAttribute("data-phase"), "2");
    const reviewed = page.one('button[data-action="reviewed"]');
    assert.equal(reviewed.textContent, "レビュー済み連絡");
    assert.equal(
      reviewed.getAttribute("title"),
      "レビューを終えたことを Claude Code に伝える文を作る（エージェントが ccnavi-review.sh check --phase 2 を打つ）",
    );
    page.click(reviewed);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "reviewed", parent: "i0001", phase: 2 });
    page.click(accept);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "accept", parent: "i0001", phase: 2 });
    // フェーズ行は依頼の投稿へ、親カードはマージリクエスト自体へ
    const inPhase = page.one(".phase a.mr-link");
    assert.equal(inPhase.getAttribute("href"), "https://example.com/o/r/pull/18#issuecomment-5");
    assert.equal(inPhase.getAttribute("title"), "フェーズ 2（設計） のレビューの依頼を開く");
    assert.equal(inPhase.textContent, "MR #18");
    const inCard = page.one(".card-head + .facts a.mr-link, .facts a.mr-link");
    assert.equal(inCard.getAttribute("href"), "https://example.com/o/r/pull/18");
    assert.equal(inCard.getAttribute("title"), "マージリクエストを開く");
  } finally {
    await page.close();
  }
  // 依頼していない見本にはリンクもボタンも無い
  const plain = await openBoard();
  try {
    assert.equal(plain.all("a.mr-link").length, 0);
    assert.equal(plain.all('button[data-action="reviewed"]').length, 0);
  } finally {
    await plain.close();
  }
  // http(s) 以外の URL は文字として出すだけで、href にしない
  const spiked = await openBoard(waitingWithMr("javascript:alert(1)"));
  try {
    assert.equal(spiked.all("a.mr-link").length, 0);
    const fact = spiked.one("span.fact.mr");
    assert.equal(fact.textContent, "MR #18");
    assert.equal(fact.getAttribute("title"), "javascript:alert(1)");
  } finally {
    await spiked.close();
  }
});

test("CB-T131o レビュー済みの連絡のオーバーレイは、題・注意・文と、承認と同じコピー・新しいセッションで開く・閉じる", async () => {
  const page = await openBoard(fixture(), {
    approval: { kind: "prompt", title: "フェーズ 2 のレビュー済みを連絡", note: "注意 <i>", prompt: "[ccnavi] レビューを終えた <b>" },
  });
  try {
    assert.equal(page.one(".approval-backdrop").getAttribute("data-approval"), "prompt");
    assert.equal(text(page, "#approval-title"), "フェーズ 2 のレビュー済みを連絡");
    assert.equal(text(page, ".approval-note"), "注意 <i>");
    assert.equal(text(page, "pre.approval-text"), "[ccnavi] レビューを終えた <b>");
    assert.equal(page.all('button[data-action="prompt-copy"]').length, 1);
    assert.equal(page.all('button[data-action="prompt-open"]').length, 1);
    assert.equal(page.all('button[data-action="approve-cancel"]').length, 1);
    assert.ok(!text(page, ".approval").includes("を承認した"));
  } finally {
    await page.close();
  }
});

test("CB-T132r 「要対応だけ」の絞り込みを出し、カードに要対応かどうかを付ける。判定は組み立てが出した値を写すだけ", async () => {
  const page = await openBoard();
  try {
    const label = page.one("label.filter.attention");
    assert.equal(
      label.getAttribute("title"),
      "人が動く必要があるカードだけを出す（承認待ち・レビュー準備中／レビュー待ち・ワークツリーなし・HIGH 以上のリスク・不備）",
    );
    assert.equal(label.textContent.trim(), "要対応だけ");
    assert.equal(page.one('.card[data-id="i0001-03"]').getAttribute("data-attention"), "1");
    assert.equal(page.one('.card[data-id="i0001"]').getAttribute("data-attention"), "0");
    assert.equal(page.one('.card[data-id="i0001-01"]').getAttribute("data-attention"), "0");
    // 絞り込み中の扱い（filtering）に入るので、承認は見えている承認待ちだけを送る
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(page.document.body.classList.contains("filtering"));
  } finally {
    await page.close();
  }
});

test("CB-T133 読み直せなかった画面にも承認のオーバーレイが載り、閉じる手立てが付いてくる", async () => {
  const plain = await openPage({ kind: "error", error: "ccnavi --explain --json が失敗した: 60 秒で返らないので打ち切った" });
  try {
    assert.ok(text(plain, ".board-empty").includes("ボードを読み直せなかった"));
    assert.ok(text(plain, "pre.load-error").includes("60 秒で返らないので打ち切った"));
    assert.equal(plain.all(".approval-backdrop").length, 0, "オーバーレイが無ければ被せない");
    // ボードの部品は出さない
    assert.equal(plain.all(".column").length, 0);
  } finally {
    await plain.close();
  }
  const withApproval = await openPage({
    kind: "error",
    error: "読めない",
    approval: { kind: "done", count: 2, prompt: "i0001-03 を承認した" },
  });
  try {
    assert.equal(text(withApproval, "#approval-title"), "2 件を承認した");
    assert.equal(text(withApproval, "pre.approval-text"), "i0001-03 を承認した");
    // ボタンが効く（画面の骨組みが載っている）
    withApproval.click(withApproval.one('button[data-action="prompt-copy"]'));
    await withApproval.settle();
    assert.deepEqual(withApproval.posted.at(-1), { type: "promptCopy" });
  } finally {
    await withApproval.close();
  }
  // 文に何が入っていても画面を壊さない
  const escaped = await openPage({ kind: "error", error: "<b>", approval: { kind: "error", error: "<script>" } });
  try {
    assert.equal(text(escaped, "pre.load-error"), "<b>");
    assert.equal(text(escaped, ".approval-note.error"), "<script>");
    assert.equal(escaped.all(".approval script").length, 0);
  } finally {
    await escaped.close();
  }
});

test("CB-T141 止まっているカードに「書き込み停止中」の札が出て、理由が tooltip と不備の行に載る", async () => {
  const base = fixture();
  const child = base.tickets.find((t) => t.ticket === "i0001-02")!;
  const reason = "親 i0001 の承認済みチケットが作業中に無い（未承認か、閉じている）";
  const stopped: TicketJson = { ...child, blocked: reason };
  // 親も渡す。外すと「親が見つからない」不備も同時に出て、見たい不備が 1 つに絞れない。
  const parent = base.tickets.find((t) => t.ticket === "i0001")!;
  const page = await openBoard({ ...base, tickets: [parent, stopped] });
  try {
    assert.equal(text(page, ".badge.blocked"), "書き込み停止中");
    assert.equal(page.one(".badge.blocked").getAttribute("title"), reason);
    assert.deepEqual(texts(page, ".card .issues li"), [`書き込みが止まっている: ${reason}`]);
  } finally {
    await page.close();
  }
  // 止まっていないカードには出さない。
  const clean = await openBoard();
  try {
    assert.equal(clean.all(".badge.blocked").length, 0);
  } finally {
    await clean.close();
  }
});

test("CB-T142 見た目の切り替えは body のクラスだけを付け替える。中身は作り直さない", async () => {
  const page = await openBoard();
  try {
    assert.equal(page.document.body.className, "");
    await page.send({ type: "appearance", value: "claude-light" });
    assert.ok(page.document.body.classList.contains("ccnavi-claude-light"));
    await page.send({ type: "appearance", value: "claude-dark" });
    assert.ok(page.document.body.classList.contains("ccnavi-claude-dark"));
    assert.ok(!page.document.body.classList.contains("ccnavi-claude-light"));
    await page.send({ type: "appearance", value: "vscode" });
    assert.equal(page.document.body.className, "");
    // 画面は作り直されていない（列はそのまま）
    assert.equal(page.all(".column").length, 4);
  } finally {
    await page.close();
  }
});
