import { test } from "node:test";
import assert from "node:assert/strict";
import { defaultSum, riskSums, rulesSums, sumLabel } from "../../src/core/sums.js";
import { targetOptions } from "../../src/core/targets.js";
import { sumsBoard } from "../helpers/sums.js";

test("CB-T307 足し算は実行ファイルの sums[] をそのまま渡す。ワークスペースが先、プロジェクトが後で、拡張は足し直さない", () => {
  const board = sumsBoard();
  const rules = rulesSums(board);
  assert.deepEqual(rules.map((s) => s.name), ["self", "lib"]);
  assert.deepEqual(rules.map((s) => s.layers), [["common", "self"], ["common", "lib"]]);
  // lib の足し算は共通のルールと lib のルールの両方を持つ。ask は ask のまま
  assert.deepEqual(rules[1].deny.map((r) => [r.id, r.source]), [["guard-approved", "common"], ["lib:no-tmp", "lib"]]);
  assert.deepEqual(rules[1].ask.map((r) => r.id), ["lib:deps"]);
  assert.deepEqual(rules[0].deny.map((r) => r.id), ["guard-approved"]);
  const risk = riskSums(board);
  assert.deepEqual(risk[1].factors.map((f) => [f.id, f.source, f.value]), [["big-diff", "common", "300"], ["schema", "lib", "db/**"]]);
  assert.deepEqual(risk[1].levels, { medium: 20, high: 40, critical: 50 });
  assert.equal(risk[0].levels.critical, 70);
  // ボードを読めなかったとき（undefined）と、sums が無い古い実行ファイルでは空
  assert.deepEqual(rulesSums(undefined), []);
  assert.deepEqual(riskSums(undefined), []);
});

test("CB-T308 足し算の欄の表記と、最初に見せる足し算。共通の設定を開いているときはワークスペースの足し算", () => {
  assert.equal(sumLabel("self"), "ワークスペースの足し算（共通の設定 + ワークスペースの設定）");
  assert.equal(sumLabel("lib"), "プロジェクト lib の足し算（共通の設定 + lib の設定）");
  const sums = [{ name: "self" }, { name: "lib" }];
  assert.equal(defaultSum(sums, { kind: "project", name: "lib" }), "lib");
  assert.equal(defaultSum(sums, { kind: "self", name: "" }), "self");
  assert.equal(defaultSum(sums, { kind: "workspace", name: "" }), "self");
  assert.equal(defaultSum(sums, undefined), "self");
  // 設定の無いプロジェクトを開いていれば先頭、足し算が無ければ空
  assert.equal(defaultSum(sums, { kind: "project", name: "app" }), "self");
  assert.equal(defaultSum([], { kind: "self", name: "" }), "");
});

test("CB-T309 設定の対象の一覧。共通の設定の呼び名を渡さない画面（フェーズ管理）は、共通の設定を並べない", () => {
  const labels = (kind: string | undefined) => targetOptions(undefined, kind, { kind: "self", name: "" }).map((o) => o.label);
  assert.deepEqual(labels("workspace"), ["共通の設定", "ワークスペース"]);
  assert.deepEqual(labels(undefined), ["ワークスペース"]);
});
