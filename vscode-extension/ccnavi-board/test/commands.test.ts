import { test } from "node:test";
import assert from "node:assert/strict";
import * as commands from "../src/core/commands.js";
import {
  acceptCommand,
  approveArgs,
  previewArgs,
  shellQuote,
  toPosixPath,
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

test("CB-T18b preview に識別子を並べると、その分だけが対象になる", () => {
  assert.deepEqual(previewArgs(["i0002", "i0002-01"]), [
    "--approve",
    "--preview",
    "--json",
    "i0002",
    "i0002-01",
  ]);
  assert.deepEqual(previewArgs([]), previewArgs());
});

test("CB-T18c yes は見せた識別子と、そのときの絞りを分けて渡す", () => {
  // 絞り込み中。見せたのは 1 件で、絞りも同じ 1 件。
  assert.deepEqual(approveArgs(["i0002"], ["i0002"]), [
    "--approve",
    "--yes",
    "i0002",
    "--json",
    "i0002",
  ]);
  // 絞り込み無し。絞りは空で、実行ファイルは絞らないときの対象と見せた識別子を比べる。
  assert.deepEqual(approveArgs(["i0002", "i0002-01"]), [
    "--approve",
    "--yes",
    "i0002,i0002-01",
    "--json",
  ]);
});

test("CB-T19 accept は親の作業ツリーで、ワークスペースルートから綴った sh を打つ", () => {
  assert.equal(
    acceptCommand("/ws", "/ws/.claude/worktrees/i0001", 2),
    "cd '/ws/.claude/worktrees/i0001' && sh '/ws/.ccnavi/scripts/ccnavi-review.sh' accept 2",
  );
  // Windows の区切りと、単引用符を含むルート。
  assert.equal(
    acceptCommand("C:\\it's\\ws", "C:\\it's\\ws\\.claude\\worktrees\\i0001", 1),
    `cd 'C:/it'\\''s/ws/.claude/worktrees/i0001' && sh 'C:/it'\\''s/ws/.ccnavi/scripts/ccnavi-review.sh' accept 1`,
  );
});
