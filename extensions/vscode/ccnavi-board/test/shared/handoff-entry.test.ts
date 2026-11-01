import { test } from "node:test";
import assert from "node:assert/strict";
import { handoffTreeKey } from "../../src/core/handoff-entry.js";
import { MAIN_TREE_KEY } from "../../src/core/handoffmodel.js";

test("CB-T1151 取り込み待ちを開くツリーのキーは、プロジェクトの設定ならそのプロジェクト、それ以外はワークスペース", () => {
  assert.equal(handoffTreeKey({ kind: "project", name: "app" }), "project:app");
  assert.equal(handoffTreeKey({ kind: "self" }), MAIN_TREE_KEY);
  assert.equal(handoffTreeKey({ kind: "workspace" }), MAIN_TREE_KEY);
});

