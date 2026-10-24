import { test } from "node:test";
import assert from "node:assert/strict";
import * as commands from "../../src/core/commands.js";
import {
  approveArgs,
  writeAndPushTargetOf,
  decideArgs,
  decidePreviewArgs,
  PUSH_APPROVED_SCRIPT,
  REVIEW_SCRIPT,
  USER_DECIDE_SCRIPT,
  overrideArgs,
  planOrderArgs,
  planOrderWriteArgs,
  previewArgs,
  claudeRunCommand,
  pushApprovedCommand,
  abandonCommand,
  consultPrompt,
  mrStateArgs,
  retireCommand,
  retireConfirmText,
  syncRetiredArgs,
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
  assert.deepEqual(previewArgs(), ["--agree", "--preview", "--json"]);
  assert.deepEqual(approveArgs(["i0001", "i0001-01-01"], "ab12"), [
    "user",
    "agree",
    "--yes",
    "i0001,i0001-01-01",
    "--digest",
    "ab12",
    "--json",
  ]);
  // ターミナルに `user agree` を送る経路は消した。y/N をターミナルで押す形には戻さない。
  assert.equal((commands as Record<string, unknown>).approveCommand, undefined);
});

test("CB-T18b preview に識別子を並べると、その分だけが対象になる", () => {
  assert.deepEqual(previewArgs(["i0002", "i0002-01-01"]), [
    "--agree",
    "--preview",
    "--json",
    "i0002",
    "i0002-01-01",
  ]);
  assert.deepEqual(previewArgs([]), previewArgs());
});

test("CB-T18c yes は見せた識別子と、そのときの絞りを分けて渡す", () => {
  // 絞り込み中。見せたのは 1 件で、絞りも同じ 1 件。
  assert.deepEqual(approveArgs(["i0002"], "ab12", ["i0002"]), [
    "user",
    "agree",
    "--yes",
    "i0002",
    "--digest",
    "ab12",
    "--json",
    "i0002",
  ]);
  // 絞り込み無し。絞りは空で、実行ファイルは絞らないときの対象と見せた識別子を比べる。
  assert.deepEqual(approveArgs(["i0002", "i0002-01-01"], "ab12"), [
    "user",
    "agree",
    "--yes",
    "i0002,i0002-01-01",
    "--digest",
    "ab12",
    "--json",
  ]);
});

test("CB-T19a ユーザ専用の sh の名前。書く側は ccnavi-user-decide.sh で、読む側は ccnavi-review.sh に残る", () => {
  assert.equal(PUSH_APPROVED_SCRIPT, ".ccnavi/scripts/ccnavi-user-push-approved.sh");
  assert.equal(USER_DECIDE_SCRIPT, ".ccnavi/scripts/ccnavi-user-decide.sh");
  assert.equal(REVIEW_SCRIPT, ".ccnavi/scripts/ccnavi-review.sh");
});

test("CB-T19 decide の引数。一覧は --preview、選んだ対応方針は JSON と見せたダイジェストで渡す", () => {
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

test("CB-T19b 承認の push の sh は、ワークスペースルートからの絶対パスで送る", () => {
  // 絶対パスなので、前のコマンドが別の場所へ cd したターミナルでも届く。
  assert.equal(pushApprovedCommand("/ws"), "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'");
  // Windows の区切りは "/" に直す（Git Bash が読める形）。
  assert.equal(
    pushApprovedCommand("C:\\Users\\x\\ws"),
    "sh 'C:/Users/x/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'",
  );
  // 単引用符を含むパスは分けて囲む。
  assert.equal(
    pushApprovedCommand("/tmp/it's ws"),
    `sh '/tmp/it'\\''s ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'`,
  );
  // 親を並べると、その親子のチケットだけをコミットして push する（フローの保存の後）。
  assert.equal(
    pushApprovedCommand("/ws", ["i0001"]),
    "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh' 'i0001'",
  );
  // 承認が書いた・消したパスを並べると、そのパスだけをコミットして push する（承認の後）。
  assert.equal(
    pushApprovedCommand("/ws", [], [".claude/worktrees/i0001/.ccnavi/approved/doing/i0001.md", "a b/x's.md"]),
    "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh' --path '.claude/worktrees/i0001/.ccnavi/approved/doing/i0001.md' --path 'a b/x'\\''s.md'",
  );
});


test("CB-T287 write-and-push の対象かは write-and-push family の target で読む。頭の write-and-push 1 がなければ空、CR は除く", () => {
  assert.equal(writeAndPushTargetOf("write-and-push 1\nfamily i0001\ntarget yes\ntree /ws/x y\n"), "yes");
  assert.equal(writeAndPushTargetOf("write-and-push 1\r\nfamily i0001\r\ntarget no\r\n"), "no");
  assert.equal(writeAndPushTargetOf("usage: ccnavi ...\n"), "");
  assert.equal(writeAndPushTargetOf("write-and-push 1\nfamily i0001\n"), "");
});

test("CB-T19c 文面の sh のパスは実行ファイルの script_command と同じ引用の規則。空白や記号があるときだけ引用する（root を解くのは呼び手）", () => {
  assert.equal(scriptCommand("/ws", "ccnavi-review.sh"), "sh /ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("C:\\Users\\me\\ws\\", "ccnavi-review.sh"), "sh C:/Users/me/ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("/my ws", "ccnavi-review.sh"), 'sh "/my ws/.ccnavi/scripts/ccnavi-review.sh"');
  assert.equal(scriptCommand("/it's", "ccnavi-review.sh"), `sh "/it's/.ccnavi/scripts/ccnavi-review.sh"`);
  assert.equal(scriptCommand("/a$b", "x.sh"), `sh '/a$b/.ccnavi/scripts/x.sh'`);
});

test("CB-T19d 相談のプロンプトは、親・フェーズ・MR・親のワークツリーを言い、読み取り専用の decide <N> --preview だけを単体で打たせ、確定の操作を禁じる", () => {
  const text = consultPrompt({
    root: "/ws",
    parent: "i0001",
    phase: 2,
    mrUrl: "https://example.com/pull/18",
    parentTree: "/ws/.claude/worktrees/i0001",
  });
  assert.ok(text.includes("親 i0001 のフェーズ 2"));
  assert.ok(text.includes("- マージリクエスト: https://example.com/pull/18"));
  assert.ok(text.includes("- 親のワークツリー: /ws/.claude/worktrees/i0001"));
  // 許すのは preview だけ。cd と連結せず、cwd が違えば先に cd だけを別の Bash で
  assert.ok(text.includes("読み取り専用の 'sh /ws/.ccnavi/scripts/ccnavi-review.sh decide 2 --preview' だけを単体の Bash で実行する"));
  assert.ok(text.includes("cwd が /ws/.claude/worktrees/i0001 でなければ、先に cd だけを別の Bash で実行する"));
  assert.ok(!text.includes("&&"));
  // 未解決の指摘を 1 件ずつ要約し、案（3 つ）と理由を返させる
  assert.ok(text.includes("1 件ずつ要約し"));
  assert.ok(text.includes("対応しない／このフェーズで直す／issue に回す"));
  // 確定の操作は禁じる。決めるのはユーザで、ボードの「レビュー完了」から
  assert.ok(text.includes("ccnavi-user-decide.sh、ccnavi-review.sh confirm、その他の状態を変える操作は実行しない"));
  assert.ok(text.includes("ボードの『レビュー完了』から行う"));
  // 実行してよい形として書いた行（'sh …' で囲んだもの）は preview と ticket status だけ
  const runnable = [...text.matchAll(/'(sh [^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(runnable, [
    "sh /ws/.ccnavi/scripts/ccnavi-review.sh decide 2 --preview",
    "sh /ws/.ccnavi/scripts/ccnavi-ticket.sh status i0001",
  ]);
  assert.ok(!text.includes("--choices") && !text.includes("--digest") && !text.includes("--yes"));
  // マージリクエストが無ければ行ごと省く。Windows の区切りは / にそろえる
  const bare = consultPrompt({ root: "C:\\ws", parent: "i0001", phase: 3, mrUrl: "", parentTree: "C:\\ws\\.claude\\worktrees\\i0001" });
  assert.ok(!bare.includes("マージリクエスト:"));
  assert.ok(bare.includes("- 親のワークツリー: C:/ws/.claude/worktrees/i0001"));
  assert.ok(bare.includes("'sh C:/ws/.ccnavi/scripts/ccnavi-review.sh decide 3 --preview'"));
});

test("CB-T19e マージ状況の引数と片付けのコマンド。mr-state と sync retired は読むだけ、片付けは cd から始まり、パスと親を単引用符で引用する", () => {
  assert.deepEqual(mrStateArgs(12), ["mr-state", "12"]);
  assert.deepEqual(syncRetiredArgs(), ["sync", "retired"]);
  assert.equal(
    retireCommand("/ws", "i0001"),
    "cd '/ws' && sh '/ws/.ccnavi/scripts/ccnavi-sync.sh' 'i0001'",
  );
  // 空白・引用符・シェルの記号を含むパスでも壊れない（単引用符の中では、単引用符だけを置き換えれば足りる）
  assert.equal(
    retireCommand("/my ws/it's;rm -rf $HOME", "i0001"),
    `cd '/my ws/it'\\''s;rm -rf $HOME' && sh '/my ws/it'\\''s;rm -rf $HOME/.ccnavi/scripts/ccnavi-sync.sh' 'i0001'`,
  );
  // Windows の区切りは / にそろえ、末尾の区切りは落とす
  assert.equal(retireCommand("C:\\Users\\me\\ws\\", "i0001"), "cd 'C:/Users/me/ws' && sh 'C:/Users/me/ws/.ccnavi/scripts/ccnavi-sync.sh' 'i0001'");
  // 捨てた親の記録を消すコマンドは、ボードが送らずコピーするだけ。引用の規則は文面の sh と同じ
  assert.equal(abandonCommand("/ws", "i0001"), "sh /ws/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon i0001");
  assert.equal(abandonCommand("/my ws", "i0001"), 'sh "/my ws/.ccnavi/scripts/ccnavi-launcher.sh" worktree retire --abandon i0001');
  // ダイアログは sync が何をするかを先に言う
  const text = retireConfirmText("i0001");
  assert.ok(text.startsWith("ccnavi-sync.sh i0001 を実行します。"));
  assert.ok(text.includes("リモートの取り込みも行い、MR がマージ済みかを確かめ直したうえで、条件を満たすものだけを片付けます"));
  assert.ok(text.includes("結果はターミナルで確認してください"));
});

test("CB-T317 リスクの配点の差し替え: 共通は --risk、ワークスペースは self=、プロジェクトは名前= で --project-risk-file に渡す", () => {
  assert.deepEqual(overrideArgs({ kind: "risk", path: "/tmp/r.yml" }), ["--risk", "/tmp/r.yml"]);
  assert.deepEqual(overrideArgs({ kind: "layerRisk", name: "self", path: "/tmp/r.yml" }), [
    "--project-risk-file",
    "self=/tmp/r.yml",
  ]);
  assert.deepEqual(overrideArgs({ kind: "layerRisk", name: "lib", path: "/tmp/r.yml" }), [
    "--project-risk-file",
    "lib=/tmp/r.yml",
  ]);
  // ルールとフェーズ定義の差し替えは従来のまま
  assert.deepEqual(overrideArgs({ kind: "self", path: "/tmp/x.yml" }), ["--project-rules-file", "self=/tmp/x.yml"]);
  assert.deepEqual(overrideArgs({ kind: "layerPhases", name: "lib", path: "/tmp/x.yml" }), [
    "--project-phases-file",
    "lib=/tmp/x.yml",
  ]);
});

test("CB-T531 フェーズ定義の共通の差し替え: 共通の設定は --phases、ワークスペースとプロジェクトは --project-phases-file の名前=", () => {
  assert.deepEqual(overrideArgs({ kind: "phases", path: "/tmp/p.yml" }), ["--phases", "/tmp/p.yml"]);
  assert.deepEqual(overrideArgs({ kind: "layerPhases", name: "self", path: "/tmp/p.yml" }), [
    "--project-phases-file",
    "self=/tmp/p.yml",
  ]);
});

test("CB-T365 計画編集タブの引数。読むだけは --plan-order <親> --json（線を変えたら --order に元の番号での先行の JSON）、保存は --order・--expect・--write・--json", () => {
  assert.deepEqual(planOrderArgs("i0001"), ["--plan-order", "i0001", "--json"]);
  assert.deepEqual(planOrderArgs("i0001", {}), ["--plan-order", "i0001", "--json"], "線を変えていなければ --order を付けない");
  const order = { plan: { "1": [], "2": [1, 3], "3": [1] } };
  assert.deepEqual(planOrderArgs("i0001", order), ["--plan-order", "i0001", "--json", "--order", JSON.stringify(order)]);
  assert.deepEqual(planOrderWriteArgs("i0001", order, "abc"), ["--plan-order", "i0001", "--order", JSON.stringify(order), "--expect", "abc", "--write", "--json"]);
  // 保存は線を変えていなくても --order を付ける（実行ファイルは --write に --order を求める）
  assert.deepEqual(planOrderWriteArgs("i0001", {}, "abc"), ["--plan-order", "i0001", "--order", "{}", "--expect", "abc", "--write", "--json"]);
});

test("CB-T1170 claude -p は、プロンプトのファイルを標準入力に渡す 1 行にする。パスは単引用符で引用し、Windows のパスは / にする", () => {
  assert.equal(claudeRunCommand("/tmp/ccnavi-prompt-x/prompt.txt"), "claude -p --permission-mode auto < '/tmp/ccnavi-prompt-x/prompt.txt'");
  assert.equal(
    claudeRunCommand("C:\\Users\\x y\\prompt.txt"),
    "claude -p --permission-mode auto < 'C:/Users/x y/prompt.txt'",
  );
  // 単引用符・$・バッククォート・日本語を含むパスも 1 語のまま
  assert.equal(claudeRunCommand("/tmp/a'b/p.txt"), "claude -p --permission-mode auto < '/tmp/a'\\''b/p.txt'");
  assert.equal(claudeRunCommand("/tmp/$x `y`/プロンプト.txt"), "claude -p --permission-mode auto < '/tmp/$x `y`/プロンプト.txt'");
});
