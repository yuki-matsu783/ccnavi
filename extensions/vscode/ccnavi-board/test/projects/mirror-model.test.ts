/**
 * プロジェクト管理画面が読む、共通レイヤーのミラーの答え（`user mirror --json`）の読み取りと、画面の判断
 * （`src/core/mirrormodel.ts`）、子プロセスに渡す引数（`src/core/commands.ts` の `mirror*Args`）、
 * 実行ファイルの出力の読み方（`src/ccnavi.ts` の `mirrorListOf` / `mirrorResultOf`）を確かめる。
 * 実行ファイルの実物は無くても試せる。答えは実行ファイルとの取り決めの例をそのまま使う。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { mirrorListOf, mirrorResultOf } from "../../src/ccnavi.js";
import { mirrorArgs, mirrorYesArgs } from "../../src/core/commands.js";
import {
  actionText,
  clockText,
  isStale,
  mirrorOf,
  parseMirrorList,
  parseMirrorResult,
  projectSyncButton,
  stateText,
  syncAllButton,
  syncAllTargets,
  syncNotice,
  type MirrorList,
  type MirrorStatus,
} from "../../src/core/mirrormodel.js";

/** 取り決めの見るだけの答え（実行ファイルの標準出力と同じ文字列） */
const LIST_TEXT = JSON.stringify({
  version: 1,
  ok: true,
  error: "",
  common_empty: false,
  projects: [
    { name: "app", state: "synced", changes: [], error: "", expect: "" },
    {
      name: "web",
      state: "differs",
      changes: [
        { rel: ".ccnavi/config/common/rules.yml", action: "update" },
        { rel: ".ccnavi/config/common/templates/a.yml", action: "create" },
        { rel: ".ccnavi/config/common/old.yml", action: "delete" },
      ],
      error: "",
      expect: "web-sha",
    },
    { name: "x", state: "error", changes: [], error: "理由", expect: "" },
  ],
  expect: "all-sha",
});

/** 取り決めの同期の答え */
const RESULT_TEXT = JSON.stringify({ version: 1, ok: true, error: "", synced: [{ name: "web", files: [".ccnavi/config/common/rules.yml"] }] });

function listOf(text: string = LIST_TEXT): MirrorList {
  const parsed = parseMirrorList(text);
  assert.ok(parsed.ok, parsed.ok ? "" : parsed.error);
  return parsed.value;
}

function ok(list: MirrorList = listOf()): MirrorStatus {
  return { kind: "ok", list };
}

test("CB-T1155 見るだけの引数は user mirror [<名前>…] --json、同期は --yes --expect <値> --via board --json を足す", () => {
  assert.deepEqual(mirrorArgs(), ["user", "mirror", "--json"]);
  assert.deepEqual(mirrorArgs(["web", "app"]), ["user", "mirror", "web", "app", "--json"]);
  assert.equal(mirrorArgs(["web"]).includes("--yes"), false);
  assert.deepEqual(mirrorYesArgs([], "all-sha"), ["user", "mirror", "--yes", "--expect", "all-sha", "--via", "board", "--json"]);
  assert.deepEqual(mirrorYesArgs(["web"], "web-sha"), ["user", "mirror", "web", "--yes", "--expect", "web-sha", "--via", "board", "--json"]);
});

test("CB-T1156 見るだけの答えを取り決めのとおりに読む。状態・変更・理由・expect を落とさない", () => {
  const list = listOf();
  assert.equal(list.ok, true);
  assert.equal(list.commonEmpty, false);
  assert.equal(list.expect, "all-sha");
  assert.deepEqual(
    list.projects.map((p) => [p.name, p.state, p.changes.length, p.error, p.expect]),
    [
      ["app", "synced", 0, "", ""],
      ["web", "differs", 3, "", "web-sha"],
      ["x", "error", 0, "理由", ""],
    ],
  );
  assert.deepEqual(list.projects[1].changes.map((c) => c.action), ["update", "create", "delete"]);
});

test("CB-T1157 ok が偽の答えは読み取りの失敗にせず、理由を持った一覧として返す。版の違い・欄の欠け・JSON でないものは失敗", () => {
  const failed = listOf(JSON.stringify({ version: 1, ok: false, error: "共通レイヤーを読み込めません", projects: [] }));
  assert.equal(failed.ok, false);
  assert.equal(failed.error, "共通レイヤーを読み込めません");
  assert.deepEqual(failed.projects, []);

  const version = parseMirrorList(JSON.stringify({ version: 2, ok: true, projects: [] }));
  assert.ok(!version.ok && /拡張は 1、実行ファイルは 2/.test(version.error));
  const noProjects = parseMirrorList(JSON.stringify({ version: 1, ok: true }));
  assert.ok(!noProjects.ok && /projects/.test(noProjects.error));
  const noName = parseMirrorList(JSON.stringify({ version: 1, ok: true, projects: [{ state: "synced" }] }));
  assert.ok(!noName.ok && /name/.test(noName.error));
  const notJson = parseMirrorList("usage: ccnavi user {agree,handoff} ...");
  assert.ok(!notJson.ok && /JSON として解釈できません/.test(notJson.error));
});

test("CB-T1158 同期の答えを読む。失敗の答えは ok:false の結果で、理由はそのまま", () => {
  const parsed = parseMirrorResult(RESULT_TEXT);
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.value, { ok: true, synced: [{ name: "web", files: [".ccnavi/config/common/rules.yml"] }] });

  const refused = parseMirrorResult(JSON.stringify({ version: 1, ok: false, error: "内容が更新されました。もう一度確認してください" }));
  assert.ok(refused.ok);
  assert.deepEqual(refused.value, { ok: false, error: "内容が更新されました。もう一度確認してください" });
  const noSynced = parseMirrorResult(JSON.stringify({ version: 1, ok: true }));
  assert.ok(!noSynced.ok && /synced/.test(noSynced.error));
});

test("CB-T1159 実行ファイルの出力の読み方: 読める答えは終了コードによらず使う。読めなければ失敗の答えの error か標準エラーの 1 行目", () => {
  const list = mirrorListOf({ code: 0, stdout: LIST_TEXT, stderr: "" });
  assert.ok(list.ok);
  assert.equal(list.value.projects.length, 3);
  // 古い実行ファイルは argparse の苦情を標準エラーに出して 2 で終わる
  const usage = mirrorListOf({ code: 2, stdout: "", stderr: "usage: ccnavi user ...\nccnavi user: error: invalid choice: 'mirror'" });
  assert.ok(!usage.ok);
  assert.equal(usage.error, "ccnavi user mirror --json が失敗しました: usage: ccnavi user ...");
  const garbage = mirrorListOf({ code: 0, stdout: "not json", stderr: "" });
  assert.ok(!garbage.ok && /出力を読み込めません/.test(garbage.error));

  assert.deepEqual(mirrorResultOf({ code: 0, stdout: RESULT_TEXT, stderr: "" }), { ok: true, synced: [{ name: "web", files: [".ccnavi/config/common/rules.yml"] }] });
  // 失敗の答えは非 0 で返っても、答えの error を使う
  const stale = JSON.stringify({ version: 1, ok: false, error: "内容が更新されました" });
  assert.deepEqual(mirrorResultOf({ code: 1, stdout: stale, stderr: "" }), { ok: false, error: "内容が更新されました" });
  const crashed = mirrorResultOf({ code: 1, stdout: "", stderr: "Traceback (most recent call last):" });
  assert.deepEqual(crashed, { ok: false, error: "ccnavi user mirror --yes --json が失敗しました: Traceback (most recent call last):" });
});

test("CB-T1160 行の一言と変更の種類の言い方。名前は大文字小文字を区別せずに引く", () => {
  const list = listOf();
  assert.deepEqual(list.projects.map(stateText), ["同期済み", "ずれ 3 件", "調べられません"]);
  assert.deepEqual(["update", "create", "delete", "rename"].map(actionText), ["更新", "追加", "削除", "rename"]);
  assert.equal(mirrorOf(list, "WEB")?.name, "web");
  assert.equal(mirrorOf(list, "nothing"), undefined);
});

test("CB-T1161 「すべて同期」の対象は differs のプロジェクトだけ。件数を出し、押すと渡すのは答えの全体の expect", () => {
  const list = listOf();
  assert.deepEqual(syncAllTargets(list), ["web"]);
  assert.deepEqual(syncAllButton(ok(list), false), { enabled: true, reason: "", count: 1 });
  // ずれが無ければ押せない
  const clean = listOf(JSON.stringify({ version: 1, ok: true, common_empty: false, projects: [{ name: "app", state: "synced", changes: [] }], expect: "" }));
  assert.deepEqual(syncAllButton(ok(clean), false), { enabled: false, reason: "ずれのあるプロジェクトはありません", count: 0 });
  // 全体の expect が返らなければ押せない（空の --expect は必ず断られる）
  const noExpect = listOf(LIST_TEXT.replace('"expect":"all-sha"', '"expect":""'));
  assert.equal(syncAllButton(ok(noExpect), false).enabled, false);
});

test("CB-T1162 ボタンを押せないとき: 同期中、調べる前、古い実行ファイル、調べられない、写すものが無い。理由を返す", () => {
  const list = listOf();
  assert.deepEqual(projectSyncButton(ok(list), false, "web"), { enabled: true, reason: "" });
  assert.match(projectSyncButton(ok(list), true, "web").reason, /同期しています/);
  assert.equal(syncAllButton(ok(list), true).enabled, false);
  assert.match(projectSyncButton({ kind: "waiting" }, false, "web").reason, /調べています/);
  const old: MirrorStatus = { kind: "unsupported", reason: "実行ファイル（ccnavi 0.1.0）が mirror の機能を持ちません（古い版です）" };
  assert.deepEqual(projectSyncButton(old, false, "web"), { enabled: false, reason: old.reason });
  assert.deepEqual(syncAllButton(old, false), { enabled: false, reason: old.reason, count: 0 });
  assert.match(projectSyncButton({ kind: "failed", error: "期限切れ" }, false, "web").reason, /調べられませんでした/);
  const empty = listOf(LIST_TEXT.replace('"common_empty":false', '"common_empty":true'));
  assert.match(projectSyncButton(ok(empty), false, "web").reason, /写すものがありません/);
  assert.equal(syncAllButton(ok(empty), false).enabled, false);
  // 行ごと: ずれが無い・調べられない・結果に無い
  assert.deepEqual(projectSyncButton(ok(list), false, "app"), { enabled: false, reason: "ずれはありません" });
  assert.deepEqual(projectSyncButton(ok(list), false, "x"), { enabled: false, reason: "理由" });
  assert.match(projectSyncButton(ok(list), false, "lib").reason, /このプロジェクトがありません/);
});

test("CB-T1163 同期の結果の通知。成功は作業ツリーへ書いてコミットしていないことを言う。「内容が更新されました」はずれ、他は失敗", () => {
  const done = syncNotice({ ok: true, synced: [{ name: "web", files: ["a", "b"] }, { name: "app", files: ["c"] }] });
  assert.equal(done.kind, "info");
  assert.match(done.message, /^web、app のミラーを同期しました（3 ファイル）。/);
  assert.match(done.message, /作業ツリー/);
  assert.match(done.message, /コミットはしていません/);
  assert.deepEqual(syncNotice({ ok: true, synced: [] }), { kind: "info", message: "同期するものはありませんでした" });
  assert.ok(isStale("内容が更新されました。もう一度確認してください"));
  const stale = syncNotice({ ok: false, error: "内容が更新されました" });
  assert.equal(stale.kind, "warn");
  assert.match(stale.message, /もう一度押してください/);
  assert.deepEqual(syncNotice({ ok: false, error: "書き込めません" }), { kind: "error", message: "ミラーを同期できませんでした: 書き込めません" });
});

test("CB-T1164 最終確認の時刻は HH:MM", () => {
  assert.equal(clockText(new Date(2026, 9, 24, 9, 5, 59)), "09:05");
  assert.equal(clockText(new Date(2026, 9, 24, 23, 40)), "23:40");
});

test("CB-T1165 安全性: 5 分ごとの確認は読むだけ。同期（runMirrorYes）を呼ぶのは画面の mirrorSync を受けた syncMirror だけ", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "..", "..", "src", "projects-panel.ts"), "utf8");
  const bodyOf = (header: string): string => {
    const start = source.indexOf(header);
    assert.ok(start >= 0, `見つからない: ${header}`);
    const end = source.indexOf("\n}\n", start);
    return source.slice(start, end);
  };
  assert.doesNotMatch(bodyOf("async function refreshMirror("), /runMirrorYes/);
  assert.doesNotMatch(bodyOf("async function readMirror("), /runMirrorYes/);
  assert.match(bodyOf("async function syncMirror("), /runMirrorYes/);
  assert.equal(source.split("runMirrorYes(").length - 1, 1, "runMirrorYes の呼び出しが syncMirror の外にもある");
  // 間隔は定数で持ち、setInterval で回さない（読み込み中の重なりを避ける）
  assert.match(source, /const MIRROR_INTERVAL_MS = 5 \* 60 \* 1000;/);
  assert.doesNotMatch(source, /setInterval\(/);
});
