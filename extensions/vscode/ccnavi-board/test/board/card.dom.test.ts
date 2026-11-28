/**
 * カード1枚と、選んだカードの右のパネルが何を描くか（Reactの画面をhappy-domで動かす）。
 * カードは 見出し（ID・タイトル・状態のバッジ）・局面（親）・動いた表示・注意・主な操作。
 * パネルは 見出し・位置・MR・ユーザ待ち・フェーズ行（親だけ）・操作・詳細。
 * バッジの決め方はstatus.test.ts、ここは構造・操作・ボタンの規則・押したときの動き。
 * 寸法と見た目は拡張開発ホストでの手動確認（docs/design/manual-checks.mdの表）に回す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import type { BoardData } from "../../src/core/board-view.js";
import type { BoardJson, TicketJson } from "../../src/core/model.js";
import { boardOf, closedJson, parentJson, phaseJson, proposalJson, reviewJson, ticketJson, waitingPhase } from "../helpers/cards.js";
import { boardPage, openBoard } from "../helpers/board.js";
import { flatStyle } from "../helpers/bundle.js";
import type { DomPage } from "../helpers/dom.js";

const cardKey = (project: string, id: string): string => JSON.stringify([project, id]);

function card(id: string, project?: string): string {
  return project === undefined ? `li.card[data-id="${id}"]` : `li.card[data-id="${id}"][data-project="${project}"]`;
}

const PANEL = "aside.card-panel";

function textOf(page: DomPage, selector: string): string {
  return page.one(selector).textContent;
}

/** カードのタイトルを押して選び、右のパネルを出す */
async function select(page: DomPage, id: string, project?: string): Promise<void> {
  page.click(page.one(`${card(id, project)} .title`));
  await page.settle();
  assert.equal(page.one(PANEL).getAttribute("data-panel"), id, `${id}のパネルが出ない`);
}

function pendingOf(json: BoardJson, ...ids: string[]): BoardJson {
  return { ...json, pending_approval: ids };
}

/** 承認待ちの親と、レビュー待ちのフェーズを持つ親・その子、要確認の子を1枚ずつ並べたボード */
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

test("CB-D700 カードは 見出し → 局面（親だけ）→ 注意 → 主な操作 だけ。位置・MR・ユーザ待ちの文・フェーズ行・補助の操作・詳細は、選んだカードの右のパネルに 見出し → 位置 → MR → ユーザ待ち → フェーズ行 → 操作 → 詳細 の順に並ぶ", async () => {
  const page = await openBoard(scene());
  try {
    const kindsOf = (selector: string): string[] => Array.from(page.one(selector).children).map((el) => el.className.split(" ")[0]);
    // 親（承認待ち・計画あり）: 見出しと主な操作（承認）だけ。計画編集・詳細はパネル
    assert.deepEqual(kindsOf(card("fresh")), ["card-head", "card-primary"]);
    // 親（作業中）: 見出し・局面・注意（未送信）。フェーズ行とMRはカードに無い
    assert.deepEqual(kindsOf(card("busy")), ["card-head", "stage", "badges"]);
    // 子（レビュー待ち）: 見出し・注意（HIGH）・主な操作（レビュー完了）。位置はカードに無い
    assert.deepEqual(kindsOf(card("busy-02-01")), ["card-head", "badges", "card-primary"]);
    assert.equal(page.all(`li.card .where, li.card .card-mr, li.card .turn, li.card .phases, li.card .card-actions, li.card details`).length, 0);
    // 見出し: ID・タイトル・バッジ（バッジは右端の1つ）
    const head = page.one(`${card("busy")} .card-head`);
    assert.deepEqual(Array.from(head.children).map((el) => el.className.split(" ")[0]), ["num", "title", "status"]);
    assert.equal(head.querySelectorAll(".status").length, 1);
    // 選ぶまでパネルは無い
    assert.equal(page.all(PANEL).length, 0);
    // 子のパネル: 位置は「親 <ID> / <フェーズ名>」
    await select(page, "busy-02-01");
    assert.deepEqual(kindsOf(PANEL), ["panel-head", "where", "card-mr", "turn", "card-actions", "detail-rows"]);
    assert.equal(textOf(page, `${PANEL} .where`), "親busy / 2（設計）");
    assert.equal(textOf(page, `${PANEL} .panel-head .num`), "busy-02-01");
    assert.equal(textOf(page, `${PANEL} .panel-head .status`), "レビュー待ち");
    // 親のパネル: 局面・MR・フェーズ行
    await select(page, "busy");
    assert.deepEqual(kindsOf(PANEL), ["panel-head", "stage", "card-mr", "phases", "card-actions", "detail-rows"]);
    assert.equal(page.all(PANEL).length, 1, "パネルは1つだけ");
    // 子のフェーズが引けなければ、番号で言う
    const orphan = await openBoard(boardOf([reviewJson("lost", { parent: "ghost", phase: 3 })]));
    try {
      await select(orphan, "lost");
      assert.equal(textOf(orphan, `${PANEL} .where`), "親ghost / フェーズ3");
    } finally {
      await orphan.close();
    }
  } finally {
    await page.close();
  }
});

test("CB-D701 バッジは見出しの右端に1つ。言葉とツールチップはバッジの決め方のとおりで、色に頼らず言葉を出す", async () => {
  const page = await openBoard(scene());
  try {
    const status = (id: string): string => page.one(`${card(id)} .card-head .status`).textContent;
    assert.equal(status("fresh"), "承認待ち");
    assert.equal(status("busy"), "レビュー待ち");
    assert.equal(status("busy-02-01"), "レビュー待ち");
    assert.equal(status("old"), "取り消し");
    assert.equal(status("held"), "要確認");
    assert.equal(page.one(`${card("held")} .status`).getAttribute("title"), "書き込み停止中: 親が引けない");
    assert.equal(page.one(`${card("busy")}`).getAttribute("data-status"), "レビュー待ち");
    // 理由の無いバッジにはツールチップを付けない
    assert.equal(page.one(`${card("fresh")} .status`).getAttribute("title"), null);
  } finally {
    await page.close();
  }
});

test("CB-D702 承認待ちのカードには［承認］（主ボタン）だけ。パネルの「ユーザ待ち」に「内容を確認して承認してください」と［承認］。要確認のバッジでも承認は出る", async () => {
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
    const approve = page.one(`${card("p")} .card-primary button[data-action="approve-one"]`);
    assert.equal(approve.textContent, "承認");
    assert.ok(approve.classList.contains("primary"));
    assert.equal(page.all(`${card("p")} .turn-text`).length, 0, "カードには説明の文を出さない");
    page.click(approve);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["p"], filtered: true });
    assert.equal(page.all(PANEL).length, 0, "ボタンを押してもカードは選ばない");
    // パネルの「ユーザ待ち」
    await select(page, "p");
    assert.equal(textOf(page, `${PANEL} .turn-head`), "ユーザ待ち");
    assert.equal(textOf(page, `${PANEL} .turn-text`), "内容を確認して承認してください");
    page.click(page.one(`${PANEL} .turn button[data-action="approve-one"]`));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["p"], filtered: true });
    // 子: バッジは「要確認」（blocked）でも、pendingApprovalだけを条件に承認が出る
    assert.equal(textOf(page, `${card("c")} .status`), "要確認");
    assert.equal(page.all(`${card("c")} button[data-action="approve-one"]`).length, 1);
    assert.equal(page.all(`${card("c")} [data-action="review-done"]`).length, 0);
    // 案内の指す先はカードのボタンにだけ付く
    await select(page, "c");
    assert.equal(page.one(`${PANEL} button[data-action="approve-one"]`).getAttribute("data-tour"), null);
    assert.equal(page.one(`${card("c")} button[data-action="approve-one"]`).getAttribute("data-tour"), "approve-one");
  } finally {
    await page.close();
  }
});

test("CB-D703 バッジが「レビュー待ち」の子のカードには［レビュー完了］（主ボタン）だけ。パネルの「ユーザ待ち」に「MRでレビューしてください」と［MRを開く］（リンク）と［レビュー完了］。ほかの子には出さない", async () => {
  const page = await openBoard(scene());
  try {
    const child = card("busy-02-01");
    const done = page.one(`${child} .card-primary button[data-action="review-done"]`);
    assert.equal(done.textContent, "レビュー完了");
    assert.ok(done.classList.contains("card-button") && done.classList.contains("primary"));
    assert.equal(page.all(`${child} a`).length, 0, "カードにMRのリンクを出さない");
    page.click(done);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decide", parent: "busy", phase: 2 });
    await select(page, "busy-02-01");
    assert.equal(textOf(page, `${PANEL} .turn-text`), "MRでレビューしてください");
    const link = page.one(`${PANEL} .turn a[data-action="open-mr"]`);
    assert.equal(link.textContent, "MRを開く");
    assert.equal(link.getAttribute("href"), "https://example.com/o/r/pull/18", "依頼の投稿ではなくMR自体");
    assert.ok(link.classList.contains("card-button") && !link.classList.contains("primary"));
    page.click(page.one(`${PANEL} .turn button[data-action="review-done"]`));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "decide", parent: "busy", phase: 2 });
    // 承認も出さない（承認待ちでない）
    assert.equal(page.all(`${child} button[data-action="approve-one"], ${PANEL} button[data-action="approve-one"]`).length, 0);
    // 作業中の子・要確認の子・完了の子には出さない
    for (const id of ["busy-01-01", "held", "old"]) {
      assert.equal(page.all(`${card(id)} .card-primary`).length, 0, id);
      await select(page, id);
      assert.equal(page.all(`${PANEL} .turn`).length, 0, id);
    }
    // 「レビューの対応」メニューと「レビュー済み連絡」は無い
    assert.equal(page.all('[data-action="review-menu"], [data-action="reviewed"], .review-menu').length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D704 フェーズ行（パネル）: 一言（9通り）・手で動かした注意の1行。行に［MRを開く］［レビュー完了］は置かない（子の操作だけ）", async () => {
  const page = await openBoard(scene());
  try {
    await select(page, "busy");
    const rows = page.all(`${PANEL} .phase`);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-status")?.textContent), ["レビュー省略", "レビュー待ち"]);
    assert.deepEqual(rows.map((r) => r.querySelector(".phase-label")?.textContent), ["1（調査）", "2（設計）"]);
    assert.equal(page.all(`${PANEL} .phase button, ${PANEL} .phase a`).length, 0, "フェーズ行にボタンやリンクを置かない");
    assert.equal(rows[1].querySelector(".phase-manual")?.textContent, "手で動かした子の確認待ち");
    assert.equal(rows[0].querySelectorAll(".phase-manual").length, 0);
    assert.ok(rows[1].classList.contains("review-hold"));
    // 状態は1か所にだけ描く。段階の名前やマーカーの経過は出さない
    assert.ok(!rows[1].textContent.includes("レビュー依頼済み"));
  } finally {
    await page.close();
  }
  // 複数のフェーズが同時にレビュー待ちでも、親のフェーズ行にボタンは置かない
  const two = await openBoard(boardOf([ticketJson("p")], [parentJson("p", [waitingPhase(1, 11), waitingPhase(2, 22, "https://example.com/o/r/pull/22")])]));
  try {
    await select(two, "p");
    const rows = two.all(`${PANEL} .phase`);
    assert.equal(rows.length, 2);
    for (const [i, row] of rows.entries()) {
      assert.equal(row.querySelectorAll("button, a.card-button").length, 0, `行${i + 1}`);
    }
  } finally {
    await two.close();
  }
});

test("CB-D705 操作のまとまりごとに主ボタンは1つ以下。カードとパネルの操作ボタンと操作のMRリンクは全部card-button、詳細の中のリンクは持たない。button.actionは使わない", async () => {
  // 改版の承認待ち + レビュー待ちが同時の親、承認待ちの子、複数フェーズが待つ親、フロー・計画編集・下書きを持つカード
  const json = pendingOf(
    boardOf(
      [
        ticketJson("rev", { proposal: { state: "todo", tree: "rev", tree_root: "/r", path: "/r/p.md", has_plan: true }, mr: 3, branch: "b", template_draft: true }),
        proposalJson("kid", { parent: "rev", phase: 1, flow: { path: "/f.yml", rel: "f.yml", tree: "/t", exists: false, linked: false, locked: false, lock_kind: null, draft: null } }),
        reviewJson("wait", { parent: "rev", phase: 2 }),
      ],
      [parentJson("rev", [waitingPhase(1, 11), waitingPhase(2, 12)])],
    ),
    "rev",
    "kid",
  );
  const page = await openBoard(json);
  try {
    const checkButtons = (scope: string): void => {
      for (const b of page.all(`${scope} button`)) {
        assert.ok(b.classList.contains("card-button"), `${b.getAttribute("data-action")}がcard-buttonでない`);
        assert.ok(!b.classList.contains("action"), `${b.getAttribute("data-action")}が共通のactionを使っている`);
      }
      for (const a of page.all(`${scope} a[data-action='open-mr']`)) {
        assert.ok(a.classList.contains("card-button"));
      }
      for (const a of page.all(`${scope} .detail-rows a, ${scope} .card-mr a`)) {
        assert.ok(!a.classList.contains("card-button"), "詳細と位置の下のリンクは普通のリンク");
      }
      for (const g of page.all(`${scope} .card-primary, ${scope} .turn, ${scope} .phase, ${scope} .card-actions`)) {
        assert.ok(g.querySelectorAll(".primary").length <= 1, `主ボタンが複数: ${g.className}`);
      }
      // 補助の操作は通常の色（主ボタンにしない）
      assert.equal(page.all(`${scope} .card-actions .primary`).length, 0);
    };
    checkButtons("li.card");
    // カードの主な操作は、どのカードでも1つまで
    for (const c of page.all("li.card")) {
      assert.ok(c.querySelectorAll("button, a").length <= 1, `${c.getAttribute("data-id")}の操作が2つ以上`);
    }
    const seen = new Set<string>();
    for (const id of ["rev", "kid", "wait"]) {
      await select(page, id);
      checkButtons(PANEL);
      for (const b of page.all(`${PANEL} [data-action]`)) {
        seen.add(b.getAttribute("data-action") ?? "");
      }
    }
    // 親の「ユーザ待ち」は承認の1つだけ、フェーズ行にボタンは無い
    await select(page, "rev");
    assert.equal(page.all(`${PANEL} .turn .primary`).length, 1);
    assert.equal(page.all(`${PANEL} .phase button`).length, 0);
    // 補助の操作が3つの種類とも、チケットを開く・閉じると一緒にパネルに出る
    for (const action of ["flow", "workflow", "template-draft", "open-ticket", "panel-close", "open-mr", "review-done", "approve-one"]) {
      assert.ok(seen.has(action), `${action}がパネルに無い`);
    }
    assert.equal(page.all('li.card [data-action="flow"], li.card [data-action="workflow"], li.card [data-action="template-draft"]').length, 0);
  } finally {
    await page.close();
  }
  // カードの中に縦に広がるメニューを持たない
  const html = boardPage({ kind: "board", board: buildBoard(json) });
  assert.doesNotMatch(flatStyle(html), /\.review-menu/);
});

test("CB-D706 ［MRを開く］はURLがhttp(s)のときだけリンクにする。それ以外は出さない（javascript: などをhrefにしない）", async () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "ftp://x/y", ""]) {
    const page = await openBoard(boardOf([reviewJson("w", { parent: "p", phase: 1 }), ticketJson("p")], [parentJson("p", [waitingPhase(1, 5, url)])]));
    try {
      assert.equal(page.all("li.card a").length, 0, `url=${url}`);
      await select(page, "w");
      assert.equal(page.all(`${PANEL} a`).length, 0, `url=${url}`);
      assert.ok(page.all('button[data-action="review-done"]').length >= 1, "レビュー完了はURLに関わらず出る");
    } finally {
      await page.close();
    }
  }
});

test("CB-D707 注意の行は例外のときだけ短い言葉でカードに出す（不備・書き込み停止・先行待ち・HIGH以上・複数の場所・ワークツリー未作成／ワークツリーが見つからない・未送信）。理由の全文はパネルの詳細にある", async () => {
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
  const page = await openBoard(json);
  try {
    const notes = (id: string): string[] => page.all(`${card(id)} .notes .badge`).map((b) => b.textContent.replace(/\s+/g, " "));
    assert.deepEqual(notes("noisy"), [
      "不備2件",
      "書き込み停止中",
      "ワークツリーが見つからない",
      "リスクCRITICAL（90点）",
      "複数の場所にある（2か所）",
      "先行チケットの条件が未達成（p-01-09）",
    ]);
    // 着手していないカードは「先行待ち」
    assert.equal(page.all(`${card("calm")} .badge.preds`).length, 0);
    // 注意の行は短い言葉だけ。理由の全文はカードに出さない
    const text = page.one(card("noisy")).textContent;
    assert.ok(!text.includes("作り直すか") && !text.includes("親が引けない") && !text.includes("main:todo"));
    // 全文はパネルの詳細にある（不備・書き込み停止の理由・複数の場所・先行）
    await select(page, "noisy");
    const rows = (key: string): string => page.one(`${PANEL} .detail-row[data-row="${key}"] dd`).textContent;
    assert.ok(rows("issues").includes("作り直すか取り消してください") && rows("issues").includes("もう一つの不備"));
    assert.equal(rows("blocked"), "親が引けない");
    assert.equal(rows("scattered"), "main:todox:doing");
    assert.ok(rows("predecessors").includes("p-01-09: 作業中（doing/）"));
    assert.equal(rows("risk"), "リスクCRITICAL（90点）");
    // 普通のこと（LOW・MEDIUMのリスク、未送信0件）は注意に出さない。LOWはパネルの詳細にだけある
    assert.equal(page.all(`${card("calm")} .notes`).length, 0);
    assert.deepEqual(notes("p"), ["未送信3件"]);
  } finally {
    await page.close();
  }
});

test("CB-D707a 承認待ち（ワークツリー未作成）は警告色にせず、作業中でワークツリーが無いものだけ警告色の「見つからない」", async () => {
  const gone = { exists: false, path: "/ws/.claude/worktrees/x", project: "" };
  const json = boardOf([proposalJson("wait", { worktree: gone }), ticketJson("busy", { worktree: gone }), ticketJson("fresh", { worktree: gone, started_at: "" })], []);
  const page = await openBoard(json);
  try {
    const badge = (id: string): { text: string; warn: boolean } => {
      const b = page.one(`${card(id)} .notes .badge.worktree`);
      return { text: b.textContent.trim(), warn: b.classList.contains("none") };
    };
    assert.deepEqual(badge("wait"), { text: "ワークツリー未作成", warn: false });
    assert.deepEqual(badge("busy"), { text: "ワークツリーが見つからない", warn: true });
    // 着手待ちの子（着手するとワークツリーを作る）も異常ではない
    assert.deepEqual(badge("fresh"), { text: "ワークツリー未作成", warn: false });
  } finally {
    await page.close();
  }
});

test("CB-D708 消すもの: base / 合流のsha・マーカー名・「承認済み」「着手済み」「未着手」の常時表示・古い言葉は、どのカードにもパネルにも出ない", async () => {
  const page = await openBoard(scene());
  try {
    const texts: string[] = page.all("li.card").map((c) => c.textContent);
    for (const t of scene().tickets) {
      await select(page, t.ticket);
      texts.push(page.one(PANEL).textContent);
    }
    const all = texts.join("\n");
    for (const banned of ["base ", "合流 ", "aaaaaaa", "bbbbbbb", "requested", "pending", "skipped", "reviewed", "承認済み", "着手済み", "クローズ", "レビュー依頼済み", "連絡", "レビュー必須 · レビュー依頼済み"]) {
      assert.ok(!all.includes(banned), `カードかパネルに「${banned}」が出ている`);
    }
    // 取り消した理由はパネルの詳細にだけ出る
    await select(page, "old");
    assert.equal(page.one(`${PANEL} .detail-row[data-row="cancel"] dd`).textContent, "要件が変わった");
    assert.ok(!page.one(card("old")).textContent.includes("要件が変わった"));
  } finally {
    await page.close();
  }
});

test("CB-D709 パネルの詳細は畳まない。MRは位置の下にある。issue・ブランチ・ワークツリー名・リスク・人間レビューの要否と理由・フェーズごとのレビューの要否とリスク（JSONの文言のまま）が見られる", async () => {
  const page = await openBoard(scene());
  try {
    await select(page, "busy");
    assert.equal(page.all(`${PANEL} details`).length, 0);
    const row = (key: string): string => page.one(`${PANEL} .detail-row[data-row="${key}"] dd`).textContent;
    assert.equal(row("review"), "必須（認証まわり）");
    assert.equal(row("issue"), "#12");
    assert.equal(row("parent-mr"), "!7");
    assert.equal(row("branch"), "feature/x");
    assert.equal(row("remote"), "未送信2件遅れ1件");
    assert.equal(row("worktree"), "busy");
    assert.equal(row("phase-1"), "レビュー不要 · リスク: 0 (LOW)");
    assert.equal(row("phase-2"), "レビュー必須 · リスク: 60 (HIGH)");
    // MRは詳細ではなく位置の下に出る。普通のリンク（ボタンの寸法にしない）
    assert.equal(page.all(`${PANEL} .detail-row[data-row="mr"]`).length, 0);
    const link = page.one(`${PANEL} .card-mr a`);
    assert.equal(link.getAttribute("href"), "https://example.com/o/r/pull/18");
    assert.ok(link.classList.contains("detail-link") && !link.classList.contains("card-button"));
    assert.match(link.textContent, /^MR #\d+$/);
    // 子のリスクはパネルの詳細に（JSONの水準と点）
    await select(page, "busy-02-01");
    assert.equal(page.one(`${PANEL} .detail-row[data-row="risk"] dd`).textContent, "リスクHIGH（60点）");
  } finally {
    await page.close();
  }
});

test("CB-D710 選んだカードはプロジェクトとIDの組でwebviewの状態に1つだけ覚え、更新・列の移動・作り直しでも外さない。同じIDの別プロジェクトを混同しない。消えたカードは外す", async () => {
  const same = (project: string): TicketJson => ticketJson("dup", { project, title: `タイトル${project || "ws"}` });
  const json = boardOf([same(""), same("web")], [], { projects: ["web"] });
  const page = await openBoard(json);
  try {
    const selected = (project: string): boolean => page.one(card("dup", project)).classList.contains("selected");
    assert.equal(selected(""), false);
    await select(page, "dup", "web");
    assert.equal(selected("web"), true);
    assert.equal(selected(""), false, "同じIDの別プロジェクトまで選ばない");
    assert.equal(page.one(card("dup", "web")).getAttribute("aria-current"), "true");
    assert.equal((page.state() as { selected: string }).selected, cardKey("web", "dup"));
    assert.equal(textOf(page, `${PANEL} .detail-row[data-row="project"] dd`), "web");
    // 更新（新しい中身が届く）でも外さない
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(json) } });
    assert.equal(selected("web"), true);
    assert.equal(page.all(PANEL).length, 1);
    // 列の移動（進行中 → 完了）でも外さない。パネルの中身は新しい状態になる
    const moved = boardOf([same(""), { ...closedJson("dup", { project: "web", title: "タイトルweb" }) }], [], { projects: ["web"] });
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(moved) } });
    assert.equal(page.one(card("dup", "web")).closest(".column")?.getAttribute("data-state"), "done");
    assert.equal(selected("web"), true, "列を移っても選んだまま");
    assert.equal(textOf(page, `${PANEL} .panel-head .status`), "完了");
    // 別のカードを選ぶと、選択は1つだけ
    await select(page, "dup", "");
    assert.deepEqual(page.all("li.card.selected").map((c) => c.getAttribute("data-project")), [""]);
    assert.equal((page.state() as { selected: string }).selected, cardKey("", "dup"));
  } finally {
    await page.close();
  }
  // 画面を作り直しても（覚えた状態で）選んだまま
  const remembered = await openBoard(json, { state: { selected: cardKey("web", "dup") } });
  try {
    assert.equal(remembered.one(PANEL).getAttribute("data-panel"), "dup");
    assert.ok(remembered.one(card("dup", "web")).classList.contains("selected"));
  } finally {
    await remembered.close();
  }
  // 消えたカードを覚えていたら外す（次に覚えるときに空にする）
  const gone = await openBoard(json, { state: { selected: cardKey("web", "gone") } });
  try {
    assert.equal(gone.all(PANEL).length, 0);
    assert.equal((gone.state() as { selected: string }).selected, "");
  } finally {
    await gone.close();
  }
});

test("CB-D711 カードを押す（または焦点を当ててEnter）と選んでパネルを出す。ファイルはパネルの「チケットを開く」で開く。ボタン・リンクの上では選ばない。［閉じる］とEscでパネルを閉じる", async () => {
  const json = boardOf(
    [
      ticketJson("p", { issue: 4 }),
      reviewJson("w", { parent: "p", phase: 1, proposal: { state: "review", tree: "w", tree_root: "/w", path: "/ws/wip/proposals/review/w.md", has_plan: false } }),
    ],
    [parentJson("p", [waitingPhase(1, 9)])],
  );
  const page = await openBoard(json);
  try {
    const opened = (): number => page.posted.filter((m) => m.type === "open").length;
    // カード本体（タイトル）を押すと選ぶ。ファイルは開かない
    page.click(page.one(`${card("w")} .title`));
    await page.settle();
    assert.equal(page.one(PANEL).getAttribute("data-panel"), "w");
    assert.equal(opened(), 0);
    // パネルの「チケットを開く」でファイルが開く
    const open = page.one(`${PANEL} button[data-action="open-ticket"]`);
    assert.equal(open.textContent, "チケットを開く");
    page.click(open);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "open", filePath: "/ws/wip/proposals/review/w.md" });
    // 焦点を当ててEnterでも選ぶ
    page.key("Enter", page.one(card("p")));
    await page.settle();
    assert.equal(page.one(PANEL).getAttribute("data-panel"), "p");
    // カードのボタンの上では選ばない（押したボタンの操作だけ）
    page.click(page.one(`${card("w")} button[data-action="review-done"]`));
    page.key("Enter", page.one(`${card("w")} button[data-action="review-done"]`));
    await page.settle();
    assert.equal(page.one(PANEL).getAttribute("data-panel"), "p");
    // ［閉じる］でパネルが消え、選択も外れる
    page.click(page.one(`${PANEL} button[data-action="panel-close"]`));
    await page.settle();
    assert.equal(page.all(PANEL).length, 0);
    assert.equal(page.all("li.card.selected").length, 0);
    assert.equal((page.state() as { selected: string }).selected, "");
    // Escでも閉じる
    await select(page, "w");
    page.key("Escape", page.document.body);
    await page.settle();
    assert.equal(page.all(PANEL).length, 0);
  } finally {
    await page.close();
  }
});

test("CB-D712 ユーザ待ちのみの絞り込み・畳んだ列・幅と同じく、選んだカードを覚えるstateは他の覚えを消さない。絞り込みで隠れている間はパネルを出さない", async () => {
  const saved = { project: "*", parent: "*", attention: true, archived: false, folded: ["done"], widths: { todo: 300 }, selected: "" };
  const page = await openBoard(boardOf([ticketJson("a", { attention: true }), ticketJson("b")]), { state: saved });
  try {
    await select(page, "a");
    assert.deepEqual(page.state(), { ...saved, selected: cardKey("", "a") });
    // 隠れたカードはパネルを出さないが、選択は覚えたまま
    page.click(page.one("#attention-filter"));
    await page.settle();
    await select(page, "b");
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(page.one(card("b")).classList.contains("hidden"));
    assert.equal(page.all(PANEL).length, 0);
    assert.equal((page.state() as { selected: string }).selected, cardKey("", "b"));
  } finally {
    await page.close();
  }
});

test("CB-D713 カードとパネルの言葉は用語の表のとおり（承認待ち・着手待ち・作業中・レビュー準備中・レビュー待ち・レビュー予定・レビュー済み・レビュー省略・完了・取り消し・要確認）", async () => {
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
    assert.deepEqual(statuses.sort(), ["early:着手待ち", "later:レビュー予定", "p:レビュー待ち", "prep:レビュー準備中", "wait:レビュー待ち", "work:作業中"]);
    await select(page, "p");
    const rows = page.all(`${PANEL} .phase-status`).map((e) => e.textContent);
    assert.deepEqual(rows, ["進行中", "レビュー準備中", "レビュー予定", "レビュー待ち", "進行中", "これから"]);
  } finally {
    await page.close();
  }
});

test("CB-D714 カードの見た目の規則: ボタンは文字サイズ・高さ・余白を明示し、狭い列でも折り返す。バッジは右端に寄せる", async () => {
  const css = flatStyle(boardPage({ kind: "board", board: buildBoard(boardOf([ticketJson("a")])) } as BoardData));
  assert.match(css, /\.card-button \{[^}]*box-sizing: border-box;[^}]*min-height: 26px;[^}]*padding: 3px 10px;[^}]*font-size: var\(--vscode-font-size, 13px\);[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;/);
  assert.match(css, /a\.card-button, a\.card-button:hover/);
  assert.match(css, /\.card-button\.primary/);
  assert.match(css, /\.status \{[^}]*margin-left: auto;/);
  // 親（フェーズ一覧など）の文字サイズを引き継ぐ指定は使わない
  assert.doesNotMatch(css, /\.card-button[^{]*\{[^}]*font: inherit/);
});

test("CB-D1291 幅が狭い（940px以下）とき、パネルは列の右ではなく選んだカードのすぐ下に開く。広い幅では列の右に1つ", async () => {
  const json = scene();
  const narrow = (window: { readonly HTMLElement: { readonly prototype: object } }): void => {
    (window as unknown as { matchMedia: (q: string) => unknown }).matchMedia = (query: string) => ({
      matches: query === "(max-width: 940px)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    });
  };
  const page = await openBoard(json, { prepare: narrow });
  try {
    await select(page, "busy-02-01");
    const slot = page.one(`${card("busy-02-01")} + li.card-panel-slot`);
    assert.ok(slot.querySelector(PANEL)?.classList.contains("below"), "選んだカードのすぐ下にない");
    assert.equal(page.all(`.board-area > ${PANEL}`).length, 0);
    assert.ok(!page.one(".board-area").classList.contains("with-panel"));
  } finally {
    await page.close();
  }
  const wide = await openBoard(json);
  try {
    await select(wide, "busy-02-01");
    assert.equal(wide.all(`.board-area.with-panel > ${PANEL}`).length, 1);
    assert.equal(wide.all("li.card-panel-slot").length, 0);
    assert.ok(!wide.one(PANEL).classList.contains("below"));
  } finally {
    await wide.close();
  }
});
