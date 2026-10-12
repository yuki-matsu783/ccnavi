/**
 * ルール管理の案内の見本（`tours/rules.yml` の `sample:`）の検査。見本のルールは rules.yml と同じ書き方で、
 * 画面は `tour-rules.ts` の小さな読み取りで `RuleForm` にする。実際の読み取り（`readRules`）と同じ結果になることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { stringify } from "yaml";

import { readRules } from "../../src/core/rules-doc.js";
import { condSummary, condProblems } from "../../src/core/rules-when.js";
import { parseRulesSample } from "../../src/core/tour-rules.js";
import { loadTour } from "../helpers/tour.js";

const sample = parseRulesSample(loadTour("rules").sample);

test("CB-T404 ルールの見本は、実際の読み取り（readRules）と同じ RuleForm になる", () => {
  const raw = (loadTour("rules").sample as { rules: unknown }).rules;
  const real = readRules(stringify({ version: 1, ...(raw as object) })).model;
  assert.deepEqual(real.problems, []);
  assert.deepEqual(sample.sections, real.sections);
});

test("CB-T405 ルールの見本には、組み合わせ条件を持つ行があり、図で見せられる（誤りが無く、深さを持つ）", () => {
  const withWhen = [...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].filter((rule) => rule.when !== undefined);
  assert.ok(withWhen.length >= 1);
  for (const rule of withWhen) {
    assert.deepEqual(condProblems(rule.when!), [], rule.id);
  }
  const env = withWhen.find((rule) => rule.id === "protect-env")!;
  assert.equal(condSummary(env.when!), "all(regex (^|[\\\\/])\\.env(\\.|$), not(any(2)))");
});

test("CB-T406 判定・期待する判定・候補の見本は、実行ファイルの JSON の読み取りを通る", () => {
  assert.ok(Object.keys(sample.judges).length >= 2);
  assert.equal(sample.judges["env-read"].verdict, "deny");
  assert.equal(sample.judges["env-example"].verdict, "");
  assert.ok(sample.checked !== undefined && sample.checked.expectations.length >= 1);
  assert.ok(sample.suggested !== undefined && sample.suggested.candidates.length >= 1);
});
