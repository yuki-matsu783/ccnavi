/**
 * ボードのカードの「フロー」ボタン（フロー編集画面の入口）。言葉は実行ファイルの答え（`flow`）をそのまま使い、
 * 押すと拡張ホストへ識別子を返すだけ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { BoardJson } from "../../src/core/model.js";
import { openBoard } from "../helpers/board.js";
import { fixture } from "../helpers/fixture.js";

function withFlow(ticket: string, change: { exists?: boolean; locked?: boolean; lock_kind?: "doing" | "review" | null }): BoardJson {
  const board = fixture();
  return {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === ticket && t.flow !== null ? { ...t, flow: { ...t.flow, ...change } } : t)),
  };
}

test("CB-D115 レビュー中の子のカードの「フロー」ボタンは、着手中と別の言葉で「閲覧（レビュー中）」", async () => {
  const dom = await openBoard(withFlow("i0001-02-04", { locked: true, lock_kind: "review" }));
  try {
    const button = dom.one('[data-action="flow"][data-ticket="i0001-02-04"]');
    assert.equal(button.textContent, "フロー: 閲覧（レビュー中）");
    assert.equal(button.getAttribute("data-flow"), "locked");
  } finally {
    await dom.close();
  }
});

test("CB-D113 子のカードに「フロー」ボタン。言葉は 作成 / 編集 / 閲覧（着手中・終了済み）で、親のカードにはありません", async () => {
  const dom = await openBoard(withFlow("i0001-02-03", { exists: true }));
  try {
    const label = (ticket: string): string => dom.one(`[data-action="flow"][data-ticket="${ticket}"]`).textContent ?? "";
    assert.equal(label("i0001-02-04"), "フロー: 作成");
    // 完了の子でもファイルが在れば中身を見られる。終わった手順を「編集」とは言わない
    assert.equal(label("i0001-01-01"), "フロー: 閲覧（終了済み）");
    assert.equal(label("i0001-02-02"), "フロー: 閲覧（着手中）");
    assert.equal(label("i0001-02-03"), "フロー: 編集");
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-02-02"]').getAttribute("data-flow"), "locked");
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
    dom.click(dom.one('[data-action="flow"][data-ticket="i0001-02-02"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "flow"), [{ type: "flow", ticket: "i0001-02-02" }]);
    assert.equal(dom.posted.filter((m) => m.type === "open").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D116 完了・取り消しの子でフローのファイルがなければ、カードに「フロー」ボタンを出さない（作成させない）", async () => {
  // 実行ファイルは、閉じた子でファイルが無ければ flow を null で返す
  const board = fixture();
  const dom = await openBoard({ ...board, tickets: board.tickets.map((t) => (t.ticket === "i0001-01-01" ? { ...t, flow: null } : t)) });
  try {
    assert.equal(dom.all('.card[data-id="i0001-02-05"]').length, 1, "取り消しのカードは出ている");
    assert.equal(dom.all('.card[data-id="i0001-01-01"]').length, 1, "完了のカードは出ている");
    assert.equal(dom.all('[data-action="flow"][data-ticket="i0001-02-05"]').length, 0);
    assert.equal(dom.all('[data-action="flow"][data-ticket="i0001-01-01"]').length, 0);
    assert.equal(dom.all('[data-flow="create"][data-ticket="i0001-01-01"], [data-flow="create"][data-ticket="i0001-02-05"]').length, 0);
    // 開いている子は、ファイルが無くても「作成」が出る
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-02-04"]').textContent, "フロー: 作成");
    assert.equal(dom.one('[data-action="flow"][data-ticket="i0001-02-03"]').textContent, "フロー: 作成");
  } finally {
    await dom.close();
  }
});
