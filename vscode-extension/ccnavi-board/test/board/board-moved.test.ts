/**
 * 前の読み直しから、どのカードが列を変えたか（`src/core/board-moved.ts`）。
 * 画面で動かして見るのは board.dom.test.ts（CB-D82）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { buildBoard } from "../../src/core/board.js";
import { movedCards, placementOf, samePlacement, type Placement } from "../../src/core/board-moved.js";
import { fixture } from "../helpers/fixture.js";

test("CB-T192 列が変わったカードと新しく出たカードだけを出す。消えたカードは出さない", () => {
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
