import { test } from "node:test";
import assert from "node:assert/strict";
import { parseDocument } from "yaml";

import { blockingProblems } from "../../src/core/yaml-read.js";
import { readPhases } from "../../src/core/phases-doc.js";
import { readRisk } from "../../src/core/risk-doc.js";
import { readRules } from "../../src/core/rules-doc.js";

test("CB-T1246 読み込みを止める問題は、構文エラー（行番号付き）と最上位がマップでないものだけで、3つの文書で共通", () => {
  const tab = "version: 1\nfactors:\n\t- a\n";
  assert.match(blockingProblems(parseDocument(tab))[0], /^YAMLの構文エラー（\d+行目）: /);
  assert.deepEqual(blockingProblems(parseDocument("- a\n")), ["最上位がマップ（キーと値の組の集まり）ではありません"]);
  assert.deepEqual(blockingProblems(parseDocument("")), []);
  assert.deepEqual(blockingProblems(parseDocument("version: 1\n")), []);
  for (const blocking of [readRisk(tab).blocking, readPhases(tab).blocking, readRules(tab).blocking]) {
    assert.match(blocking[0], /^YAMLの構文エラー/);
  }
  // versionが無いなど、保存で直せるものは止めない
  assert.deepEqual(readRules("rules: []\n").blocking, []);
});
