/** ボードのスクリプトを happy-dom で動かす。列の畳み・絞り込み・承認の送り先。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { renderBoard, renderErrorPage } from "../../src/core/render.js";
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
    // 要対応の絞り込みで、レビュー待ちの親と子は残る
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

test("CB-D44 「更新」を押すと非活性になり、回り記号と「更新中」に替わる。読み直した画面では活性に戻っている", async () => {
  const page = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS));
  try {
    const button = page.one<HTMLButtonElement>('.controls button[data-action="refresh"]');
    assert.equal(button.disabled, false);
    assert.equal(button.querySelector(".label")?.textContent, "更新");
    page.click(button);
    assert.deepEqual(page.posted.at(-1), { type: "refresh" });
    assert.equal(button.disabled, true, "押した瞬間に非活性になる");
    assert.ok(button.classList.contains("busy"), "回り記号が出る");
    assert.equal(button.getAttribute("aria-busy"), "true");
    assert.equal(button.querySelector(".label")?.textContent, "更新中");
    // 非活性の間はもう 1 度押しても送らない
    const sent = page.posted.length;
    page.click(button);
    assert.equal(page.posted.length, sent);
  } finally {
    await page.close();
  }
  // 拡張が読み直しを終えて HTML を作り直した後（同じ内容でも新しい画面）
  const again = await loadPage(renderBoard(buildBoard(fixture()), OPTIONS));
  try {
    const button = again.one<HTMLButtonElement>('.controls button[data-action="refresh"]');
    assert.equal(button.disabled, false);
    assert.ok(!button.classList.contains("busy"));
    assert.equal(button.querySelector(".label")?.textContent, "更新");
  } finally {
    await again.close();
  }
});

test("CB-D45 読み直せなかった画面でも、承認のオーバーレイのボタンが効く。覚えていた絞り込みは上書きしない", async () => {
  const saved = { project: "alpha", parent: "i0001", attention: true, folded: ["done"], widths: {} };
  const page = await loadPage(
    renderErrorPage("読めない", { ...OPTIONS, approval: { kind: "done", count: 1, prompt: "文" } }),
    saved,
  );
  try {
    page.click(page.one('button[data-action="prompt-copy"]'));
    assert.deepEqual(page.posted.at(-1), { type: "promptCopy" });
    page.click(page.one('button[data-action="approve-cancel"]'));
    assert.deepEqual(page.posted.at(-1), { type: "approveCancel" });
    // 絞り込みの部品が無い画面なので、覚えていた値に触らない
    assert.deepEqual(page.state(), saved);
  } finally {
    await page.close();
  }
});
