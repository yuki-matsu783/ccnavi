import { test } from "node:test";
import assert from "node:assert/strict";
import * as commands from "../src/core/commands.js";
import {
  acceptCommand,
  approveArgs,
  previewArgs,
  shellQuote,
  toPosixPath,
  wrapupCommand,
} from "../src/core/commands.js";

test("CB-T17 単引用符で囲み、中の単引用符を割る", () => {
  assert.equal(shellQuote("abc"), "'abc'");
  assert.equal(shellQuote("it's"), `'it'\\''s'`);
  assert.equal(toPosixPath("C:\\Users\\x\\ws"), "C:/Users/x/ws");
});

test("CB-T18 承認は子プロセスの引数で、preview は見るだけ、yes は見せた識別子をそのまま返す", () => {
  assert.deepEqual(previewArgs(), ["--approve", "--preview", "--json"]);
  assert.deepEqual(approveArgs(["i0001", "i0001-01"]), ["--approve", "--yes", "i0001,i0001-01", "--json"]);
  // ターミナルに `--approve` を送る経路は消した。y/N を端末で押す形には戻さない。
  assert.equal((commands as Record<string, unknown>).approveCommand, undefined);
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
