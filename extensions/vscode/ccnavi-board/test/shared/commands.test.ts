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

test("CB-T18 承認は子プロセスの引数で、previewは見るだけ、yesは見せた識別子をそのまま返す", () => {
  assert.deepEqual(previewArgs(), ["--agree", "--preview", "--json"]);
  assert.deepEqual(approveArgs(["i0001", "i0001-01-01"], "ab12"), [
    "--entry",
    "board",
    "user",
    "agree",
    "--yes",
    "i0001,i0001-01-01",
    "--digest",
    "ab12",
    "--json",
  ]);
  // ターミナルに`user agree`を送る経路は消した。y/Nをターミナルで押す形には戻さない。
  assert.equal((commands as Record<string, unknown>).approveCommand, undefined);
});

test("CB-T18b previewに識別子を並べると、その分だけが対象になる", () => {
  assert.deepEqual(previewArgs(["i0002", "i0002-01-01"]), [
    "--agree",
    "--preview",
    "--json",
    "i0002",
    "i0002-01-01",
  ]);
  assert.deepEqual(previewArgs([]), previewArgs());
});

test("CB-T18c yesは見せた識別子と、そのときの絞りを分けて渡す", () => {
  // 絞り込み中。見せたのは1件で、絞りも同じ1件。
  assert.deepEqual(approveArgs(["i0002"], "ab12", ["i0002"]), [
    "--entry",
    "board",
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
    "--entry",
    "board",
    "user",
    "agree",
    "--yes",
    "i0002,i0002-01-01",
    "--digest",
    "ab12",
    "--json",
  ]);
});

test("CB-T19a ユーザ専用のshの名前。書く側はccnavi-user-decide.shで、読む側はccnavi-review.shに残る", () => {
  assert.equal(PUSH_APPROVED_SCRIPT, ".ccnavi/scripts/ccnavi-user-push-approved.sh");
  assert.equal(USER_DECIDE_SCRIPT, ".ccnavi/scripts/ccnavi-user-decide.sh");
  assert.equal(REVIEW_SCRIPT, ".ccnavi/scripts/ccnavi-review.sh");
});

test("CB-T19 decideの引数。一覧は --preview、選んだ対応方針はJSONと見せたダイジェストで渡す", () => {
  assert.deepEqual(decidePreviewArgs(2), ["decide", "2", "--preview"]);
  // ccnavi-user-decide.shは第1引数をフェーズ番号として読む。使い方は`<N> [--choices …]`。先頭にdecideを付けると終了コード2で止まる
  assert.deepEqual(decideArgs(1, { u1: "keep", "https://x/y#z": "fix" }, "d0"), [
    "1",
    "--choices",
    '{"u1":"keep","https://x/y#z":"fix"}',
    "--digest",
    "d0",
  ]);
});

test("CB-T19b 承認のpushのshは、ワークスペースルートからの絶対パスで送る", () => {
  // 絶対パスなので、前のコマンドが別の場所へcdしたターミナルでも届く。
  assert.equal(pushApprovedCommand("/ws"), "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'");
  // Windowsの区切りは "/" に直す（Git Bashが読める形）。
  assert.equal(
    pushApprovedCommand("C:\\Users\\x\\ws"),
    "sh 'C:/Users/x/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'",
  );
  // 単引用符を含むパスは分けて囲む。
  assert.equal(
    pushApprovedCommand("/tmp/it's ws"),
    `sh '/tmp/it'\\''s ws/.ccnavi/scripts/ccnavi-user-push-approved.sh'`,
  );
  // 親を並べると、その親子のチケットだけをコミットしてpushする（フローの保存の後）。
  assert.equal(
    pushApprovedCommand("/ws", ["i0001"]),
    "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh' 'i0001'",
  );
  // 承認が書いた・消したパスを並べると、そのパスだけをコミットしてpushする（承認の後）。
  assert.equal(
    pushApprovedCommand("/ws", [], [".claude/worktrees/i0001/.ccnavi/approved/doing/i0001.md", "a b/x's.md"]),
    "sh '/ws/.ccnavi/scripts/ccnavi-user-push-approved.sh' --path '.claude/worktrees/i0001/.ccnavi/approved/doing/i0001.md' --path 'a b/x'\\''s.md'",
  );
});


test("CB-T287 write-and-pushの対象かはwrite-and-push familyのtargetで読む。頭のwrite-and-push 1がなければ空、CRは除く", () => {
  assert.equal(writeAndPushTargetOf("write-and-push 1\nfamily i0001\ntarget yes\ntree /ws/x y\n"), "yes");
  assert.equal(writeAndPushTargetOf("write-and-push 1\r\nfamily i0001\r\ntarget no\r\n"), "no");
  assert.equal(writeAndPushTargetOf("usage: ccnavi ...\n"), "");
  assert.equal(writeAndPushTargetOf("write-and-push 1\nfamily i0001\n"), "");
});

test("CB-T19c 文面のshのパスは実行ファイルのscript_commandと同じ引用の規則。空白や記号があるときだけ引用する（rootを解くのは呼び手）", () => {
  assert.equal(scriptCommand("/ws", "ccnavi-review.sh"), "sh /ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("C:\\Users\\me\\ws\\", "ccnavi-review.sh"), "sh C:/Users/me/ws/.ccnavi/scripts/ccnavi-review.sh");
  assert.equal(scriptCommand("/my ws", "ccnavi-review.sh"), 'sh "/my ws/.ccnavi/scripts/ccnavi-review.sh"');
  assert.equal(scriptCommand("/it's", "ccnavi-review.sh"), `sh "/it's/.ccnavi/scripts/ccnavi-review.sh"`);
  assert.equal(scriptCommand("/a$b", "x.sh"), `sh '/a$b/.ccnavi/scripts/x.sh'`);
});

test("CB-T19d 相談のプロンプトは、親・フェーズ・MR・親のワークツリーを言い、読み取り専用のdecide <N> --previewだけを単体で打たせ、確定の操作を禁じる", () => {
  const text = consultPrompt({
    root: "/ws",
    parent: "i0001",
    phase: 2,
    mrUrl: "https://example.com/pull/18",
    parentTree: "/ws/.claude/worktrees/i0001",
  });
  assert.ok(text.includes("親i0001のフェーズ2"));
  assert.ok(text.includes("- MR: https://example.com/pull/18"));
  assert.ok(text.includes("- 親のワークツリー: /ws/.claude/worktrees/i0001"));
  // 許すのはpreviewだけ。cdと連結せず、cwdが違えば先にcdだけを別のBashで
  assert.ok(text.includes("読み取り専用の 'sh /ws/.ccnavi/scripts/ccnavi-review.sh decide 2 --preview' だけを単体のBashで実行する"));
  assert.ok(text.includes("cwdが/ws/.claude/worktrees/i0001でなければ、先にcdだけを別のBashで実行する"));
  assert.ok(!text.includes("&&"));
  // 未解決の指摘を1件ずつ要約し、案（3つ）と理由を返させる
  assert.ok(text.includes("1件ずつ要約し"));
  assert.ok(text.includes("対応しない／このフェーズで直す／issueに回す"));
  // 確定の操作は禁じる。決めるのはユーザで、ボードの「レビュー完了」から
  assert.ok(text.includes("ccnavi-user-decide.sh、ccnavi-review.sh confirm、その他の状態を変える操作は実行しない"));
  assert.ok(text.includes("ボードの「レビュー完了」から行う"));
  // 実行してよい形として書いた行（'sh …' で囲んだもの）はpreviewとticket statusだけ
  const runnable = [...text.matchAll(/'(sh [^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(runnable, [
    "sh /ws/.ccnavi/scripts/ccnavi-review.sh decide 2 --preview",
    "sh /ws/.ccnavi/scripts/ccnavi-ticket.sh status i0001",
  ]);
  assert.ok(!text.includes("--choices") && !text.includes("--digest") && !text.includes("--yes"));
  // MRが無ければ行ごと省く。Windowsの区切りは / にそろえる
  const bare = consultPrompt({ root: "C:\\ws", parent: "i0001", phase: 3, mrUrl: "", parentTree: "C:\\ws\\.claude\\worktrees\\i0001" });
  assert.ok(!bare.includes("MR:"));
  assert.ok(bare.includes("- 親のワークツリー: C:/ws/.claude/worktrees/i0001"));
  assert.ok(bare.includes("'sh C:/ws/.ccnavi/scripts/ccnavi-review.sh decide 3 --preview'"));
});

test("CB-T19e マージ状況の引数と片付けのコマンド。mr-stateとsync retiredは読むだけ、片付けはcdから始まり、パスと親を単引用符で引用する", () => {
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
  // Windowsの区切りは / にそろえ、末尾の区切りは落とす
  assert.equal(retireCommand("C:\\Users\\me\\ws\\", "i0001"), "cd 'C:/Users/me/ws' && sh 'C:/Users/me/ws/.ccnavi/scripts/ccnavi-sync.sh' 'i0001'");
  // 破棄した親の記録を消すコマンドは、ボードが送らずコピーするだけ。引用の規則は文面のshと同じ
  assert.equal(abandonCommand("/ws", "i0001"), "sh /ws/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon i0001");
  assert.equal(abandonCommand("/my ws", "i0001"), 'sh "/my ws/.ccnavi/scripts/ccnavi-launcher.sh" worktree retire --abandon i0001');
  // ダイアログはsyncが何をするかを先に言う
  const text = retireConfirmText("i0001");
  assert.ok(text.startsWith("ccnavi-sync.sh i0001を実行します。"));
  assert.ok(text.includes("リモートの取り込みも行い、MRがマージ済みかを確かめ直したうえで、条件を満たすものだけを片付けます"));
  assert.ok(text.includes("結果はターミナルで確認してください"));
});

test("CB-T317 リスクの配点の差し替え: 共通は --risk、ワークスペースはself=、プロジェクトは名前= で --project-risk-fileに渡す", () => {
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

test("CB-T531 フェーズ定義の共通の差し替え: 共通の設定は --phases、ワークスペースとプロジェクトは --project-phases-fileの名前=", () => {
  assert.deepEqual(overrideArgs({ kind: "phases", path: "/tmp/p.yml" }), ["--phases", "/tmp/p.yml"]);
  assert.deepEqual(overrideArgs({ kind: "layerPhases", name: "self", path: "/tmp/p.yml" }), [
    "--project-phases-file",
    "self=/tmp/p.yml",
  ]);
});

test("CB-T365 計画編集タブの引数。読むだけは --plan-order <親> --json（線を変えたら --orderに元の番号での先行のJSON）、保存は --order・--expect・--write・--json", () => {
  assert.deepEqual(planOrderArgs("i0001"), ["--plan-order", "i0001", "--json"]);
  assert.deepEqual(planOrderArgs("i0001", {}), ["--plan-order", "i0001", "--json"], "線を変えていなければ --orderを付けない");
  const order = { plan: { "1": [], "2": [1, 3], "3": [1] } };
  assert.deepEqual(planOrderArgs("i0001", order), ["--plan-order", "i0001", "--json", "--order", JSON.stringify(order)]);
  assert.deepEqual(planOrderWriteArgs("i0001", order, "abc"), ["--plan-order", "i0001", "--order", JSON.stringify(order), "--expect", "abc", "--write", "--json"]);
  // 保存は線を変えていなくても --orderを付ける（実行ファイルは --writeに --orderを求める）
  assert.deepEqual(planOrderWriteArgs("i0001", {}, "abc"), ["--plan-order", "i0001", "--order", "{}", "--expect", "abc", "--write", "--json"]);
});

test("CB-T1170 claude -pは、プロンプトのファイルを標準入力に渡す1行にする。パスは単引用符で引用し、Windowsのパスは / にする", () => {
  assert.equal(claudeRunCommand("/tmp/ccnavi-prompt-x/prompt.txt"), "claude -p --permission-mode auto < '/tmp/ccnavi-prompt-x/prompt.txt'");
  assert.equal(
    claudeRunCommand("C:\\Users\\x y\\prompt.txt"),
    "claude -p --permission-mode auto < 'C:/Users/x y/prompt.txt'",
  );
  // 単引用符・$・バッククォート・日本語を含むパスも1語のまま
  assert.equal(claudeRunCommand("/tmp/a'b/p.txt"), "claude -p --permission-mode auto < '/tmp/a'\\''b/p.txt'");
  assert.equal(claudeRunCommand("/tmp/$x `y`/プロンプト.txt"), "claude -p --permission-mode auto < '/tmp/$x `y`/プロンプト.txt'");
});
