/**
 * ボード画面の案内（`tours/board.yml`）。案内の間は今のチケットではなく見本（`core/tour-board.ts`）を描き、
 * 見本の上の操作は画面の state にも拡張ホストにも届かない。使い方ごとの案内は、全部の手順で指す先が画面にある。
 * 図の部品が大きさを測れるよう、承認の一覧を出す画面は `measure: true` で開く。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import type { HTMLButtonElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };
import { fixture } from "../helpers/fixture.js";
import { openBoard } from "../helpers/board.js";
import type { DomPage } from "../helpers/dom.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("board");

/** 「?」から案内の一覧を開き、キーの案内を始める */
async function start(dom: DomPage, course: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${course}"]`));
  await dom.settle();
}

function cardIds(dom: DomPage): string[] {
  return dom.all(".card").map((el) => el.getAttribute("data-id") ?? "").sort();
}

/** ふだん（実際のボード）の画面。fixture の `i0001` が親 */
const REAL_IDS = ["i0001", "i0001-01-01", "i0001-02-02", "i0001-02-03", "i0001-02-04", "i0001-02-05"].sort();

test("CB-D200 「?」は案内の一覧を開き、基本の案内が先頭で、使い方ごとの案内を選ぶと、その 1 本だけが始まる", async () => {
  const dom = await openBoard();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all('[data-action="tour-course"]');
    assert.deepEqual(items.map((item) => item.getAttribute("data-course")), def.courses.map((course) => course.id));
    assert.equal(items[0].getAttribute("data-course"), "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    dom.click(dom.one('[data-action="tour-course"][data-course="review"]'));
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    const review = def.courses.find((course) => course.id === "review")!;
    assert.equal(dom.one("#tour-title").textContent, review.steps[0].title);
    assert.equal(dom.one(".tour-count").textContent?.replace(/\s+/g, ""), `1/${review.steps.length}`);
  } finally {
    await dom.close();
  }
});

test("CB-D201 全部の案内の全部の手順で、指す先が画面にあり吹き出しが中ほどに出ず、見本だけを描く。最後の手順のあとで閉じる", async () => {
  for (const course of def.courses) {
    const dom = await openBoard(fixture(), { measure: true });
    try {
      await start(dom, course.id);
      const posted = dom.posted.length;
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面にありません`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
        // 実際のチケットではなく見本。実際のカードは 1 枚も出ない
        assert.ok(cardIds(dom).length > 0 && cardIds(dom).every((id) => id.startsWith("sample-")), `${at}: 実際のカードが出ている`);
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
        // 手順の scene が言うオーバーレイだけが出る
        const overlay = step.scene.overlay === undefined ? 0 : 1;
        assert.equal(dom.all(".approval-backdrop").length, overlay, `${at}: オーバーレイの出入りが scene と違う`);
        dom.click(dom.one('[data-action="tour-next"]'));
        await dom.settle();
      }
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
      assert.deepEqual(cardIds(dom), REAL_IDS, `${course.id}: 閉じたあと実際のボードに戻らない`);
      assert.equal(dom.all(".approval-backdrop").length, 0);
      assert.deepEqual(dom.posted.slice(posted), [{ type: "tourDone" }], `${course.id}: tourDone のほかに送った`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D202 案内の間は、実際のチケットではなく見本のカードを描き、閉じたら実際のボードに戻る", async () => {
  const dom = await openBoard();
  try {
    assert.deepEqual(cardIds(dom), REAL_IDS);
    await start(dom, "progress");
    assert.equal(dom.all(".banner.tour-sample").length, 1);
    assert.ok(cardIds(dom).length > 0 && cardIds(dom).every((id) => id.startsWith("sample-")), `実際のカードが出ている: ${cardIds(dom).join(",")}`);
    // 見本の集計
    assert.equal(dom.one(".summary .pending").textContent, "承認待ち 3 件");
    assert.equal(dom.one(".summary .counts").textContent?.replace(/\s+/g, " "), "残り 8 / 全 10");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".banner.tour-sample").length, 0);
    assert.deepEqual(cardIds(dom), REAL_IDS, "閉じても実際のカードに戻らない");
    assert.equal(dom.all(".approval-backdrop").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D203 案内の間の見本の絞り込み・畳みは見本の上だけに効き、実際の絞り込み・畳んだ列・state には触れない", async () => {
  const saved = { project: "*", parent: "*", attention: false, archived: false, folded: ["cancelled"], widths: {}, details: [] };
  const dom = await openBoard(fixture(), { state: saved });
  try {
    const before = JSON.stringify(dom.state());
    const doneFolded = (): boolean => dom.one('.column[data-state="done"]').classList.contains("folded");
    assert.equal(doneFolded(), false);
    // プロジェクトで絞る案内: 見本の欄が web になり、プロジェクト外のカードが隠れる
    await start(dom, "filter");
    assert.equal(dom.one<HTMLSelectElement>("#project-filter").value, "web");
    assert.ok(dom.all(".card.hidden").length > 0 && dom.all(".card:not(.hidden)").length > 0);
    assert.equal(dom.one('.card[data-id="sample-a"]').classList.contains("hidden"), true);
    assert.equal(dom.one('.card[data-id="sample-b"]').classList.contains("hidden"), false);
    // 4 番目の手順: 承認のボタンは、見えている承認待ち（web の 1 件）だけが対象
    for (let i = 0; i < 3; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.equal(dom.one('[data-action="approve"]').textContent?.trim(), "承認待ち 1 件を承認");
    assert.equal(dom.one<HTMLButtonElement>('[data-action="approve"]').disabled, false);
    dom.key("Escape");
    await dom.settle();
    // 閉じたら実際の絞り込みと畳みに戻り、state は動いていない（実際のボードにはプロジェクトが無く、欄も出ない）
    assert.equal(dom.all("#project-filter").length, 0);
    assert.equal(dom.one('.column[data-state="cancelled"]').classList.contains("folded"), true);
    assert.equal(JSON.stringify(dom.state()), before, "案内が覚えている state を変更した");

    // 列を畳む案内: 見本の完了の列が畳まれ、実際の畳み（取り消し）は見本に持ち込まない
    await start(dom, "progress");
    for (let i = 0; i < 4; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.equal(dom.one("#tour-title").textContent, "列を畳む");
    assert.equal(doneFolded(), true);
    assert.equal(dom.one('.column[data-state="cancelled"]').classList.contains("folded"), false);
    dom.key("Escape");
    await dom.settle();
    assert.equal(doneFolded(), false);
    assert.equal(JSON.stringify(dom.state()), before);
  } finally {
    await dom.close();
  }
});

test("CB-D204 案内の間は見本のボタンを押しても拡張ホストへ何も送らず、見本のオーバーレイの操作も届かない。送るのは tourDone だけ", async () => {
  const dom = await openBoard(fixture(), { measure: true });
  try {
    await start(dom, "approve");
    const posted = dom.posted.length;
    const stateBefore = JSON.stringify(dom.state());
    const click = async (selector: string): Promise<void> => {
      dom.click(dom.one(selector));
      await dom.settle();
    };
    // 見本のカードとヘッダの操作
    await click('[data-action="approve"]');
    await click('[data-action="approve-one"]');
    await click('[data-action="refresh"]');
    await click('.card[data-id="sample-a"] .title');
    await click('[data-action="workflow"]');
    await click('[data-action="flow"]');
    // カードのレビュー操作と、マージ状況の確認・帯の操作・詳細の開閉も、見本の上では何も送らない
    await click('[data-action="review-done"]');
    await click('[data-action="merge-check"]');
    await click('.card-details summary');
    // 見本の承認の一覧（4 手順進める）
    for (let i = 0; i < 4; i += 1) {
      await click('[data-action="tour-next"]');
    }
    assert.equal(dom.all('.approval-backdrop[data-approval="preview"]').length, 1);
    await click('[data-action="approve-confirm"]');
    await click('[data-action="edit-workflow"]');
    await click('.approval [data-action="approve-cancel"]');
    dom.key("Escape");
    await dom.settle();
    assert.deepEqual(dom.posted.slice(posted), [{ type: "tourDone" }], "案内の間に拡張ホストへ余計な送信があった");
    // 見本の上で詳細を開閉しても、実際の状態（覚えている詳細の開閉）を書き換えない
    assert.equal(JSON.stringify(dom.state()), stateBefore, "案内の間の操作が覚えている state を書き換えた");
    assert.equal(dom.all(".tour").length, 0);
    // 閉じたら実際の操作は届く
    dom.click(dom.one('[data-action="approve"]'));
    await dom.settle();
    assert.equal(dom.posted.at(-1)?.type, "approve");
  } finally {
    await dom.close();
  }
});

test("CB-D205 案内の間に拡張ホストから新しい中身が届いても見本は変わらず、閉じたあとに新しい中身が出る", async () => {
  const json = fixture();
  const dom = await openBoard(json);
  try {
    await start(dom, "progress");
    const sampleIds = cardIds(dom);
    const next = { ...json, tickets: json.tickets.filter((t) => t.ticket !== "i0001-02-05") };
    await dom.send({ type: "data", data: { kind: "board", board: buildBoard(next) } });
    await dom.settle();
    assert.deepEqual(cardIds(dom), sampleIds, "見本が入れ替わった");
    dom.key("Escape");
    await dom.settle();
    assert.deepEqual(cardIds(dom), REAL_IDS.filter((id) => id !== "i0001-02-05"));
  } finally {
    await dom.close();
  }
});

test("CB-D206 案内の間に実際の承認のオーバーレイが出たら案内を閉じる。見本のオーバーレイは実際のものと混ざらない", async () => {
  const json = fixture();
  const dom = await openBoard(json, { measure: true });
  try {
    await start(dom, "approve");
    for (let i = 0; i < 4; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.equal(dom.all('.approval-backdrop[data-approval="preview"]').length, 1);
    assert.ok(dom.one(".approval-batch").textContent?.includes("sample-a"));
    await dom.send({ type: "data", data: { kind: "board", board: buildBoard(json), approval: { kind: "loading" } } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.all(".approval-backdrop").length, 1);
    assert.equal(dom.one(".approval-backdrop").getAttribute("data-approval"), "loading", "実際のオーバーレイでなく見本が残った");
  } finally {
    await dom.close();
  }
});
