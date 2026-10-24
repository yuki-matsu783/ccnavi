/**
 * カード 1 枚が何を描くか（React の画面を happy-dom で動かす）。上から 見出し（ID・題・状態の札）・位置・ユーザ待ち・
 * 注意・フェーズ行（親だけ）・補助の操作・詳細（畳む）。札の決め方は status.test.ts、ここは構造・操作・ボタンの規則・押したときの動き。
 * 寸法と見た目は拡張開発ホストでの手動確認（docs/design/manual-checks.md の表）に回す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import type { BoardData } from "../../src/core/board-view.js";
import type { BoardJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import { boardOf, closedJson, parentJson, phaseJson, proposalJson, reviewJson, ticketJson, waitingPhase } from "../helpers/cards.js";
import { boardPage, openBoard, openPage } from "../helpers/board.js";
import { flatStyle } from "../helpers/bundle.js";
import type { DomPage } from "../helpers/dom.js";

const detailsKey = (project: string, id: string): string => JSON.stringify([project, id]);

function card(page: DomPage, id: string, project = ""): string {
  void project;
  return `li.card[data-id="${id}"]`;
}

function textOf(page: DomPage, selector: string): string {
  return page.one(selector).textContent;
}

function pendingOf(json: BoardJson, ...ids: string[]): BoardJson {
  return { ...json, pending_approval: ids };
}

/** 承認待ちの親と、レビュー待ちのフェーズを持つ親・その子、要確認の子を 1 枚ずつ並べたボード */
function scene(): BoardJson {
  return pendingOf(
    boardOf(
      [
        proposalJson("fresh", { proposal: { state: "todo", tree: "fresh", tree_root: "/f", path: "/f/p.md", has_plan: true } }),
        proposalJson("fresh-01-01", { parent: "fresh", phase: 1 }),
        ticketJson("busy", { branch: "feature/x", issue: 12, mr: 7, remote: { branch: "feature/x", ahead: 2, behind: 1 }, human_review: { required: true, reason: "認証まわり" } }),
        ticketJson("busy-01-01", { parent: "busy", phase: 1, worktree: { exists: false, path: "/ws/.claude/worktrees/busy-01-01", project: "" } }),
        reviewJson("busy-02-01", { parent: "busy", phase: 2, risk: { points: 60, level: "HIGH" } }),
        closedJson("old", { cancelled_at: "2026-10-03T00:00:00+0900", cancel_reason: "要件が変わった", base_sha: "a".repeat(40), merged_sha: "b".repeat(40) }),
        ticketJson("held", { blocked: "親が引けない", issues: ["書き込みが止まっています: 親が引けない"], attention: true }),
      ],
      [parentJson("busy", [phaseJson(1, { label: "1（調査）", state: "ended", marks: { skipped: {} }, risk_line: "リスク: 0 (LOW)" }), waitingPhase(2, 18, "https://example.com/o/r/pull/18#issuecomment-5", { label: "2（設計）", risk_line: "リスク: 60 (HIGH)", manual: "手で動かした子の確認待ち" })])],
    ),
    "fresh",
    "fresh-01-01",
  );
}

test("CB-D700 カードは 見出し（ID・題・状態の札）→ 位置 → ユーザ待ち → 注意 → フェーズ行 → 補助の操作 → 詳細 の順に並ぶ", async () => {
  const page = await openBoard(scene());
  try {
    const kindsOf = (id: string): string[] => Array.from(page.one(card(page, id)).children).map((el) => el.className.split(" ")[0]);
    // 親（承認待ち・計画あり）: 見出し・ユーザ待ち・補助（計画編集）・詳細（人間レビューの要否はいつも詳細にある）。注意は出すものが無い
    assert.deepEqual(kindsOf("fresh"), ["card-head", "turn", "card-actions", "card-details"]);
    const parent = page.one(card(page, "busy"));
    assert.deepEqual(kindsOf("busy"), ["card-head", "stage", "badges", "phases", "card-details"]);
    // 見出し: ID・題・札（札は右端の 1 つ）
    const head = parent.querySelector(".card-head")!;
    assert.deepEqual(Array.from(head.children).map((el) => el.className.split(" ")[0]), ["num", "title", "status"]);
    assert.equal(head.querySelectorAll(".status").length, 1);
    // 子: 位置は「親 <ID> / <フェーズ名>」。見出しの中には入れない
    const child = page.one(card(page, "busy-02-01"));
    assert.equal(child.querySelector(".where")?.textContent, "親 busy / 2（設計）");
    assert.equal(head.querySelector(".where"), null);
    assert.deepEqual(kindsOf("busy-02-01"), ["card-head", "where", "turn", "badges", "card-details"]);
    // 子のフェーズが引けなければ、番号で言う
    const orphan = await openBoard(boardOf([reviewJson("lost", { parent: "ghost", phase: 3 })]));
    try {
      assert.equal(orphan.one(card(orphan, "lost")).querySelector(".where")?.textContent, "親 ghost / フェーズ 3");
    } finally {
      await orphan.close();
    }
  } finally {
    await page.close();
  }
});

test("CB-D701 札は見出しの右端に 1 つ。言葉とツールチップは札の決め方のとおりで、色に頼らず言葉を出す", async () => {
  const page = await openBoard(scene());
  try {
    const status = (id: string): string => page.one(`${card(page, id)} .card-head .status`).textContent;
    assert.equal(status("fresh"), "承認待ち");
    assert.equal(status("busy"), "レビュー待ち");
    assert.equal(status("busy-02-01"), "レビュー待ち");
    assert.equal(status("old"), "取り消し");
    assert.equal(status("held"), "要確認");
    assert.equal(page.one(`${card(page, "held")} .status`).getAttribute("title"), "書き込み停止中: 親が引けない");
    assert.equal(page.one(`${card(page, "busy")}`).getAttribute("data-status"), "レビュー待ち");
    // 理由の無い札にはツールチップを付けない
    assert.equal(page.one(`${card(page, "fresh")} .status`).getAttribute("title"), null);
  } finally {
    await page.close();
  }
});

test("CB-D702 ユーザ待ち: 親の承認待ちは「内容を確認して承認してください」と［承認］（主ボタン）。子の承認待ちは［承認］だけ。要確認の札でも承認は出る", async () => {
  const page = await openBoard(
    pendingOf(
      boardOf([
        proposalJson("p", { proposal: { state: "todo", tree: "p", tree_root: "/p", path: "/p/x.md", has_plan: true } }),
        proposalJson("c", { parent: "p", phase: 1, blocked: "停止" }),
      ]),
      "p",
      "c",
    ),
  );
  try {
    const parent = card(page, "p");
    assert.equal(textOf(page, `${parent} .turn-head`), "ユーザ待ち");
    assert.equal(textOf(page, `${parent} .turn-text`), "内容を確認して承認してください");
    const approve = page.one(`${parent} button[data-action="approve-one"]`);
    assert.equal(approve.textContent, "承認");
    assert.ok(approve.classList.contains("primary"));
    page.click(approve);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["p"], filtered: true });
    // 子: 札は「要確認」（blocked）でも、pendingApproval だけを条件に承認が出る。文は付かない
    const child = card(page, "c");
    assert.equal(textOf(page, `${child} .status`), "要確認");
    assert.equal(page.all(`${child} button[data-action="approve-one"]`).length, 1);
    assert.equal(page.all(`${child} .turn-text`).length, 0);
    assert.equal(page.all(`${child} [data-action="review-done"]`).length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D703 ユーザ待ち: 札が「レビュー待ち」の子には［MR を開く］（リンク）と［レビュー完了］（主ボタン）。押すとレビュー完了の流れに入る。ほかの子には出さない", async () => {
  const page = await openBoard(scene());
  try {
    const child = card(page, "busy-02-01");
    assert.equal(textOf(page, `${child} .turn-text`), "マージリクエストでレビューしてください");
    const link = page.one(`${child} .turn a[data-action="open-mr"]`);
    assert.equal(link.textContent, "MR を開く");
    assert.equal(link.getAttribute("href"), "https://example.com/o/r/pull/18", "依頼の投稿ではなくマージリクエスト自体");
    assert.ok(link.classList.contains("card-button") && !link.classList.contains("primary"));
    const done = page.one(`${child} .turn button[data-action="review-done"]`);
    assert.equal(done.textContent, "レビュー完了");
    assert.ok(done.classList.contains("card-button") && done.classList.contains("primary"));
    page.click(done);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decide", parent: "busy", phase: 2 });
    // 承認も出さない（承認待ちでない）
    assert.equal(page.all(`${child} button[data-action="approve-one"]`).length, 0);
    // 作業中の子・要確認の子・完了の子には出さない
    for (const id of ["busy-01-01", "held", "old"]) {
      assert.equal(page.all(`${card(page, id)} .turn`).length, 0, id);
    }
    // 旧「レビューの対応」メニューと「レビュー済み連絡」は無い
    assert.equal(page.all('[data-action="review-menu"], [data-action="reviewed"], .review-menu').length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D704 フェーズ行: 一言（9 通り）・手で動かした注意の 1 行・レビュー待ちの行の［MR を開く］［レビュー完了］。複数が同時に待てば行ごとに置く", async () => {
  const page = await openBoard(scene());
  try {
    const rows = page.all(`${card(page, "busy")} .phase`);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-status")?.textContent), ["レビュー省略", "レビュー待ち"]);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-label")?.textContent), ["1（調査）", "2（設計）"]);
    assert.equal(rows[0].querySelectorAll(".phase-actions").length, 0, "操作の無い行には 2 段目を出さない");
    assert.equal(rows[1].querySelector(".phase-manual")?.textContent, "手で動かした子の確認待ち");
    assert.equal(rows[0].querySelectorAll(".phase-manual").length, 0);
    assert.ok(rows[1].classList.contains("review-hold"));
    assert.equal(rows[1].querySelector("a.card-button")?.getAttribute("href"), "https://example.com/o/r/pull/18");
    const done = rows[1].querySelector('button[data-action="review-done"]')!;
    assert.ok(done.classList.contains("card-button") && done.classList.contains("primary"));
    page.click(done);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decide", parent: "busy", phase: 2 });
    // 状態は 1 か所にだけ描く。段の名前やマーカーの経過は出さない
    assert.ok(!rows[1].textContent.includes("レビュー依頼済み"));
  } finally {
    await page.close();
  }
  // 複数のフェーズが同時にレビュー待ち: それぞれの行に置く。主ボタンは行ごとに 1 つ
  const two = await openBoard(boardOf([ticketJson("p")], [parentJson("p", [waitingPhase(1, 11), waitingPhase(2, 22, "https://example.com/o/r/pull/22")])]));
  try {
    const rows = two.all(`${card(two, "p")} .phase`);
    assert.equal(rows.length, 2);
    for (const [i, row] of rows.entries()) {
      assert.equal(row.querySelectorAll("button.primary").length, 1, `行 ${i + 1}`);
      assert.equal(row.querySelectorAll('button[data-action="review-done"]').length, 1);
      assert.equal(row.querySelector("a.card-button")?.getAttribute("href"), `https://example.com/o/r/pull/${(i + 1) * 11}`);
    }
    two.click(two.one(`${card(two, "p")} .phase[data-phase="1"] button.primary`));
    await two.settle();
    assert.deepEqual(two.posted.at(-1), { type: "decide", parent: "p", phase: 1 });
    two.click(two.one(`${card(two, "p")} .phase[data-phase="2"] button.primary`));
    await two.settle();
    assert.deepEqual(two.posted.at(-1), { type: "decide", parent: "p", phase: 2 });
  } finally {
    await two.close();
  }
});

test("CB-D705 操作のまとまりごとに主ボタンは 1 つ以下。操作ボタンと操作の MR リンクは全部 card-button、詳細の中のリンクは持たない。button.action は使わない", async () => {
  // 改版の承認待ち + レビュー待ちが同時の親、承認待ちの子、複数フェーズが待つ親、フロー・計画編集・下書きを持つカード
  const json = pendingOf(
    boardOf(
      [
        ticketJson("rev", { proposal: { state: "todo", tree: "rev", tree_root: "/r", path: "/r/p.md", has_plan: true }, mr: 3, branch: "b", template_draft: true }),
        proposalJson("kid", { parent: "rev", phase: 1, flow: { path: "/f.yml", rel: "f.yml", tree: "/t", exists: false, linked: false, locked: false, draft: null } }),
        reviewJson("wait", { parent: "rev", phase: 2 }),
      ],
      [parentJson("rev", [waitingPhase(1, 11), waitingPhase(2, 12)])],
    ),
    "rev",
    "kid",
  );
  const page = await openBoard(json, { state: { details: [detailsKey("", "rev")] } });
  try {
    // カードの中の button はみな card-button。共通の button.action は使わない
    const buttons = page.all("li.card button");
    assert.ok(buttons.length >= 6, `操作ボタンが少なすぎる: ${buttons.length}`);
    for (const b of buttons) {
      if (b.closest("summary") === null) {
        assert.ok(b.classList.contains("card-button"), `${b.getAttribute("data-action")} が card-button でない`);
      }
      assert.ok(!b.classList.contains("action"), `${b.getAttribute("data-action")} が共通の action を使っている`);
    }
    // 操作の MR リンク（open-mr）は全部 card-button
    const mrLinks = page.all("li.card a[data-action='open-mr']");
    assert.ok(mrLinks.length >= 3);
    for (const a of mrLinks) {
      assert.ok(a.classList.contains("card-button"));
    }
    // 詳細の中のリンクは普通のリンクのまま（card-button を持たない）
    const inDetails = page.all("li.card details a");
    for (const a of inDetails) {
      assert.ok(!a.classList.contains("card-button"));
    }
    // 操作のまとまりごとに主ボタンは 1 つ以下: ユーザ待ち・各フェーズ行・補助の操作
    const groups = page.all("li.card .turn, li.card .phase, li.card .card-actions");
    assert.ok(groups.length >= 5);
    for (const g of groups) {
      assert.ok(g.querySelectorAll("button.primary").length <= 1, `主ボタンが複数: ${g.className}`);
    }
    // 補助の操作は通常の色（主ボタンにしない）
    assert.equal(page.all("li.card .card-actions .primary").length, 0);
    // 親の「ユーザ待ち」は承認の 1 つだけ、フェーズ行の［レビュー完了］は行ごと
    assert.equal(page.all(`${card(page, "rev")} .turn button.primary`).length, 1);
    assert.equal(page.all(`${card(page, "rev")} .phase button.primary`).length, 2);
    // 補助の操作が 3 つ種類とも出る
    assert.equal(page.all('button[data-action="flow"]').length, 1);
    assert.equal(page.all('button[data-action="workflow"]').length, 1);
    assert.equal(page.all('button[data-action="template-draft"]').length, 1);
  } finally {
    await page.close();
  }
  // カードの中に縦に広がるメニューを持たない
  const html = boardPage({ kind: "board", board: buildBoard(json) });
  assert.doesNotMatch(flatStyle(html), /\.review-menu/);
});

test("CB-D706 ［MR を開く］は URL が http(s) のときだけリンクにする。それ以外は出さない（javascript: などを href にしない）", async () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "ftp://x/y", ""]) {
    const page = await openBoard(boardOf([reviewJson("w", { parent: "p", phase: 1 }), ticketJson("p")], [parentJson("p", [waitingPhase(1, 5, url)])]));
    try {
      assert.equal(page.all("li.card a").length, 0, `url=${url}`);
      assert.ok(page.all('button[data-action="review-done"]').length >= 1, "レビュー完了は URL に関わらず出る");
    } finally {
      await page.close();
    }
  }
});

test("CB-D707 注意の行は例外のときだけ短い言葉で出す（不備・書き込み停止・先行待ち・HIGH 以上・複数の場所・ワークツリーなし・未送信）。理由の全文は詳細にある", async () => {
  const where = [{ tree: "", state: "todo", path: "/a" }, { tree: "x", state: "doing", path: "/b" }];
  const json = boardOf(
    [
      ticketJson("p", { remote: { branch: "b", ahead: 3, behind: 0 } }),
      ticketJson("noisy", {
        parent: "p",
        phase: 1,
        issues: ["承認済みですが、ワークツリーがありません。作り直すか取り消してください", "もう一つの不備"],
        blocked: "親が引けない",
        scattered: where,
        seen_in: where,
        worktree: { exists: false, path: "/ws/x", project: "" },
        risk: { points: 90, level: "CRITICAL" },
        predecessors_unmet: [{ ticket: "p-01-09", state: "doing", label: "作業中（doing/）" }],
      }),
      ticketJson("calm", { parent: "p", phase: 1, risk: { points: 10, level: "LOW" } }),
    ],
    [parentJson("p", [phaseJson(1)])],
  );
  const page = await openBoard(json, { state: { details: [detailsKey("", "noisy")] } });
  try {
    const notes = (id: string): string[] => page.all(`${card(page, id)} .notes .badge`).map((b) => b.textContent.replace(/\s+/g, " "));
    assert.deepEqual(notes("noisy"), [
      "不備 2 件",
      "書き込み停止中",
      "ワークツリーなし",
      "リスク CRITICAL（90 点）",
      "複数の場所にある（2 か所）",
      "先行チケットの条件が未達成（p-01-09）",
    ]);
    // 着手していないカードは「先行待ち」
    assert.equal(page.all(`${card(page, "calm")} .badge.preds`).length, 0);
    // 注意の行は短い言葉だけ。理由の全文はここに出さない
    const text = page.one(`${card(page, "noisy")} .notes`).textContent;
    assert.ok(!text.includes("作り直すか") && !text.includes("親が引けない") && !text.includes("main:todo"));
    // 全文は詳細にある（不備・書き込み停止の理由・複数の場所・先行）
    const rows = (key: string): string => page.one(`${card(page, "noisy")} .detail-row[data-row="${key}"] dd`).textContent;
    assert.ok(rows("issues").includes("作り直すか取り消してください") && rows("issues").includes("もう一つの不備"));
    assert.equal(rows("blocked"), "親が引けない");
    assert.equal(rows("scattered"), "main:todox:doing");
    assert.ok(rows("predecessors").includes("p-01-09: 作業中（doing/）"));
    assert.equal(rows("risk"), "リスク CRITICAL（90 点）");
    // 普通のこと（LOW・MEDIUM のリスク、未送信 0 件）は注意に出さない。LOW は詳細にだけある
    assert.equal(page.all(`${card(page, "calm")} .notes`).length, 0);
    assert.deepEqual(notes("p"), ["未送信 3 件"]);
  } finally {
    await page.close();
  }
});

test("CB-D708 消すもの: base / 合流の sha・マーカー名・「承認済み」「着手済み」「未着手」の常時表示・旧い言葉は、どのカードにも出ない（詳細を開いても）", async () => {
  const everything = scene().tickets.map((t) => `${detailsKey(t.project, t.ticket)}`);
  const page = await openBoard(scene(), { state: { details: everything } });
  try {
    const all = page.all("li.card").map((c) => c.textContent).join("\n");
    for (const banned of ["base ", "合流 ", "aaaaaaa", "bbbbbbb", "requested", "pending", "skipped", "reviewed", "承認済み", "着手済み", "クローズ", "レビュー依頼済み", "連絡", "レビュー必須 · レビュー依頼済み"]) {
      assert.ok(!all.includes(banned), `カードに「${banned}」が出ている`);
    }
    // 取り消した理由は詳細にだけ出る
    assert.equal(page.one(`${card(page, "old")} .detail-row[data-row="cancel"] dd`).textContent, "要件が変わった");
  } finally {
    await page.close();
  }
});

test("CB-D709 詳細は畳んである。MR・issue・ブランチ・ワークツリー名・リスク・人間レビューの要否と理由・フェーズごとのレビューの要否とリスク（JSON の文言のまま）を開くと見られる", async () => {
  const page = await openBoard(scene());
  try {
    const parent = card(page, "busy");
    assert.equal(page.one(`${parent} details.card-details`).hasAttribute("open"), false);
    assert.equal(page.all(`${parent} .detail-rows`).length, 0);
    page.click(page.one(`${parent} details > summary`));
    await page.settle();
    assert.equal(page.one(`${parent} details.card-details`).hasAttribute("open"), true);
    const row = (key: string): string => page.one(`${parent} .detail-row[data-row="${key}"] dd`).textContent;
    assert.equal(row("review"), "必須（認証まわり）");
    assert.equal(row("issue"), "#12");
    assert.equal(row("parent-mr"), "!7");
    assert.equal(row("branch"), "feature/x");
    assert.equal(row("remote"), "未送信 2 件遅れ 1 件");
    assert.equal(row("worktree"), "busy");
    assert.equal(row("phase-1"), "レビュー不要 · リスク: 0 (LOW)");
    assert.equal(row("phase-2"), "レビュー必須 · リスク: 60 (HIGH)");
    // 詳細の MR は普通のリンク（ボタンの寸法にしない）
    const link = page.one(`${parent} .detail-row[data-row="mr"] a`);
    assert.equal(link.getAttribute("href"), "https://example.com/o/r/pull/18");
    assert.ok(link.classList.contains("detail-link") && !link.classList.contains("card-button"));
    // 子のリスクは詳細に（JSON の水準と点）
    page.click(page.one(`${card(page, "busy-02-01")} details > summary`));
    await page.settle();
    assert.equal(page.one(`${card(page, "busy-02-01")} .detail-row[data-row="risk"] dd`).textContent, "リスク HIGH（60 点）");
    // 閉じる
    page.click(page.one(`${parent} details > summary`));
    await page.settle();
    assert.equal(page.all(`${parent} .detail-rows`).length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D710 詳細の開閉はカードごと（プロジェクトと ID の組）に webview の状態へ覚え、更新や列の移動でも閉じない。同じ ID の別プロジェクトを混同しない", async () => {
  const same = (project: string): TicketJson => ticketJson("dup", { project, title: `題 ${project || "ws"}` });
  const json = boardOf([same(""), same("web")], [], { projects: ["web"] });
  const page = await openBoard(json);
  try {
    const open = (project: string): boolean => page.all(`li.card[data-id="dup"][data-project="${project}"] details.card-details`)[0].hasAttribute("open");
    // 両方に詳細があるようにプロジェクト名を持たせてある（project 行）
    assert.equal(open(""), false);
    page.click(page.one(`li.card[data-id="dup"][data-project="web"] details > summary`));
    await page.settle();
    assert.equal(open("web"), true);
    assert.equal(open(""), false, "同じ ID の別プロジェクトまで開かない");
    assert.deepEqual((page.state() as { details: string[] }).details, [detailsKey("web", "dup")]);
    // 更新（新しい中身が届く）でも閉じない
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json) } });
    assert.equal(open("web"), true);
    // 列の移動（作業中 → 完了）でも閉じない
    const moved = boardOf([same(""), { ...closedJson("dup", { project: "web", title: "題 web" }) }], [], { projects: ["web"] });
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(moved) } });
    assert.equal(page.one('li.card[data-id="dup"][data-project="web"]').closest(".column")?.getAttribute("data-state"), "done");
    assert.equal(open("web"), true, "列を移っても開いたまま");
    // 閉じれば覚えも消える。もう一度開いて、画面を作り直しても（覚えた状態で）開いている
    page.click(page.one(`li.card[data-id="dup"][data-project="web"] details > summary`));
    await page.settle();
    assert.deepEqual((page.state() as { details: string[] }).details, []);
  } finally {
    await page.close();
  }
  const remembered = await openBoard(json, { state: { details: [detailsKey("web", "dup"), detailsKey("web", "gone")] } });
  try {
    assert.equal(remembered.all('li.card[data-id="dup"][data-project="web"] details.card-details')[0].hasAttribute("open"), true);
    // 消えたカードの分は、次に覚えるときに捨てる
    remembered.click(remembered.one('li.card[data-id="dup"][data-project=""] details > summary'));
    await remembered.settle();
    assert.deepEqual((remembered.state() as { details: string[] }).details.sort(), [detailsKey("", "dup"), detailsKey("web", "dup")].sort());
  } finally {
    await remembered.close();
  }
});

test("CB-D711 カードを押す（または焦点を当てて Enter）とチケットのファイルが開く。ボタン・リンク・詳細の開閉・展開した本文の上では開かない", async () => {
  const json = boardOf(
    [
      ticketJson("p", { issue: 4 }),
      reviewJson("w", { parent: "p", phase: 1, proposal: { state: "review", tree: "w", tree_root: "/w", path: "/ws/wip/proposals/review/w.md", has_plan: false } }),
    ],
    [parentJson("p", [waitingPhase(1, 9)])],
  );
  const page = await openBoard(json, { state: { details: [detailsKey("", "w")] } });
  try {
    const opened = (): number => page.posted.filter((m) => m.type === "open").length;
    const target = card(page, "w");
    // カード本体（題）を押すと開く
    page.click(page.one(`${target} .title`));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "open", filePath: "/ws/wip/proposals/review/w.md" });
    const base = opened();
    // 焦点を当てて Enter でも開く
    page.key("Enter", page.one(target));
    assert.equal(opened(), base + 1);
    // ボタンの上・リンクの上・詳細の開閉の上・展開した本文の上では開かない
    page.document.addEventListener("click", (event) => {
      if ((event.target as unknown as { closest: (s: string) => unknown }).closest("a")) {
        event.preventDefault();
      }
    });
    for (const selector of [`${target} a[data-action="open-mr"]`, `${target} button[data-action="review-done"]`, `${target} .detail-rows`, `${target} .detail-rows dd`, `${target} details > summary`]) {
      const before = opened();
      page.click(page.one(selector));
      await page.settle();
      assert.equal(opened(), before, `${selector} の上で開いた`);
    }
    // Enter も同じ（ボタンや詳細の上で押しても開かない）
    const before = opened();
    page.key("Enter", page.one(`${target} button[data-action="review-done"]`));
    page.key("Enter", page.one(`${target} details > summary`));
    assert.equal(opened(), before);
    // フェーズ行のボタンも同じ
    page.click(page.one(`${card(page, "p")} .phase button[data-action="review-done"]`));
    await page.settle();
    assert.equal(opened(), before);
  } finally {
    await page.close();
  }
});

test("CB-D712 要対応のみの絞り込み・畳んだ列・幅と同じく、詳細の開閉を覚える state は他の覚えを消さない", async () => {
  const saved = { project: "*", parent: "*", attention: true, archived: false, folded: ["done"], widths: { todo: 300 }, details: [] };
  const page = await openBoard(boardOf([ticketJson("a", { attention: true })]), { state: saved });
  try {
    page.click(page.one(`${card(page, "a")} details > summary`).closest("details") === null ? page.one(card(page, "a")) : page.one(`${card(page, "a")} details > summary`));
    await page.settle();
    assert.deepEqual(page.state(), { ...saved, details: [detailsKey("", "a")] });
  } finally {
    await page.close();
  }
});

test("CB-D713 カードの言葉は用語の表のとおり（承認待ち・開始前・作業中・レビュー準備中・レビュー待ち・レビュー予定・レビュー済み・レビュー省略・完了・取り消し・要確認）", async () => {
  const json = boardOf(
    [
      ticketJson("p"),
      ticketJson("early", { parent: "p", phase: 1, started_at: "", in_progress: false }),
      ticketJson("work", { parent: "p", phase: 1 }),
      reviewJson("prep", { parent: "p", phase: 2 }),
      reviewJson("later", { parent: "p", phase: 3 }),
      reviewJson("wait", { parent: "p", phase: 4 }),
    ],
    [parentJson("p", [
      phaseJson(1),
      phaseJson(2, { state: "ended", gate_closed: true, review_required: true }),
      phaseJson(3, { state: "ended", marks: { skipped: { deferred_to: 5 } } }),
      waitingPhase(4),
      phaseJson(5, { state: "active" }),
      phaseJson(6, { state: "planned" }),
    ])],
  );
  const page = await openBoard(json);
  try {
    const statuses = page.all("li.card").map((c) => `${c.getAttribute("data-id")}:${c.getAttribute("data-status")}`);
    assert.deepEqual(statuses.sort(), ["early:開始前", "later:レビュー予定", "p:レビュー待ち", "prep:レビュー準備中", "wait:レビュー待ち", "work:作業中"]);
    const rows = page.all('li.card[data-id="p"] .phase-status').map((e) => e.textContent);
    assert.deepEqual(rows, ["進行中", "レビュー準備中", "レビュー予定", "レビュー待ち", "進行中", "これから"]);
  } finally {
    await page.close();
  }
});

test("CB-D714 カードの見た目の規則: ボタンは文字サイズ・高さ・余白を明示し、狭い列でも折り返す。札は右端に寄せる", async () => {
  const css = flatStyle(boardPage({ kind: "board", board: buildBoard(boardOf([ticketJson("a")])) } as BoardData));
  assert.match(css, /\.card-button \{[^}]*box-sizing: border-box;[^}]*min-height: 26px;[^}]*padding: 3px 10px;[^}]*font-size: var\(--vscode-font-size, 13px\);[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;/);
  assert.match(css, /a\.card-button, a\.card-button:hover/);
  assert.match(css, /\.card-button\.primary/);
  assert.match(css, /\.status \{[^}]*margin-left: auto;/);
  // 親（フェーズ一覧など）の文字サイズを引き継ぐ指定は使わない
  assert.doesNotMatch(css, /\.card-button[^{]*\{[^}]*font: inherit/);
});

void openPage;
void (null as unknown as PhaseJson);
