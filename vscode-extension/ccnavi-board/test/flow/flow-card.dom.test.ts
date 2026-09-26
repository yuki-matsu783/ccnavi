/**
 * ボードのカードの「フロー」ボタン（フロー編集画面の入口）。言葉は実行ファイルの答え（`flow`）の写しで、
 * 押すと拡張ホストへ識別子を返すだけ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { BoardJson } from "../../src/core/model.js";
import { openBoard } from "../helpers/board.js";
import { fixture } from "../helpers/fixture.js";

function withFlow(ticket: string, change: { exists?: boolean; locked?: boolean }): BoardJson {
  const board = fixture();
  return {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === ticket && t.flow !== null ? { ...t, flow: { ...t.flow, ...change } } : t)),
  };
}

test("CB-D113 子のカードに「フロー」ボタン。言葉は 作成 / 編集 / 閲覧（着手中）で、親のカードには無い", async () => {
  const dom = await openBoard(withFlow("i0001-03", { exists: true }));
  try {
    const label = (ticket: string): string => dom.one(`[data-action="flow"][data-ticket="${ticket}"]`).textContent ?? "";
    assert.equal(label("i0001-01"), "フロー: 作成");
    assert.equal(label("i0001-02"), "フロー: 閲覧（着手中）");
    assert.equal(label("i0001-03"), "フロー: 編集");
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-02"]').getAttribute("data-flow"), "locked");
    // 親のカードには出ない
    assert.equal(dom.all('.card.parent [data-action="flow"]').length, 0);
    // 子の数だけ
    const children = fixture().tickets.filter((t) => t.flow !== null).length;
    assert.equal(dom.all('[data-action="flow"]').length, children);
  } finally {
    await dom.close();
  }
});

test("CB-D114 「フロー」を押すと識別子だけを拡張ホストへ返し、カードのファイルは開かない", async () => {
  const dom = await openBoard();
  try {
    dom.click(dom.one('[data-action="flow"][data-ticket="i0001-02"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "flow"), [{ type: "flow", ticket: "i0001-02" }]);
    assert.equal(dom.posted.filter((m) => m.type === "open").length, 0);
  } finally {
    await dom.close();
  }
});
