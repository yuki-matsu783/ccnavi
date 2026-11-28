/** 拡張全体で1つの答えを読み直して配る仕組み（`core/feed.ts`）。VS Codeは要らない */
import { test } from "node:test";
import assert from "node:assert/strict";
import { makeFeed } from "../../src/core/feed.js";

/** 呼ばれるたびに番号を返す読み込み。`release`を呼ぶまで返らない */
function slowLoader(): { readonly load: () => Promise<number>; readonly release: () => void; calls: () => number } {
  let count = 0;
  const waiting: (() => void)[] = [];
  return {
    load: () =>
      new Promise<number>((resolve) => {
        count += 1;
        const mine = count;
        waiting.push(() => resolve(mine));
      }),
    release: () => {
      for (const resolve of waiting.splice(0)) {
        resolve();
      }
    },
    calls: () => count,
  };
}

test("CB-T1280 読めた答えを残し、聞き手みんなに同じ答えを渡す。聞くのをやめた聞き手には渡さない", async () => {
  const feed = makeFeed(async () => "答え");
  assert.equal(feed.latest(), undefined);
  const a: string[] = [];
  const b: string[] = [];
  const stopA = feed.subscribe((r) => a.push(r));
  feed.subscribe((r) => b.push(r));
  await feed.refresh();
  assert.equal(feed.latest(), "答え");
  assert.deepEqual(a, ["答え"]);
  assert.deepEqual(b, ["答え"]);
  stopA();
  await feed.refresh();
  assert.deepEqual(a, ["答え"]);
  assert.deepEqual(b, ["答え", "答え"]);
});

test("CB-T1281 読んでいる最中の頼みは、終わってからの1回にまとめる。返るのは頼んだ後の読み込みが終わったとき", async () => {
  const loader = slowLoader();
  const feed = makeFeed(loader.load);
  const seen: number[] = [];
  feed.subscribe((r) => seen.push(r));
  const first = feed.refresh();
  // 最中に3回頼んでも、追いかけるのは1回だけ
  const second = feed.refresh();
  const third = feed.refresh();
  assert.equal(second, third);
  assert.equal(loader.calls(), 1);
  loader.release();
  await first;
  // 1回目が終わったら、まとめた1回が始まる
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(loader.calls(), 2);
  loader.release();
  await second;
  assert.deepEqual(seen, [1, 2]);
  assert.equal(feed.latest(), 2);
  // 終わったあとの頼みは、新しく1回読む
  const again = feed.refresh();
  assert.equal(loader.calls(), 3);
  loader.release();
  await again;
  assert.deepEqual(seen, [1, 2, 3]);
});
