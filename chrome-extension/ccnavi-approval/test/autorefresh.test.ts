/**
 * ボードの自動の読み直し（5 分おき・タブが見えない間は止める・重ねない）。時計とタイマーは手で進める。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { AUTO_REFRESH_MS, startAutoRefresh } from "../src/core/autorefresh.js";

const MIN = 60 * 1000;

function harness(opts: { busy?: () => boolean; run?: () => Promise<void> } = {}) {
  let now = 1_000_000;
  let visibility = "visible";
  const listeners: (() => void)[] = [];
  const timers = new Map<number, { at: number; cb: () => void }>();
  let nextId = 1;
  const calls = { run: 0 };
  const ar = startAutoRefresh({
    doc: { get visibilityState() { return visibility; }, addEventListener: (_t, cb) => void listeners.push(cb) },
    now: () => now,
    setTimeout: (cb, ms) => {
      const id = nextId++;
      timers.set(id, { at: now + ms, cb });
      return id;
    },
    clearTimeout: (id) => void timers.delete(id as number),
    busy: opts.busy,
    run: async () => {
      calls.run++;
      await opts.run?.();
    },
  });
  return {
    ar,
    calls,
    timers,
    setVisibility(v: string) {
      visibility = v;
      for (const l of listeners) l();
    },
    /** 時計を進め、期限の来たタイマーを順に鳴らす */
    async advance(ms: number) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = Math.max(now, due[1].at);
        due[1].cb();
        await new Promise((r) => setImmediate(r));
      }
      now = end;
    },
  };
}

test("CX-T200 間隔は固定の 5 分で、5 分おきに読み直す", async () => {
  assert.equal(AUTO_REFRESH_MS, 5 * MIN);
  const h = harness();
  await h.advance(4 * MIN);
  assert.equal(h.calls.run, 0);
  await h.advance(1 * MIN);
  assert.equal(h.calls.run, 1);
  await h.advance(5 * MIN);
  assert.equal(h.calls.run, 2);
});

test("CX-T201 タブが見えない間は読み直さない。見える状態に戻ったとき、5 分以上たっていればすぐ読み直す", async () => {
  const h = harness();
  h.setVisibility("hidden");
  await h.advance(20 * MIN);
  assert.equal(h.calls.run, 0);
  h.setVisibility("visible");
  await new Promise((r) => setImmediate(r));
  assert.equal(h.calls.run, 1);
});

test("CX-T202 見える状態に戻ったとき、前回から 5 分たっていなければ読み直さず、残りで数え直す", async () => {
  const h = harness();
  await h.advance(3 * MIN);
  h.setVisibility("hidden");
  h.setVisibility("visible");
  await new Promise((r) => setImmediate(r));
  assert.equal(h.calls.run, 0);
  await h.advance(2 * MIN);
  assert.equal(h.calls.run, 1);
});

test("CX-T203 手動など別の読み直しが終わったら、そこから 5 分を数え直す", async () => {
  const h = harness();
  await h.advance(4 * MIN);
  h.ar.done();
  await h.advance(4 * MIN);
  assert.equal(h.calls.run, 0);
  await h.advance(1 * MIN);
  assert.equal(h.calls.run, 1);
});

test("CX-T204 読み直しが走っている間は重ねない。busy の間は飛ばして次の周期に回す", async () => {
  let release: () => void = () => undefined;
  const h = harness({ run: () => new Promise<void>((r) => (release = r)) });
  await h.advance(5 * MIN);
  assert.equal(h.calls.run, 1);
  // 走っている間に見える状態へ戻っても、重ねない
  h.setVisibility("hidden");
  await h.advance(10 * MIN);
  h.setVisibility("visible");
  await new Promise((r) => setImmediate(r));
  assert.equal(h.calls.run, 1);
  release();
  await new Promise((r) => setImmediate(r));
  assert.equal(h.calls.run, 1);

  let busy = true;
  const b = harness({ busy: () => busy });
  await b.advance(5 * MIN);
  assert.equal(b.calls.run, 0);
  busy = false;
  await b.advance(5 * MIN);
  assert.equal(b.calls.run, 1);
});

test("CX-T205 読み直しが失敗しても止まらず、次の周期でまた試す", async () => {
  let n = 0;
  const h = harness({
    run: async () => {
      if (n++ === 0) throw new Error("通信に失敗した");
    },
  });
  await h.advance(5 * MIN);
  await h.advance(5 * MIN);
  assert.equal(h.calls.run, 2);
});
