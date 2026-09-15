/** ボードのスクリプトを happy-dom で動かす。列の畳み・絞り込み・承認の送り先。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { renderBoard } from "../../src/core/render.js";
import { fixture } from "../helpers/fixture.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

const OPTIONS = { nonce: "n" };

test("CB-D40 列の見出しを押すと畳み、state に列名が入る。読み直しても畳んだまま", async () => {
  const page = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS));
  try {
    page.click(page.one('button.fold[data-fold="done"]'));
    assert.ok(page.one('.column[data-state="done"]').classList.contains("folded"));
    assert.equal(page.one('button.fold[data-fold="done"]').getAttribute("aria-expanded"), "false");
    assert.deepEqual((page.state() as { folded: string[] }).folded, ["done"]);
  } finally {
    await page.close();
  }
  const again = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS), { folded: ["done"] });
  try {
    assert.ok(again.one('.column[data-state="done"]').classList.contains("folded"));
  } finally {
    await again.close();
  }
});

test("CB-D41 親で絞り込むと他の家族のカードが隠れ、列の件数と承認ボタンは見えている数になる。承認は見えている承認待ちだけを送る", async () => {
  const base = fixture();
  // 先頭は親 i0001。同じ形でもう 1 つ親（承認待ち）を足す
  const other = { ...base.tickets[0], ticket: "i0002", title: "別の親", pending_approval: true };
  const json = { ...base, tickets: [...base.tickets, other], pending_approval: [...base.pending_approval, "i0002"] };
  const page = await loadPage(renderBoard(buildBoard(json), OPTIONS));
  try {
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').textContent, "承認待ち 2 件を承認");
    page.type(page.one("#parent-filter"), "i0001");
    assert.ok(page.document.body.classList.contains("filtering"));
    assert.ok(page.one('.card[data-id="i0002"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').textContent, "承認待ち 1 件を承認");
    assert.equal((page.state() as { parent: string }).parent, "i0001");
    page.click(page.one('.controls button[data-action="approve"]'));
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    page.click(page.one('button[data-action="approve-one"][data-ticket="i0001-03"]'));
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    // カードを押すと提案を開く。ボタンの上では開かない
    page.click(page.one('.card[data-id="i0001-01"]'));
    assert.equal(page.posted.at(-1)?.type, "open");
  } finally {
    await page.close();
  }
});
