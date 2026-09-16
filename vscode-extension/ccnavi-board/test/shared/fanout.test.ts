import { test } from "node:test";
import assert from "node:assert/strict";
import { Fanout } from "../../src/core/fanout.js";

test("CB-T138 聞いている人全員に配る。外した人には配らない", () => {
  const fanout = new Fanout();
  const heard: string[] = [];
  const removeA = fanout.add(() => heard.push("a"));
  fanout.add(() => heard.push("b"));
  assert.equal(fanout.size, 2);

  fanout.fire();
  assert.deepEqual(heard, ["a", "b"]);

  removeA();
  assert.equal(fanout.size, 1);
  fanout.fire();
  assert.deepEqual(heard, ["a", "b", "b"]);
});

test("CB-T139 1 人が投げても、残りには配る", () => {
  const fanout = new Fanout();
  const heard: string[] = [];
  const errors: unknown[] = [];
  fanout.add(() => {
    throw new Error("わざと");
  });
  fanout.add(() => heard.push("b"));

  fanout.fire((error) => errors.push(error));
  assert.deepEqual(heard, ["b"], "投げた人の後ろにも届く");
  assert.equal(errors.length, 1);
  assert.match((errors[0] as Error).message, /わざと/);
});

test("CB-T140 配っている途中に外れた人には配らない", () => {
  const fanout = new Fanout();
  const heard: string[] = [];
  fanout.add(() => {
    heard.push("a");
    removeB();
  });
  const removeB = fanout.add(() => heard.push("b"));

  fanout.fire();
  assert.deepEqual(heard, ["a"], "a の中で外した b は呼ばれない");
});

test("CB-T141 同じ関数を 2 回渡せば購読も 2 つ。片方を外しても、もう片方は残る", () => {
  const fanout = new Fanout();
  const heard: string[] = [];
  const listener = () => heard.push("x");
  const removeFirst = fanout.add(listener);
  fanout.add(listener);
  assert.equal(fanout.size, 2);

  removeFirst();
  assert.equal(fanout.size, 1);
  fanout.fire();
  assert.deepEqual(heard, ["x"]);
});

test("CB-T142 二重に外しても数は狂わない", () => {
  const fanout = new Fanout();
  const remove = fanout.add(() => undefined);
  fanout.add(() => undefined);

  remove();
  remove();
  assert.equal(fanout.size, 1);
});
