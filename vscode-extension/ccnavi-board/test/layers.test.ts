import { test } from "node:test";
import assert from "node:assert/strict";
import { projectLayer, selfLayer } from "../src/core/layers.js";
import type { BoardJson, LayerJson } from "../src/core/model.js";
import { fixture } from "./fixture.js";

function layer(name: string, path: string): LayerJson {
  return { name, rules: { path, unreadable: "" }, phasesFile: { path: "", unreadable: "" } };
}

function board(layers: readonly LayerJson[]): BoardJson {
  return { ...fixture(), layers };
}

test("CB-T111 層は layers[] から名前で引き、予約名のプロジェクトは大文字小文字を問わず層を引かない", () => {
  const b = board([
    layer("common", "/ws/.claude/ccnavi/rules.yml"),
    layer("self", "/ws/.ccnavi/config/rules.yml"),
    layer("lib", "/ws/projects/lib/.ccnavi/config/rules.yml"),
  ]);
  assert.equal(selfLayer(b)?.rules.path, "/ws/.ccnavi/config/rules.yml");
  assert.equal(projectLayer(b, "lib")?.rules.path, "/ws/projects/lib/.ccnavi/config/rules.yml");
  // projects/self は自身の層を、projects/Common は共通層を引いてはいけない
  for (const reserved of ["self", "Self", "common", "COMMON"]) {
    assert.equal(projectLayer(b, reserved), undefined, reserved);
  }
  assert.equal(projectLayer(b, "app"), undefined);
  // 古い実行ファイルは layers を出さない
  assert.equal(selfLayer(board([])), undefined);
});
