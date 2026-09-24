/**
 * 前の読み直しから、どのカードが列を変えたか（`src/core/board-moved.ts`）。
 * 渡された分を画面が出すところは board.dom.test.ts（CB-D82）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import { movedCards, movedStep, NOTHING_MOVED, placementOf, samePlacement, type Placement } from "../../src/core/board-moved.js";
import { fixture } from "../helpers/fixture.js";

test("CB-T192 列が変わったカードと新規起票のカードだけを出す。消えたカードは出さない", () => {
  // 見本のボードの置き場所。列は組み立て（board.ts）が決めたものをそのまま読む。
  // `assert.deepEqual` は `asserts actual is T` なので、変数に当てると以後の型が literal に狭まる。
  // 戻り値に直に当てて、`before` は `Placement` のままにする
  const board = buildBoard(fixture());
  const before = placementOf(board);
  assert.deepEqual(placementOf(board), {
    i0001: "doing",
    "i0001-01": "done",
    "i0001-02": "doing",
    "i0001-03": "todo",
    "i0001-04": "doing",
    "i0001-05": "cancelled",
  });

  // 承認は 未着手 → 作業中。着手も完了も同じ形で出る
  const after: Placement = { ...before, "i0001-03": "doing", "i0001-02": "done" };
  // 並びは `after` の並び順。`placementOf` が作ったものなら列の順（未着手 → 作業中 → …）
  assert.deepEqual(movedCards(before, after), [
    { id: "i0001-03", from: "todo", to: "doing" },
    { id: "i0001-02", from: "doing", to: "done" },
  ]);

  // 新しく現れたカードは from を持たない。消えたカードは印を付ける先が無いので出さない
  const { "i0001-05": _gone, ...rest } = before;
  const added: Placement = { ...rest, i0002: "todo" };
  assert.deepEqual(movedCards(before, added), [{ id: "i0002", to: "todo" }]);

  // 何も動いていなければ空
  assert.deepEqual(movedCards(before, { ...before }), []);
});

test("CB-T192b 置き場所が同じかを見る（同じなら印を作り直さない）", () => {
  const before = placementOf(buildBoard(fixture()));
  assert.equal(samePlacement(before, { ...before }), true, "写しは同じ");
  assert.equal(samePlacement(before, { ...before, "i0001-03": "doing" }), false, "列が変われば違う");
  const { "i0001-05": _gone, ...fewer } = before;
  assert.equal(samePlacement(before, fewer), false, "減っても違う");
  assert.equal(samePlacement(fewer, before), false, "増えても違う");
  // 件数が同じで中身が違う（1 枚消えて 1 枚増えた）のも違う。数だけで畳まない
  assert.equal(samePlacement(before, { ...fewer, i0002: "done" }), false);
});

/** 見本のボードの `ticket` を、指定の列に置いた 1 枚 */
function moveTo(board: ReturnType<typeof buildBoard>, ticket: string, to: string): ReturnType<typeof buildBoard> {
  return {
    ...board,
    columns: board.columns.map((column) => {
      const cards = column.cards.filter((card) => card.id !== ticket);
      const moving = board.columns.flatMap((c) => c.cards).find((card) => card.id === ticket);
      if (moving === undefined) {
        throw new Error(`見本に ${ticket} が無い`);
      }
      const own = column.state === to ? [...cards, moving] : cards;
      return { ...column, cards: own, count: own.length };
    }),
  };
}

/** 見本のボードから `ticket` を 1 枚落とした形 */
function without(board: ReturnType<typeof buildBoard>, ticket: string): ReturnType<typeof buildBoard> {
  return {
    ...board,
    columns: board.columns.map((column) => {
      const cards = column.cards.filter((card) => card.id !== ticket);
      return { ...column, cards, count: cards.length };
    }),
  };
}

test("CB-T192c 1 枚目は印を付けず、列が動かない読み直しでは前の印を持ち越す", () => {
  const board = buildBoard(fixture());

  // 1 枚目。比べる相手が無いので、何にも印を付けない（開いた直後に全部が光ると意味が無い）
  const first = movedStep(NOTHING_MOVED, board);
  assert.deepEqual(first.moved, []);
  assert.notEqual(first.placement, undefined);

  // 承認された（未着手 → 作業中）
  const approved = movedStep(first, moveTo(board, "i0001-03", "doing"));
  assert.deepEqual(approved.moved, [{ id: "i0001-03", from: "todo", to: "doing" }]);

  // 同じ列のまま渡り直った（承認のオーバーレイの出し入れ、何も変わらなかった「更新」）。
  // **ここで作り直すと、承認の文を閉じた瞬間に印が消える**
  const again = movedStep(approved, moveTo(board, "i0001-03", "doing"));
  assert.equal(again, approved, "何も変わらないなら、同じ状態をそのまま返す");
  assert.deepEqual(again.moved, [{ id: "i0001-03", from: "todo", to: "doing" }]);

  // 次に何かが動いたら、前の印は消えて新しい動きに入れ替わる
  const next = movedStep(again, moveTo(board, "i0001-02", "done"));
  // 並びは列の順（未着手 → 作業中 → 完了）
  assert.deepEqual(next.moved, [
    { id: "i0001-03", from: "doing", to: "todo" },
    { id: "i0001-02", from: "doing", to: "done" },
  ]);
});

test("CB-T192d カードが消えただけの読み直しも「変わった」と数える（戻ってきたら新規起票と言える）", () => {
  const board = buildBoard(fixture());
  const first = movedStep(NOTHING_MOVED, board);

  // 消えたカードには印を付けられないので `moved` は空。**それでも置き場所は更新する**。
  // ここを「動いた分が 0 件なら据え置き」にすると、戻ってきたカードが「新規起票」にならない
  const gone = movedStep(first, without(board, "i0001-03"));
  assert.deepEqual(gone.moved, []);
  assert.notEqual(gone, first, "置き場所が変わったので、同じ状態は返さない");

  const back = movedStep(gone, board);
  assert.deepEqual(back.moved, [{ id: "i0001-03", to: "todo" }]);
});
