import { test } from "node:test";
import assert from "node:assert/strict";
import {
  acceptCommand,
  approveCommand,
  shellQuote,
  toPosixPath,
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

test("CB-T18b --approve に識別子を並べると、その分だけの束になる", () => {
  assert.equal(
    approveCommand({ kind: "uv", root: "/ws" }, "/ws", ["i0002", "i0002-01"]),
    "cd '/ws' && uv run python -m ccnavi --root '/ws' --approve 'i0002' 'i0002-01'",
  );
  assert.equal(approveCommand({ kind: "uv", root: "/ws" }, "/ws", []), approveCommand({ kind: "uv", root: "/ws" }, "/ws"));
});

test("CB-T19 accept は親の作業ツリーで sh を打つ", () => {
  assert.equal(
    acceptCommand("/ws/.claude/worktrees/i0001", 2),
    "cd '/ws/.claude/worktrees/i0001' && sh .claude/scripts/ccnavi-review.sh accept 2",
  );
});
