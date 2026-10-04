/**
 * GitLab・プロジェクトのリポジトリ・「始める」。模擬の GitHub・GitLab と Node の上の Pyodide で回す。
 *
 * - 録ったホストの応答の見本（test/fixtures/host/gitlab/）から TS が組む結果は、sh が組んだ期待値と同じ
 * - GitLab のボードは、GitHub と同じ見本のリポジトリから同じ親子のチケット・承認待ち・ダイジェストを出す（読み取りの一致）
 * - GitLab への書き込みは Commits API の 1 コミット。事後確認: 書いたコミットの親が読んだ先頭と
 *   違えば、直前の状態で判定し直し、同じなら残し、違えば元に戻して読み直す。元に戻すコミットも収まらなければユーザの対応に切り替える
 * - 「始める」: issue から `feature-<番号>-<slug>` のブランチを統合先の先頭に作る。閉じた識別子・既にある名前は拒否
 * - プロジェクトのリポジトリ: ワークスペースの統合先の共通層で判定し、プロジェクトの親のブランチへ書く
 * - PAT は画面に渡らない。GitLab のスレッドの本文は承認の画面と同じ規則で描く
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { JSDOM } from "jsdom";

import * as gl from "../src/core/gitlab.js";
import { dispatch, type Deps } from "../src/core/protocol.js";
import type { PyCall } from "../src/core/py.js";
import { renderRepo } from "../src/core/render.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, type RepoBoard } from "../src/core/snapshot.js";
import { listIssues, startIssue } from "../src/core/start.js";
import { approveFamily, confirmPhase, withdrawTicket, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, projectFixture, requestedMark, reviewFamilyFiles, type FixtureBranch } from "./fixtures/repo.js";
import { gitlabSceneFetch, gitlabSceneNames, GITLAB_SCENES, loadGitLabScene } from "./helpers/gitlab-fixture.js";
import { BOARD, deps, GITLAB_REPO, hostCall, HOSTS, memoryCache, newStats, OPTIONS } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { GL_LOGIN, MockGitLab } from "./helpers/mock-gitlab.js";
import { nativePy, pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});
const native = nativePy();
after(() => native?.close());

const GH_REPO: RepoConfig = { ...GITLAB_REPO, host: "github.com" };
const GL = HOSTS.find((h) => h.id === "gitlab.com")!;
const noWait = async () => undefined;
const TOKENS = new Map([
  ["github.com", TOKEN],
  ["gitlab.com", TOKEN],
]);

function glDeps(mock: MockGitHub, host = "gitlab.com", repos: RepoConfig[] = [GITLAB_REPO, GH_REPO], pyCall: PyCall = py): WriteDeps {
  const stats = newStats();
  const d = { ...deps(mock, TOKENS, new Map(), () => new Date(NOW), repos), sleep: noWait };
  return {
    call: hostCall(d, stats, host),
    py: pyCall,
    cache: memoryCache(),
    now: () => new Date(NOW),
    stats,
    version: "9.9.9",
    sleep: noWait,
    kind: host === "gitlab.com" ? "gitlab" : "github",
  };
}

function parentOnly(): Record<string, FixtureBranch> {
  const f = fixture();
  delete f.i0001.files["wip/proposals/todo/i0001-01-01.md"];
  return f;
}

function shownOf(b: RepoBoard, family: string) {
  const r = b.families.find((f) => f.family.name === family)?.result;
  assert.ok(r?.digest, b.error || JSON.stringify(b.families.map((f) => [f.family.name, f.error, f.result?.refused])));
  return { ids: (r.batch ?? []).map((e) => e.ticket), digest: r.digest, only: r.only ?? null };
}

function placeFiles(files: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(files).filter(([p]) => p.startsWith(".ccnavi/approved/") || p.startsWith("wip/proposals/")));
}

const TODO = "wip/proposals/todo/i0001.md";
const DOING = ".ccnavi/approved/doing/i0001.md";
const EVENTS = ".ccnavi/approved/events/i0001.ndjson";

test("CX-T144 手で組んだ GitLab の応答の見本ごとに、TS が組む結果は sh が組んだ期待値（expected.json）と同じ", async () => {
  assert.deepEqual(gitlabSceneNames(), ["hostile", "impostor", "nested", "odd-types", "paged", "requested-changes", "resolved"]);
  for (const name of gitlabSceneNames()) {
    const scene = loadGitLabScene(name);
    const client = { host: GL, token: TOKEN, fetch: gitlabSceneFetch(scene), counter: { rest: 0, graphql: 0 }, sleep: noWait };
    const copy = (await gl.reviewCopy(client, scene.namespace, scene.project, scene.branch)) as unknown as Record<string, unknown>;
    assert.match(String(copy.fetched_at), /^\d{4}-\d\d-\d\dT/);
    delete copy.fetched_at;
    const expected = JSON.parse(fs.readFileSync(path.join(GITLAB_SCENES, name, "expected.json"), "utf8"));
    assert.deepEqual(copy, expected, name);
  }
});

test("CX-T145 GitLab のボードは、GitHub と同じ見本のリポジトリから同じ親子のチケット・承認待ち・ダイジェスト・取り下げの可否を出す", async () => {
  const hub = await collectRepo(GH_REPO, glDeps(new MockGitHub(fixture()), "github.com"));
  const lab = await collectRepo(GITLAB_REPO, glDeps(new MockGitLab(fixture())));
  assert.equal(lab.error, "", lab.error);
  const view = (b: RepoBoard) =>
    b.families.map((f) => ({
      name: f.family.name,
      error: f.error,
      batch: f.result?.batch?.map((e) => [e.ticket, e.body]),
      text: f.result?.text,
      digest: f.result?.digest,
      rejected: f.result?.rejected,
      undecided: f.result?.undecided,
      withdrawable: f.result?.withdrawable,
    }));
  assert.ok(lab.families.length >= 2);
  assert.deepEqual(view(lab), view(hub));
  assert.deepEqual(lab.candidates, hub.candidates);
  // GitLab は REST だけ
  assert.equal(lab.stats.graphql, 0);
});

test("CX-T146 GitLab へ承認と取り下げを書く: Commits API の 1 コミット（作る・書き換える・消すを分ける）、親は読んだ先頭", async () => {
  const mock = new MockGitLab(parentOnly());
  const d = glDeps(mock);
  const before = mock.head("i0001");
  const out = await approveFamily(GITLAB_REPO, "i0001", shownOf(await collectRepo(GITLAB_REPO, d), "i0001"), d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.glCommits.length, 1);
  const call = mock.glCommits[0];
  assert.deepEqual([call.branch, call.parent, call.result], ["i0001", before, "written"]);
  assert.match(call.message, /^ccnavi: i0001 を承認（Chrome 拡張 9\.9\.9）/);
  assert.deepEqual(
    call.actions.map((a) => [a.action, a.file_path]).sort(),
    [
      ["create", DOING],
      ["create", EVENTS],
      ["delete", TODO],
    ].sort(),
  );
  assert.ok(call.actions.every((a) => a.action === "delete" || a.encoding === "base64"));
  const files = mock.files("i0001");
  assert.ok(DOING in files && !(TODO in files));
  assert.match(files[EVENTS], /"via": "chrome"/);
  assert.match(files[EVENTS], new RegExp(`"actor": "${GL_LOGIN}"`));

  // 取り下げ: 承認コミットを GitLab の履歴（first_parent）から引き、元の提案をそのまま戻す
  const back = await withdrawTicket(GITLAB_REPO, "i0001", "i0001", "押し間違い", glDeps(mock));
  assert.equal(back.kind, "written", JSON.stringify(back));
  const second = mock.glCommits[1];
  assert.deepEqual(second.actions.map((a) => [a.action, a.file_path]).sort(), [
    ["create", TODO],
    ["delete", DOING],
    ["update", EVENTS],
  ]);
  assert.equal(mock.files("i0001")[TODO], parentOnly().i0001.files[TODO]);
});

test("CX-T147 事後確認: 書く直前に関係の無い書き込み（コード）が割り込んでも、判定し直して同じなら残す（元に戻さない）", async () => {
  const mock = new MockGitLab(parentOnly());
  const d = glDeps(mock);
  const shown = shownOf(await collectRepo(GITLAB_REPO, d), "i0001");
  const before = mock.head("i0001");
  mock.beforeCommit = (b) => void mock.push(b, { "src/other.py": "print('other')\n" }, "割り込んだコード");
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.glCommits.length, 1);
  // 書いたコミットの親は読んだ先頭でない（間に割り込んだ）が、元に戻さない
  assert.notEqual(mock.glCommits[0].parent, before);
  const files = mock.files("i0001");
  assert.ok(DOING in files && "src/other.py" in files);
});

test("CX-T148 事後確認: 判定の変わる書き込み（別のファイル）が割り込むと、元に戻すコミットを積み、読み直して見直しを求める。同じファイルなら書かずに止まる", async () => {
  const mock = new MockGitLab(parentOnly());
  const d = glDeps(mock);
  const shown = shownOf(await collectRepo(GITLAB_REPO, d), "i0001");
  let raced = "";
  const CHILD = "wip/proposals/todo/i0001-01-01.md";
  mock.beforeCommit = (b) => {
    raced = mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込んだ子の提案");
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.deepEqual(
    mock.glCommits.map((c) => [c.result, c.message.split("\n")[0]]),
    [
      ["written", "ccnavi: i0001 を承認（Chrome 拡張 9.9.9）"],
      ["written", "ccnavi: i0001 への書き込みを元に戻す（Chrome 拡張 9.9.9）"],
    ],
  );
  // 元に戻すコミットは各ファイルに「最後に変えたのは自分のコミット」を付ける
  const mine = mock.glCommits[0].oid;
  assert.ok(mock.glCommits[1].actions.filter((a) => a.action !== "create").every((a) => a.last_commit_id === mine));
  // 元に戻した後の置き場は、割り込んだ書き込みの直後と同じ（承認は残らない）
  assert.deepEqual(placeFiles(mock.files("i0001")), placeFiles(mock.commits.get(raced)!.files));

  // 同じファイル（消す提案）を割り込みが変えていれば、GitLab が断るので何も書かない（last_commit_id）
  const same = new MockGitLab(parentOnly());
  const sd = glDeps(same);
  const sshown = shownOf(await collectRepo(GITLAB_REPO, sd), "i0001");
  same.beforeCommit = (b) => void same.push(b, { [TODO]: `${same.files(b)[TODO]}\n割り込みで足した本文\n` }, "割り込んだ提案の書き換え");
  const sout = await approveFamily(GITLAB_REPO, "i0001", sshown, sd);
  assert.equal(sout.kind, "changed", JSON.stringify(sout));
  assert.deepEqual(same.glCommits.map((c) => c.result), ["error"]);
  assert.ok(!(DOING in same.files("i0001")));
});

test("CX-T149 連鎖競合: 元に戻す前・元に戻すあいだに同じファイルが変わると、止めてユーザの対応に切り替える（要確認）。他人の変更は消さない（PROBE-1）", async () => {
  const CHILD = "wip/proposals/todo/i0001-01-01.md";
  const mock = new MockGitLab(parentOnly());
  const d = glDeps(mock);
  const shown = shownOf(await collectRepo(GITLAB_REPO, d), "i0001");
  mock.beforeCommit = (b) => {
    mock.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み 1");
    mock.beforeCommit = (b2) => void mock.push(b2, { [DOING]: "別の書き手が書いた\n" }, "割り込み 2");
  };
  const out = await approveFamily(GITLAB_REPO, "i0001", shown, d);
  assert.equal(out.kind, "attention", JSON.stringify(out));
  assert.match(out.kind === "attention" ? out.message : "", /ホストの履歴を確かめてください/);
  // 元に戻すコミットは last_commit_id で断られ、割り込み 2 の変更は残る（PROBE-1）
  assert.deepEqual(mock.glCommits.map((c) => c.result), ["written", "error"]);
  assert.equal(mock.files("i0001")[DOING], "別の書き手が書いた\n");

  // 書いた後、元に戻す前に同じファイルが変わっていれば、元に戻すコミットを送らずに止める
  const late = new MockGitLab(parentOnly());
  const ld = glDeps(late);
  const lshown = shownOf(await collectRepo(GITLAB_REPO, ld), "i0001");
  late.beforeCommit = (b) => void late.push(b, { [CHILD]: fixture().i0001.files[CHILD] }, "割り込み");
  let pushed = false;
  late.onRequest = (method, u) => {
    if (!pushed && late.glCommits.length === 1 && method === "GET" && u.pathname.endsWith("/repository/branches/i0001")) {
      pushed = true;
      late.push("i0001", { [DOING]: "後から変えた\n" }, "後から");
    }
  };
  const lout = await approveFamily(GITLAB_REPO, "i0001", lshown, ld);
  assert.equal(lout.kind, "attention", JSON.stringify(lout));
  assert.equal(late.glCommits.length, 1);
  assert.equal(late.files("i0001")[DOING], "後から変えた\n");
});

test("CX-T150 「始める」: issue から feature-<番号>-<slug> のブランチを統合先の先頭に作る（GitHub と GitLab）。閉じた識別子・既にある名前は作らない", async () => {
  for (const [mock, repo, host] of [
    [new MockGitHub(fixture()), GH_REPO, "github.com"],
    [new MockGitLab(fixture()), GITLAB_REPO, "gitlab.com"],
  ] as const) {
    mock.issues.push({ number: 12, title: "新しい機能" }, { number: 5, title: "Closed" }, { number: 1, title: "open" }, { number: 30, title: "PR", pull: true });
    // 閉じた親子のチケット（feature-5-closed）と既にあるブランチ（feature-1-open）を統合先に足す
    const closedText = mock.files("main")[".ccnavi/approved/done/i0005.md"].replaceAll("i0005", "feature-5-closed");
    mock.push("main", { ".ccnavi/approved/done/feature-5-closed.md": closedText }, "閉じた親子のチケット");
    mock.branch("feature-1-open", "main");
    const d = glDeps(mock, host);
    const list = await listIssues(repo, d);
    assert.deepEqual(
      list.map((i) => i.number),
      host === "github.com" ? [12, 5, 1] : [12, 5, 1, 30],
      host,
    );
    const b = await collectRepo(repo, d);
    const taken = [...b.candidates, ...b.families.map((f) => f.family.name)];
    const out = await startIssue(repo, list[0], b.seen ?? null, taken, d);
    assert.deepEqual(out, { kind: "started", name: "feature-12-新しい機能", head: mock.head("main") }, host);
    assert.deepEqual(mock.createdBranches, [{ name: "feature-12-新しい機能", sha: mock.head("main") }]);
    const closed = await startIssue(repo, { number: 5, title: "Closed" }, b.seen ?? null, taken, d);
    assert.equal(closed.kind, "refused", host);
    assert.match(closed.kind === "refused" ? closed.message : "", /feature-5-closed は統合先 main の done\/ で閉じている[\s\S]*フォールバック/);
    const open = await startIssue(repo, { number: 1, title: "OPEN" }, b.seen ?? null, taken, d);
    assert.match(open.kind === "refused" ? open.message : "", /同じ名前のブランチが既にある（feature-1-open）/);
    const again = await startIssue(repo, list[0], b.seen ?? null, taken, d);
    assert.equal(again.kind, "refused", host);
    // 全部のブランチの名前を大文字小文字をそろえて比べる（直近 N 日の外のブランチも）
    assert.match(again.kind === "refused" ? again.message : "", /同じ名前のブランチが既にある（feature-12-新しい機能）/);
    assert.equal(mock.createdBranches.length, 1, host);
  }
});

test("CX-T151 service worker の「始める」の保護: ボードからだけ、登録したリポジトリだけ、issue から決める形の名前だけ、統合先の今の先頭からだけ", async () => {
  const mock = new MockGitLab(fixture());
  const d: Deps = deps(mock, TOKENS, new Map(), () => new Date(NOW), [GITLAB_REPO]);
  const head = mock.head("main");
  const ask = (args: unknown[], sender = BOARD) => dispatch({ kind: "host", host: "gitlab.com", op: "createBranch", args }, sender, d);
  const refused: [unknown[], typeof BOARD, RegExp][] = [
    [["acme", "widgets", "feature-12-x", head], OPTIONS, /ボードからだけ/],
    [["acme", "other", "feature-12-x", head], BOARD, /登録していない/],
    [["acme", "widgets", "main", head], BOARD, /issue から作る/],
    [["acme", "widgets", "i0012", head], BOARD, /issue から作る/],
    [["acme", "widgets", "feature-012-x", head], BOARD, /issue から作る/],
    [["acme", "widgets", "release-12-x", head], BOARD, /issue から作る/],
    [["acme", "widgets", "feature-12-x-01", head], BOARD, /issue から作る/],
    [["acme", "widgets", "feature-12-ＡＢ", head], BOARD, /issue から作る/],
    [["acme", "widgets", "feature-12-か\u3099", head], BOARD, /issue から作る/],
    [["acme", "widgets", `feature-12-${"a".repeat(60)}`, head], BOARD, /issue から作る/],
    [["acme", "widgets", "feature-12-x", "f".repeat(40)], BOARD, /先頭が、ボードで読んだときから動いている/],
  ];
  for (const [args, sender, why] of refused) {
    const res = await ask(args, sender);
    assert.equal(res.ok, false, JSON.stringify(args));
    assert.match((res as { error: string }).error, why);
  }
  assert.equal(mock.createdBranches.length, 0);
  const made = await ask(["acme", "widgets", "feature-12-統合先の解決", head]);
  assert.ok(made.ok, JSON.stringify(made));
  const issues = await dispatch({ kind: "host", host: "gitlab.com", op: "issues", args: ["acme", "other"] }, BOARD, d);
  assert.match((issues as { error: string }).error, /登録していない/);
  // プロジェクトのリポジトリは slug の頭が `<名前>-` のものだけ
  const project: RepoConfig = { ...GITLAB_REPO, project: "web", workspace: "github.com/acme/widgets" };
  const pd: Deps = deps(mock, TOKENS, new Map(), () => new Date(NOW), [project]);
  const bad = await dispatch({ kind: "host", host: "gitlab.com", op: "createBranch", args: ["acme", "widgets", "feature-13-login", head] }, BOARD, pd);
  assert.match((bad as { error: string }).error, /<先頭の語>-<番号>-web-<slug>/);
  assert.ok(!JSON.stringify([made, issues, bad]).includes(TOKEN));
});

const FAMILY = "i0004";
const REQUESTED = `.ccnavi/approved/phases/${FAMILY}/1.requested`;

function reviewingOnGitLab(scene: string, host: "github" | "gitlab" = "gitlab"): MockGitLab {
  const mock = new MockGitLab(fixture());
  mock.branch(FAMILY, "main");
  const at = mock.push(FAMILY, reviewFamilyFiles(FAMILY), "作業とレビュー待ちの子");
  // 依頼を投稿したアカウントは id で残る（見本の lab-bot は id 201）
  mock.push(FAMILY, { [REQUESTED]: requestedMark(at, 7, host, "201") }, "ccnavi: レビューを依頼した");
  mock.attachGitLabScene(FAMILY, loadGitLabScene(scene));
  return mock;
}

async function panelOn(mock: MockGitLab) {
  const b = await collectRepo(GITLAB_REPO, glDeps(mock));
  const fam = b.families.find((f) => f.family.name === FAMILY);
  assert.ok(fam, b.error);
  return { board: b, fam, panel: fam.reviews?.[0] };
}

test("CX-T152 GitLab のレビュー済み: 依頼したアカウントの ccnavi の依頼のスレッドは数えず、ほかの未解決・変更要求で止める。通れば 1 コミットで書く", async () => {
  for (const [scene, why] of [
    ["resolved", null],
    ["impostor", /未解決のスレッドが 2 件残っている/],
    ["requested-changes", /変更要求のレビューが立っている/],
    ["paged", /未解決のスレッドが 11 件残っている/],
  ] as const) {
    const { panel } = await panelOn(reviewingOnGitLab(scene));
    assert.ok(panel, scene);
    assert.equal(panel.error, "", scene);
    if (why) assert.match(panel.problems.join("\n"), why, scene);
    else assert.deepEqual(panel.problems, [], scene);
  }
  const mock = reviewingOnGitLab("resolved");
  const out = await confirmPhase(GITLAB_REPO, FAMILY, 1, glDeps(mock));
  assert.equal(out.kind, "written", JSON.stringify(out));
  const mark = JSON.parse(mock.files(FAMILY)[`.ccnavi/approved/phases/${FAMILY}/1.reviewed`]);
  // actor は PAT の持ち主（見本の user.json）
  assert.deepEqual([mark.mr, mark.actor, mark.via], [7, "lab-reviewer", "chrome"]);
  // 依頼を記録したホストが違えば（GitHub の MR で依頼した）書かない
  const { panel } = await panelOn(reviewingOnGitLab("resolved", "github"));
  assert.match(panel?.error ?? "", /依頼したホスト（github）/);
});

test("CX-T153 GitLab のスレッドの悪意のある本文は描いても実行されず、隠れる書き方は見える形になる。MR は !番号", async () => {
  const { board } = await panelOn(reviewingOnGitLab("hostile"));
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  const md = createRenderer(dom.window as unknown as Window & typeof globalThis);
  const html = renderRepo(dom.window.document, md, board, { approve: () => undefined, withdraw: () => undefined, review: () => undefined });
  dom.window.document.body.append(html);
  const box = dom.window.document.querySelector(`[data-family="${FAMILY}"] .review`) as HTMLElement;
  assert.match(box.querySelector("h4")?.textContent ?? "", /マージリクエスト !7/);
  assert.equal(box.querySelectorAll(".thread").length, 8);
  assert.equal(box.querySelectorAll("script, img, svg, iframe, form, style, details, summary").length, 0);
  const attrs = [...box.querySelectorAll(".markdown *")].flatMap((e) => [...e.attributes].map((a) => a.name));
  assert.ok(!attrs.some((n) => /^on/i.test(n) || ["style", "hidden", "class"].includes(n)), attrs.join(","));
  const text = box.textContent ?? "";
  for (const seen of ["隠れた本文", "畳んだ", "〈HTML コメント: 見えないコメント〉"]) assert.ok(text.includes(seen), seen);
  for (const a of box.querySelectorAll("a")) (a as HTMLElement).click();
  assert.equal((dom.window as unknown as { __pwned?: string }).__pwned, undefined);
  assert.equal(box.querySelectorAll("button").length, 0);
});

/** ワークスペース（GitHub）とプロジェクト（GitLab）を 1 つの fetch で返す */
function twoHosts() {
  const hub = new MockGitHub(fixture());
  const lab = new MockGitLab(projectFixture());
  const project: RepoConfig = { ...GITLAB_REPO, project: "web", workspace: "github.com/acme/widgets" };
  const repos = [GH_REPO, project];
  const fetch: Deps["fetch"] = (url, init) => (url.startsWith("https://gitlab.com/") ? lab.fetch(url, init) : hub.fetch(url, init));
  const make = (pyCall: PyCall = py): WriteDeps => {
    const stats = newStats();
    const d: Deps = { ...deps(hub, TOKENS, new Map(), () => new Date(NOW), repos), fetch, sleep: noWait };
    return {
      call: hostCall(d, stats, "gitlab.com"),
      py: pyCall,
      cache: memoryCache(),
      now: () => new Date(NOW),
      stats,
      version: "9.9.9",
      sleep: noWait,
      kind: "gitlab",
      workspace: { repo: GH_REPO, call: hostCall(d, stats, "github.com") },
    };
  };
  return { hub, lab, project, make };
}

test("CX-T154 プロジェクトのリポジトリ: ワークスペースの統合先の共通層で判定し、プロジェクトの親のブランチへ書く。ワークスペースが無ければ止める", async () => {
  const { lab, project, make } = twoHosts();
  const b = await collectRepo(project, make());
  assert.equal(b.error, "", b.error);
  const fam = b.families.find((f) => f.family.name === "web-i0012");
  assert.ok(fam?.result?.digest, JSON.stringify(fam));
  assert.deepEqual(fam.result.batch?.map((e) => e.ticket), ["web-i0012", "web-i0012-01-01"]);
  assert.equal(fam.result.write?.allowed, true);
  const out = await approveFamily(project, "web-i0012", shownOf(b, "web-i0012"), make());
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(lab.glCommits.length, 1);
  assert.ok(".ccnavi/approved/doing/web-i0012.md" in lab.files("web-i0012"));
  // ワークスペースのリポジトリが登録されていなければ読まない
  const alone = await collectRepo(project, { ...make(), workspace: undefined });
  assert.match(alone.error, /ワークスペースのリポジトリ/);
});

test("CX-T155 GitLab・プロジェクト・「始める」で Python に投げた要求（board・plan・confirm・start）すべてに、Pyodide と手元の CPython が同じ答えを返す", { skip: native === null ? "uv が無い" : false }, async () => {
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: PyCall = async (req) => {
    const res = await py(req);
    log.push({ req, res });
    return res;
  };
  const { project, make } = twoHosts();
  const pb = await collectRepo(project, make(spy));
  assert.equal((await approveFamily(project, "web-i0012", shownOf(pb, "web-i0012"), make(spy))).kind, "written");
  const mock = reviewingOnGitLab("impostor");
  await collectRepo(GITLAB_REPO, glDeps(mock, "gitlab.com", undefined, spy));
  const starter = new MockGitLab(fixture());
  const sd = glDeps(starter, "gitlab.com", undefined, spy);
  const sb = await collectRepo(GITLAB_REPO, sd);
  assert.equal((await startIssue(GITLAB_REPO, { number: 12, title: "新しい機能" }, sb.seen ?? null, [], sd)).kind, "started");
  const ops = new Set(log.map((l) => l.req.op));
  for (const op of ["board", "plan", "confirm", "start"]) assert.ok(ops.has(op), op);
  for (const { req, res } of log) {
    assert.deepEqual(await native!.call(req), res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});

test("CX-T156 GitLab の PAT の期限は GET /personal_access_tokens/self から 1 日 1 回読み、画面には期限だけを返す", async () => {
  const mock = new MockGitLab(fixture());
  mock.glExpiry = "2026-10-03";
  const metas = new Map();
  let now = new Date(NOW);
  const d: Deps = deps(mock, TOKENS, metas, () => now, [GITLAB_REPO]);
  const call = () => dispatch({ kind: "host", host: "gitlab.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
  assert.ok((await call()).ok);
  const status = await dispatch({ kind: "token.status", host: "gitlab.com" }, BOARD, d);
  assert.ok(status.ok);
  const notice = (status as { value: { notice: { level: string; expiresAt: string } } }).value.notice;
  assert.deepEqual([notice.level, notice.expiresAt], ["soon", "2026-10-03T23:59:59.000Z"]);
  assert.ok(!JSON.stringify(status).includes(TOKEN));
  const asked = () => mock.calls.filter((c) => c.endsWith("/personal_access_tokens/self")).length;
  await call();
  assert.equal(asked(), 1);
  now = new Date(Date.parse(NOW) + 25 * 3600 * 1000);
  await call();
  assert.equal(asked(), 2);
});

test("CX-T158 ボードの「始める」の欄と「要確認」: issue の題は素の文字列で描き、押すと頼む。要確認は外すボタンを添えて出す", async () => {
  const mock = new MockGitLab(fixture());
  const b = await collectRepo(GITLAB_REPO, glDeps(mock));
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  const md = createRenderer(dom.window as unknown as Window & typeof globalThis);
  const pressed: string[] = [];
  const actions = {
    approve: () => undefined,
    withdraw: () => undefined,
    loadIssues: () => void pressed.push("issues"),
    start: (_r: RepoBoard, issue: { number: number }) => void pressed.push(`start ${issue.number}`),
    dismiss: (_r: RepoBoard, family: string) => void pressed.push(`dismiss ${family}`),
  };
  const first = renderRepo(dom.window.document, md, b, actions);
  (first.querySelector("[data-testid=start] button[data-action=issues]") as HTMLElement).click();
  const hostile = '<img src=x onerror="window.__pwned=\'issue\'">題';
  const html = renderRepo(dom.window.document, md, b, actions, {
    issues: { list: [{ number: 12, title: hostile, url: "javascript:window.__pwned='url'" }], error: "" },
    attention: { i0001: "元に戻せなかった", gone: "見えない親子のチケット" },
  });
  dom.window.document.body.append(html);
  const item = html.querySelector("[data-testid=start] li[data-issue='12']") as HTMLElement;
  assert.ok(item.textContent?.includes(hostile));
  assert.equal(item.querySelectorAll("img, a").length, 0);
  (item.querySelector("button[data-action=start]") as HTMLElement).click();
  const marks = [...html.querySelectorAll("[data-testid=attention]")];
  assert.equal(marks.length, 2);
  assert.match(marks[0].textContent ?? "", /要確認: 元に戻せなかった/);
  (marks[0].querySelector("button[data-action=dismiss]") as HTMLElement).click();
  assert.deepEqual(pressed, ["issues", "start 12", "dismiss i0001"]);
  assert.equal((dom.window as unknown as { __pwned?: string }).__pwned, undefined);
});
