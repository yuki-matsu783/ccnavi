import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard, isKnownPath, parentTreeOf, type Card } from "../src/core/board.js";
import type { BoardJson, ParentJson, PhaseJson, TicketJson } from "../src/core/model.js";
import { fixture } from "./fixture.js";

function cardsOf(board: ReturnType<typeof buildBoard>): Map<string, Card> {
  return new Map(board.columns.flatMap((c) => c.cards).map((card) => [card.id, card]));
}

test("CB-T05 列は提案の置き場で、親のあとに子が並ぶ", () => {
  const board = buildBoard(fixture());
  assert.deepEqual(
    board.columns.map((c) => [c.state, c.cards.map((card) => card.id)]),
    [
      ["todo", ["i0001", "i0001-03"]],
      ["doing", ["i0001-02"]],
      ["done", ["i0001-01"]],
      ["cancelled", []],
    ],
  );
  assert.equal(board.totalCount, 4);
  assert.equal(board.remainingCount, 3);
});

test("CB-T06 カードに写し・作業ツリー・印・承認待ちが載る", () => {
  const cards = cardsOf(buildBoard(fixture()));
  const parent = cards.get("i0001")!;
  assert.equal(parent.isParent, true);
  assert.equal(parent.copyStatus, "open");
  assert.equal(parent.worktreeExists, true);
  assert.match(parent.stage, /作業中/);
  assert.equal(parent.phases.length, 2);
  assert.deepEqual(parent.actions, [{ kind: "wrapup", parent: "i0001" }]);

  const done = cards.get("i0001-01")!;
  assert.equal(done.copyStatus, "closed");
  assert.equal(done.column, "done");
  assert.equal(done.parent, "i0001");
  assert.equal(done.phase, 1);

  const waiting = cards.get("i0001-03")!;
  assert.equal(waiting.copyStatus, "none");
  assert.equal(waiting.pendingApproval, true);
  assert.deepEqual(waiting.actions, [{ kind: "approve" }]);
  assert.equal(waiting.worktreeExists, false);
  assert.equal(waiting.seenIn.length, 2);
});

test("CB-T07 提案の無い写しは不備として出し、閉じていれば完了に置く", () => {
  const base = fixture();
  const orphan: TicketJson = {
    ...base.tickets[1],
    ticket: "i0001-09",
    proposal: null,
    copy: { status: "open", path: "/x/.claude/ccnavi/tickets/i0001-09.md" },
  };
  const closed: TicketJson = {
    ...orphan,
    ticket: "i0001-08",
    copy: { status: "closed" },
    cancelled_at: "2026-01-01T00:00:00+0000",
  };
  const json: BoardJson = { ...base, tickets: [...base.tickets, orphan, closed] };
  const cards = cardsOf(buildBoard(json));
  assert.equal(cards.get("i0001-09")!.column, "todo");
  assert.match(cards.get("i0001-09")!.issues[0], /提案が見つからない/);
  assert.equal(cards.get("i0001-09")!.openPath, "/x/.claude/ccnavi/tickets/i0001-09.md");
  assert.equal(cards.get("i0001-08")!.column, "cancelled");
  assert.equal(buildBoard(json).issueCount, 1);
});

test("CB-T08 親の無い子は不備", () => {
  const base = fixture();
  const stray: TicketJson = { ...base.tickets[1], ticket: "i0002-01", parent: "i0002" };
  const cards = cardsOf(buildBoard({ ...base, tickets: [...base.tickets, stray] }));
  assert.match(cards.get("i0002-01")!.issues[0], /親 i0002 が見つからない/);
});

test("CB-T09 依頼済みでゲートが閉じたフェーズに accept、締めた親に wrapup は出ない", () => {
  const base = fixture();
  const parent: ParentJson = {
    ...base.parents[0],
    wrapup: { reason: "ここまで", at: "t" },
    phases: base.parents[0].phases.map((p): PhaseJson => {
      const marks: Record<string, Record<string, unknown>> =
        p.number === 2 ? { requested: { at: "t" } } : {};
      return { ...p, state: p.number === 2 ? "ended" : p.state, gate_closed: true, marks };
    }),
  };
  const cards = cardsOf(buildBoard({ ...base, parents: [parent] }));
  const card = cards.get("i0001")!;
  assert.deepEqual(card.actions, []);
  assert.equal(card.wrapped, true);
  assert.deepEqual(card.phases[0].actions, []);
  assert.deepEqual(card.phases[1].actions, [{ kind: "accept", parent: "i0001", phase: 2 }]);
  assert.deepEqual(card.phases[1].marks, ["requested"]);
  // 子のカードには自分のフェーズの印とゲートが写る
  assert.equal(cards.get("i0001-02")!.gateClosed, true);
  assert.deepEqual(cards.get("i0001-02")!.marks, ["requested"]);
});

test("CB-T10 表示しているパスだけを開く", () => {
  const board = buildBoard(fixture());
  const card = cardsOf(board).get("i0001-03")!;
  assert.equal(isKnownPath(board, card.openPath), true);
  assert.equal(isKnownPath(board, card.seenIn[1].path), true);
  assert.equal(isKnownPath(board, "/etc/passwd"), false);
  assert.equal(isKnownPath(board, ""), false);
});

test("CB-T11 親の作業ツリーを引ける", () => {
  const board = buildBoard(fixture());
  assert.match(parentTreeOf(board, "i0001") ?? "", /worktrees\/i0001$/);
  assert.equal(parentTreeOf(board, "i0001-01"), undefined);
  assert.equal(parentTreeOf(board, "nope"), undefined);
});
