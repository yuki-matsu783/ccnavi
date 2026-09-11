import { test } from "node:test";
import assert from "node:assert/strict";
import {
  acceptCommand,
  approveCommand,
  shellQuote,
  toPosixPath,
  wrapupCommand,
} from "../src/core/commands.js";

test("CB-T17 単引用符で囲み、中の単引用符を割る", () => {
  assert.equal(shellQuote("abc"), "'abc'");
  assert.equal(shellQuote("it's"), `'it'\\''s'`);
  assert.equal(toPosixPath("C:\\Users\\x\\ws"), "C:/Users/x/ws");
});

test("CB-T18 --approve は実行ファイルかソースで、ワークスペースルートで打つ", () => {
  assert.equal(
    approveCommand({ kind: "exe", path: "C:\\ws\\dist\\ccnavi\\ccnavi.exe" }, "C:\\ws"),
    "cd 'C:/ws' && 'C:/ws/dist/ccnavi/ccnavi.exe' --root 'C:/ws' --approve",
  );
  assert.equal(
    approveCommand({ kind: "uv", root: "/ws" }, "/ws"),
    "cd '/ws' && uv run python -m ccnavi --root '/ws' --approve",
  );
});

test("CB-T19 accept と wrapup は親の作業ツリーで sh を打つ", () => {
  assert.equal(
    acceptCommand("/ws/.claude/worktrees/i0001", 2),
    "cd '/ws/.claude/worktrees/i0001' && sh .claude/scripts/ccnavi-review.sh accept 2",
  );
  assert.equal(
    wrapupCommand("/ws/.claude/worktrees/i0001", "ここで十分", true),
    "cd '/ws/.claude/worktrees/i0001' && sh .claude/scripts/ccnavi-review.sh wrapup --reason 'ここで十分'",
  );
  assert.equal(
    wrapupCommand("/ws/.claude/worktrees/i0001", "it's done", false),
    `cd '/ws/.claude/worktrees/i0001' && sh .claude/scripts/ccnavi-review.sh wrapup --reason 'it'\\''s done' --no-issue`,
  );
});
