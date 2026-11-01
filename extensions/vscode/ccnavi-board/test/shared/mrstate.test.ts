/**
 * マージ状況の確認が読む2つの答え（`mr-state`と`sync retired`）の検証。`src/core/mrstate.ts`。
 * 形が想定外のものを「マージ待ち」「マージ済み」「ありません」と読み間違えない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { interpretMrState, parseRetired, projectOfRepo, repoDirOf, type Ran } from "../../src/core/mrstate.js";

function ran(stdout: string, over: Partial<Ran> = {}): Ran {
  return { code: 0, stdout, stderr: "", killed: false, ...over };
}

const URL = "https://example.com/o/r/pull/12";

function state(second: string, over: Partial<Ran> = {}, expected = 12) {
  return interpretMrState(ran(`mr-state 1\n${second}\n`, over), expected);
}

function formatError(result: ReturnType<typeof interpretMrState>): string {
  assert.equal(result.kind, "unknown");
  assert.equal(result.kind === "unknown" ? result.reason : "", "format");
  return result.kind === "unknown" ? result.detail : "";
}

test("CB-T620 mr-state: 4つの状態を読む（openはDraftかも、merged / closedはURL）", () => {
  assert.deepEqual(state(`open 12 no ${URL}`), { kind: "open", number: 12, draft: false, url: URL });
  assert.deepEqual(state(`open 12 yes ${URL}`), { kind: "open", number: 12, draft: true, url: URL });
  assert.deepEqual(state(`merged 12 ${URL}`), { kind: "merged", number: 12, url: URL });
  assert.deepEqual(state(`closed 12 ${URL}`), { kind: "closed", number: 12, url: URL });
  // 末尾の改行の有無・CRLFは読める
  assert.deepEqual(interpretMrState(ran(`mr-state 1\r\nmerged 12 ${URL}`), 12), { kind: "merged", number: 12, url: URL });
});

test("CB-T621 mr-state: unknownは種類ごとに読み、理由（空白を含む）を残す。終了コード1のときだけ", () => {
  for (const kind of ["no-tool", "auth", "network", "api", "not-found"]) {
    const result = state(`unknown ${kind} ホストに届かない（ネットワーク）`, { code: 1 });
    assert.deepEqual(result, { kind: "unknown", reason: kind, detail: "ホストに届かない（ネットワーク）" });
  }
  // 種類を知らない・終了コードが合わない・理由が無い: 形が想定外
  assert.match(formatError(state("unknown frobnicate 理由", { code: 1 })), /unknownの種類が想定外です（frobnicate）/);
  assert.match(formatError(state("unknown auth 理由", { code: 0 })), /終了コードが状態と合いません（0）/);
  assert.match(formatError(state("unknown auth", { code: 1 })), /unknownの項目の数が違います/);
});

test("CB-T622 mr-stateの検証: 1行目・語の数・番号・Draft・終了コード・余計な行。どれが違っても「出力の形が想定外」", () => {
  // 1行目
  assert.match(formatError(interpretMrState(ran(`mr-state 2\nmerged 12 ${URL}\n`), 12)), /1行目がmr-state 1ではありません/);
  assert.match(formatError(interpretMrState(ran(`merged 12 ${URL}\n`), 12)), /1行目がmr-state 1ではありません/);
  assert.match(formatError(interpretMrState(ran(""), 12)), /出力が空です/);
  assert.match(formatError(interpretMrState(ran("\n\n"), 12)), /出力が空です/);
  // 状態の行が無い・多すぎる
  assert.match(formatError(interpretMrState(ran("mr-state 1\n"), 12)), /状態の行がありません/);
  assert.match(formatError(interpretMrState(ran(`mr-state 1\nmerged 12 ${URL}\nmerged 12 ${URL}\n`), 12)), /行が多すぎます/);
  assert.match(formatError(interpretMrState(ran(`mr-state 1\n\nmerged 12 ${URL}\n`), 12)), /1行目|行が多すぎます|状態が想定外です/);
  // 状態を知らない
  assert.match(formatError(state(`pending 12 ${URL}`)), /状態が想定外です（pending）/);
  assert.match(formatError(state("")), /状態の行がありません/);
  // 項目の数: openは4、merged / closedは3
  assert.match(formatError(state(`open 12 no`)), /openの項目の数が違います/);
  assert.match(formatError(state(`open 12 no ${URL} extra`)), /openの項目の数が違います/);
  assert.match(formatError(state(`merged 12`)), /mergedの項目の数が違います/);
  assert.match(formatError(state(`closed 12 ${URL} extra`)), /closedの項目の数が違います/);
  // 番号: 要求したMRと同じでなければ、別のMRの答え
  assert.match(formatError(state(`merged 13 ${URL}`)), /要求したMR（#12）と番号が違います（13）/);
  assert.match(formatError(state(`open 13 no ${URL}`)), /要求したMR（#12）と番号が違います（13）/);
  for (const bad of ["abc", "0", "012", "1e1", "-1", "12.0", "0x0c", "１２"]) {
    assert.match(formatError(state(`merged ${bad} ${URL}`)), /要求したMR/, bad);
  }
  // Draftはyes / noだけ
  for (const bad of ["true", "YES", "1", "", "maybe"]) {
    assert.match(formatError(state(`open 12 ${bad} ${URL}`.replace("  ", " "), {}, 12)), /Draftがyes \/ noではありません|項目の数が違います/, bad);
  }
  // 終了コードが状態と合わない（状態が分かれば0）
  assert.match(formatError(state(`merged 12 ${URL}`, { code: 1 })), /終了コードが状態と合いません（1）/);
  assert.match(formatError(state(`open 12 no ${URL}`, { code: 2 })), /終了コードが状態と合いません（2）/);
});

test("CB-T623 mr-state: URLがhttp(s)でなくても状態は出す（リンクにしないのは画面の仕事）。期限切れはtimeout、実行できなかったときはfailed", () => {
  assert.deepEqual(state("merged 12 javascript:alert(1)"), { kind: "merged", number: 12, url: "javascript:alert(1)" });
  assert.deepEqual(state("open 12 no ftp://x/y"), { kind: "open", number: 12, draft: false, url: "ftp://x/y" });
  // 期限で打ち切った: 拡張の側だけの種類timeout（出力が途中まで読めてもtimeoutが先）
  const timeout = interpretMrState(ran(`mr-state 1\nmerged 12 ${URL}\n`, { killed: true, code: -1 }), 12);
  assert.deepEqual(timeout, { kind: "unknown", reason: "timeout", detail: "時間内に終わりませんでした" });
  // シェルが無い・shが落ちた（想定外の終了コードで、頭の行が無い）
  const failed = interpretMrState(ran("", { code: 127, stderr: "sh: not found" }), 12);
  assert.equal(failed.kind === "unknown" ? failed.reason : "", "failed");
  assert.match(failed.kind === "unknown" ? failed.detail : "", /終了コード127で終わりました: sh: not found/);
  const spawn = interpretMrState(ran("", { code: -1, stderr: "spawn sh ENOENT" }), 12);
  assert.equal(spawn.kind === "unknown" ? spawn.reason : "", "failed");
  // 終了コード2でもstderrに理由があれば（ワークスペースが見つからないなど）、形の異常と言わず失敗として出す
  const refused = interpretMrState(ran("", { code: 2, stderr: "ccnavi: ワークスペースが見つかりません" }), 12);
  assert.equal(refused.kind === "unknown" ? refused.reason : "", "failed");
  assert.match(refused.kind === "unknown" ? refused.detail : "", /終了コード2で終わりました/);
});

test("CB-T624 引数の誤り（終了コード2）は、実行できなかったこととしてshの言葉を添える", () => {
  const badArg = ran("", { code: 2, stderr: "ccnavi-review: mr-stateのMRの番号(abc)は1から始まる数字で渡してください。" });
  const bad = interpretMrState(badArg, 12);
  assert.equal(bad.kind === "unknown" ? bad.reason : "", "failed");
  assert.match(bad.kind === "unknown" ? bad.detail : "", /番号\(abc\)は1から始まる数字/);
});

const HEAD = "retired 1\n";

test("CB-T625 sync retired: retiredの行は対象、incompleteの行は確認できない記録。両方0件でも、正しく取れたときの空", () => {
  const parsed = parseRetired(ran(`${HEAD}retired i0001 self 12 feature/a\nretired i0002 web - -\nincomplete i0003 記録を読めない（壊れている）\n`));
  assert.ok(parsed.ok);
  if (!parsed.ok) return;
  assert.deepEqual(parsed.entries, [
    { parent: "i0001", repo: "self", mr: 12, branch: "feature/a" },
    { parent: "i0002", repo: "web", mr: null, branch: "" },
  ]);
  assert.deepEqual(parsed.incomplete, [{ parent: "i0003", reason: "記録を読めない（壊れている）" }]);
  const empty = parseRetired(ran(HEAD));
  assert.deepEqual(empty, { ok: true, entries: [], incomplete: [] });
  // CRLFは読める
  assert.ok(parseRetired(ran("retired 1\r\nretired i0001 self 3 b\r\n")).ok);
});

test("CB-T626 sync retiredの異常は「一覧を取得できません」にする。「ありません」とは言わず、行を黙って破棄しない", () => {
  const error = (r: Pick<Ran, "code" | "stdout" | "killed">): string => {
    const parsed = parseRetired(r);
    assert.equal(parsed.ok, false);
    return parsed.ok ? "" : parsed.error;
  };
  const base = { code: 0, killed: false };
  assert.match(error({ ...base, stdout: "" }), /一覧を取得できません（形式の異常: 先頭の行がretired 1ではありません）/);
  assert.match(error({ ...base, stdout: "retired 2\n" }), /先頭の行がretired 1ではありません/);
  assert.match(error({ ...base, stdout: "usage: ccnavi ...\n" }), /先頭の行がretired 1ではありません/);
  assert.match(error({ code: 1, killed: false, stdout: HEAD }), /一覧を取得できません（コマンドが失敗しました。終了コード1）/);
  assert.match(error({ code: 0, killed: true, stdout: HEAD }), /一覧を取得できません（時間内に終わりませんでした）/);
  // 知らない行（黙って破棄しない）
  assert.match(error({ ...base, stdout: `${HEAD}retired i0001 self 1 b\nmystery i0002\n` }), /3行目が想定外です/);
  assert.match(error({ ...base, stdout: `${HEAD}\nretired i0001 self 1 b\n` }), /2行目が想定外です/);
  // retiredの行の項目が足りない
  for (const line of ["retired", "retired i0001", "retired i0001 self", "retired i0001 self 12"]) {
    assert.match(error({ ...base, stdout: `${HEAD}${line}\n` }), /retiredの項目が足りません/, line);
  }
  // incompleteに親が無い
  assert.match(error({ ...base, stdout: `${HEAD}incomplete\n` }), /incompleteに親がありません/);
  // 先に取れた行があっても、異常があれば全体を異常にする（一部だけ見せて「ありません」と読ませない）
  assert.equal(parseRetired({ ...base, stdout: `${HEAD}retired i0001 self 1 b\nmystery\n` }).ok, false);
});

test("CB-T627 リポジトリの名前からプロジェクトとディレクトリを引く（ccnavi-sync.shのrepo_dir_ofと同じ規則）", () => {
  assert.equal(projectOfRepo("self"), "");
  assert.equal(projectOfRepo("web"), "web");
  assert.equal(repoDirOf("/ws", "projects", "self"), "/ws");
  assert.equal(repoDirOf("/ws/", "projects", "web"), "/ws/projects/web");
  assert.equal(repoDirOf("/ws", "/abs/projects/", "web"), "/abs/projects/web");
  assert.equal(repoDirOf("C:\\ws", "projects", "web"), "C:/ws/projects/web");
  assert.equal(repoDirOf("C:\\ws", "D:\\p", "web"), "D:/p/web");
});
