/**
 * 取り込み待ち画面が読む実行ファイルの答え（`user handoff --json`）の読み取りと、画面の判断
 * （`src/core/handoffmodel.ts`）を確かめる。実行ファイルが無くても試せる。
 * 取り込めるか・錠・端末の案内・選択の引き継ぎは、手で書いた答えから決める。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  blockReason,
  findTree,
  foldPath,
  HANDOFF_VERSION,
  isImportable,
  keyLabel,
  lockForTree,
  LOCKED_KINDS,
  lockReason,
  parseHandoffList,
  parseHandoffResult,
  reconcileSelection,
  refusalReason,
  resultSummary,
  samePath,
  selectablePaths,
  terminalCommand,
  isTerminalCommand,
  treeKey,
  treeOptions,
} from "../../src/core/handoffmodel.js";
import type { HandoffEntry } from "../../src/core/handoff-view.js";
import type { Lock } from "../../src/core/lock.js";
import { fixture } from "../helpers/fixture.js";
import { entry, list, sampleEntries } from "../helpers/handoff.js";

const OPEN: Lock = { locked: false, reason: "", doing: [] };
const LOCKED: Lock = { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] };

/** 見るだけの答えの JSON（実行ファイルの標準出力と同じ文字列） */
function listText(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 1,
    tree: "/ws",
    ignored: 3,
    entries: [
      {
        path: ".ccnavi/config/project/templates/login.yml",
        kind: "template",
        state: "new",
        board: true,
        selected: true,
        errors: [],
        warnings: ["description に TODO が残っている"],
        diff: "+name: login\n",
        entry_sha: "ab12cd34ab12cd34",
      },
    ],
    expect: "9f".repeat(32),
    ok: true,
    ...overrides,
  });
}

test("CB-T1104 見るだけの答えを読む。対象の欄・対象外の件数・expect・ok を型どおりに持つ", () => {
  const parsed = parseHandoffList(listText());
  assert.ok(parsed.ok);
  const value = parsed.value;
  assert.equal(value.version, HANDOFF_VERSION);
  assert.equal(value.tree, "/ws");
  assert.equal(value.ignored, 3);
  assert.equal(value.expect, "9f".repeat(32));
  assert.equal(value.ok, true);
  assert.deepEqual(value.entries, [
    {
      path: ".ccnavi/config/project/templates/login.yml",
      kind: "template",
      state: "new",
      board: true,
      selected: true,
      errors: [],
      warnings: ["description に TODO が残っている"],
      diff: "+name: login\n",
      entry_sha: "ab12cd34ab12cd34",
    },
  ]);
});

test("CB-T1105 見るだけの答えが読めないとき、文面で失敗する（版違い・欄の型違い・path の無い対象・JSON でない）", () => {
  assert.deepEqual(parseHandoffList(listText({ version: 2 })), {
    ok: false,
    error: `取り込み待ちの JSON の形が違います（拡張は ${HANDOFF_VERSION}、実行ファイルは 2）`,
  });
  assert.equal(parseHandoffList(listText({ entries: "x" })).ok, false);
  assert.equal(parseHandoffList(listText({ expect: 1 })).ok, false);
  assert.equal(parseHandoffList(listText({ ok: "yes" })).ok, false);
  assert.equal(parseHandoffList(listText({ entries: [{ kind: "docs" }] })).ok, false);
  assert.equal(parseHandoffList("これは JSON ではない").ok, false);
  assert.deepEqual(parseHandoffList("[1]"), { ok: false, error: "JSON の最上位がオブジェクトではありません" });
  // entries を持たない失敗の答えは、error をそのまま返す
  assert.deepEqual(parseHandoffList('{"version":1,"ok":false,"error":"ツリーを解けない"}'), { ok: false, error: "ツリーを解けない" });
});

test("CB-T1106 知らない種類・状態の対象も読む。欄が足りなければ空で補い、board は真のときだけ真", () => {
  const parsed = parseHandoffList(listText({ entries: [{ path: "x.md", kind: "future", state: "later" }] }));
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.value.entries[0], {
    path: "x.md",
    kind: "future",
    state: "later",
    board: false,
    selected: false,
    errors: [],
    warnings: [],
    diff: "",
    entry_sha: "",
  });
});

test("CB-T1107 取り込みの答えを読む。applied・removed_drafts（文字列でも {path} でも）・kept_drafts", () => {
  const text = JSON.stringify({
    version: 1,
    ok: true,
    applied: [{ path: "a.yml", kind: "template" }, { path: "b.md", kind: "docs" }],
    removed_drafts: ["wip/handoff/a.yml", { path: "wip/handoff/b.md" }],
    kept_drafts: [{ path: "wip/handoff/c.md", reason: "取り込み後に下書きが編集されていた" }],
  });
  const parsed = parseHandoffResult(text);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.value, {
    ok: true,
    applied: [{ path: "a.yml", kind: "template" }, { path: "b.md", kind: "docs" }],
    removedDrafts: ["wip/handoff/a.yml", "wip/handoff/b.md"],
    keptDrafts: [{ path: "wip/handoff/c.md", reason: "取り込み後に下書きが編集されていた" }],
  });
  // 空の配列（成功したが何も消さず、何も残さなかった）
  const empty = parseHandoffResult(JSON.stringify({ version: 1, ok: true, applied: [], removed_drafts: [], kept_drafts: [] }));
  assert.ok(empty.ok);
  assert.deepEqual(empty.value, { ok: true, applied: [], removedDrafts: [], keptDrafts: [] });
});

test("CB-T1108 取り込みの失敗の答え（ok が偽・error）は、失敗の結果として読む。読めない答えは読み取りの失敗にする", () => {
  assert.deepEqual(parseHandoffResult('{"version":1,"ok":false,"error":"--expect が合わない"}'), {
    ok: true,
    value: { ok: false, error: "--expect が合わない" },
  });
  assert.deepEqual(parseHandoffResult('{"version":1,"ok":false}'), {
    ok: true,
    value: { ok: false, error: "取り込めませんでした（理由は返りませんでした）" },
  });
  assert.equal(parseHandoffResult('{"version":2,"ok":true,"applied":[]}').ok, false);
  assert.equal(parseHandoffResult('{"version":1,"ok":true}').ok, false, "applied が無い成功は読めない");
  assert.equal(parseHandoffResult('{"version":1}').ok, false);
  assert.equal(parseHandoffResult("{").ok, false);
});

test("CB-T1109 取り込めるのは board が真・状態が new か changed・error 無しの対象だけ", () => {
  assert.equal(isImportable(entry()), true);
  assert.equal(isImportable(entry({ state: "changed" })), true);
  assert.equal(isImportable(entry({ state: "unchanged" })), false);
  assert.equal(isImportable(entry({ state: "conflict" })), false);
  assert.equal(isImportable(entry({ state: "rejected" })), false);
  assert.equal(isImportable(entry({ state: "later" })), false, "知らない状態は取り込めると決めない");
  assert.equal(isImportable(entry({ errors: ["x"] })), false);
  assert.equal(isImportable(entry({ board: false, kind: "defense" })), false);
  // warnings だけなら取り込める
  assert.equal(isImportable(entry({ warnings: ["TODO"] })), true);
});

test("CB-T1110 取り込めない対象には理由を言う。端末のみ・conflict・rejected・unchanged・error・知らない状態", () => {
  assert.equal(refusalReason(entry()), "");
  assert.match(refusalReason(entry({ board: false, kind: "defense" })), /ボードからは取り込めません。端末で取り込みます/);
  assert.match(refusalReason(entry({ state: "conflict" })), /conflict/);
  assert.match(refusalReason(entry({ state: "rejected" })), /rejected/);
  assert.match(refusalReason(entry({ state: "unchanged" })), /同じ内容/);
  assert.match(refusalReason(entry({ errors: ["x"] })), /error/);
  assert.match(refusalReason(entry({ state: "later" })), /知らない状態です（later）/);
  // 端末のみの対象は、状態より先に端末で取り込むことを言う
  assert.match(refusalReason(entry({ board: false, state: "conflict" })), /端末で取り込みます/);
});

test("CB-T1111 作業中の錠は template と layer-config にだけ掛かる。docs と defense には掛けない", () => {
  assert.deepEqual([...LOCKED_KINDS], ["template", "layer-config"]);
  assert.equal(lockReason("template", LOCKED), LOCKED.reason);
  assert.equal(lockReason("layer-config", LOCKED), LOCKED.reason);
  assert.equal(lockReason("docs", LOCKED), "");
  assert.equal(lockReason("defense", LOCKED), "");
  assert.equal(lockReason("template", OPEN), "");
  // 取り込めない理由が先。取り込めるのに錠が掛かっていれば錠の理由
  assert.equal(blockReason(entry(), OPEN), "");
  assert.equal(blockReason(entry(), LOCKED), LOCKED.reason);
  assert.equal(blockReason(entry({ kind: "docs" }), LOCKED), "");
  assert.match(blockReason(entry({ state: "conflict" }), LOCKED), /conflict/);
});

test("CB-T1112 選べる対象は、取り込めて錠が止めないもの。錠が掛かると template と layer-config が外れ、docs は残る", () => {
  const entries = sampleEntries();
  assert.deepEqual(selectablePaths(entries, OPEN), [".ccnavi/config/project/templates/login.yml", ".ccnavi/config/project/rules.yml", ".ccnavi/docs/guide.md"]);
  assert.deepEqual(selectablePaths(entries, LOCKED), [".ccnavi/docs/guide.md"]);
});

test("CB-T1113 読み直したあとの選択は、同じパスで entry_sha が同じ、いまも取り込める対象だけ残す", () => {
  const before = new Map([
    [".ccnavi/config/project/templates/login.yml", "a".repeat(16)],
    [".ccnavi/config/project/rules.yml", "b".repeat(16)],
    [".ccnavi/docs/guide.md", "c".repeat(16)],
    [".ccnavi/docs/gone.md", "0".repeat(16)],
  ]);
  const entries: HandoffEntry[] = [
    entry(),
    entry({ path: ".ccnavi/config/project/rules.yml", kind: "layer-config", state: "changed", entry_sha: "9".repeat(16) }),
    entry({ path: ".ccnavi/docs/guide.md", kind: "docs", state: "conflict", entry_sha: "c".repeat(16) }),
  ];
  const kept = reconcileSelection(before, entries, OPEN);
  assert.deepEqual([...kept.keys()], [".ccnavi/config/project/templates/login.yml"], "sha が変わった・conflict になった・消えた対象は外す");
  // 錠が掛かれば template も外れる
  assert.equal(reconcileSelection(before, entries, LOCKED).size, 0);
});

test("CB-T1114 端末で打つコマンドは sh .ccnavi/scripts/ccnavi-user-handoff.sh <パス> --yes --expect <ハッシュ>。空白や記号のあるパスは単引用符で囲む", () => {
  assert.equal(terminalCommand(".claude/hooks/guard.sh", "ab".repeat(32)), `sh .ccnavi/scripts/ccnavi-user-handoff.sh .claude/hooks/guard.sh --yes --expect ${"ab".repeat(32)}`);
  assert.equal(terminalCommand(".claude/hooks/my guard.sh", "h"), "sh .ccnavi/scripts/ccnavi-user-handoff.sh '.claude/hooks/my guard.sh' --yes --expect h");
  assert.equal(terminalCommand(".claude/it's.sh", "h"), "sh .ccnavi/scripts/ccnavi-user-handoff.sh '.claude/it'\\''s.sh' --yes --expect h");
});

test("CB-T1138 端末のコマンドは --root で下書きのあるツリーを指す。入口の sh は、ワークスペースルートを渡せば絶対パスで書く。空白・単引用符のあるツリーは POSIX の形で囲む", () => {
  assert.equal(terminalCommand(".claude/hooks/guard.sh", "h", "/ws/.claude/worktrees/a"), "sh .ccnavi/scripts/ccnavi-user-handoff.sh --root /ws/.claude/worktrees/a .claude/hooks/guard.sh --yes --expect h");
  assert.equal(terminalCommand("x.sh", "h", "C:\\Users\\me\\ws"), "sh .ccnavi/scripts/ccnavi-user-handoff.sh --root 'C:\\Users\\me\\ws' x.sh --yes --expect h");
  assert.equal(terminalCommand("x.sh", "h", "/w's"), "sh .ccnavi/scripts/ccnavi-user-handoff.sh --root '/w'\\''s' x.sh --yes --expect h");
  assert.equal(terminalCommand("x.sh", "h", "/ws", "/home/me/ws"), "sh /home/me/ws/.ccnavi/scripts/ccnavi-user-handoff.sh --root /ws x.sh --yes --expect h");
  assert.equal(terminalCommand("x.sh", "h", "", "C:\\Users\\me\\ws\\"), "sh C:/Users/me/ws/.ccnavi/scripts/ccnavi-user-handoff.sh x.sh --yes --expect h");
  for (const tree of ["", "/ws", "/my ws", "/w's"]) {
    for (const workspace of ["", "/home/me/ws", "/home/me/my ws", "C:\\Users\\me\\ws"]) {
      assert.ok(isTerminalCommand(terminalCommand("a b.sh", "h", tree, workspace)), `${tree} / ${workspace}`);
    }
  }
  assert.ok(!isTerminalCommand("rm -rf / ; sh .ccnavi/scripts/ccnavi-user-handoff.sh x"));
  assert.ok(!isTerminalCommand("sh .ccnavi/scripts/ccnavi-user-handoff.sh; rm x"));
  assert.ok(!isTerminalCommand("sh /tmp/evil.sh --root /ws x"));
  assert.ok(!isTerminalCommand("ccnavi user handoff x --yes --expect h"), "実行ファイルを直に打つ形は、断られるので写さない");
});

test("CB-T1115 パスは大文字小文字と NFC/NFD の違いを無視して比べる", () => {
  assert.equal(samePath(".CCNAVI/Docs/A.md", ".ccnavi/docs/a.md"), true);
  assert.equal(samePath("が.md", "が.md"), true);
  assert.equal(samePath("a.md", "b.md"), false);
  assert.equal(foldPath("Ａ.md"), "Ａ.md".normalize("NFC").toLowerCase());
});

test("CB-T1116 結果の 1 行。取り込んだ件数・消した下書き・残した下書き。失敗は理由を添える", () => {
  assert.equal(resultSummary({ ok: true, applied: [{ path: "a", kind: "docs" }], removedDrafts: [], keptDrafts: [] }), "取り込みました（1 件）");
  assert.equal(
    resultSummary({ ok: true, applied: [{ path: "a", kind: "docs" }, { path: "b", kind: "docs" }], removedDrafts: ["x"], keptDrafts: [{ path: "y", reason: "z" }] }),
    "取り込みました（2 件）。下書きを 1 件削除しました。下書きを 1 件残しました",
  );
  assert.equal(resultSummary({ ok: false, error: "合わない" }), "取り込めませんでした: 合わない");
});

test("CB-T1117 見るツリーの選択肢は、ワークスペース・プロジェクト・ワークツリーの順。key から名前を戻せる", () => {
  const board = fixture();
  const options = treeOptions(board);
  assert.equal(options[0].key, "main:");
  assert.equal(options[0].label, "ワークスペース");
  assert.ok(options.slice(1).every((o) => o.key.startsWith("worktree:") && o.label.startsWith("ワークツリー ")));
  assert.deepEqual(treeOptions(undefined), [{ key: "main:", label: "ワークスペース" }]);
  assert.equal(keyLabel("project:lib"), "プロジェクト lib");
  assert.equal(keyLabel("worktree:i0001"), "ワークツリー i0001");
  assert.equal(keyLabel("main:"), "ワークスペース");
  const tree = findTree(board, treeKey({ kind: "worktree", name: "i0001" }));
  assert.equal(tree?.name, "i0001");
  assert.equal(findTree(board, "project:nothing"), undefined);
});

test("CB-T1118 見るツリーの錠は、プロジェクトならそのプロジェクトの作業中だけ、ワークスペースは全体を見る", () => {
  const board = fixture();
  const main = findTree(board, "main:");
  assert.ok(main !== undefined);
  const whole = lockForTree(board, main);
  const doing = board.tickets.filter((t) => t.in_progress === true).map((t) => t.ticket);
  assert.equal(whole.locked, doing.length > 0 || board.tickets.some((t) => t.in_progress === null));
  // 別のプロジェクトの作業中は、そのプロジェクトのツリーを止めない
  const other = { name: "lib", root: "/ws/projects/lib", project: "lib", kind: "project" } as const;
  const noDoing = { ...board, tickets: board.tickets.map((t) => ({ ...t, project: "other", in_progress: true })) };
  assert.equal(lockForTree(noDoing, other).locked, false);
  assert.equal(lockForTree(noDoing, main).locked, true);
  // 作業中かを答えない古い実行ファイルは、止める側
  const unknown = { ...board, tickets: board.tickets.map((t) => ({ ...t, project: "lib", in_progress: null })) };
  assert.equal(lockForTree(unknown, other).locked, true);
});

test("CB-T1119 一覧の見本は、種類と状態を網羅している（画面のテストが同じ見本を使う）", () => {
  const entries = list().entries;
  assert.deepEqual([...new Set(entries.map((e) => e.kind))].sort(), ["defense", "docs", "layer-config", "template"]);
  assert.deepEqual([...new Set(entries.map((e) => e.state))].sort(), ["changed", "conflict", "new", "unchanged"]);
});
