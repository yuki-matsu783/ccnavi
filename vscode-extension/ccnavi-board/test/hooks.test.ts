import { test } from "node:test";
import assert from "node:assert/strict";
import { envFromSettingsJson, hooksFor, matcherHits, parseHooks } from "../src/core/hooks.js";

const SETTINGS = JSON.stringify({
  env: { CCNAVI_MODE: "dry-run" },
  hooks: {
    PreToolUse: [
      { matcher: "", hooks: [{ type: "command", command: "ccnavi", timeout: 10 }] },
    ],
    PostToolUse: [
      { matcher: "", hooks: [{ type: "command", command: "ccnavi", timeout: 10 }] },
      {
        matcher: "Write|Edit|MultiEdit|NotebookEdit",
        hooks: [{ type: "command", command: "sh lint-py.sh", timeout: 180 }],
      },
    ],
    Stop: [{ hooks: [{ type: "command", command: "sh test-py.sh" }] }],
  },
});

test("CB-T30 hooks をイベント・matcher・コマンドの平らな並びで読む", () => {
  const entries = parseHooks(SETTINGS, "settings");
  assert.deepEqual(
    entries.map((e) => [e.event, e.matcher, e.command, e.timeout]),
    [
      ["PreToolUse", "", "ccnavi", 10],
      ["PostToolUse", "", "ccnavi", 10],
      ["PostToolUse", "Write|Edit|MultiEdit|NotebookEdit", "sh lint-py.sh", 180],
      ["Stop", "", "sh test-py.sh", null],
    ],
  );
  assert.equal(entries[0].source, "settings");
  assert.deepEqual(parseHooks("not json", "settings"), []);
  assert.deepEqual(parseHooks("{}", "settings-local"), []);
});

test("CB-T31 matcher は空か * で全部、それ以外はツール名への正規表現", () => {
  assert.equal(matcherHits("", "Bash"), true);
  assert.equal(matcherHits("*", "Bash"), true);
  assert.equal(matcherHits("Write|Edit", "Edit"), true);
  assert.equal(matcherHits("Write|Edit", "Bash"), false);
  assert.equal(matcherHits("Notebook.*", "NotebookEdit"), true);
  assert.equal(matcherHits("Write", "WriteFile"), false);
  // 正規表現として読めなければ文字列そのもの
  assert.equal(matcherHits("(", "("), true);
  assert.equal(matcherHits("(", "Bash"), false);
});

test("CB-T32 ツール名で走る hook を絞る。ツール名の無いイベントは常に走る", () => {
  const entries = parseHooks(SETTINGS, "settings");
  assert.deepEqual(
    hooksFor(entries, "Bash").map((e) => `${e.event}:${e.command}`),
    ["PreToolUse:ccnavi", "PostToolUse:ccnavi", "Stop:sh test-py.sh"],
  );
  assert.deepEqual(
    hooksFor(entries, "Edit").map((e) => `${e.event}:${e.command}`),
    ["PreToolUse:ccnavi", "PostToolUse:ccnavi", "PostToolUse:sh lint-py.sh", "Stop:sh test-py.sh"],
  );
});

test("CB-T33 env の値を読む。無ければ空", () => {
  assert.equal(envFromSettingsJson(SETTINGS, "CCNAVI_MODE"), "dry-run");
  assert.equal(envFromSettingsJson(SETTINGS, "CCNAVI_RULES"), "");
  assert.equal(envFromSettingsJson("{}", "CCNAVI_MODE"), "");
  assert.equal(envFromSettingsJson("broken", "CCNAVI_MODE"), "");
});
