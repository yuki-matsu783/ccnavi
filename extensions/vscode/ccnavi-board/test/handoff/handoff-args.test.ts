/**
 * 取り込み待ち画面が子プロセスに渡す引数（`src/core/commands.ts` の `handoff*Args`）を確かめる。
 * 引数は実行ファイルとの契約なので、期待する配列そのものと比べる。`--root` は `run()` が付けるので含めない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { handoffArgs, handoffYesArgs } from "../../src/core/commands.js";

test("CB-T1120 見るだけの引数は user handoff [<パス>…] --json。--yes は付けない", () => {
  assert.deepEqual(handoffArgs(), ["--entry", "board", "user", "handoff", "--json"]);
  assert.deepEqual(handoffArgs([".ccnavi/docs/a.md", ".claude/hooks/x.sh"]), ["--entry", "board", "user", "handoff", ".ccnavi/docs/a.md", ".claude/hooks/x.sh", "--json"]);
  assert.equal(handoffArgs([".ccnavi/docs/a.md"]).includes("--yes"), false);
});

test("CB-T1121 取り込みの引数は user handoff <パス…> --yes --expect <ハッシュ> --via board --json", () => {
  assert.deepEqual(handoffYesArgs([".ccnavi/docs/a.md"], "ab".repeat(32)), [
    "--entry",
    "board",
    "user",
    "handoff",
    ".ccnavi/docs/a.md",
    "--yes",
    "--expect",
    "ab".repeat(32),
    "--via",
    "board",
    "--json",
  ]);
  assert.deepEqual(handoffYesArgs(["a", "b"], "h").slice(0, 6), ["--entry", "board", "user", "handoff", "a", "b"]);
});
