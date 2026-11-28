/**
 * ボードのカードを選んだ右のパネルの「フロー」ボタン（フロー編集画面の入口）。言葉は実行ファイルの答え（`flow`）をそのまま使い、
 * 押すと拡張ホストへ識別子を返すだけ。カードそのものには出さない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { BoardJson } from "../../src/core/model.js";
import { openBoard } from "../helpers/board.js";
import type { DomPage } from "../helpers/dom.js";
import { fixture } from "../helpers/fixture.js";

function withFlow(ticket: string, change: { exists?: boolean; locked?: boolean; lock_kind?: "doing" | "review" | null }): BoardJson {
  const board = fixture();
  return {
    ...board,
    tickets: board.tickets.map((t) => (t.ticket === ticket && t.flow !== null ? { ...t, flow: { ...t.flow, ...change } } : t)),
  };
}

/** カードを選び、パネルの「フロー」ボタンを返す。無ければnull */
async function flowButton(dom: DomPage, ticket: string): Promise<ReturnType<DomPage["one"]> | null> {
  dom.click(dom.one(`li.card[data-id="${ticket}"] .title`));
  await dom.settle();
  assert.equal(dom.one("aside.card-panel").getAttribute("data-panel"), ticket);
  return dom.all(`aside.card-panel [data-action="flow"][data-ticket="${ticket}"]`)[0] ?? null;
}

test("CB-D115 レビュー中の子の「フロー」ボタンは、着手中と別の言葉で「閲覧のみ（レビュー中）」", async () => {
  const dom = await openBoard(withFlow("i0001-02-04", { locked: true, lock_kind: "review" }));
  try {
    const button = await flowButton(dom, "i0001-02-04");
    assert.equal(button?.textContent, "フロー: 閲覧のみ（レビュー中）");
    assert.equal(button?.getAttribute("data-flow"), "locked");
  } finally {
    await dom.close();
  }
});

test("CB-D113 子を選んだパネルに「フロー」ボタン。言葉は 作成 / 編集 / 閲覧のみ（着手中・終了済み）で、親とカードそのものにはありません", async () => {
  const dom = await openBoard(withFlow("i0001-02-03", { exists: true }));
  try {
    const label = async (ticket: string): Promise<string> => (await flowButton(dom, ticket))?.textContent ?? "";
    assert.equal(await label("i0001-02-04"), "フロー: 作成");
    // 完了の子でもファイルが在れば中身を見られる。終わった手順を「編集」とは言わない
    assert.equal(await label("i0001-01-01"), "フロー: 閲覧のみ（終了済み）");
    assert.equal(await label("i0001-02-02"), "フロー: 閲覧のみ（着手中）");
    assert.equal((await flowButton(dom, "i0001-02-02"))?.getAttribute("data-flow"), "locked");
    assert.equal(await label("i0001-02-03"), "フロー: 編集");
    // 親には出ない
    assert.equal(await flowButton(dom, "i0001"), null);
    // カードそのものには出さない
    assert.equal(dom.all('li.card [data-action="flow"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D114 「フロー」を押すと識別子だけを拡張ホストへ返し、チケットのファイルは開かない", async () => {
  const dom = await openBoard();
  try {
    const button = await flowButton(dom, "i0001-02-02");
    assert.ok(button !== null);
    dom.click(button);
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "flow"), [{ type: "flow", ticket: "i0001-02-02" }]);
    assert.equal(dom.posted.filter((m) => m.type === "open").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D116 完了・取り消しの子でフローのファイルがなければ、「フロー」ボタンを出さない（作成させない）", async () => {
  // 実行ファイルは、クローズした子でファイルが無ければflowをnullで返す
  const board = fixture();
  const dom = await openBoard({ ...board, tickets: board.tickets.map((t) => (t.ticket === "i0001-01-01" ? { ...t, flow: null } : t)) });
  try {
    assert.equal(dom.all('.card[data-id="i0001-02-05"]').length, 1, "取り消しのカードは出ている");
    assert.equal(dom.all('.card[data-id="i0001-01-01"]').length, 1, "完了のカードは出ている");
    assert.equal(await flowButton(dom, "i0001-02-05"), null);
    assert.equal(await flowButton(dom, "i0001-01-01"), null);
    // 開いている子は、ファイルが無くても「作成」が出る
    assert.equal((await flowButton(dom, "i0001-02-04"))?.textContent, "フロー: 作成");
    assert.equal((await flowButton(dom, "i0001-02-03"))?.textContent, "フロー: 作成");
  } finally {
    await dom.close();
  }
});
