/**
 * 手で `done/` に動かした子の移動の時刻（`pathCommitTime`）。パスを最初に加えたコミットの時刻を、
 * ブランチの履歴（GitHub の commits、GitLab の repository/commits）から読む。
 *
 * - 履歴の最も古いコミットの時刻（epoch 秒）を返す
 * - 履歴が上限に届いた・空・404・日付が読めない、のどれでも null（確定しない側）
 * - ブランチ名・パスの形が正しくなければ投げる
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import * as gh from "../src/core/github.js";
import * as gl from "../src/core/gitlab.js";
import { HOSTS } from "./helpers/host.js";
import { TOKEN } from "./helpers/mock-github.js";

const noWait = async () => undefined;
const GITLAB = HOSTS.find((h) => h.id === "gitlab.com")!;

function answer(status: number, json: unknown, seen: string[] = []): gh.Fetch {
  return async (url: string) => {
    seen.push(String(url));
    return { status, ok: status < 300, json: async () => json, headers: { get: () => null } };
  };
}

function ghClient(fetch: gh.Fetch) {
  return { host: HOSTS[0], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
}

function glClient(fetch: gh.Fetch) {
  return { host: GITLAB, token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
}

const PATH = ".ccnavi/approved/done/i0001-01-01.md";

test("CX-T258 GitHub: パスの履歴のうち最も古いコミットの時刻を返す。読めなければ null", async () => {
  const seen: string[] = [];
  const list = [
    { commit: { committer: { date: "2026-10-02T00:00:00Z" } } },
    { commit: { committer: { date: "2026-10-01T00:00:00Z" } } },
  ];
  const got = await gh.pathCommitTime(ghClient(answer(200, list, seen)), "acme", "widgets", "i0001", PATH);
  assert.deepEqual(got, { time: Date.parse("2026-10-01T00:00:00Z") / 1000 });
  assert.match(seen[0], /commits\?sha=i0001&path=.*done.*&per_page=100/);
  for (const body of [[], { message: "x" }, [{ commit: { committer: { date: "いつか" } } }], new Array(gh.PATH_HISTORY_LIMIT).fill(list[0])]) {
    assert.deepEqual(await gh.pathCommitTime(ghClient(answer(200, body)), "acme", "widgets", "i0001", PATH), { time: null });
  }
  assert.deepEqual(await gh.pathCommitTime(ghClient(answer(404, {})), "acme", "widgets", "i0001", PATH), { time: null });
  await assert.rejects(gh.pathCommitTime(ghClient(answer(200, list)), "acme", "widgets", "i0001", "../x"), /パス/);
  await assert.rejects(gh.pathCommitTime(ghClient(answer(200, list)), "acme", "widgets", "bad branch", PATH), /ブランチ名/);
});

test("CX-T259 GitLab: パスの履歴のうち最も古いコミットの時刻を返す。読めなければ null", async () => {
  const list = [{ committed_date: "2026-10-02T09:00:00.000+09:00" }, { committed_date: "2026-10-01T09:00:00.000+09:00" }];
  const got = await gl.pathCommitTime(glClient(answer(200, list)), "acme", "widgets", "i0001", PATH);
  assert.deepEqual(got, { time: Date.parse("2026-10-01T09:00:00+09:00") / 1000 });
  for (const body of [[], { message: "x" }, [{ committed_date: "いつか" }], new Array(gl.PATH_HISTORY_LIMIT).fill(list[0])]) {
    assert.deepEqual(await gl.pathCommitTime(glClient(answer(200, body)), "acme", "widgets", "i0001", PATH), { time: null });
  }
  assert.deepEqual(await gl.pathCommitTime(glClient(answer(404, {})), "acme", "widgets", "i0001", PATH), { time: null });
});
