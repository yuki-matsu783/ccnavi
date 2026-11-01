/**
 * ボードのカードの「計画編集」ボタン（計画編集タブの入口）。出すのは、計画を持つ承認待ち（`todo/`）の
 * 親の提案だけ。計画を持つかは実行ファイルが提案の `has_plan` で言い、画面は計画を読まない。押すと拡張ホストへ
 * 親の識別子を返すだけ（拡張ホストはいまのボードで確かめてからタブを開く）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard, type Card } from "../../src/core/board.js";
import type { BoardJson, TicketJson } from "../../src/core/model.js";
import { openBoard } from "../helpers/board.js";
import { fixture } from "../helpers/fixture.js";

/**
 * 見本に親の提案を足す。i0002 は計画を持つ新規の親、i0003 は計画を持たない新規の親、i0001 は承認済みの親の改版
 * （計画を持つ）を `todo/` に置いた形。子 i0001-02-03 は承認待ちの子
 */
function withParents(revision = true): BoardJson {
  const board = fixture();
  const base = board.tickets.find((t) => t.ticket === "i0001-02-03") as TicketJson;
  const proposal = (ticket: string, hasPlan: boolean) => ({ state: "todo" as const, tree: "", tree_root: "/x", path: `/x/wip/proposals/todo/${ticket}.md`, has_plan: hasPlan });
  const parent = (ticket: string, hasPlan: boolean): TicketJson => ({
    ...base,
    ticket,
    parent: "",
    phase: null,
    title: `親 ${ticket}`,
    flow: null,
    proposal: proposal(ticket, hasPlan),
    copy: { status: "none" },
  });
  return {
    ...board,
    pending_approval: [...board.pending_approval, "i0002", "i0003", ...(revision ? ["i0001"] : [])],
    tickets: [
      ...board.tickets.map((t) => (revision && t.ticket === "i0001" ? { ...t, proposal: proposal("i0001", true) } : t)),
      parent("i0002", true),
      parent("i0003", false),
    ],
  };
}

function cardOf(json: BoardJson, id: string): Card {
  for (const column of buildBoard(json).columns) {
    const found = column.cards.find((card) => card.id === id);
    if (found !== undefined) {
      return found;
    }
  }
  throw new Error(`カードがない: ${id}`);
}

test("CB-D185 カードの workflow は、計画を持つ承認待ちの親の提案（新規も改版も）だけ真。承認済みだけの親・計画のない親・子・has_plan が欠けた壊れた JSON は偽", () => {
  const json = withParents();
  assert.equal(cardOf(json, "i0002").workflow, true);
  assert.equal(cardOf(json, "i0001").workflow, true, "改版の提案");
  assert.equal(cardOf(json, "i0003").workflow, false);
  assert.equal(cardOf(json, "i0001-02-03").workflow, false, "子");
  assert.equal(cardOf(withParents(false), "i0001").workflow, false, "承認済みで提案のない親");
  // レビュー待ち（review/）の提案は承認待ちではない
  const review = withParents();
  const reviewed = { ...review, tickets: review.tickets.map((t) => (t.ticket === "i0002" && t.proposal !== null ? { ...t, proposal: { ...t.proposal, state: "review" as const } } : t)) };
  assert.equal(cardOf(reviewed, "i0002").workflow, false);
  // 欄が欠けた壊れた JSON への備え（型は必須）。計画があると決めつけて出さない
  const old = withParents();
  const stripped = {
    ...old,
    tickets: old.tickets.map((t) => (t.proposal === null ? t : { ...t, proposal: { state: t.proposal.state, tree: t.proposal.tree, tree_root: t.proposal.tree_root, path: t.proposal.path } as never })),
  };
  assert.equal(cardOf(stripped, "i0002").workflow, false);
});

test("CB-D184 計画を持つ承認待ちの親の提案のカードに「計画編集」を出し、押すと親の識別子だけを返す（カードのファイルは開かない）", async () => {
  const dom = await openBoard(withParents());
  try {
    assert.deepEqual(
      dom.all('[data-action="workflow"]').map((b) => [b.getAttribute("data-ticket"), b.textContent]).sort(),
      [
        ["i0001", "計画編集"],
        ["i0002", "計画編集"],
      ],
    );
    assert.equal(dom.all('.card.child [data-action="workflow"]').length, 0);
    dom.click(dom.one('[data-action="workflow"][data-ticket="i0002"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "workflow"), [{ type: "workflow", ticket: "i0002" }]);
    assert.equal(dom.posted.filter((m) => m.type === "open").length, 0);
  } finally {
    await dom.close();
  }
  const plain = await openBoard();
  try {
    assert.equal(plain.all('[data-action="workflow"]').length, 0, "見本（承認待ちの親がありません）には出ない");
  } finally {
    await plain.close();
  }
});
