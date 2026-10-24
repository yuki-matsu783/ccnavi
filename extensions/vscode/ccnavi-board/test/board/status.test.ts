/**
 * 状態の札（`statusOf`）とフェーズ行の状態の一言（`phaseStatusOf`）。
 * 札は「状況」、操作は「ユーザができること」で、別の規則で決める。表の全行を、実行ファイルの JSON と同じ形のチケットから
 * `buildBoard` を通して確かめる（画面が見るのと同じ道）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard, type Card } from "../../src/core/board.js";
import type { ArchivedTicketJson, BoardJson, PhaseJson } from "../../src/core/model.js";
import { phaseStatusOf } from "../../src/core/status.js";
import { boardOf, closedJson, parentJson, phaseJson, proposalJson, reviewJson, ticketJson, waitingPhase } from "../helpers/cards.js";

function cardsOf(json: BoardJson): Map<string, Card> {
  return new Map(buildBoard(json).columns.flatMap((c) => c.cards).map((card) => [card.id, card]));
}

function pending(json: BoardJson, ...ids: string[]): BoardJson {
  return { ...json, pending_approval: ids };
}

const label = (card: Card | undefined): string => card?.status.label ?? "(カードなし)";

// --- フェーズ行（2-3 の表の全行） ---

test("CB-T600 フェーズ行の状態の一言は、JSON の欄から表の順に最初に当たったものを採る（全 10 行）", () => {
  const p = (n: number, over: Partial<PhaseJson>): PhaseJson => phaseJson(n, over);
  const all = [
    p(1, { gate_closed: true, review_waiting: true, state: "ended" }), // 1
    p(2, { gate_closed: true, state: "ended" }), // 2
    p(3, { marks: { reviewed: {} }, state: "ended" }), // 3
    p(4, { marks: { skipped: { deferred_to: 5 } }, state: "ended" }), // 4: 引き受け先 5 がレビュー済み
    p(5, { marks: { reviewed: { at: "t" } }, state: "ended" }),
    p(6, { marks: { skipped: { deferred_to: 7 } }, state: "ended" }), // 5: 引き受け先 7 はまだ
    p(7, { state: "active" }),
    p(8, { marks: { skipped: {} }, state: "ended" }), // 6: 延期でない省略
    p(9, { state: "ended" }), // 7
    p(10, { state: "active" }), // 8
    p(11, { state: "planned" }), // 9
    p(12, { state: "weird" as never }), // 10
  ];
  const of = (n: number): string => phaseStatusOf(all[n - 1], all);
  assert.deepEqual(
    all.map((_, i) => of(i + 1)),
    ["レビュー待ち", "レビュー準備中", "レビュー済み", "レビュー済み", "レビュー済み", "レビュー予定", "進行中", "レビュー省略", "終了", "進行中", "これから", "要確認"],
  );
});

test("CB-T601 フェーズ行の優先順位: 止まっているのが先（reviewed が残っていても止まっていればレビュー待ち）。gate_closed でない review_waiting は採らない", () => {
  const stillWaiting = phaseJson(1, { gate_closed: true, review_waiting: true, marks: { reviewed: {}, skipped: {} }, state: "ended" });
  assert.equal(phaseStatusOf(stillWaiting, [stillWaiting]), "レビュー待ち");
  // review_waiting だけが真で gate_closed が偽: 1・2 のどちらにも当たらず、マーカーと状態で決まる（判定し直さない）
  const odd = phaseJson(1, { gate_closed: false, review_waiting: true, state: "ended" });
  assert.equal(phaseStatusOf(odd, [odd]), "終了");
  // reviewed は延期より先
  const both = phaseJson(1, { marks: { reviewed: {}, skipped: { deferred_to: 2 } }, state: "ended" });
  assert.equal(phaseStatusOf(both, [both, phaseJson(2)]), "レビュー済み");
});

test("CB-T602 延期の引き受け先: 番号のフェーズが無い・数でない・reviewed が無いときは「レビュー予定」。deferred_to が無い（marks の中身が無い）ときは「レビュー省略」", () => {
  const phases = (skipped: Record<string, unknown>, target: PhaseJson[]): string => {
    const own = phaseJson(1, { marks: { skipped }, state: "ended" });
    return phaseStatusOf(own, [own, ...target]);
  };
  assert.equal(phases({ deferred_to: 2 }, [phaseJson(2, { marks: { reviewed: {} }, state: "ended" })]), "レビュー済み");
  assert.equal(phases({ deferred_to: 2 }, [phaseJson(2, { state: "active" })]), "レビュー予定");
  assert.equal(phases({ deferred_to: 9 }, [phaseJson(2, { marks: { reviewed: {} } })]), "レビュー予定", "引き受け先が親のフェーズに無い");
  assert.equal(phases({ deferred_to: "2" }, [phaseJson(2, { marks: { reviewed: {} } })]), "レビュー予定", "番号でなければ引き受け先を引かない");
  assert.equal(phases({ deferred_to: null }, []), "レビュー省略", "null は延期ではない");
  assert.equal(phases({}, []), "レビュー省略", "marks の中身が無い");
});

// --- 子の札（2-1 の表の全行） ---

test("CB-T603 子の札: 要確認（書き込み停止・複数の場所）が最優先。理由は札のツールチップに出る。承認ボタンは pendingApproval だけを条件に出す", () => {
  const where = [{ tree: "", state: "todo", path: "/a" }, { tree: "i0001", state: "doing", path: "/b" }];
  const json = pending(
    boardOf(
      [
        ticketJson("i0001", { blocked: "" }),
        proposalJson("c1", { parent: "i0001", phase: 1, blocked: "親が引けない" }),
        ticketJson("c2", { parent: "i0001", phase: 1, scattered: where, seen_in: where }),
        ticketJson("c3", { parent: "i0001", phase: 1, blocked: "停止", scattered: where }),
      ],
      [parentJson("i0001", [phaseJson(1)])],
    ),
    "c1",
  );
  const cards = cardsOf(json);
  // blocked + pendingApproval: 札は要確認、承認ボタンは出る
  assert.equal(label(cards.get("c1")), "要確認");
  assert.match(cards.get("c1")!.status.title, /書き込み停止中: 親が引けない/);
  assert.deepEqual(cards.get("c1")!.actions, [{ kind: "approve" }]);
  assert.equal(label(cards.get("c2")), "要確認");
  assert.match(cards.get("c2")!.status.title, /複数の場所にあります（2 か所）: main:todo, i0001:doing/);
  // 両方あれば理由を並べる
  assert.match(cards.get("c3")!.status.title, /書き込み停止中: 停止\n複数の場所にあります/);
});

test("CB-T604 子の札: 承認待ち → 取り消し → 完了（done・取り消しでないアーカイブ）の順", () => {
  const archived = (ticket: string, over: Partial<ArchivedTicketJson> = {}): ArchivedTicketJson => ({
    ticket, parent: "i0001", phase: 1, title: ticket, project: "", path: `/ws/logs/archive/x/done/${ticket}.md`,
    started_at: "", completed_at: "t", cancelled_at: "", cancel_reason: "", ...over,
  });
  const json = pending(
    boardOf(
      [
        ticketJson("i0001"),
        proposalJson("pend", { parent: "i0001", phase: 1 }),
        closedJson("done", { parent: "i0001", phase: 1 }),
        closedJson("cancelled", { parent: "i0001", phase: 1, cancelled_at: "2026-10-03T00:00:00+0900", cancel_reason: "要件が変わった" }),
        // 取り消し + フェーズがレビュー待ち: 札は取り消し。レビュー完了の操作は出ない
        closedJson("cancelled-waiting", { parent: "i0001", phase: 2, cancelled_at: "t" }),
      ],
      [parentJson("i0001", [phaseJson(1), waitingPhase(2)])],
      { archived: [archived("arch"), archived("arch-cancelled", { cancelled_at: "2026-10-03T00:00:00+0900" })] },
    ),
    "pend",
  );
  const cards = cardsOf(json);
  assert.equal(label(cards.get("pend")), "承認待ち");
  assert.equal(label(cards.get("done")), "完了");
  assert.equal(label(cards.get("cancelled")), "取り消し");
  assert.equal(label(cards.get("cancelled-waiting")), "取り消し");
  assert.deepEqual(cards.get("cancelled-waiting")!.actions, []);
  // アーカイブ: 取り消しでなければ完了、取り消しならアーカイブでも取り消し
  assert.equal(cards.get("arch")!.column, "archived");
  assert.equal(label(cards.get("arch")), "完了");
  assert.equal(cards.get("arch-cancelled")!.column, "archived");
  assert.equal(label(cards.get("arch-cancelled")), "取り消し");
  // アーカイブのカードは操作を持たない
  assert.deepEqual(cards.get("arch")!.actions, []);
});

test("CB-T605 子の札: copyStatus が review のときは自分のフェーズの状態を使う（全分岐）。合わなければ要確認でレビューの操作を出さない", () => {
  const phases = [
    waitingPhase(1), // レビュー待ち
    phaseJson(2, { state: "ended", gate_closed: true, review_required: true, marks: { pending: {} } }), // レビュー準備中
    phaseJson(3, { state: "ended", marks: { skipped: { deferred_to: 5 } } }), // レビュー予定
    phaseJson(4, { state: "ended" }), // 終了 → 合わない
    phaseJson(5, { state: "active" }),
    phaseJson(6, { state: "ended", marks: { reviewed: {} } }), // レビュー済み → 合わない
  ];
  const json = boardOf(
    [ticketJson("i0001"), ...[1, 2, 3, 4, 6].map((n) => reviewJson(`c${n}`, { parent: "i0001", phase: n }))],
    [parentJson("i0001", phases)],
  );
  const cards = cardsOf(json);
  assert.equal(label(cards.get("c1")), "レビュー待ち");
  assert.equal(label(cards.get("c2")), "レビュー準備中");
  assert.equal(label(cards.get("c3")), "レビュー予定");
  for (const id of ["c4", "c6"]) {
    assert.equal(label(cards.get(id)), "要確認", id);
    assert.equal(cards.get(id)!.status.title, "レビュー待ちの置き場にあるが、フェーズの状態と合いません", id);
    assert.deepEqual(cards.get(id)!.actions, [], `${id}: 要確認の子にはレビューの操作を出さない`);
  }
  // 操作は札が「レビュー待ち」の子だけ。MR の URL は断片を除いたマージリクエスト自体
  assert.deepEqual(cards.get("c1")!.actions, [{ kind: "review", parent: "i0001", phase: 1, mrUrl: "https://example.com/o/r/pull/12" }]);
  assert.deepEqual(cards.get("c2")!.actions, []);
  assert.deepEqual(cards.get("c3")!.actions, []);
});

test("CB-T606 子の札: copyStatus が open のとき、フェーズがレビューで止まっていれば要確認、そうでなければ開始前／作業中", () => {
  const json = boardOf(
    [
      ticketJson("i0001"),
      ticketJson("early", { parent: "i0001", phase: 1, started_at: "", in_progress: false }),
      ticketJson("working", { parent: "i0001", phase: 1 }),
      ticketJson("held", { parent: "i0001", phase: 2 }),
      ticketJson("waiting", { parent: "i0001", phase: 3 }),
    ],
    [parentJson("i0001", [
      phaseJson(1),
      phaseJson(2, { state: "ended", gate_closed: true, review_required: true }),
      waitingPhase(3),
    ])],
  );
  const cards = cardsOf(json);
  assert.equal(label(cards.get("early")), "開始前");
  assert.equal(label(cards.get("working")), "作業中");
  for (const id of ["held", "waiting"]) {
    assert.equal(label(cards.get(id)), "要確認", id);
    assert.equal(cards.get(id)!.status.title, "作業中なのに、フェーズがレビューで止まっています", id);
    // open + レビュー待ちの子にレビューの操作は出さない
    assert.deepEqual(cards.get(id)!.actions, [], id);
  }
});

test("CB-T607 子の札: どの規則にも当たらなければ要確認で、読めた欄をツールチップに出す（承認待ちでない提案、フェーズが引けない子）", () => {
  const json = boardOf(
    [
      ticketJson("i0001"),
      proposalJson("stray", { parent: "i0001", phase: 1 }),
      reviewJson("orphan", { parent: "i0001", phase: 9 }),
    ],
    [parentJson("i0001", [phaseJson(1)])],
  );
  const cards = cardsOf(json);
  assert.equal(label(cards.get("stray")), "要確認");
  assert.match(cards.get("stray")!.status.title, /置き場: none/);
  assert.match(cards.get("stray")!.status.title, /承認待ち: いいえ/);
  assert.match(cards.get("stray")!.status.title, /フェーズ: レビュー待ち|フェーズ: 進行中/);
  // フェーズが引けない（親にその番号が無い）review の子: フェーズの状態が無いので合わない
  assert.equal(label(cards.get("orphan")), "要確認");
  assert.equal(cards.get("orphan")!.phaseStatus, "");
});

// --- 親の札 ---

test("CB-T608 親の札: 要確認 → 承認待ち（改版は「承認待ち（改版）」）→ 取り消し → 完了（早めに終了）→ フェーズのレビュー → 開始前／作業中 → 要確認", () => {
  const json = pending(
    boardOf(
      [
        ticketJson("blocked", { blocked: "停止" }),
        proposalJson("fresh"),
        // 改版: 承認済み（doing/ にある）のまま、改版の提案が todo/ にある
        ticketJson("revised", { proposal: { state: "todo", tree: "r", tree_root: "/r", path: "/r/p.md", has_plan: true } }),
        closedJson("cancelled", { cancelled_at: "2026-10-03T00:00:00+0900" }),
        closedJson("finished"),
        closedJson("early"),
        ticketJson("waiting"),
        ticketJson("preparing"),
        ticketJson("fresh-open", { started_at: "", in_progress: false }),
        ticketJson("busy"),
        proposalJson("odd"),
      ],
      [
        parentJson("waiting", [waitingPhase(1), phaseJson(2, { state: "ended", gate_closed: true, review_required: true })]),
        parentJson("preparing", [phaseJson(1, { state: "ended", gate_closed: true, review_required: true }), phaseJson(2)]),
        parentJson("early", [phaseJson(1, { state: "ended" })], { close_early: { reason: "ここまで" } }),
        parentJson("busy", [phaseJson(1)]),
      ],
    ),
    "fresh",
    "revised",
  );
  const cards = cardsOf(json);
  const expected: Record<string, string> = {
    blocked: "要確認",
    fresh: "承認待ち",
    revised: "承認待ち（改版）",
    cancelled: "取り消し",
    finished: "完了",
    early: "完了（早めに終了）",
    waiting: "レビュー待ち",
    preparing: "レビュー準備中",
    "fresh-open": "開始前",
    busy: "作業中",
    odd: "要確認",
  };
  for (const [id, want] of Object.entries(expected)) {
    assert.equal(label(cards.get(id)), want, id);
  }
  // 親のレビュー状態はフェーズ一覧から見る。親カードの gateClosed は使わない（親は常に false）
  assert.equal(cards.get("waiting")!.gateClosed, false);
  assert.match(cards.get("odd")!.status.title, /置き場: none/);
});

test("CB-T609 親の札: アーカイブの親は完了（取り消しなら取り消し）。レビュー待ちのフェーズがあっても閉じていれば完了", () => {
  const archived = (ticket: string, over: Partial<ArchivedTicketJson> = {}): ArchivedTicketJson => ({
    ticket, parent: "", phase: null, title: ticket, project: "", path: `/ws/logs/archive/x/done/${ticket}.md`,
    started_at: "", completed_at: "t", cancelled_at: "", cancel_reason: "", ...over,
  });
  const json = boardOf([], [], { archived: [archived("a"), archived("b", { cancelled_at: "t" })] });
  const cards = cardsOf(json);
  assert.equal(label(cards.get("a")), "完了");
  assert.equal(label(cards.get("b")), "取り消し");
  // 閉じた親（done/）の子がレビュー待ちの行を持っていても、札は完了
  const done = cardsOf(boardOf([closedJson("p")], [parentJson("p", [waitingPhase(1)])]));
  assert.equal(label(done.get("p")), "完了");
});

// --- 操作（ユーザの番） ---

test("CB-T610 操作: 子の承認待ち + レビュー待ちは承認だけ。親は承認待ちなら承認、レビュー完了は各フェーズ行に置く。改版の承認待ちとレビュー待ちが同時でもそれぞれ", () => {
  const json = pending(
    boardOf(
      [
        ticketJson("p", { proposal: { state: "todo", tree: "p", tree_root: "/p", path: "/p/x.md", has_plan: true } }),
        proposalJson("c", { parent: "p", phase: 1 }),
        reviewJson("r", { parent: "p", phase: 2 }),
      ],
      [parentJson("p", [waitingPhase(1, 11), waitingPhase(2, 22, "https://example.com/o/r/pull/22#x")])],
    ),
    "p",
    "c",
  );
  const cards = cardsOf(json);
  // 子: 承認待ち + フェーズがレビュー待ち → 承認だけ
  assert.deepEqual(cards.get("c")!.actions, [{ kind: "approve" }]);
  // 子: 札がレビュー待ち → レビュー完了（承認は出ない）
  assert.equal(label(cards.get("r")), "レビュー待ち");
  assert.deepEqual(cards.get("r")!.actions.map((a) => a.kind), ["review"]);
  // 親: 改版の承認待ちが札。承認はカード、レビュー完了は同時に待っている 2 フェーズそれぞれの行
  const p = cards.get("p")!;
  assert.equal(p.status.label, "承認待ち（改版）");
  assert.deepEqual(p.actions, [{ kind: "approve" }]);
  assert.deepEqual(p.phases.map((c) => c.actions.map((a) => a.kind)), [["review"], ["review"]]);
  assert.deepEqual(p.phases.map((c) => (c.actions[0] as { phase: number }).phase), [1, 2]);
  assert.deepEqual(p.phases.map((c) => (c.actions[0] as { mrUrl: string }).mrUrl), ["https://example.com/o/r/pull/11", "https://example.com/o/r/pull/22"]);
});
