import { test } from "node:test";
import assert from "node:assert/strict";
import { commonLayer, projectLayer, selfLayer } from "../../src/core/layers.js";
import type { BoardJson, LayerJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";

function layer(name: string, path: string): LayerJson {
  return { name, rules: { path, unreadable: "" }, risk: { path: "", unreadable: "" }, phasesFile: { path: "", unreadable: "" } };
}

function board(layers: readonly LayerJson[]): BoardJson {
  return { ...fixture(), layers };
}

test("CB-T111 レイヤーは layers[] から名前で引き、予約名のプロジェクトは大文字小文字を問わずレイヤーを引かない", () => {
  const b = board([
    layer("common", "/ws/.ccnavi/common/rules.yml"),
    layer("self", "/ws/.ccnavi/config/rules.yml"),
    layer("lib", "/ws/projects/lib/.ccnavi/config/rules.yml"),
  ]);
  assert.equal(commonLayer(b)?.rules.path, "/ws/.ccnavi/common/rules.yml");
  assert.equal(selfLayer(b)?.rules.path, "/ws/.ccnavi/config/rules.yml");
  assert.equal(projectLayer(b, "lib")?.rules.path, "/ws/projects/lib/.ccnavi/config/rules.yml");
  // projects/self は自身のレイヤーを、projects/Common は共通レイヤーを引いてはいけない
  for (const reserved of ["self", "Self", "common", "COMMON"]) {
    assert.equal(projectLayer(b, reserved), undefined, reserved);
  }
  assert.equal(projectLayer(b, "app"), undefined);
});
