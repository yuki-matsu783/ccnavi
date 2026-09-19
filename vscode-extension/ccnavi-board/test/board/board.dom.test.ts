/**
 * ボード画面の操作。React の画面を happy-dom で動かし、列の畳み・絞り込み・承認の送り先を見る。
 * 何を描くかは render.dom.test.ts。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../../src/core/board.js";
import { fixture } from "../helpers/fixture.js";
import { approvePreview, openBoard, openPage } from "../helpers/board.js";
import type { Element, Event, HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };
import type { DomPage } from "../helpers/dom.js";

test("CB-D40 列の見出しを押すと畳み、state に列名が入る。読み直しても畳んだまま", async () => {
  const page = await openBoard();
  try {
    page.click(page.one('button.fold[data-fold="done"]'));
    await page.settle();
    assert.ok(page.one('.column[data-state="done"]').classList.contains("folded"));
    assert.equal(page.one('button.fold[data-fold="done"]').getAttribute("aria-expanded"), "false");
    assert.deepEqual((page.state() as { folded: string[] }).folded, ["done"]);
  } finally {
    await page.close();
  }
  const again = await openBoard(fixture(), { state: { folded: ["done"] } });
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
  const page = await openBoard(json);
  try {
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').textContent, "承認待ち 2 件を承認");
    page.change(page.one("#parent-filter"), "i0001");
    await page.settle();
    assert.ok(page.document.body.classList.contains("filtering"));
    assert.ok(page.one('.card[data-id="i0002"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.equal(page.one<HTMLButtonElement>('.controls button[data-action="approve"]').textContent, "承認待ち 1 件を承認");
    assert.equal((page.state() as { parent: string }).parent, "i0001");
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    page.click(page.one('button[data-action="approve-one"][data-ticket="i0001-03"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    // カードを押すと提案を開く。ボタンの上では開かない
    page.click(page.one('.card[data-id="i0001-01"]'));
    await page.settle();
    assert.equal(page.posted.at(-1)?.type, "open");
  } finally {
    await page.close();
  }
});

test("CB-D46 書き込みが止まっているカードは「要対応だけ」でも残る", async () => {
  // 印は不備として積まれ、`attention` が立つ（board.ts）。素の版では i0001-02 は隠れる
  // （CB-D42）ので、印を付けたときだけ残ることが確かめられる。
  const base = fixture();
  const stopped = base.tickets.map((t) =>
    t.ticket === "i0001-02" ? { ...t, blocked: "親 i0001 の承認済みチケットが作業中に無い（未承認か、閉じている）" } : t,
  );
  const page = await openBoard({ ...base, tickets: stopped });
  try {
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(!page.one('.card[data-id="i0001-02"]').classList.contains("hidden"));
    assert.match(page.one('.card[data-id="i0001-02"]').textContent ?? "", /書き込み停止中/);
  } finally {
    await page.close();
  }
});

test("CB-D42 「要対応だけ」で人が動く必要の無いカードが隠れ、列の件数が減り、state に残る。承認は見えている承認待ちだけ", async () => {
  const page = await openBoard();
  try {
    const box = page.one<HTMLInputElement>("#attention-filter");
    assert.equal(box.checked, false);
    page.click(box);
    await page.settle();
    assert.ok(page.document.body.classList.contains("filtering"));
    assert.ok(page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-01"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-02"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001-03"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-04"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-05"]').classList.contains("hidden"));
    assert.equal(page.one('.column[data-state="todo"] > h2 > .count').textContent, "1");
    assert.equal(page.one('.column[data-state="doing"] > h2 > .count').textContent, "0");
    assert.equal(page.one('.column[data-state="done"] > h2 > .count').textContent, "0");
    assert.equal(page.one('.column[data-state="cancelled"] > h2 > .count').textContent, "0");
    assert.equal((page.state() as { attention: boolean }).attention, true);
    page.click(page.one('.controls button[data-action="approve"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approve", tickets: ["i0001-03"], filtered: true });
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(!page.document.body.classList.contains("filtering"));
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.equal((page.state() as { attention: boolean }).attention, false);
  } finally {
    await page.close();
  }
  // 読み直しても絞り込みは残る
  const again = await openBoard(fixture(), { state: { attention: true } });
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
  const page = await openBoard({ ...base, parents: [parent] });
  try {
    page.click(page.one('button[data-action="reviewed"][data-parent="i0001"][data-phase="2"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "reviewed", parent: "i0001", phase: 2 });
    const before = page.posted.length;
    // 本物の Webview では VS Code がリンクの遷移を横取りして既定のブラウザで開く。happy-dom には無いので既定の動きだけ止める
    page.document.addEventListener("click", (event) => {
      if ((event.target as unknown as { closest: (s: string) => unknown }).closest("a")) { event.preventDefault(); }
    });
    page.click(page.one('.card[data-id="i0001"] a.mr-link'));
    await page.settle();
    assert.equal(page.posted.length, before, "リンクを押しても open を送らない");
    // 要対応の絞り込みで、レビュー待ちの親と子は残る
    page.click(page.one("#attention-filter"));
    await page.settle();
    assert.ok(!page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    assert.ok(!page.one('.card[data-id="i0001-02"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001-01"]').classList.contains("hidden"));
  } finally {
    await page.close();
  }
});

test("CB-D44 「更新」を押すと非活性になり、回り記号と「更新中」に替わる。次の中身が届くと活性に戻る", async () => {
  const page = await openBoard();
  try {
    const button = page.one<HTMLButtonElement>('.controls button[data-action="refresh"]');
    assert.equal(button.disabled, false);
    assert.equal(button.querySelector(".label")?.textContent, "更新");
    page.click(button);
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "refresh" });
    assert.equal(button.disabled, true, "押した瞬間に非活性になる");
    assert.ok(button.classList.contains("busy"), "回り記号が出る");
    assert.equal(button.getAttribute("aria-busy"), "true");
    assert.equal(button.querySelector(".label")?.textContent, "更新中");
    // 非活性の間はもう 1 度押しても送らない
    const sent = page.posted.length;
    page.click(button);
    await page.settle();
    assert.equal(page.posted.length, sent);
    // 拡張が読み直しを終えて次の中身を渡したら、活性に戻る（HTML は作り直さない）
    await page.send({ type: "data", data: { kind: "board", board: buildBoard(fixture()) } });
    assert.equal(button.disabled, false);
    assert.ok(!button.classList.contains("busy"));
    assert.equal(button.querySelector(".label")?.textContent, "更新");
  } finally {
    await page.close();
  }
});

test("CB-D45 読み直せなかった画面でも、承認のオーバーレイのボタンが効く。覚えていた絞り込みは上書きしない", async () => {
  const saved = { project: "alpha", parent: "i0001", attention: true, folded: ["done"], widths: {} };
  const page = await openPage({ kind: "error", error: "読めない", approval: { kind: "done", count: 1, prompt: "文" } }, saved);
  try {
    page.click(page.one('button[data-action="prompt-copy"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "promptCopy" });
    page.click(page.one('button[data-action="approve-cancel"]'));
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approveCancel" });
    // 絞り込みの部品が無い画面なので、覚えていた値に触らない
    assert.deepEqual(page.state(), saved);
  } finally {
    await page.close();
  }
});

test("CB-D47 裏から表に戻って作り直された画面は、いまの中身を拡張ホストに頼む", async () => {
  const page = await openBoard();
  try {
    assert.deepEqual(page.posted[0], { type: "ready" });
  } finally {
    await page.close();
  }
});

test("CB-D48 プロジェクトの絞り込みは拡張ホストからの指定でも効き、覚える。候補に無ければ今の絞りを外さない", async () => {
  const base = fixture();
  // lib のカードを 1 枚足す。絞ったときに他が隠れることを見る
  const mine = { ...base.tickets[0], ticket: "i0002", title: "lib の親", project: "lib" };
  const json = { ...base, projects: ["lib", "app"], tickets: [...base.tickets, mine] };
  const page = await openBoard(json, { filter: "lib" });
  try {
    assert.equal(page.one<HTMLInputElement>("#project-filter").value, "lib");
    assert.ok(page.document.body.classList.contains("filtering"));
    // 指定されたプロジェクトのカードだけが残る
    assert.ok(!page.one('.card[data-id="i0002"]').classList.contains("hidden"));
    assert.ok(page.one('.card[data-id="i0001"]').classList.contains("hidden"));
    // 人が触らなくても覚える。裏に回って作り直されたときに絞りが戻ってしまわないように
    assert.equal((page.state() as { project: string }).project, "lib");
    await page.send({ type: "filter", project: "app" });
    assert.equal(page.one<HTMLInputElement>("#project-filter").value, "app");
    assert.equal((page.state() as { project: string }).project, "app");
    // 候補に無い名前では何も動かさない（いまの絞りを外さない）
    await page.send({ type: "filter", project: "無い名前" });
    assert.equal(page.one<HTMLInputElement>("#project-filter").value, "app");
    assert.equal((page.state() as { project: string }).project, "app");
    // ワークスペース本体（空）も候補。覚え直しても「すべて」に落ちない
    page.change(page.one("#project-filter"), "");
    await page.settle();
    assert.equal(page.one<HTMLInputElement>("#project-filter").value, "");
    assert.equal((page.state() as { project: string }).project, "");
    assert.ok(page.document.body.classList.contains("filtering"));
    page.change(page.one("#project-filter"), "*");
    await page.settle();
    assert.ok(!page.document.body.classList.contains("filtering"));
  } finally {
    await page.close();
  }
  // プロジェクトが無いボードでは欄も出ないので、覚えていた「ワークスペース本体」も効かせない
  // （解除する手立てが画面に無いまま「絞り込み中」になってしまう）
  const without = await openBoard(fixture(), { state: { project: "" } });
  try {
    assert.equal(without.all("#project-filter").length, 0);
    assert.ok(!without.document.body.classList.contains("filtering"));
  } finally {
    await without.close();
  }
});

test("CB-D49 承認のオーバーレイは Esc で閉じる。承認している最中は閉じない", async () => {
  const page = await openBoard(fixture(), { approval: { kind: "done", count: 1, prompt: "文" } });
  try {
    page.key("Escape");
    await page.settle();
    assert.deepEqual(page.posted.at(-1), { type: "approveCancel" });
    // 他のキーでは閉じない
    const sent = page.posted.length;
    page.key("a");
    await page.settle();
    assert.equal(page.posted.length, sent);
  } finally {
    await page.close();
  }
  // 承認している最中は取り返しがつかないので、Esc を受けない
  const approving = await openBoard(fixture(), {
    approval: { kind: "approving", preview: approvePreview() },
  });
  try {
    const sent = approving.posted.length;
    approving.key("Escape");
    await approving.settle();
    assert.equal(approving.posted.length, sent);
  } finally {
    await approving.close();
  }
  // オーバーレイが無ければ、Esc は何も起こさない
  const plain = await openBoard();
  try {
    const sent = plain.posted.length;
    plain.key("Escape");
    await plain.settle();
    assert.equal(plain.posted.length, sent);
  } finally {
    await plain.close();
  }
});

test("CB-D50 カードは Enter でも開く。ボタンやリンクの上では開かない", async () => {
  const page = await openBoard();
  try {
    page.key("Enter", page.one('.card[data-id="i0001-01"]'));
    await page.settle();
    assert.equal(page.posted.at(-1)?.type, "open");
    const sent = page.posted.length;
    // カードの中のボタンの上で押しても、提案は開かない
    page.key("Enter", page.one('button[data-action="approve-one"]'));
    await page.settle();
    assert.equal(page.posted.length, sent);
    // 他のキーでは開かない
    page.key("a", page.one('.card[data-id="i0001-01"]'));
    await page.settle();
    assert.equal(page.posted.length, sent);
  } finally {
    await page.close();
  }
});

/** 取っ手を押して動かして離す。動かす量を渡さなければ、押して離すだけ */
function drag(page: DomPage, handle: Element, to?: number): void {
  const window = page.window as unknown as { PointerEvent: new (type: string, init: Record<string, unknown>) => Event };
  const event = (type: string, clientX: number): Event => new window.PointerEvent(type, { bubbles: true, button: 0, clientX, pointerId: 1 });

  handle.dispatchEvent(event("pointerdown", 0));
  if (to !== undefined) {
    handle.dispatchEvent(event("pointermove", to));
  }
  handle.dispatchEvent(event("pointerup", to ?? 0));
}

test("CB-D51 列の幅は取っ手のドラッグで決まって覚え、押しただけでは決まらない。ダブルクリックで戻る", async () => {
  const page = await openBoard();
  try {
    const column = (): Element => page.one('.column[data-state="done"]');
    const handle = page.one('.resizer[data-resize="done"]');
    // 押して離すだけ（動かしていない）なら、幅は決めない。押しただけで窓幅への追従が切れると困る
    drag(page, handle);
    await page.settle();
    assert.ok(!column().classList.contains("sized"));
    assert.deepEqual((page.state() as { widths?: Record<string, number> }).widths ?? {}, {});
    // ドラッグしたら px で固定し、覚える
    drag(page, handle, 320);
    await page.settle();
    assert.ok(column().classList.contains("sized"));
    assert.equal((column() as unknown as { style: { width: string } }).style.width, "320px");
    assert.deepEqual((page.state() as { widths: Record<string, number> }).widths, { done: 320 });
    // ダブルクリックで元の伸び縮みに戻る
    const Plain = (page.window as unknown as { Event: new (type: string, init: unknown) => Event }).Event;
    page.one('.resizer[data-resize="done"]').dispatchEvent(new Plain("dblclick", { bubbles: true }));
    await page.settle();
    assert.ok(!column().classList.contains("sized"));
    assert.equal((column() as unknown as { style: { width: string } }).style.width, "");
    assert.deepEqual((page.state() as { widths: Record<string, number> }).widths, {});
  } finally {
    await page.close();
  }
  // 覚えていた幅は、読み直した画面でも効く
  const again = await openBoard(fixture(), { state: { widths: { done: 280 } } });
  try {
    const column = again.one('.column[data-state="done"]');
    assert.ok(column.classList.contains("sized"));
    assert.equal((column as unknown as { style: { width: string } }).style.width, "280px");
  } finally {
    await again.close();
  }
});

test("CB-D52 ドラッグの途中で列が消えても、掴んだままの印を残さない", async () => {
  const page = await openBoard();
  try {
    const window = page.window as unknown as { PointerEvent: new (type: string, init: Record<string, unknown>) => Event };
    page.one('.resizer[data-resize="done"]').dispatchEvent(new window.PointerEvent("pointerdown", { bubbles: true, button: 0, clientX: 0, pointerId: 1 }));
    await page.settle();
    assert.ok(page.document.body.classList.contains("resizing"));
    // 読み直せずエラーの画面に替わると列ごと消え、pointerup を受ける相手が居なくなる。
    // 印が残ると、カーソルが col-resize のまま文字も選べなくなる
    await page.send({ type: "data", data: { kind: "error", error: "読めない" } });
    assert.equal(page.all(".column").length, 0);
    assert.ok(!page.document.body.classList.contains("resizing"));
  } finally {
    await page.close();
  }
});
