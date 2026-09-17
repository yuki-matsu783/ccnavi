import { test } from "node:test";
import assert from "node:assert/strict";
import { forgetScreens, registerScreens, screens, type Screens } from "../../src/core/screens.js";

function spy(): { readonly screens: Screens; readonly calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    screens: {
      board: async (project) => void calls.push(`board:${project ?? "-"}`),
      rules: async (target) => void calls.push(`rules:${target.kind}`),
      risk: async () => void calls.push("risk"),
      phases: async (target) => void calls.push(`phases:${target.kind}`),
      projects: async () => void calls.push("projects"),
    },
  };
}

test("CB-T136 登録した入口を帳面から開く。要求する側は相手のパネルを知らない", async () => {
  const { screens: registered, calls } = spy();
  registerScreens(registered);
  await screens().board("lib");
  await screens().rules({ kind: "project", name: "lib" });
  await screens().phases({ kind: "self" });
  await screens().risk();
  await screens().projects();
  assert.deepEqual(calls, ["board:lib", "rules:project", "phases:self", "risk", "projects"]);
  forgetScreens();
});

test("CB-T137 登録前に開こうとしたら止める（組み立ての誤りで、利用者の操作では起きない）", () => {
  forgetScreens();
  assert.throws(() => screens(), /registerScreens/);
});
