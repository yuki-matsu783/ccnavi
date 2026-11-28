import { test } from "node:test";
import assert from "node:assert/strict";
import type { BoardJson } from "../../src/core/model.js";
import { layerFrom, openingLayer } from "../../src/core/targets.js";
import { fixture } from "../helpers/fixture.js";

/** プロジェクトapiを設定の対象に持つボード */
function withApi(): BoardJson {
  const base = fixture();
  const self = base.layers.find((l) => l.name === "self");
  assert.ok(self);
  return { ...base, trees: [...base.trees, { ...base.trees[0], kind: "project", name: "api" }], layers: [...base.layers, { ...self, name: "api" }] };
}

test("CB-T1299 設定の画面は覚えた最後のレイヤーで開く。いまの答えの選択肢に無ければ画面の既定で開き、そのことを1行で返す", () => {
  const board = withApi();
  // 覚えたものがあり、選択肢にある
  assert.deepEqual(openingLayer({ kind: "project", name: "api" }, board, { kind: "self" }), { target: { kind: "project", name: "api" }, notice: "" });
  assert.deepEqual(openingLayer({ kind: "workspace" }, board, { kind: "self" }), { target: { kind: "workspace" }, notice: "" });
  // 覚えたものが選択肢に無い（プロジェクトが消えた・設定の対象でない）
  assert.deepEqual(openingLayer({ kind: "project", name: "web" }, board, { kind: "self" }), {
    target: { kind: "self" },
    notice: "プロジェクトwebにはこの設定が無いため、ワークスペースを開きました",
  });
  assert.equal(openingLayer({ kind: "project", name: "web" }, board, { kind: "workspace" }).notice, "プロジェクトwebにはこの設定が無いため、共通の設定を開きました");
  // 覚えたものが無ければ既定で、知らせない
  assert.deepEqual(openingLayer(undefined, board, { kind: "workspace" }), { target: { kind: "workspace" }, notice: "" });
  // 答えがまだ無いときは確かめられないので、覚えたものを使う
  assert.deepEqual(openingLayer({ kind: "project", name: "web" }, undefined, { kind: "self" }), { target: { kind: "project", name: "web" }, notice: "" });
});

test("CB-T1300 選ぶ欄の値（kindとname）をレイヤーに戻す。知らない種類と名前の無いプロジェクトは戻さない", () => {
  assert.deepEqual(layerFrom({ kind: "workspace", name: "" }), { kind: "workspace" });
  assert.deepEqual(layerFrom({ kind: "self", name: "x" }), { kind: "self" });
  assert.deepEqual(layerFrom({ kind: "project", name: "api" }), { kind: "project", name: "api" });
  assert.equal(layerFrom({ kind: "project", name: "" }), undefined);
  assert.equal(layerFrom({ kind: "common", name: "" }), undefined);
  assert.equal(layerFrom({ kind: undefined, name: undefined }), undefined);
});
