import { test } from "node:test";
import assert from "node:assert/strict";
import { shareInFlight } from "../../src/core/share.js";

/** 手で決めた時に answer を返す読み。何回走ったかを数える */
function fakeRun() {
  const calls: string[] = [];
  const pending: Array<(value: string) => void> = [];
  const rejects: Array<(error: Error) => void> = [];
  const run = (key: string): Promise<string> => {
    calls.push(key);
    return new Promise<string>((resolve, reject) => {
      pending.push(resolve);
      rejects.push(reject);
    });
  };
  return { calls, pending, rejects, run };
}

test("CB-T131 走っている間に来た呼び出しは同じ答えを待つ", async () => {
  const fake = fakeRun();
  const shared = shareInFlight(fake.run, (key: string) => key);

  const first = shared("a");
  const second = shared("a");
  assert.equal(fake.calls.length, 1, "走るのは 1 つ");

  fake.pending[0]("答え");
  assert.equal(await first, "答え");
  assert.equal(await second, "答え");
});

test("CB-T132 鍵が違えば別に走る", async () => {
  const fake = fakeRun();
  const shared = shareInFlight(fake.run, (key: string) => key);

  const a = shared("a");
  const b = shared("b");
  assert.deepEqual(fake.calls, ["a", "b"]);

  fake.pending[0]("A");
  fake.pending[1]("B");
  assert.equal(await a, "A");
  assert.equal(await b, "B");
});

test("CB-T133 答えが返った後は分け合わない。次に呼ぶ人は新しく走らせる", async () => {
  const fake = fakeRun();
  const shared = shareInFlight(fake.run, (key: string) => key);

  const first = shared("a");
  fake.pending[0]("古い");
  assert.equal(await first, "古い");

  const second = shared("a");
  assert.equal(fake.calls.length, 2, "もう一度走る");
  fake.pending[1]("新しい");
  assert.equal(await second, "新しい");
});

test("CB-T134 失敗も控えない。待っていた全員に同じ失敗が届き、次は走り直す", async () => {
  const fake = fakeRun();
  const shared = shareInFlight(fake.run, (key: string) => key);

  const first = shared("a");
  const second = shared("a");
  fake.rejects[0](new Error("走らせられない"));
  await assert.rejects(first, /走らせられない/);
  await assert.rejects(second, /走らせられない/);

  const third = shared("a");
  assert.equal(fake.calls.length, 2, "失敗を覚えずに走り直す");
  fake.pending[1]("答え");
  assert.equal(await third, "答え");
});

test("CB-T135 引数が複数でも、鍵の作り方で分け合う相手が決まる", async () => {
  const calls: Array<[string, string]> = [];
  const run = (root: string, setting: string): Promise<string> => {
    calls.push([root, setting]);
    return Promise.resolve(`${root}/${setting}`);
  };
  const shared = shareInFlight(run, (root: string, setting: string) => JSON.stringify([root, setting]));

  const [a, b] = await Promise.all([shared("/w", ""), shared("/w", "")]);
  assert.equal(a, "/w/");
  assert.equal(b, "/w/");
  assert.equal(calls.length, 1);

  await shared("/w", "bin");
  assert.deepEqual(calls[1], ["/w", "bin"]);
});
