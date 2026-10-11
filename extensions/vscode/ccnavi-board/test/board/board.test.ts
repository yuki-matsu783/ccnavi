import { test } from "node:test";
import assert from "node:assert/strict";
import { sampleBoard } from "../../src/core/tour-sample.js";
import { buildBoard, isKnownPath, parentCardOf, parentTreeOf, phaseChipOf, type Card } from "../../src/core/board.js";
import type { ArchivedTicketJson, BoardJson, ParentJson, PhaseJson, TicketJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";

function cardsOf(board: ReturnType<typeof buildBoard>): Map<string, Card> {
  return new Map(board.columns.flatMap((c) => c.cards).map((card) => [card.id, card]));
}

test("CB-T05 列は置き場から引き、親のあとに子が並ぶ。レビュー待ちは作業中、取り消しは取り消しの列", () => {
  const board = buildBoard(fixture());
  assert.deepEqual(
    board.columns.map((c) => [c.state, c.cards.map((card) => card.id)]),
    [
      ["todo", ["i0001-02-03"]],
      ["doing", ["i0001", "i0001-02-02", "i0001-02-04"]],
      ["done", ["i0001-01-01"]],
      ["cancelled", ["i0001-02-05"]],
      ["archived", []],
    ],
  );
  assert.deepEqual(board.columns.map((c) => c.label), ["未着手", "作業中", "完了", "取り消し", "アーカイブ"]);
  assert.equal(board.totalCount, 6);
  // 残りは未着手と作業中（レビュー待ちを含む）
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
  // 早めに閉じる操作（close-early）は拡張からは出さない。ターミナルで打つ
  assert.deepEqual(parent.actions, []);
  assert.equal(parent.family, "i0001");

  const done = cards.get("i0001-01-01")!;
  assert.equal(done.copyStatus, "closed");
  assert.equal(done.column, "done");
  assert.equal(done.parent, "i0001");
  assert.equal(done.phase, 1);
  assert.equal(done.family, "i0001");

  const waiting = cards.get("i0001-02-03")!;
  assert.equal(waiting.copyStatus, "none");
  assert.equal(waiting.pendingApproval, true);
  assert.deepEqual(waiting.actions, [{ kind: "approve" }]);
  assert.equal(waiting.worktreeExists, false);
  assert.equal(waiting.seenIn.length, 2);

  // レビュー待ちは提案の側（wip/proposals/review/）にあり、承認済みチケットでもある。列は作業中で、待ちは属性
  const review = cards.get("i0001-02-04")!;
  assert.equal(review.column, "doing");
  assert.equal(review.proposalState, "review");
  assert.equal(review.copyStatus, "review");
  assert.match(review.openPath, /wip\/proposals\/review\/i0001-02-04\.md$/);
  assert.equal(review.cancelledAt, "");

  // 取り消しは approved/done/ に cancelled_at を持って入る。提案の側には無く、列は取り消し
  const cancelled = cards.get("i0001-02-05")!;
  assert.equal(cancelled.column, "cancelled");
  assert.equal(cancelled.proposalState, null);
  assert.equal(cancelled.copyStatus, "closed");
  assert.notEqual(cancelled.cancelledAt, "");
  assert.equal(cancelled.cancelReason, "やめた");
  assert.match(cancelled.openPath, /\.ccnavi\/approved\/done\/i0001-02-05\.md$/);
});

test("CB-T07 提案が無ければ承認済みチケットの置き場が列。どちらにも無ければ未着手に置き、不備の文は実行ファイルのものを出す", () => {
  const base = fixture();
  const doing: TicketJson = {
    ...base.tickets[1],
    ticket: "i0001-01-09",
    proposal: null,
    copy: { status: "open", path: "/x/.ccnavi/approved/doing/i0001-01-09.md" },
  };
  const closed: TicketJson = {
    ...doing,
    ticket: "i0001-01-08",
    copy: { status: "closed", path: "/x/.ccnavi/approved/done/i0001-01-08.md" },
    cancelled_at: "2026-01-01T00:00:00+0000",
  };
  const nowhere: TicketJson = {
    ...doing,
    ticket: "i0001-01-07",
    copy: { status: "none" },
    issues: ["提案が見つかりません（承認済みチケットだけがあります）"],
  };
  const json: BoardJson = { ...base, tickets: [...base.tickets, doing, closed, nowhere] };
  const cards = cardsOf(buildBoard(json));
  assert.equal(cards.get("i0001-01-09")!.column, "doing");
  assert.deepEqual(cards.get("i0001-01-09")!.issues, []);
  assert.equal(cards.get("i0001-01-09")!.openPath, "/x/.ccnavi/approved/doing/i0001-01-09.md");
  assert.equal(cards.get("i0001-01-08")!.column, "cancelled");
  assert.equal(cards.get("i0001-01-08")!.cancelledAt, "2026-01-01T00:00:00+0000");
  assert.equal(cardsOf(buildBoard({ ...base, tickets: [{ ...closed, cancelled_at: "" }] })).get("i0001-01-08")!.column, "done");
  assert.equal(cards.get("i0001-01-07")!.column, "todo");
  assert.match(cards.get("i0001-01-07")!.issues[0], /提案が見つかりません/);
  assert.equal(buildBoard(json).issueCount, 1);
});

test("CB-T08 不備は実行ファイルの issues をそのまま持ち、ボードは足しも削りもしない", () => {
  const base = fixture();
  // 親の無い子でも、実行ファイルが言わなければボードは不備を作らない
  const stray: TicketJson = { ...base.tickets[1], ticket: "i0002-01-01", parent: "i0002" };
  assert.deepEqual(cardsOf(buildBoard({ ...base, tickets: [...base.tickets, stray] })).get("i0002-01-01")!.issues, []);
  const said: TicketJson = { ...stray, issues: ["親 i0002 が見つかりません"] };
  const board = buildBoard({ ...base, tickets: [...base.tickets, said] });
  assert.deepEqual(cardsOf(board).get("i0002-01-01")!.issues, ["親 i0002 が見つかりません"]);
  assert.equal(board.issueCount, 1);
});

test("CB-T09 依頼済みで止まったフェーズに decide、早めに閉じた親にはバッジだけ", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    close_early: { reason: "ここまで", at: "t" },
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
  // 残った指摘を決めるボタンと、レビューを終えたことの連絡（マーカーは置かない）が並ぶ
  assert.deepEqual(card.phases[1].actions, [{ kind: "review", parent: "i0001", phase: 2 }]);
  assert.deepEqual(card.phases[1].marks, ["requested"]);
  // ユーザのレビュー待ちは JSON の review_waiting をそのまま使う。依頼していないフェーズ 1 は閉じていても待ちではない
  assert.equal(card.phases[0].reviewWaiting, false);
  assert.equal(card.phases[1].reviewWaiting, true);
  // 子のカードには自分のフェーズのマーカーと、止まっているかとレビュー待ちが反映される。親は false
  assert.equal(cards.get("i0001-02-02")!.gateClosed, true);
  assert.deepEqual(cards.get("i0001-02-02")!.marks, ["requested"]);
  assert.equal(cards.get("i0001-02-02")!.reviewWaiting, true);
  assert.equal(cards.get("i0001-01-01")!.reviewWaiting, false);
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
  assert.deepEqual(cards.get("i0001-01-01")!.marks, ["requested", "reviewed"]);
  assert.equal(cards.get("i0001-01-01")!.reviewWaiting, false);
});

test("CB-T09c 親カードは、自分の番号のフェーズがレビュー待ちでも、バッジの元になる項目を持たない", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p): PhaseJson =>
      p.number === 1
        ? { ...p, marks: { requested: { at: "t" } }, review_required: true, gate_closed: true, review_waiting: true }
        : p,
    ),
  };
  // 親のチケットに phase が入っていても（判定は入れないが）、親は子の項目を反映しない
  const tickets = base.tickets.map((t) => (t.ticket === "i0001" ? { ...t, phase: 1 } : t));
  const cards = cardsOf(buildBoard({ ...base, tickets, parents: [parent] }));
  const card = cards.get("i0001")!;
  assert.equal(card.reviewWaiting, false);
  assert.equal(card.gateClosed, false);
  assert.deepEqual(card.marks, []);
  // フェーズ行と子のカードには反映される
  assert.equal(card.phases[0].reviewWaiting, true);
  assert.equal(cards.get("i0001-01-01")!.reviewWaiting, true);
});

test("CB-T10 表示しているパスだけを開く", () => {
  const board = buildBoard(fixture());
  const card = cardsOf(board).get("i0001-02-03")!;
  assert.equal(isKnownPath(board, card.openPath), true);
  assert.equal(isKnownPath(board, card.seenIn[1].path), true);
  assert.equal(isKnownPath(board, "/etc/passwd"), false);
  assert.equal(isKnownPath(board, ""), false);
});

test("CB-T11b 親の絞り込みの候補は親だけを識別子順に並べる", () => {
  const base = fixture();
  const other: TicketJson = { ...base.tickets[0], ticket: "i0000", title: "先に起きた親" };
  const child: TicketJson = { ...base.tickets[1], ticket: "i0000-01-01", parent: "i0000" };
  const board = buildBoard({ ...base, tickets: [...base.tickets, other, child] });
  assert.deepEqual(
    board.parents,
    [
      { id: "i0000", title: "先に起きた親" },
      { id: "i0001", title: base.tickets[0].title },
    ],
  );
  assert.equal(cardsOf(board).get("i0000-01-01")!.family, "i0000");
});

test("CB-T11 親のワークツリーを引ける", () => {
  const board = buildBoard(fixture());
  assert.match(parentTreeOf(board, "i0001") ?? "", /worktrees\/i0001$/);
  assert.equal(parentTreeOf(board, "i0001-01-01"), undefined);
  assert.equal(parentTreeOf(board, "nope"), undefined);
});

test("CB-T117 散在は実行ファイルの答えをそのまま載せ、ほかのツリー上のチケット自体は数えない", () => {
  const base = fixture();
  const cards = cardsOf(buildBoard(base));
  // 正常な場面。提案の側にあるもの（承認待ち・レビュー待ち）が親と兄弟のワークツリー上にもあっても、
  // 実行ファイルが「正は決まっている」と言うので散在ではない。承認済みチケットの側にあるものは提案が無いのでほかのツリー上のチケットも無い
  for (const id of ["i0001", "i0001-01-01", "i0001-02-02", "i0001-02-03", "i0001-02-04", "i0001-02-05"]) {
    assert.deepEqual(cards.get(id)!.scattered, [], id);
  }
  for (const id of ["i0001-02-03", "i0001-02-04"]) {
    assert.ok(cards.get(id)!.seenIn.length > 1, id);
  }
  for (const id of ["i0001", "i0001-01-01", "i0001-02-02", "i0001-02-05"]) {
    assert.equal(cards.get(id)!.seenIn.length, 0, id);
  }

  // 決まらないときは、実行ファイルが挙げた候補をそのまま持つ。まとめ直さない。
  const child = base.tickets.find((t) => t.ticket === "i0001-02-03")!;
  const lost: TicketJson = {
    ...child,
    seen_in: [
      { tree: "", state: "todo", path: "/x/wip/proposals/todo/i0001-02-03.md" },
      { tree: "i0001-02-02", state: "todo", path: "/x/w/i0001-02-02/wip/proposals/todo/i0001-02-03.md" },
    ],
    scattered: [
      { tree: "", state: "todo", path: "/x/wip/proposals/todo/i0001-02-03.md" },
      { tree: "i0001-02-02", state: "todo", path: "/x/w/i0001-02-02/wip/proposals/todo/i0001-02-03.md" },
    ],
  };
  const card = cardsOf(buildBoard({ ...base, tickets: [lost] })).get("i0001-02-03")!;
  assert.deepEqual(
    card.scattered.map((s) => `${s.tree}:${s.state}`),
    [":todo", "i0001-02-02:todo"],
  );
  // ほかのツリー上のチケット自体は残す。開いたファイルからカードを引き当てるのに使う。
  assert.equal(card.seenIn.length, 2);
});

/** フェーズ 2 を「依頼済みで止まったまま（ユーザのレビュー待ち）」にし、依頼のマーカーにマージリクエストを持たせる */
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

test("CB-T131 レビュー待ちのフェーズに「レビュー済み連絡」も付き、依頼のマーカーのマージリクエストがフェーズ行と親カードに載る", () => {
  const cards = cardsOf(buildBoard(waitingWithMr("https://example.com/o/r/pull/18#issuecomment-5")));
  const card = cards.get("i0001")!;
  assert.deepEqual(card.phases[1].actions, [{ kind: "review", parent: "i0001", phase: 2 }]);
  // フェーズ行は依頼の投稿を指す。親カードは断片を除いてマージリクエスト自体を指す。子カードには持たせない
  assert.equal(card.phases[1].mrUrl, "https://example.com/o/r/pull/18#issuecomment-5");
  assert.equal(card.phases[1].mrNumber, 18);
  assert.equal(card.phases[0].mrUrl, "");
  assert.equal(card.phases[0].mrNumber, null);
  assert.equal(card.mrUrl, "https://example.com/o/r/pull/18");
  assert.equal(card.mrNumber, 18);
  assert.equal(cards.get("i0001-02-02")!.mrUrl, "");
  // 引く関数。承認と残った指摘を決めるボタンが使う parentTreeOf と同じ場所を見る
  assert.equal(parentCardOf(buildBoard(waitingWithMr("u")), "i0001")?.id, "i0001");
  assert.equal(parentCardOf(buildBoard(waitingWithMr("u")), "i0001-02-02"), undefined);
  assert.equal(phaseChipOf(buildBoard(waitingWithMr("u")), "i0001", 2)?.mrUrl, "u");
  assert.equal(phaseChipOf(buildBoard(waitingWithMr("u")), "i0001", 9), undefined);
  // 依頼のマーカーが無くレビュー済みだけ残った形（close-early が置く）では、リンクも番号も出さない（url が無い）。連絡のボタンは待ちでなければ出ない
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

test("CB-T132 要対応は実行ファイルの attention をそのまま持ち、条件をボードで組み直さない", () => {
  // 見本: 実行ファイルは承認待ちでワークツリーの無い子だけを要対応と言っている
  const cards = cardsOf(buildBoard(fixture()));
  assert.deepEqual(
    [...cards.values()].filter((card) => card.attention).map((card) => card.id),
    ["i0001-02-03"],
  );
  // 承認待ち・ワークツリーなし・HIGH のリスク・止まったフェーズを足しても、実行ファイルが偽と言えば偽のまま
  const base = fixture();
  const loud: TicketJson = {
    ...base.tickets[2],
    worktree: { exists: false, path: "" },
    risk: { points: 70, level: "CRITICAL" },
    scattered: [{ tree: "a", state: "doing", path: "x" }],
    blocked: "止まっている",
  };
  const parent: ParentJson = { ...base.parents[0], phases: base.parents[0].phases.map((p) => ({ ...p, gate_closed: true, review_waiting: true })) };
  const quiet = cardsOf(
    buildBoard({ ...base, pending_approval: [...base.pending_approval, loud.ticket, "i0001"], tickets: base.tickets.map((t) => (t.ticket === loud.ticket ? loud : t)), parents: [parent] }),
  );
  assert.equal(quiet.get(loud.ticket)!.attention, false);
  assert.equal(quiet.get("i0001")!.attention, false);
  // 実行ファイルが真と言えば、ほかが順調でも真
  const said = cardsOf(buildBoard({ ...base, tickets: base.tickets.map((t) => (t.ticket === "i0001-01-01" ? { ...t, attention: true } : t)) }));
  assert.equal(said.get("i0001-01-01")!.attention, true);
});

test("CB-T138 止まっているチケットは、理由と実行ファイルが組んだ不備・要対応をそのまま持つ", () => {
  // 判定はこのチケットのワークツリーへの書き込みを全部止めるが、`copy.status` は `open` の
  // ままなので、列からも承認済みのバッジからも分からない。
  const base = fixture();
  const child = base.tickets.find((t) => t.ticket === "i0001-02-02")!;
  const blocked = "親 i0001 の承認済みチケットが作業中に無い（未承認か、閉じている）";
  const stopped: TicketJson = { ...child, blocked, issues: [`書き込みが止まっています: ${blocked}`], attention: true };
  const parent = base.tickets.find((t) => t.ticket === "i0001")!;
  const card = cardsOf(buildBoard({ ...base, tickets: [parent, stopped] })).get("i0001-02-02")!;

  assert.equal(card.blocked, stopped.blocked);
  assert.deepEqual(card.issues, [`書き込みが止まっています: ${stopped.blocked}`]);
  assert.equal(card.attention, true);
  // 列は今までどおり。止まっているのは書き込みであって、置き場は動いていない。
  assert.equal(card.column, "doing");
  assert.equal(card.copyStatus, "open");
});

test("CB-T139 止まっていないチケットは今までどおり、不備も注意も増えない", () => {
  const cards = cardsOf(buildBoard(fixture()));
  for (const card of cards.values()) {
    assert.equal(card.blocked, "");
    assert.equal(
      card.issues.some((i) => i.startsWith("書き込みが止まっている")),
      false,
    );
  }
});

test("CB-T215 案内の見本のボードは、承認待ち・作業中・完了のカードを持ち、どれも見本と分かる題を持つ", () => {
  const board = sampleBoard("/ws", "2026-01-01");
  const byState = Object.fromEntries(board.columns.map((column) => [column.state, column.cards.map((card) => card.id)]));
  assert.deepEqual(byState, { todo: ["sample-a"], doing: ["sample-b", "sample-b-02-01"], done: ["sample-b-01-01"], cancelled: [], archived: [] });
  assert.deepEqual(board.pendingApproval, ["sample-a"]);
  assert.equal(board.root, "/ws");
  const cards = board.columns.flatMap((column) => column.cards);
  assert.ok(cards.every((card) => card.title.startsWith("（見本）")));
  // 見本から開くファイルは無い（押しても何も開かない）。不備も出さない
  assert.ok(cards.every((card) => card.openPath === "" && card.issues.length === 0));
});

test("CB-T260 履歴はカードへそのまま渡り、列・注意・バッジの材料にはならない", () => {
  // 履歴は補助の記録で、状態の正は置き場。履歴が「取り消し」と言っていても、列は置き場で決まる。
  const base = fixture();
  const child = base.tickets.find((t) => t.ticket === "i0001-02-02")!;
  const history = [
    { at: "2026-09-26T09:00:00Z", kind: "cancelled", from: "doing", to: "done", via: "cli", phase: null, mark: "", reason: "試し" },
  ];
  const withHistory: TicketJson = { ...child, history };
  const before = cardsOf(buildBoard(base)).get("i0001-02-02")!;
  const after = cardsOf(buildBoard({ ...base, tickets: base.tickets.map((t) => (t.ticket === "i0001-02-02" ? withHistory : t)) })).get("i0001-02-02")!;
  assert.deepEqual(after.history, history);
  assert.equal(after.column, before.column);
  assert.equal(after.attention, before.attention);
  assert.deepEqual(after.issues, before.issues);
  assert.equal(after.cancelledAt, before.cancelledAt);
});

test("CB-T263 先行待ちはカードへそのまま渡り、列と要対応は変えない", () => {
  // 判定（先行が done/ に在って取り消しでないか）は実行ファイルが出す。ボードは先行の置き場から組み直さない。
  const base = fixture();
  const unmet = [{ ticket: "i0001-02-02", state: "doing", label: "作業中（doing/）" }];
  const before = cardsOf(buildBoard(base)).get("i0001-02-03")!;
  const after = cardsOf(buildBoard({ ...base, tickets: base.tickets.map((t) => (t.ticket === "i0001-02-03" ? { ...t, predecessors_unmet: unmet } : t)) })).get("i0001-02-03")!;
  assert.deepEqual(after.predecessorsUnmet, unmet);
  assert.equal(after.column, before.column);
  assert.equal(after.attention, before.attention);
  // 欄が空なら何も持たない
  assert.deepEqual(before.predecessorsUnmet, []);
});

function archivedJson(ticket: string, parent = ""): ArchivedTicketJson {
  return {
    ticket,
    parent,
    phase: parent === "" ? null : 1,
    title: `退避した ${ticket}`,
    project: "",
    path: `/ws/logs/archive/self/done/${ticket}.md`,
    approved_at: "2026-01-01T00:00:00+09:00",
    started_at: "",
    completed_at: "2026-01-02T00:00:00+09:00",
    cancelled_at: "",
    cancel_reason: "",
    history: [{ at: "2026-01-03T00:00:00Z", kind: "archived", from: "done", to: "archive", via: "cli", phase: null, mark: "", reason: "" }],
  };
}

test("CB-T301 退避のチケットはアーカイブの列に並び、操作も要対応も持たず、集計と親の候補に数えない", () => {
  const base = fixture();
  const board = buildBoard({ ...base, archived: [archivedJson("old"), archivedJson("old-01-01", "old")] });
  const archived = board.columns.find((c) => c.state === "archived")!;
  assert.deepEqual(archived.cards.map((card) => card.id), ["old", "old-01-01"]);
  const parent = archived.cards[0];
  assert.equal(parent.column, "archived");
  assert.equal(parent.copyStatus, "archived");
  assert.equal(parent.attention, false);
  assert.deepEqual(parent.actions, []);
  assert.equal(parent.history.at(-1)?.kind, "archived");
  assert.equal(archived.cards[1].family, "old");
  assert.equal(board.archivedCount, 2);
  assert.equal(board.totalCount, buildBoard(base).totalCount);
  assert.equal(board.remainingCount, buildBoard(base).remainingCount);
  assert.ok(!board.parents.some((p) => p.id === "old"));
  // 退避したファイルは開ける（カードの openPath がそのパス）
  assert.ok(isKnownPath(board, "/ws/logs/archive/self/done/old.md"));
  assert.ok(!isKnownPath(buildBoard(base), "/ws/logs/archive/self/done/old.md"));
});

test("CB-T302 置き場に同じ識別子があれば、退避のカードは出さない（置き場の側が正）", () => {
  const base = fixture();
  const board = buildBoard({ ...base, archived: [archivedJson("i0001-01-01", "i0001")] });
  const ids = board.columns.flatMap((c) => c.cards).filter((card) => card.id === "i0001-01-01");
  assert.equal(ids.length, 1);
  assert.equal(ids[0].column, "done");
  assert.equal(board.archivedCount, 0);
});

test("CB-T304 退避のカードの重なりはプロジェクトと識別子で見る。別のプロジェクトの同じ識別子は出す", () => {
  const base = fixture();
  const other = { ...archivedJson("i0001-01-01", "i0001"), project: "lib" };
  const board = buildBoard({ ...base, archived: [other] });
  const archived = board.columns.find((c) => c.state === "archived")!;
  assert.deepEqual(archived.cards.map((card) => `${card.project}/${card.id}`), ["lib/i0001-01-01"]);
});
