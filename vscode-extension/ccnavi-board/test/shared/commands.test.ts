import { test } from "node:test";
import assert from "node:assert/strict";
import * as commands from "../../src/core/commands.js";
import {
  approveArgs,
  decideArgs,
  decidePreviewArgs,
  previewArgs,
  pushApprovedCommand,
  reviewedPrompt,
  scriptCommand,
  shellQuote,
  toPosixPath,
} from "../../src/core/commands.js";

test("CB-T17 単引用符で囲み、中の単引用符を割る", () => {
  assert.equal(shellQuote("abc"), "'abc'");
  assert.equal(shellQuote("it's"), `'it'\\''s'`);
  assert.equal(toPosixPath("C:\\Users\\x\\ws"), "C:/Users/x/ws");
});

test("CB-T18 承認は子プロセスの引数で、preview は見るだけ、yes は見せた識別子をそのまま返す", () => {
  assert.deepEqual(previewArgs(), ["--approve", "--preview", "--json"]);
  assert.deepEqual(approveArgs(["i0001", "i0001-01"], "ab12"), [
    "--approve",
    "--yes",
    "i0001,i0001-01",
    "--digest",
    "ab12",
    "--json",
  ]);
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
  assert.deepEqual(approveArgs(["i0002"], "ab12", ["i0002"]), [
    "--approve",
    "--yes",
    "i0002",
    "--digest",
    "ab12",
    "--json",
    "i0002",
  ]);
  // 絞り込み無し。絞りは空で、実行ファイルは絞らないときの対象と見せた識別子を比べる。
  assert.deepEqual(approveArgs(["i0002", "i0002-01"], "ab12"), [
    "--approve",
    "--yes",
    "i0002,i0002-01",
    "--digest",
    "ab12",
    "--json",
  ]);
});

test("CB-T19 decide の引数。一覧は --preview、選んだ行き先は JSON と見せた指紋で渡す", () => {
  assert.deepEqual(decidePreviewArgs(2), ["decide", "2", "--preview"]);
  assert.deepEqual(decideArgs(1, { u1: "keep", "https://x/y#z": "fix" }, "d0"), [
    "decide",
    "1",
    "--choices",
    '{"u1":"keep","https://x/y#z":"fix"}',
    "--digest",
    "d0",
  ]);
});

test("CB-T19b 承認済みチケットを運ぶ sh は、ワークスペースルートからの絶対パスで送る", () => {
  // 絶対パスなので、前のコマンドが別の場所へ cd したターミナルでも届く。
  assert.equal(pushApprovedCommand("/ws"), "sh '/ws/.ccnavi/scripts/ccnavi-push-approved.sh'");
  // Windows の区切りは "/" に直す（Git Bash が読める形）。
  assert.equal(
    pushApprovedCommand("C:\\Users\\x\\ws"),
    "sh 'C:/Users/x/ws/.ccnavi/scripts/ccnavi-push-approved.sh'",
  );
  // 単引用符を含むパスは割って囲む。
  assert.equal(
    pushApprovedCommand("/tmp/it's ws"),
    `sh '/tmp/it'\\''s ws/.ccnavi/scripts/ccnavi-push-approved.sh'`,
  );
});

test("CB-T19c 文面の sh の綴りは実行ファイルの script_command と同じ引用の規則。空白や記号があるときだけ引用する（root を解くのは呼び手）", () => {
  assert.equal(scriptCommand("/ws", "ccnavi-review.sh"), "sh /ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("C:\\Users\\me\\ws\\", "ccnavi-review.sh"), "sh C:/Users/me/ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("/my ws", "ccnavi-review.sh"), 'sh "/my ws/.ccnavi/scripts/ccnavi-review.sh"');
  assert.equal(scriptCommand("/it's", "ccnavi-review.sh"), `sh "/it's/.ccnavi/scripts/ccnavi-review.sh"`);
  assert.equal(scriptCommand("/a$b", "x.sh"), `sh '/a$b/.ccnavi/scripts/x.sh'`);
});

test("CB-T19d レビュー済みの連絡の文は、親が親のワークツリーで confirm を単体で打つこととマージリクエストの URL を言い、マーカーは置かせない", () => {
  const text = reviewedPrompt("/ws", "i0001", 2, "2（設計）", "/ws/.claude/worktrees/i0001", "https://example.com/pull/18#issuecomment-5");
  assert.ok(
    text.startsWith(
      "[ccnavi] 利用者が親 i0001 のフェーズ 2（設計） のレビューを終えた。\n- マージリクエスト: https://example.com/pull/18#issuecomment-5\n親（メインエージェント）が、親のワークツリー /ws/.claude/worktrees/i0001 で 'sh /ws/.ccnavi/scripts/ccnavi-review.sh confirm --phase 2' を打ち、",
    ),
    text,
  );
  // 止めている間の例外は sh …ccnavi-review.sh の形を単体で打ったときだけ（設計 9.8）。cd と連結する形へ誘導しない。
  // サブエージェントには常に禁止（9.12）
  assert.ok(text.includes("cd や他のコマンドと連結せず、単体の Bash で打つ（cwd が /ws/.claude/worktrees/i0001 でなければ、先に cd だけを別の Bash で打つ）"));
  assert.ok(text.includes("サブエージェントには渡さない"));
  assert.ok(!text.includes("&&"));
  // 人の判断（--reviewed / decide）を代行させず、confirm が返す道を先取りしない
  assert.ok(!text.includes("--reviewed"));
  assert.ok(!text.includes("decide"));
  assert.ok(!text.includes("依頼し直す"));
  assert.ok(text.includes("confirm が一覧と次の道を返すので、それに従う"));
  // マージリクエストが無ければ行ごと省き、フェーズの表示名が無ければ番号で言う。Windows の区切りは / に寄せる
  const bare = reviewedPrompt("C:\\ws", "i0001", 3, "", "C:\\ws\\.claude\\worktrees\\i0001", "");
  assert.ok(!bare.includes("マージリクエスト:"));
  assert.ok(bare.includes("フェーズ 3 のレビューを終えた"));
  assert.ok(bare.includes("親のワークツリー C:/ws/.claude/worktrees/i0001 で 'sh C:/ws/.ccnavi/scripts/ccnavi-review.sh confirm --phase 3'"));
});
