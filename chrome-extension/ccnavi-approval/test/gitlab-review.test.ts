/**
 * GitLab 対応のレビューで直したもの。模擬の GitHub・GitLab と Node の上の Pyodide で回す。
 *
 * - 応答が落ちた書き込みの受け直し（PROBE-2・3）: 自分のコミットを「書いた中身と親の組」で見分けたときだけ受け直す。
 *   見分けられなければユーザの対応に切り替える
 * - 事後確認は周ごとに 1 つの時刻で判定し直す（時計が進んでも、無関係な割り込みでは元に戻さない）
 * - 事後確認と元に戻すコミットの途中でホストが落ちたら、書いたが確認できなかったとしてユーザの対応に切り替える
 * - service worker: 読み取りも登録したリポジトリだけ。「始める」は統合先の先頭で閉じた識別子と互換の版を確かめ直す
 * - GitLab の compare は、折りたたまれた・大きすぎる・時間切れ・上限に近い一覧を読めないとする
 * - GitLab の tree と discussions のページの上限、429 と 403、転送を追わない、シンボリックリンク
 * - 元に戻すコミットはバイト列のまま戻す（BOM も）
 * - 「要確認」の親子のチケットには、そのブラウザで書くボタンを出さない
 *
 * 最新のレビューで足したもの: 確かめが落ちたら要確認、元に戻す前の 412 で元に戻し直す、別の線に付け替わったら
 * ユーザの対応に切り替える、update の last_commit_id、MR の全ページとフォークの除外、転送を追わない
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import * as gh from "../src/core/github.js";
import * as gl from "../src/core/gitlab.js";
import { dispatch, type Deps } from "../src/core/protocol.js";
import type { PyCall } from "../src/core/py.js";
import { renderRepo } from "../src/core/render.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, type RepoBoard } from "../src/core/snapshot.js";
import { approveFamily, withdrawTicket, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, type FixtureBranch } from "./fixtures/repo.js";
import { BOARD, deps, GITLAB_REPO, hostCall, HOSTS, memoryCache, newStats } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { COMPAT } from "./helpers/compat.js";
import { MockGitLab } from "./helpers/mock-gitlab.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const GH_REPO: RepoConfig = { ...GITLAB_REPO, host: "github.com" };
const GL = HOSTS.find((h) => h.id === "gitlab.com")!;
const noWait = async () => undefined;
const TOKENS = new Map([
  ["github.com", TOKEN],
  ["gitlab.com", TOKEN],
]);
const TODO = "wip/proposals/todo/i0001.md";
const CHILD = "wip/proposals/todo/i0001-01.md";
const DOING = ".ccnavi/approved/doing/i0001.md";
const EVENTS = ".ccnavi/approved/events/i0001.ndjson";

function glDeps(mock: MockGitHub, host = "gitlab.com", now: () => Date = () => new Date(NOW)): WriteDeps {
  const stats = newStats();
  const d = { ...deps(mock, TOKENS, new Map(), () => new Date(NOW), [GITLAB_REPO, GH_REPO]), sleep: noWait };
  return {
    call: hostCall(d, stats, host),
    py,
    cache: memoryCache(),
    now,
    stats,
    version: "9.9.9",
    sleep: noWait,
    kind: host === "gitlab.com" ? "gitlab" : "github",
  };
}

function parentOnly(): Record<string, FixtureBranch> {
  const f = fixture();
  delete f.i0001.files[CHILD];
  return f;
}

function shownOf(b: RepoBoard, family: string) {
  const r = b.families.find((f) => f.family.name === family)?.result;
  assert.ok(r?.digest, b.error);
  return { ids: (r.batch ?? []).map((e) => e.ticket), digest: r.digest, only: r.only ?? null };
}

async function start(mock: MockGitHub, host = "gitlab.com", repo = GITLAB_REPO, now?: () => Date) {
  const d = glDeps(mock, host, now);
  return { d, shown: shownOf(await collectRepo(repo, d), "i0001") };
}

test("CX-T161 GitLab で応答が落ち、その上に無関係な書き込みが積まれても、自分のコミットを中身と親の組で見分けて事後確認する（PROBE-2）", async () => {
  const mock = new MockGitLab(parentOnly());
  const { d, shown } = await start(mock);
  // 書く直前に判定の変わる書き込み（子の提案）が入り、応答が落ち、その後に無関係なコードが積まれる
  mock.beforeCommit = (b) => void mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み X");
  mock.loseCommitResponse = true;
  let pushed = false;
  mock.onRequest = (method, u) => {
    if (!pushed && mock.glCommits.length === 1 && method === "GET" && u.pathname.endsWith("/repository/branches/i0001")) {
      pushed = true;
      mock.push("i0001", { "src/zzz.py": "x\n" }, "Y 無関係");
    }
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.deepEqual(mock.glCommits.map((c) => [c.result, c.message.split("\n")[0]]), [
    ["written", "ccnavi: i0001 を承認（Chrome 拡張 9.9.9）"],
    ["written", "ccnavi: i0001 への書き込みを元に戻す（Chrome 拡張 9.9.9）"],
  ]);
  const files = mock.files("i0001");
  assert.ok(!(DOING in files) && TODO in files && CHILD in files && "src/zzz.py" in files);
});

test("CX-T162 GitHub で応答が落ち、その上に無関係な書き込みが積まれても、親が読んだ先頭の自分のコミットだけを受け直す（PROBE-3）", async () => {
  const mock = new MockGitHub(parentOnly());
  const { d, shown } = await start(mock, "github.com", GH_REPO);
  const before = mock.head("i0001");
  mock.loseCommitResponse = true;
  let pushed = false;
  mock.onRequest = () => {
    if (!pushed && mock.commitCalls.length === 1) {
      pushed = true;
      mock.push("i0001", { "src/zzz.py": "x\n" }, "Y 無関係");
    }
  };
  const out = await approveFamily(GH_REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(out.kind === "written" ? out.oid : "", mock.commitCalls[0].oid);
  assert.equal(mock.commits.get(mock.commitCalls[0].oid as string)?.parents[0], before);
  assert.equal(mock.commitCalls.length, 1);
});

test("CX-T163 GitLab で応答が落ち、自分のコミットを見分けられない（上に merge が積まれた）のに書いた中身が在るなら、ユーザの対応に切り替える", async () => {
  const mock = new MockGitLab(parentOnly());
  mock.branch("side", "main");
  mock.push("side", { "src/side.py": "s\n" }, "別の枝");
  const { d, shown } = await start(mock);
  mock.loseCommitResponse = true;
  let merged = false;
  mock.onRequest = () => {
    if (!merged && mock.glCommits.length === 1) {
      merged = true;
      mock.merge("i0001", "side");
    }
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "attention", JSON.stringify(out));
  assert.match(out.kind === "attention" ? out.message : "", /見分けられなかった/);
  assert.equal(mock.glCommits.length, 1);
});

test("CX-T164 事後確認は周ごとに 1 つの時刻で判定し直す。時計が呼ぶたびに進んでも、無関係な割り込みでは元に戻さない", async () => {
  const mock = new MockGitLab(parentOnly());
  let t = Date.parse(NOW);
  const { d, shown } = await start(mock, "gitlab.com", GITLAB_REPO, () => new Date((t += 61_000)));
  mock.beforeCommit = (b) => void mock.push(b, { "src/other.py": "o\n" }, "割り込んだコード");
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.glCommits.length, 1);
});

test("CX-T165 事後確認の途中でホストが落ちたら、書いたが確認できなかったとしてユーザの対応に切り替える（失敗とは文面を分ける）", async () => {
  const mock = new MockGitLab(parentOnly());
  const { d, shown } = await start(mock);
  mock.beforeCommit = (b) => void mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み");
  mock.onRequest = (method, u) => {
    if (mock.glCommits.length === 1 && method === "GET" && u.pathname.endsWith("/repository/tree")) throw new Error("502 Bad Gateway");
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "attention", JSON.stringify(out));
  assert.match(out.kind === "attention" ? out.message : "", /書いたが確認できなかった/);
});

test("CX-T166 service worker の読み取りも、設定画面で登録したリポジトリだけ受ける", async () => {
  const mock = new MockGitLab(fixture());
  const d: Deps = deps(mock, TOKENS, new Map(), () => new Date(NOW), []);
  for (const [op, args] of [
    ["repoInfo", ["acme", "widgets"]],
    ["branchHead", ["acme", "widgets", "main"]],
    ["recentRefs", ["acme", "widgets", NOW]],
    ["branchNames", ["acme", "widgets"]],
    ["viewer", ["acme", "widgets"]],
  ] as const) {
    const res = await dispatch({ kind: "host", host: "gitlab.com", op, args }, BOARD, d);
    assert.equal(res.ok, false, op);
    assert.match((res as { error: string }).error, /登録していないリポジトリなので読まない/, op);
  }
  assert.equal(mock.calls.length, 0);
});

test("CX-T167 「始める」: service worker も統合先の先頭で閉じた識別子と互換の版を確かめ直す", async () => {
  const mock = new MockGitLab(fixture());
  const head = mock.head("main");
  const ask = (d: Deps, name: string) => dispatch({ kind: "host", host: "gitlab.com", op: "createBranch", args: ["acme", "widgets", name, head] }, BOARD, d);
  const d: Deps = deps(mock, TOKENS, new Map(), () => new Date(NOW), [GITLAB_REPO]);
  const closed = await ask(d, "i0005");
  assert.match((closed as { error: string }).error, /i0005 は統合先 main の done\/ で閉じている/);
  const skew = await ask({ ...d, compat: COMPAT + 1 }, "i0012");
  assert.match((skew as { error: string }).error, /互換の版/);
  mock.branch("I0012", "main");
  const folded = await ask(d, "i0012");
  assert.match((folded as { error: string }).error, /i0012 は既にある（I0012）/);
  // 互換分解で同じになる名前（全角）も重なりとして拾う
  mock.branch("ｉ００１３", "main");
  const wide = await ask(d, "i0013");
  assert.match((wide as { error: string }).error, /i0013 は既にある（ｉ００１３）/);
  assert.equal(mock.createdBranches.length, 0);
});

test("CX-T168 GitLab の compare: 畳まれた・大きすぎる・時間切れ・上限に近い一覧は読めないとする（動いたと数える）", async () => {
  const mock = new MockGitLab(parentOnly());
  const base = mock.head("i0001") as string;
  const head = mock.push("i0001", { "src/a.py": "a\n" });
  const client = { host: GL, token: TOKEN, fetch: mock.fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
  assert.deepEqual((await gl.compareFiles(client, "acme", "widgets", base, head)).files, ["src/a.py"]);
  for (const extra of [{ timeout: true }, { collapsed: true }, { tooLarge: true }]) {
    mock.compareExtra = extra;
    assert.equal((await gl.compareFiles(client, "acme", "widgets", base, head)).files, null, JSON.stringify(extra));
  }
  mock.compareExtra = {};
  const many: Record<string, string> = {};
  for (let i = 0; i < gl.COMPARE_FILES_NEAR; i += 1) many[`src/m${i}.py`] = `${i}\n`;
  const big = mock.push("i0001", many);
  assert.equal((await gl.compareFiles(client, "acme", "widgets", base, big)).files, null);
});

test("CX-T169 GitLab の読みの上限と断り: tree は 50 ページ、discussions は 20 ページを超えたら止め、429 は 1 回だけ待ち直し、403 は権限を言う。転送は追わない", async () => {
  const redirects: string[] = [];
  const pageOf = (n: number) => Array.from({ length: n }, (_, i) => ({ id: "a".repeat(40), name: `f${i}`, type: "blob", path: `x/f${i}`, mode: "100644" }));
  const answer = (fn: (u: URL, calls: number) => { status: number; json: unknown; headers?: Record<string, string> }) => {
    let calls = 0;
    return async (url: string, init: { redirect?: string }) => {
      redirects.push(String(init.redirect));
      const r = fn(new URL(url), (calls += 1));
      return { status: r.status, ok: r.status < 300, json: async () => r.json, headers: { get: (n: string) => r.headers?.[n.toLowerCase()] ?? null } };
    };
  };
  const client = (fetch: gh.Fetch) => ({ host: GL, token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait });
  await assert.rejects(gl.tree(client(answer(() => ({ status: 200, json: pageOf(100) }))), "acme", "widgets", "a".repeat(40), "b".repeat(40), "x"), /取り切れない/);
  await assert.rejects(gl.discussions(client(answer(() => ({ status: 200, json: Array(100).fill({ notes: [] }) }))), "acme", "widgets", 7, "u"), /多すぎて読み切れない/);
  const limited = answer((_, n) => (n === 1 ? { status: 429, json: {}, headers: { "retry-after": "1" } } : { status: 200, json: { id: 42, default_branch: "main" } }));
  assert.equal((await gl.repoInfo(client(limited), "acme", "widgets")).defaultBranch, "main");
  await assert.rejects(gl.repoInfo(client(answer(() => ({ status: 429, json: {}, headers: { "retry-after": "600" } }))), "acme", "widgets"), /レート制限/);
  await assert.rejects(gl.repoInfo(client(answer(() => ({ status: 403, json: {} }))), "acme", "widgets"), /権限/);
  assert.ok(redirects.length > 0 && redirects.every((r) => r === "error"), redirects.join(","));
});

test("CX-T170 GitLab のシンボリックリンク（mode 120000）はパスで引いても読まず、親子のチケットは決まらない", async () => {
  const f = parentOnly();
  f.main.files[".ccnavi/common/linked.yml"] = "../../etc/passwd";
  const mock = new MockGitLab(f);
  mock.linkPaths.add(".ccnavi/common/linked.yml");
  const client = { host: GL, token: TOKEN, fetch: mock.fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
  const objs = await gl.pathObjects(client, "acme", "widgets", mock.head("main") as string, [".ccnavi/common/linked.yml"]);
  assert.equal(objs[".ccnavi/common/linked.yml"]?.type, "link");
  const b = await collectRepo(GITLAB_REPO, glDeps(mock));
  const fam = b.families.find((x) => x.family.name === "i0001");
  assert.match(fam?.result?.undecided ?? "", /シンボリックリンク/);
});

test("CX-T171 元に戻すコミットはバイト列のまま戻す（BOM も落とさない）", async () => {
  const f = parentOnly();
  const original = '﻿{"at": "2026-09-01T00:00:00Z", "ticket": "i0001", "kind": "note"}\n';
  f.i0001.files[EVENTS] = original;
  const mock = new MockGitLab(f);
  const { d, shown } = await start(mock);
  mock.beforeCommit = (b) => void mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み");
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.equal(mock.glCommits.length, 2);
  assert.equal(mock.files("i0001")[EVENTS], original);
});

test("CX-T172 「要確認」の親子のチケットには、そのブラウザで承認・取り下げ・レビュー済みのボタンを出さず、ほかの承認者には見えないと言う", async () => {
  const mock = new MockGitLab(fixture());
  const b = await collectRepo(GITLAB_REPO, glDeps(mock));
  const dom = new JSDOM("<!doctype html><body></body>");
  const md = createRenderer(dom.window as unknown as Window & typeof globalThis);
  const actions = { approve: () => undefined, withdraw: () => undefined, review: () => undefined, dismiss: () => undefined };
  const html = renderRepo(dom.window.document, md, b, actions, { attention: { i0001: "元に戻せなかった" } });
  const box = html.querySelector('[data-family="i0001"]') as HTMLElement;
  assert.deepEqual([...box.querySelectorAll("button")].map((x) => (x as HTMLElement).dataset.action), ["dismiss"]);
  assert.match(box.textContent ?? "", /ほかの承認者には見えない/);
  // ほかの親子のチケットは今までどおり
  assert.equal(html.querySelectorAll('[data-family="i0002"] button[data-action=approve]').length, 1);
});

// ---- 最新のレビューで直したもの ------------------------------------------------

test("CX-T174 応答が落ちた後の確かめや、書いた後の確かめが落ちたら、失敗でなく要確認（書いたかもしれないが確認できない）", async () => {
  // 応答が落ちた直後に先頭を読む要求も落ちる
  const lost = new MockGitLab(parentOnly());
  const a = await start(lost);
  lost.loseCommitResponse = true;
  lost.onRequest = (method, u) => {
    if (lost.glCommits.length === 1 && method === "GET" && u.pathname.endsWith("/repository/branches/i0001")) throw new Error("503 Service Unavailable");
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", a.shown, a.d);
  assert.equal(out.kind, "attention", JSON.stringify(out));
  assert.match(out.kind === "attention" ? out.message : "", /書いたかもしれないが確認できなかった/);
  assert.equal(lost.glCommits.length, 1);

  // 割り込みは無く書けたが、書いた後の中身を読む要求が落ちる
  const after = new MockGitLab(parentOnly());
  const b = await start(after);
  after.onRequest = (method, u) => {
    if (after.glCommits.length === 1 && method === "GET" && u.pathname.endsWith("/repository/tree")) throw new Error("502 Bad Gateway");
  };
  const out2 = await approveFamily(GITLAB_REPO, "i0001", b.shown, b.d);
  assert.equal(out2.kind, "attention", JSON.stringify(out2));
  assert.match(out2.kind === "attention" ? out2.message : "", /書いた後の中身を確認できなかった/);
});

test("CX-T175 元に戻すコミットを送る前の確認で先頭が動いた（412。何も送っていない）なら、上限の範囲で読み直して元に戻し直す", async () => {
  const mock = new MockGitLab(parentOnly());
  const { d, shown } = await start(mock);
  mock.beforeCommit = (b) => void mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み");
  const statuses: (number | undefined)[] = [];
  let commits = 0;
  const call = d.call;
  const wrapped: WriteDeps = {
    ...d,
    call: async (op, args) => {
      if (op === "commit" && (commits += 1) === 2) mock.push("i0001", { "src/zzz.py": "z\n" }, "元に戻す直前の無関係な push");
      try {
        return await call(op, args);
      } catch (err) {
        if (op === "commit") statuses.push((err as { status?: number }).status);
        throw err;
      }
    },
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, wrapped);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.deepEqual(statuses, [gl.HOST_MOVED]);
  assert.deepEqual(mock.glCommits.map((c) => [c.result, c.message.split("\n")[0]]), [
    ["written", "ccnavi: i0001 を承認（Chrome 拡張 9.9.9）"],
    ["written", "ccnavi: i0001 への書き込みを元に戻す（Chrome 拡張 9.9.9）"],
  ]);
  const files = mock.files("i0001");
  assert.ok(!(DOING in files) && TODO in files && "src/zzz.py" in files);
});

test("CX-T176 GitLab で応答が落ち、先頭が読んだ先頭に届かない別の線（同じ中身のコミット）に付け替わったら、自分のものと取らずユーザの対応に切り替える", async () => {
  const mock = new MockGitLab(parentOnly());
  const { d, shown } = await start(mock);
  const read = mock.head("i0001") as string;
  mock.loseCommitResponse = true;
  let forged = "";
  mock.onRequest = () => {
    if (forged || mock.glCommits.length !== 1) return;
    // 読んだ先頭を含まない線: main から、読んだ先頭と同じ中身 → 自分の書いた中身、の 2 つを積む
    const inner = mock as unknown as { store(c: object, salt?: string): string; heads: Map<string, string> };
    const ours = mock.glCommits[0].oid as string;
    const p1 = inner.store({ parents: [mock.head("main")], files: { ...mock.commits.get(read)!.files }, date: NOW, message: "別の線 1" }, "forged-1");
    forged = inner.store({ parents: [p1], files: { ...mock.commits.get(ours)!.files }, date: NOW, message: "別の線 2" }, "forged-2");
    inner.heads.set("i0001", forged);
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "attention", JSON.stringify(out));
  assert.match(out.kind === "attention" ? out.message : "", /見分けられなかった/);
  assert.equal(mock.head("i0001"), forged);
  assert.equal(mock.glCommits.length, 1);
});

test("CX-T177 GitLab の update には、読んだ先頭でそのファイルを最後に変えたコミットを last_commit_id に付ける。割り込みで変わっていれば 400 で何も書かない", async () => {
  const mock = new MockGitLab(parentOnly());
  const d = glDeps(mock);
  const shown = shownOf(await collectRepo(GITLAB_REPO, d), "i0001");
  assert.equal((await approveFamily(GITLAB_REPO, "i0001", shown, d)).kind, "written");
  const approval = mock.head("i0001") as string;
  // 取り下げは events を書き換える（update）。その last_commit_id は承認コミット（読んだ先頭でそのファイルを最後に変えた）
  const back = await withdrawTicket(GITLAB_REPO, "i0001", "i0001", "", glDeps(mock));
  assert.equal(back.kind, "written", JSON.stringify(back));
  const update = mock.glCommits[1].actions.find((a) => a.action === "update");
  assert.deepEqual([update?.file_path, update?.last_commit_id], [EVENTS, approval]);
  const deleted = mock.glCommits[1].actions.find((a) => a.action === "delete");
  assert.deepEqual([deleted?.file_path, deleted?.last_commit_id], [DOING, approval]);

  // 書く直前に、書き換える events を別の書き手が変える: GitLab が 400 で断り、そのコミットは何も書かない
  const race = new MockGitLab(parentOnly());
  const rd = glDeps(race);
  assert.equal((await approveFamily(GITLAB_REPO, "i0001", shownOf(await collectRepo(GITLAB_REPO, rd), "i0001"), rd)).kind, "written");
  const line = '{"at": "2026-09-30T00:00:00Z", "ticket": "i0001", "kind": "note", "by": "別の書き手"}\n';
  race.beforeCommit = (b) => void race.push(b, { [EVENTS]: `${race.files(b)[EVENTS]}${line}` }, "別の書き手の events");
  const raced = await withdrawTicket(GITLAB_REPO, "i0001", "i0001", "", glDeps(race));
  assert.equal(race.glCommits[1].result, "error");
  assert.equal(race.glCommits[1].actions.find((a) => a.action === "update")?.last_commit_id, race.glCommits[0].oid);
  // 読み直して書いたとしても、別の書き手の行は残る（上書きしない）
  assert.ok(race.files("i0001")[EVENTS].includes(line), JSON.stringify(raced));
});

test("CX-T178 GitLab の開いた MR は全ページを読んでからこのプロジェクトのものに絞る（1 ページ目がフォークで埋まっても外さない）。Approve の数もフォークを数えない", async () => {
  const mock = new MockGitLab(parentOnly());
  const client = { host: GL, token: TOKEN, fetch: mock.fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
  // フォークだけ: 拾わない
  mock.forkMrs.push({ branch: "i0001", number: 900 });
  assert.equal(await gl.openMr(client, "acme", "widgets", "i0001"), null);
  assert.deepEqual(await gl.pullApprovals(client, "acme", "widgets", "i0001"), []);
  // 1 ページ目（100 件）がフォークで埋まり、本物は 2 ページ目
  for (let i = 1; i < 120; i += 1) mock.forkMrs.push({ branch: "i0001", number: 900 + i });
  mock.pulls.i0001 = [{ number: 7, reviews: [{ user: "reviewer", state: "APPROVED" }] }];
  assert.equal((await gl.openMr(client, "acme", "widgets", "i0001"))?.number, 7);
  assert.deepEqual((await gl.pullApprovals(client, "acme", "widgets", "i0001")).map((p) => p.number), [7]);
  assert.ok(mock.calls.some((c) => c.includes("/merge_requests")));
});

test("CX-T179 転送は追わない: GitHub と GitLab の要求に redirect: \"error\" を付け、fetch そのものが落ちたら原因の分かる文面にする", async () => {
  const seen: string[] = [];
  const ok = async (_url: string, init: { redirect?: string }) => {
    seen.push(String(init.redirect));
    return { status: 200, ok: true, json: async () => ({ default_branch: "main", id: 42 }), headers: { get: () => null } };
  };
  const GH = HOSTS.find((h) => h.id === "github.com")!;
  await gh.repoInfo({ host: GH, token: TOKEN, fetch: ok, counter: { rest: 0, graphql: 0 }, sleep: noWait }, "acme", "widgets");
  await gl.repoInfo({ host: GL, token: TOKEN, fetch: ok, counter: { rest: 0, graphql: 0 }, sleep: noWait }, "acme", "widgets");
  assert.deepEqual(seen, ["error", "error"]);
  const broken = async () => {
    throw new TypeError("Failed to fetch");
  };
  for (const [mod, host] of [
    [gh, GH],
    [gl, GL],
  ] as const) {
    await assert.rejects(
      mod.repoInfo({ host, token: TOKEN, fetch: broken, counter: { rest: 0, graphql: 0 }, sleep: noWait }, "acme", "widgets"),
      (err: Error & { status?: number }) => /転送された/.test(err.message) && /owner\/repo/.test(err.message) && /Failed to fetch/.test(err.message) && !err.status,
    );
  }
});
