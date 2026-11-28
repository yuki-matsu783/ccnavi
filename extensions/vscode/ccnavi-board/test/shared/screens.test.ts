import { test } from "node:test";
import assert from "node:assert/strict";
import { forgetScreens, registerScreens, screens, type Screens } from "../../src/core/screens.js";

function spy(): { readonly screens: Screens; readonly calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    screens: {
      board: async (target) => void calls.push(`board:${target === undefined ? "-" : target.kind === "project" ? target.project : target.kind}`),
      rules: async () => void calls.push("rules"),
      risk: async () => void calls.push("risk"),
      phases: async () => void calls.push("phases"),
      templates: async () => void calls.push("templates"),
      handoff: async (tree) => void calls.push(`handoff:${tree ?? "-"}`),
      projects: async () => void calls.push("projects"),
      flow: async (ticket) => void calls.push(`flow:${ticket}`),
      workflow: async (ticket) => void calls.push(`workflow:${ticket}`),
    },
  };
}

test("CB-T136 登録した入口を帳面から開く。要求する側は相手のパネルを知らない", async () => {
  const { screens: registered, calls } = spy();
  registerScreens(registered);
  await screens().board({ kind: "project", project: "lib" });
  await screens().board({ kind: "mergeCheck" });
  await screens().rules();
  await screens().phases();
  await screens().templates();
  await screens().handoff("project:lib");
  await screens().handoff();
  await screens().risk();
  await screens().projects();
  await screens().flow("i0001-01-01");
  await screens().workflow("i0001");
  assert.deepEqual(calls, ["board:lib", "board:mergeCheck", "rules", "phases", "templates", "handoff:project:lib", "handoff:-", "risk", "projects", "flow:i0001-01-01", "workflow:i0001"]);
  forgetScreens();
});

test("CB-T137 登録前に開こうとしたら止める（組み立ての誤りで、ユーザの操作では起きない）", () => {
  forgetScreens();
  assert.throws(() => screens(), /registerScreens/);
});
