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
    assert.equal(label("i0001-04"), "フロー: 作成");
    // 完了の子でもファイルが在れば「編集」（中身を見られる）
    assert.equal(label("i0001-01"), "フロー: 編集");
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

test("CB-D116 完了・取り消しの子でフローのファイルが無ければ、カードに「フロー」ボタンを出さない（作成させない）", async () => {
  // 取り消しの i0001-05 は実行ファイルが flow を null で返す。完了の i0001-01 は古い実行ファイルの答え（exists: false）
  const dom = await openBoard(withFlow("i0001-01", { exists: false }));
  try {
    assert.equal(dom.all('.card[data-id="i0001-05"]').length, 1, "取り消しのカードは出ている");
    assert.equal(dom.all('.card[data-id="i0001-01"]').length, 1, "完了のカードは出ている");
    assert.equal(dom.all('[data-action="flow"][data-ticket="i0001-05"]').length, 0);
    assert.equal(dom.all('[data-action="flow"][data-ticket="i0001-01"]').length, 0);
    assert.equal(dom.all('[data-flow="create"][data-ticket="i0001-01"], [data-flow="create"][data-ticket="i0001-05"]').length, 0);
    // 開いている子は、ファイルが無くても「作成」が出る
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-04"]').textContent, "フロー: 作成");
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-03"]').textContent, "フロー: 作成");
  } finally {
    await dom.close();
  }
});
