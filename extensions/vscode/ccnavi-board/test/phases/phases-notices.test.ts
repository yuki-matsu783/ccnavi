/**
 * 吹き出しの置き場所（`src/core/tour-place.ts`）。DOM に触れないので単体で試す。
 *
 * 前はフェーズ管理画面の図の下の注意（`graphNotices`）もここで見ていた。図と待ち方の選択を
 * 画面から外したので、その注意も無い（図が無いことは `phases.dom.test.ts` が見張る）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { placeBubble } from "../../src/core/tour-place.js";

test("CB-T214 吹き出しは画面の外に出ない。下に収まらなければ上、どちらにも収まらなければ画面の下端に寄せる", () => {
  const view = { width: 800, height: 600 };
  const bubble = { width: 340, height: 200 };
  // 下に収まる
  assert.deepEqual(placeBubble({ top: 50, left: 20, width: 100, height: 30 }, bubble, view), { top: 90, left: 20 });
  // 下に収まらず、上に収まる
  assert.equal(placeBubble({ top: 450, left: 20, width: 100, height: 30 }, bubble, view).top, 240);
  // 画面より大きな一覧を指す。前は下に出して画面の外へ消え、「次へ」が押せなかった
  const tall = placeBubble({ top: 40, left: 20, width: 700, height: 900 }, bubble, view);
  assert.ok(tall.top >= 8 && tall.top + bubble.height <= view.height - 8, `画面の外に出た: ${tall.top}`);
  // 右端の要素でも、吹き出しの右端は画面に収まる
  assert.ok(placeBubble({ top: 50, left: 780, width: 20, height: 20 }, bubble, view).left + bubble.width <= view.width - 8);
});
