/** ボードのスクリプトを happy-dom で動かす。列の畳み・絞り込み・承認の送り先。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { renderBoard } from "../../src/core/render.js";
import { fixture } from "../helpers/fixture.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

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
    page.change(page.one("#parent-filter"), "i0001");
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

test("CB-D42 「要対応だけ」で人が動く必要の無いカードが隠れ、列の件数が減り、state に残る。承認は見えている承認待ちだけ", async () => {
  const page = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS));
  try {
    const box = page.one<HTMLInputElement>("#attention-filter");
    assert.equal(box.checked, false);
    box.checked = true;
    page.change(box);
    assert.ok(page.document.body.classList.contains("filtering"));
    assert.ok(page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-01"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-02"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001-03"]').classList.contains("hidden"));
    assert.equal(page.one('.column[data-state="todo"] > h2 > .count').textContent, "1");
    assert.equal(page.one('.column[data-state="done"] > h2 > .count').textContent, "0");
    assert.equal((page.state() as { attention: boolean }).attention, true);
    page.click(page.one('.controls button[data-action="approve"]'));
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    box.checked = false;
    page.change(box);
    assert.ok(!page.document.body.classList.contains("filtering"));
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.equal((page.state() as { attention: boolean }).attention, false);
  } finally {
    await page.close();
  }
  // 読み直しても絞り込みは残る
  const again = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS), { attention: true });
  try {
    assert.equal(again.one<HTMLInputElement>("#attention-filter").checked, true);
    assert.ok(again.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.ok(!again.one('.card[data-id="i0001-03"]').classList.contains("hidden"));
  } finally {
    await again.close();
  }
});

test("CB-D43 「レビュー済み連絡」は親とフェーズを送り、提案は開かない。MR のリンクの上でも提案は開かない", async () => {
  const base = fixture();
  const parent = {
    ...base.parents[0],
    phases: base.parents[0].phases.map((p) =>
      p.number === 2
        ? {
            ...p,
            state: "ended" as const,
            gate_closed: true,
            review_required: true,
            review_waiting: true,
            marks: { requested: { mr: 18, url: "https://example.com/o/r/pull/18#issuecomment-5", at: "t" } },
          }
        : p,
    ),
  };
  const page = await loadPage(renderBoard(buildBoard({ ...base, parents: [parent] }), OPTIONS));
  try {
    page.click(page.one('button[data-action="reviewed"][data-parent="i0001"][data-phase="2"]'));
    assert.deepEqual(page.posted.at(-1), { type: "reviewed", parent: "i0001", phase: 2 });
    const before = page.posted.length;
    // 本物の Webview では VS Code がリンクの遷移を横取りして既定のブラウザで開く。happy-dom には無いので既定の動きだけ止める
    page.document.addEventListener("click", (event) => {
      if ((event.target as unknown as { closest: (s: string) => unknown }).closest("a")) { event.preventDefault(); }
    });
    page.click(page.one('.card[data-id="i0001"] a.mr-link'));
    assert.equal(page.posted.length, before, "リンクを押しても open を送らない");
    // 要対応の絞り込みで、レビュー待ちの親とゲート閉の子は残る
    const box = page.one<HTMLInputElement>("#attention-filter");
    box.checked = true;
    page.change(box);
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001-02"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-01"]').classList.contains("hidden"));
  } finally {
    await page.close();
  }
});
