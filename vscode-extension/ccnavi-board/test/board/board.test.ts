import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard, isKnownPath, parentCardOf, parentTreeOf, phaseChipOf, type Card } from "../../src/core/board.js";
import type { BoardJson, ParentJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";

function cardsOf(board: ReturnType<typeof buildBoard>): Map<string, Card> {
  return new Map(board.columns.flatMap((c) => c.cards).map((card) => [card.id, card]));
}

test("CB-T05 列はチケットの置き場で、親のあとに子が並ぶ。取り消しは完了に入る", () => {
  const board = buildBoard(fixture());
  assert.deepEqual(
    board.columns.map((c) => [c.state, c.cards.map((card) => card.id)]),
    [
      ["todo", ["i0001-03"]],
      ["doing", ["i0001", "i0001-02"]],
      ["review", ["i0001-04"]],
      ["done", ["i0001-01", "i0001-05"]],
    ],
  );
  assert.deepEqual(board.columns.map((c) => c.label), ["承認待ち", "作業中", "レビュー待ち", "完了"]);
  assert.equal(board.totalCount, 6);
  // 残りは承認待ち・作業中・レビュー待ち
  assert.equal(board.remainingCount, 4);
});

test("CB-T06 カードに承認済みチケット・ワークツリー・マーカー・承認待ちが載る", () => {
  const cards = cardsOf(buildBoard(fixture()));
  const parent = cards.get("i0001")!;
  assert.equal(parent.isParent, true);
  assert.equal(parent.copyStatus, "open");
  assert.equal(parent.worktreeExists, true);
  assert.match(parent.stage, /作業中/);
  assert.equal(parent.phases.length, 2);
  // 締める（wrapup）は拡張からは出さない。端末で打つ
  assert.deepEqual(parent.actions, []);
  assert.equal(parent.family, "i0001");

  const done = cards.get("i0001-01")!;
  assert.equal(done.copyStatus, "closed");
  assert.equal(done.column, "done");
  assert.equal(done.parent, "i0001");
  assert.equal(done.phase, 1);
  assert.equal(done.family, "i0001");

  const waiting = cards.get("i0001-03")!;
  assert.equal(waiting.copyStatus, "none");
  assert.equal(waiting.pendingApproval, true);
  assert.deepEqual(waiting.actions, [{ kind: "approve" }]);
  assert.equal(waiting.worktreeExists, false);
  assert.equal(waiting.seenIn.length, 2);

  // レビュー待ちは提案の側（wip/proposals/review/）にあり、承認済みチケットでもある
  const review = cards.get("i0001-04")!;
  assert.equal(review.column, "review");
  assert.equal(review.proposalState, "review");
  assert.equal(review.copyStatus, "review");
  assert.match(review.openPath, /wip\/proposals\/review\/i0001-04\.md$/);
  assert.equal(review.cancelledAt, "");

  // 取り消しは完了列に cancelled_at を持って入る。提案の側には無い
  const cancelled = cards.get("i0001-05")!;
  assert.equal(cancelled.column, "done");
  assert.equal(cancelled.proposalState, null);
  assert.equal(cancelled.copyStatus, "closed");
  assert.notEqual(cancelled.cancelledAt, "");
  assert.equal(cancelled.cancelReason, "やめた");
  assert.match(cancelled.openPath, /\.ccnavi\/approved\/done\/i0001-05\.md$/);
});

test("CB-T07 提案が無ければ承認済みチケットの置き場が列。どちらにも無ければ不備として承認待ちに置く", () => {
  const base = fixture();
  const doing: TicketJson = {
    ...base.tickets[1],
    ticket: "i0001-09",
    proposal: null,
    copy: { status: "open", path: "/x/.ccnavi/approved/doing/i0001-09.md" },
  };
  const closed: TicketJson = {
    ...doing,
    ticket: "i0001-08",
    copy: { status: "closed", path: "/x/.ccnavi/approved/done/i0001-08.md" },
    cancelled_at: "2026-01-01T00:00:00+0000",
  };
  const nowhere: TicketJson = { ...doing, ticket: "i0001-07", copy: { status: "none" } };
  const json: BoardJson = { ...base, tickets: [...base.tickets, doing, closed, nowhere] };
  const cards = cardsOf(buildBoard(json));
  assert.equal(cards.get("i0001-09")!.column, "doing");
  assert.deepEqual(cards.get("i0001-09")!.issues, []);
  assert.equal(cards.get("i0001-09")!.openPath, "/x/.ccnavi/approved/doing/i0001-09.md");
  assert.equal(cards.get("i0001-08")!.column, "done");
  assert.equal(cards.get("i0001-08")!.cancelledAt, "2026-01-01T00:00:00+0000");
  assert.equal(cards.get("i0001-07")!.column, "todo");
  assert.match(cards.get("i0001-07")!.issues[0], /提案が見つからない/);
  assert.equal(buildBoard(json).issueCount, 1);
});

test("CB-T08 親の無い子は不備", () => {
  const base = fixture();
  const stray: TicketJson = { ...base.tickets[1], ticket: "i0002-01", parent: "i0002" };
  const cards = cardsOf(buildBoard({ ...base, tickets: [...base.tickets, stray] }));
  assert.match(cards.get("i0002-01")!.issues[0], /親 i0002 が見つからない/);
});

test("CB-T09 依頼済みで止まったフェーズに accept、締めた親にはバッジだけ", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    wrapup: { reason: "ここまで", at: "t" },
    phases: base.parents[0].phases.map((p): PhaseJson => {
      const marks: Record<string, Record<string, unknown>> =
        p.number === 2 ? { requested: { at: "t" } } : {};
      // 判定が出す形に揃える。止まるのはレビュー要のときで、レビュー待ちは依頼済の閉じたフェーズだけ
      return {
        ...p,
        state: p.number === 2 ? "ended" : p.state,
        gate_closed: true,
        review_required: true,
        review_waiting: p.number === 2,
        marks,
      };
    }),
  };
  const cards = cardsOf(buildBoard({ ...base, parents: [parent] }));
  const card = cards.get("i0001")!;
  assert.deepEqual(card.actions, []);
  assert.equal(card.wrapped, true);
  assert.deepEqual(card.phases[0].actions, []);
  // 受け入れと、レビューを終えたことの連絡（マーカーは置かない）が並ぶ
  assert.deepEqual(card.phases[1].actions, [
    { kind: "accept", parent: "i0001", phase: 2 },
    { kind: "reviewed", parent: "i0001", phase: 2 },
  ]);
  assert.deepEqual(card.phases[1].marks, ["requested"]);
  // 人のレビュー待ちは JSON の review_waiting の写し。依頼していないフェーズ 1 は閉じていても待ちではない
  assert.equal(card.phases[0].reviewWaiting, false);
  assert.equal(card.phases[1].reviewWaiting, true);
  // 子のカードには自分のフェーズのマーカーと、止まっているかとレビュー待ちが写る。親は false
  assert.equal(cards.get("i0001-02")!.gateClosed, true);
  assert.deepEqual(cards.get("i0001-02")!.marks, ["requested"]);
  assert.equal(cards.get("i0001-02")!.reviewWaiting, true);
  assert.equal(cards.get("i0001-01")!.reviewWaiting, false);
  assert.equal(card.reviewWaiting, false);
});

test("CB-T09b レビューが済んで止まらなくなったフェーズは、依頼済のマーカーが残っていてもレビュー待ちではない", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson =>
      p.number === 1
        ? {
            ...p,
            marks: { requested: { at: "t" }, reviewed: { at: "t" } },
            review_required: true,
            gate_closed: false,
            review_waiting: false,
          }
        : p,
    ),
  };
  const cards = cardsOf(buildBoard({ ...base, parents: [parent] }));
  const card = cards.get("i0001")!;
  // マーカーは残り、待ちだけが false になる。マーカーを削って「直す」形は取らない
  assert.deepEqual(card.phases[0].marks, ["requested", "reviewed"]);
  assert.equal(card.phases[0].reviewWaiting, false);
  assert.deepEqual(card.phases[0].actions, []);
  assert.deepEqual(cards.get("i0001-01")!.marks, ["requested", "reviewed"]);
  assert.equal(cards.get("i0001-01")!.reviewWaiting, false);
});

test("CB-T09c 親カードは、自分の番号のフェーズがレビュー待ちでも、札の元になる項目を持たない", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson =>
      p.number === 1
        ? { ...p, marks: { requested: { at: "t" } }, review_required: true, gate_closed: true, review_waiting: true }
        : p,
    ),
  };
  // 親のチケットに phase が入っていても（判定は入れないが）、親は子の項目を写さない
  const tickets = base.tickets.map((t) => (t.ticket === "i0001" ? { ...t, phase: 1 } : t));
  const cards = cardsOf(buildBoard({ ...base, tickets, parents: [parent] }));
  const card = cards.get("i0001")!;
  assert.equal(card.reviewWaiting, false);
  assert.equal(card.gateClosed, false);
  assert.deepEqual(card.marks, []);
  // フェーズ行と子のカードには写る
  assert.equal(card.phases[0].reviewWaiting, true);
  assert.equal(cards.get("i0001-01")!.reviewWaiting, true);
});

test("CB-T10 表示しているパスだけを開く", () => {
  const board = buildBoard(fixture());
  const card = cardsOf(board).get("i0001-03")!;
  assert.equal(isKnownPath(board, card.openPath), true);
  assert.equal(isKnownPath(board, card.seenIn[1].path), true);
  assert.equal(isKnownPath(board, "/etc/passwd"), false);
  assert.equal(isKnownPath(board, ""), false);
});

test("CB-T11b 親の絞り込みの候補は親だけを識別子順に並べる", () => {
  const base = fixture();
  const other: TicketJson = { ...base.tickets[0], ticket: "i0000", title: "先に起きた親" };
  const child: TicketJson = { ...base.tickets[1], ticket: "i0000-01", parent: "i0000" };
  const board = buildBoard({ ...base, tickets: [...base.tickets, other, child] });
  assert.deepEqual(
    board.parents,
    [
      { id: "i0000", title: "先に起きた親" },
      { id: "i0001", title: base.tickets[0].title },
    ],
  );
  assert.equal(cardsOf(board).get("i0000-01")!.family, "i0000");
});

test("CB-T11 親のワークツリーを引ける", () => {
  const board = buildBoard(fixture());
  assert.match(parentTreeOf(board, "i0001") ?? "", /worktrees\/i0001$/);
  assert.equal(parentTreeOf(board, "i0001-01"), undefined);
  assert.equal(parentTreeOf(board, "nope"), undefined);
});

test("CB-T117 散在は実行ファイルの答えをそのまま載せ、写り自体は数えない", () => {
  const base = fixture();
  const cards = cardsOf(buildBoard(base));
  // 正常な場面。提案の側にあるもの（承認待ち・レビュー待ち）が親と兄弟のワークツリーに写っていても、
  // 実行ファイルが「本物は決まっている」と言うので散在ではない。承認済みチケットの側にあるものは提案が無いので写りも無い
  for (const id of ["i0001", "i0001-01", "i0001-02", "i0001-03", "i0001-04", "i0001-05"]) {
    assert.deepEqual(cards.get(id)!.scattered, [], id);
  }
  for (const id of ["i0001-03", "i0001-04"]) {
    assert.ok(cards.get(id)!.seenIn.length > 1, id);
  }
  for (const id of ["i0001", "i0001-01", "i0001-02", "i0001-05"]) {
    assert.equal(cards.get(id)!.seenIn.length, 0, id);
  }

  // 決まらないときは、実行ファイルが挙げた候補をそのまま持つ。畳み直さない。
  const child = base.tickets.find((t) => t.ticket === "i0001-03")!;
  const lost: TicketJson = {
    ...child,
    seen_in: [
      { tree: "", state: "todo", path: "/x/wip/proposals/todo/i0001-03.md" },
      { tree: "i0001-02", state: "todo", path: "/x/w/i0001-02/wip/proposals/todo/i0001-03.md" },
    ],
    scattered: [
      { tree: "", state: "todo", path: "/x/wip/proposals/todo/i0001-03.md" },
      { tree: "i0001-02", state: "todo", path: "/x/w/i0001-02/wip/proposals/todo/i0001-03.md" },
    ],
  };
  const card = cardsOf(buildBoard({ ...base, tickets: [lost] })).get("i0001-03")!;
  assert.deepEqual(
    card.scattered.map((s) => `${s.tree}:${s.state}`),
    [":todo", "i0001-02:todo"],
  );
  // 写り自体は残す。開いたファイルからカードを引き当てるのに使う。
  assert.equal(card.seenIn.length, 2);
});

/** フェーズ 2 を「依頼済みで止まったまま（人のレビュー待ち）」にし、依頼のマーカーに MR を持たせる */
function waitingWithMr(url: string): BoardJson {
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

test("CB-T131 レビュー待ちのフェーズに「レビュー済み連絡」も付き、依頼のマーカーの MR がフェーズ行と親カードに写る", () => {
  const cards = cardsOf(buildBoard(waitingWithMr("https://example.com/o/r/pull/18#issuecomment-5")));
  const card = cards.get("i0001")!;
  assert.deepEqual(card.phases[1].actions, [
    { kind: "accept", parent: "i0001", phase: 2 },
    { kind: "reviewed", parent: "i0001", phase: 2 },
  ]);
  // フェーズ行は依頼の投稿を指す。親カードは断片を落としてマージリクエスト自体を指す。子カードには持たせない
  assert.equal(card.phases[1].mrUrl, "https://example.com/o/r/pull/18#issuecomment-5");
  assert.equal(card.phases[1].mrNumber, 18);
  assert.equal(card.phases[0].mrUrl, "");
  assert.equal(card.phases[0].mrNumber, null);
  assert.equal(card.mrUrl, "https://example.com/o/r/pull/18");
  assert.equal(card.mrNumber, 18);
  assert.equal(cards.get("i0001-02")!.mrUrl, "");
  // 引く道具。承認と受け入れが使う parentTreeOf と同じ場所を見る
  assert.equal(parentCardOf(buildBoard(waitingWithMr("u")), "i0001")?.id, "i0001");
  assert.equal(parentCardOf(buildBoard(waitingWithMr("u")), "i0001-02"), undefined);
  assert.equal(phaseChipOf(buildBoard(waitingWithMr("u")), "i0001", 2)?.mrUrl, "u");
  assert.equal(phaseChipOf(buildBoard(waitingWithMr("u")), "i0001", 9), undefined);
  // 依頼のマーカーが無くレビュー済みだけ残った形（wrapup が置く）では、リンクも番号も出さない（url が無い）。連絡のボタンは待ちでなければ出ない
  const base = fixture();
  const reviewedOnly: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson => (p.number === 1 ? { ...p, marks: { reviewed: { mr: 7, accepted: [], at: "t" } } } : p)),
  };
  const quiet = cardsOf(buildBoard({ ...base, parents: [reviewedOnly] })).get("i0001")!;
  assert.equal(quiet.phases[0].mrNumber, null);
  assert.equal(quiet.phases[0].mrUrl, "");
  assert.equal(quiet.mrUrl, "");
  assert.deepEqual(quiet.phases[0].actions, []);
  assert.deepEqual(quiet.phases[1].actions, []);
});

test("CB-T132 要対応は承認待ち・札・不備・フェーズ行の要約の条件で、判定はし直さない", () => {
  // 見本: 親は順調、閉じた子・作業中の子・レビュー待ちの子・取り消した子は順調、承認待ちでワークツリーの無い子だけが要対応
  const cards = cardsOf(buildBoard(fixture()));
  assert.equal(cards.get("i0001")!.attention, false);
  assert.equal(cards.get("i0001-01")!.attention, false);
  assert.equal(cards.get("i0001-02")!.attention, false);
  assert.equal(cards.get("i0001-03")!.attention, true);
  assert.equal(cards.get("i0001-04")!.attention, false);
  // 取り消した子はワークツリーが無いが、完了列なので要対応ではない
  assert.equal(cards.get("i0001-05")!.attention, false);
  // 承認待ちは pending_approval で見る。親の改版は承認済みチケットが開いたまま（札の「未承認」は出ない）でも要対応。
  // 落とすと「要対応だけ」の絞り込みで隠れ、承認の対象から外れる
  const base = fixture();
  const revision = cardsOf(buildBoard({ ...base, pending_approval: [...base.pending_approval, "i0001"] }));
  assert.equal(revision.get("i0001")!.copyStatus, "open");
  assert.equal(revision.get("i0001")!.pendingApproval, true);
  assert.equal(revision.get("i0001")!.attention, true);
  // ワークツリーが無いのが要対応なのは承認待ちと作業中だけ。レビュー待ちは畳んだ後でも普通
  const noTree: TicketJson = { ...base.tickets[4], worktree: { exists: false, path: "" } };
  const reviewNoTree = cardsOf(buildBoard({ ...base, tickets: [...base.tickets.slice(0, 4), noTree, base.tickets[5]] }));
  assert.equal(reviewNoTree.get("i0001-04")!.column, "review");
  assert.equal(reviewNoTree.get("i0001-04")!.attention, false);
  const doingNoTree = cardsOf(buildBoard({ ...base, tickets: base.tickets.map((t) => (t.ticket === "i0001-02" ? { ...t, worktree: { exists: false, path: "" } } : t)) }));
  assert.equal(doingNoTree.get("i0001-02")!.attention, true);
  // 人のレビュー待ちのフェーズがあれば、その子（レビュー待ち）も親（フェーズ行の要約）も要対応
  const waiting = cardsOf(buildBoard(waitingWithMr("u")));
  assert.equal(waiting.get("i0001")!.attention, true);
  assert.equal(waiting.get("i0001-02")!.attention, true);
  assert.equal(waiting.get("i0001-01")!.attention, false);
  // HIGH 以上のリスクは要対応、MEDIUM は違う。不備（親が見つからない）も要対応
  const risky = (level: string): TicketJson => ({ ...base.tickets[1], risk: { points: 70, level } });
  const withRisk = (level: string) => cardsOf(buildBoard({ ...base, tickets: [base.tickets[0], risky(level), ...base.tickets.slice(2)] }));
  assert.equal(withRisk("HIGH").get("i0001-01")!.attention, true);
  assert.equal(withRisk("CRITICAL").get("i0001-01")!.attention, true);
  assert.equal(withRisk("MEDIUM").get("i0001-01")!.attention, false);
  const stray: TicketJson = { ...base.tickets[1], ticket: "i0002-01", parent: "i0002" };
  assert.equal(cardsOf(buildBoard({ ...base, tickets: [...base.tickets, stray] })).get("i0002-01")!.attention, true);
});
