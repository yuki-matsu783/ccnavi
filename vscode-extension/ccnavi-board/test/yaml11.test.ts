import { test } from "node:test";
import assert from "node:assert/strict";
import { yaml11Ambiguous } from "../src/core/yaml11.js";

test("CB-T96 PyYAML（YAML 1.1）が文字列以外に読む語を見分ける", () => {
  for (const word of ["yes", "No", "ON", "off", "y", "n", "true", "null", "~", "", "0755", "0x1F", "0b101", "1_000", "25", "-3", "1:30", "1:30.5", "1.5", ".5", "1.0e+3", ".inf", ".NaN", "2026-09-12", "2026-9-1 10:00:00", "=", "<<"]) {
    assert.equal(yaml11Ambiguous(word), true, word);
  }
  for (const word of ["yes!", "行数が多い", "big-diff", "implement", "1e3", "0o17", "v1", "on-call", "1:30:", "wip/research/*", "CI やエージェント定義に触った", "テストの無い振る舞いの変更を含むか"]) {
    assert.equal(yaml11Ambiguous(word), false, word);
  }
});
