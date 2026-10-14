/**
 * 親カードの出どころ（issue・MR・ブランチ名）とリモートとの差（未送信・遅れ・最終同期）、
 * 同期で取り込んで動いたカードの表示、親のブランチ名 -> 識別子の変換。
 *
 * 値は実行ファイルの JSON（`remote`・`mr`・`issue`・`branch`）のとおりで、拡張は計算し直さない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { buildBoard, unpreparedOf, type Board } from "../../src/core/board.js";
import { movedStep, NOTHING_MOVED, syncedFamilies, syncedOf } from "../../src/core/board-moved.js";
import type { BoardJson, RemoteJson, TicketJson } from "../../src/core/model.js";
import { branchToId } from "../../src/core/ticket-id.js";
import { fixture } from "../helpers/fixture.js";
import { openBoard, openPage } from "../helpers/board.js";
import { movedLabel, remoteItems, syncedText } from "../../src/webview/board/text.js";

const FETCHED = 1_800_000_000;

/** 見本のボードの親 i0001 に、出どころとリモートとの差を載せる */
function withRemote(remote: RemoteJson | null, over: Partial<TicketJson> = {}): BoardJson {
  const board = fixture();
  return {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === "i0001" ? { ...t, remote, ...over } : t)),
  };
}

function parentCard(board: Board) {
  const card = board.columns.flatMap((c) => c.cards).find((c) => c.id === "i0001");
  assert.ok(card !== undefined);
  return card;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function label(seconds: number): string {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

test("CB-T578 親のカードに issue・MR・ブランチ名とリモートとの差が載る。MR は親チケットの mr: が先、無ければ取り込み状態の番号", () => {
  const remote: RemoteJson = { branch: "feature/12-login", ahead: 2, behind: 1, fetched_at: FETCHED, mr: 7 };
  const card = parentCard(buildBoard(withRemote(remote, { issue: 12, mr: null, branch: "feature/12-login" })));
  assert.equal(card.issueNumber, 12);
  assert.equal(card.parentMr, 7);
  assert.equal(card.branch, "feature/12-login");
  assert.deepEqual(card.remote, remote);
  // 親チケットの mr: が取り込み状態より先
  assert.equal(parentCard(buildBoard(withRemote(remote, { mr: 9 }))).parentMr, 9);
  // 子には載せない
  const child = buildBoard(withRemote(remote)).columns.flatMap((c) => c.cards).find((c) => c.id === "i0001-01-01");
  assert.ok(child !== undefined);
  assert.equal(child.remote, null);
  assert.equal(child.parentMr, null);
  assert.equal(child.branch, "");
});

test("CB-T579 リモートとの差の言い方。未送信・遅れ・最終同期。最終同期が無ければリモート未確認、読めない差は不明", () => {
  const board = (remote: RemoteJson | null) => parentCard(buildBoard(withRemote(remote)));
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: 2, behind: 1, fetched_at: FETCHED, mr: null })), [
    "未送信 2 件",
    "遅れ 1 件",
    `最終同期 ${label(FETCHED)}`,
  ]);
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: 0, behind: 0, fetched_at: 0, mr: null })), ["未送信 なし", "遅れ なし", "リモート未確認"]);
  assert.deepEqual(remoteItems(board({ branch: "a", ahead: null, behind: null, fetched_at: FETCHED, mr: null })).slice(0, 2), ["未送信 不明", "遅れ 不明"]);
  // ワークツリーが無い親（remote が無い）は出さない
  assert.deepEqual(remoteItems(board(null)), []);
  assert.equal(syncedText(0), "リモート未確認");
  assert.equal(syncedText(Number.NaN), "リモート未確認");
});

test("CB-D363 親のカードが出どころとリモートとの差を描く。未送信と遅れは目立たせ、リモート未確認も言う", async () => {
  const remote: RemoteJson = { branch: "feature/12-login", ahead: 2, behind: 1, fetched_at: 0, mr: 7 };
  const page = await openBoard(withRemote(remote, { issue: 12, branch: "feature/12-login" }));
  try {
    const parent = page.one('li.card[data-id="i0001"]');
    const names = Array.from(parent.querySelectorAll(".origin-names .fact")).map((e) => e.textContent);
    assert.deepEqual(names, ["issue #12", "MR !7", "ブランチ feature/12-login"]);
    const remoteFacts = Array.from(parent.querySelectorAll(".origin-remote .fact")).map((e) => e.textContent);
    assert.deepEqual(remoteFacts, ["未送信 2 件", "遅れ 1 件", "リモート未確認"]);
    assert.equal(parent.querySelectorAll(".fact.remote-unsent").length, 1);
    assert.equal(parent.querySelectorAll(".fact.remote-behind").length, 1);
    // 子のカードには出さない
    assert.equal(page.one('li.card[data-id="i0001-01-01"]').querySelectorAll(".origin").length, 0);
  } finally {
    page.close();
  }
});

/** 最終同期だけを変えたボード */
function atSync(fetchedAt: number, ticket = "i0001-02-03", to: "doing" | "todo" = "todo"): Board {
  const board = buildBoard(
    withRemote({ branch: "i0001", ahead: 0, behind: 0, fetched_at: fetchedAt, mr: null }),
  );
  if (to === "todo") {
    return board;
  }
  // ticket を作業中の列へ動かす
  const moving = board.columns.flatMap((c) => c.cards).find((c) => c.id === ticket);
  assert.ok(moving !== undefined);
  return {
    ...board,
    columns: board.columns.map((column) => {
      const cards = column.cards.filter((c) => c.id !== ticket);
      const own = column.state === to ? [...cards, moving] : cards;
      return { ...column, cards: own, count: own.length };
    }),
  };
}

test("CB-T580 最終同期が進んだ親のカードが動いたら「同期で取り込み」を付ける。同期が進んでいなければ付けない", () => {
  assert.deepEqual(syncedOf(atSync(FETCHED)), { i0001: FETCHED });
  assert.deepEqual([...syncedFamilies({ i0001: 1 }, { i0001: 2 })], ["i0001"]);
  assert.deepEqual([...syncedFamilies({ i0001: 2 }, { i0001: 2 })], []);
  // 前に居なかった親は数えない
  assert.deepEqual([...syncedFamilies({}, { i0001: 2 })], []);

  const first = movedStep(NOTHING_MOVED, atSync(FETCHED));
  assert.deepEqual(first.moved, []);
  // 同期が進んで、未着手だった子が作業中に動いた
  const synced = movedStep(first, atSync(FETCHED + 60, "i0001-02-03", "doing"));
  assert.deepEqual(synced.moved, [{ id: "i0001-02-03", from: "todo", to: "doing", by: "sync" }]);
  assert.equal(movedLabel(synced.moved[0]), "未着手 → 作業中（同期で取り込み）");
  // 同期が進まないまま動いた（承認など手元の操作）には付けない
  const local = movedStep(first, atSync(FETCHED, "i0001-02-03", "doing"));
  assert.deepEqual(local.moved, [{ id: "i0001-02-03", from: "todo", to: "doing" }]);
  assert.equal(movedLabel(local.moved[0]), "未着手 → 作業中");
  // 列が動かなければ、同期だけが進んでも動いた表示は変わらない（時刻は次の比べのために覚える）
  const quiet = movedStep(first, atSync(FETCHED + 60));
  assert.deepEqual(quiet.moved, []);
  assert.deepEqual(quiet.synced, { i0001: FETCHED + 60 });
});

test("CB-D364 同期で取り込んで動いたカードの帯に「同期で取り込み」と出る", async () => {
  const page = await openPage({ kind: "board", board: buildBoard(fixture()), moved: [{ id: "i0001-02-03", from: "todo", to: "doing", by: "sync" }] });
  try {
    const mark = page.one('li.card[data-id="i0001-02-03"] .moved-mark');
    assert.equal(mark.textContent, "未着手 → 作業中（同期で取り込み）");
  } finally {
    page.close();
  }
});

test("CB-T581 ブランチ名の / を - に置き換えた識別子を作る。Python・sh と共通の表（tests/fixtures/branch-to-id.json）で一致する", () => {
  const table = path.join(__dirname, "..", "..", "..", "..", "..", "..", "tests", "fixtures", "branch-to-id.json");
  const cases = (JSON.parse(fs.readFileSync(table, "utf8")) as { convert: { branch: string; id: string }[] }).convert;
  assert.ok(cases.length > 0);
  for (const c of cases) {
    assert.equal(branchToId(c.branch), c.id, c.branch);
  }
});

const UNPREPARED = {
  family: "feature-99-x",
  project: "",
  branch: "feature/99-x",
  mr: 12,
  reason: "親のワークツリー（.claude/worktrees/feature-99-x）がありません。ほかのツリーのコピーでは代用しない（どのチケットを正とするかが決まらない）",
};

test("CB-T582 親のワークツリーが無い親は「未準備」として読み、いまのボードの一覧に在るものだけを引ける", () => {
  const board = buildBoard({ ...fixture(), unprepared: [UNPREPARED] });
  assert.deepEqual(board.unprepared, [UNPREPARED]);
  assert.deepEqual(unpreparedOf(board, "feature-99-x", ""), UNPREPARED);
  // 画面が言った識別子・プロジェクトが一覧に無ければ引けない
  assert.equal(unpreparedOf(board, "feature-99-x", "lib"), undefined);
  assert.equal(unpreparedOf(board, "other", ""), undefined);
  // この欄を持たない古い実行ファイルの答えは空
  assert.deepEqual(buildBoard(fixture()).unprepared, []);
});

test("CB-D365 未準備のカードは理由と「準備する」を出し、押すと prepare を送る。MR の番号が控えに無ければボタンは出さない", async () => {
  const page = await openBoard({ ...fixture(), unprepared: [UNPREPARED, { ...UNPREPARED, family: "feature-98-y", project: "lib", mr: null }] });
  try {
    const cards = page.all("li.unprepared-card");
    assert.equal(cards.length, 2);
    const first = page.one('li.unprepared-card[data-family="feature-99-x"]');
    assert.match(first.textContent ?? "", /このPCでは未準備/);
    assert.match(first.textContent ?? "", /ブランチ feature\/99-x/);
    assert.match(first.textContent ?? "", /MR !12/);
    assert.match(first.querySelector(".unprepared-reason")?.textContent ?? "", /親のワークツリー/);
    page.click(page.one('li.unprepared-card[data-family="feature-99-x"] [data-action="prepare"]'));
    await page.settle();
    assert.deepEqual(page.posted.filter((m) => m.type === "prepare"), [{ type: "prepare", family: "feature-99-x", project: "" }]);
    const second = page.one('li.unprepared-card[data-family="feature-98-y"]');
    assert.equal(second.querySelectorAll('[data-action="prepare"]').length, 0);
    assert.match(second.querySelector(".unprepared-hint")?.textContent ?? "", /ccnavi-start\.sh --mr/);
  } finally {
    page.close();
  }
});
