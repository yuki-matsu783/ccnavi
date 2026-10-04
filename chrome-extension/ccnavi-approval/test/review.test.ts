/**
 * 承認と取り下げを入れたときのレビューで直したものの回帰試験。
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { ensureDailyAlarm, PERIOD_MINUTES } from "../src/core/alarm.js";
import { expiryNotice } from "../src/core/expiry.js";
import * as gh from "../src/core/github.js";
import type { PyCall } from "../src/core/py.js";
import { renderRepo } from "../src/core/render.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo } from "../src/core/snapshot.js";
import { approveFamily, commitMessage, withdrawTicket, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, type FixtureBranch } from "./fixtures/repo.js";
import { deps, hostCall, HOSTS, memoryCache, newStats } from "./helpers/host.js";
import { blobSha, MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };
const TODO = "wip/proposals/todo/i0001.md";
const DOING = ".ccnavi/approved/doing/i0001.md";
const noWait = async () => undefined;

function depsFor(mock: MockGitHub, repos?: RepoConfig[]): WriteDeps {
  const stats = newStats();
  const d = { ...deps(mock, new Map([["github.com", TOKEN]]), new Map(), () => new Date(), repos), sleep: noWait };
  return { call: hostCall(d, stats), py, cache: memoryCache(), now: () => new Date(NOW), stats, version: "9.9.9", sleep: noWait };
}

function client(mock: MockGitHub) {
  return { host: HOSTS[0], token: TOKEN, fetch: mock.fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
}

function parentOnly(): Record<string, FixtureBranch> {
  const f = fixture();
  delete f.i0001.files["wip/proposals/todo/i0001-01.md"];
  return f;
}

async function shown(d: WriteDeps, family = "i0001") {
  const b = await collectRepo(REPO, d);
  const r = b.families.find((f) => f.family.name === family)?.result;
  assert.ok(r?.digest, JSON.stringify(r ?? b.error));
  return { ids: (r.batch ?? []).map((e) => e.ticket), digest: r.digest, only: r.only ?? null };
}

test("CX-T118 取り下げ: GitHub が承認コミットを renamed で返しても、変更の一覧が切れていても、木を比べて「足した」と分かる", async () => {
  const mock = new MockGitHub(parentOnly());
  const d = depsFor(mock);
  assert.equal((await approveFamily(REPO, "i0001", await shown(d), d)).kind, "written");
  const approval = mock.head("i0001") as string;
  const detail = (await mock.fetch(`https://api.github.com/repos/acme/widgets/commits/${approval}`, { method: "GET", headers: { Authorization: `Bearer ${TOKEN}` } }).then((r) => r.json())) as {
    files: { filename: string; status: string; previous_filename?: string }[];
  };
  assert.deepEqual(detail.files.find((f) => f.filename === DOING), { filename: DOING, status: "renamed", previous_filename: TODO });
  mock.filesLimit = 1;
  const out = await withdrawTicket(REPO, "i0001", "i0001", "", d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.files("i0001")[TODO], parentOnly().i0001.files[TODO]);
});

test("CX-T119 取り下げ: 承認コミットが P の first-parent の鎖の上に無ければ（別の枝で承認して merge）出さない（決定 B）", async () => {
  const mock = new MockGitHub(parentOnly());
  const d = depsFor(mock);
  // 承認した中身を作るだけのコピー
  const copy = new MockGitHub(parentOnly());
  const cd = depsFor(copy);
  assert.equal((await approveFamily(REPO, "i0001", await shown(cd), cd)).kind, "written");
  const approved = copy.files("i0001");
  const events = ".ccnavi/approved/events/i0001.ndjson";
  // 別の枝 side で承認して P に merge する。git log -- path は side のコミットを返す
  mock.branch("side", "i0001");
  mock.push("side", { [TODO]: null, [DOING]: approved[DOING], [events]: approved[events] }, "side で承認");
  mock.push("i0001", { "src/app.py": "print('p')\n" }, "P の上の別の変更");
  mock.merge("i0001", "side");
  const b = await collectRepo(REPO, d);
  const w = b.families.find((x) => x.family.name === "i0001")?.result?.withdrawable ?? [];
  assert.deepEqual(w.map((x) => x.ticket), ["i0001"]);
  assert.match(w[0].problems.join(" "), /承認コミット/);
  const out = await withdrawTicket(REPO, "i0001", "i0001", "", d);
  assert.equal(out.kind, "refused");
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T120 レート制限: Retry-After が短ければ 1 回待ち直し、長ければ原因を言う。GraphQL の RATE_LIMITED も原因を言う", async () => {
  const mock = new MockGitHub(fixture());
  const waited: number[] = [];
  const c = { ...client(mock), sleep: async (ms: number) => void waited.push(ms) };
  mock.limits.push({ match: /^GET \/repos\/acme\/widgets$/, status: 403, headers: { "retry-after": "2" } });
  assert.deepEqual(await gh.repoInfo(c, "acme", "widgets"), { defaultBranch: "main" });
  assert.deepEqual(waited, [2000]);
  mock.limits.push({ match: /^GET \/repos\/acme\/widgets$/, status: 429, headers: { "retry-after": "600" } });
  await assert.rejects(gh.repoInfo(c, "acme", "widgets"), /二次のレート制限.*600 秒/);
  mock.limits.push({ match: /^GET \/repos\/acme\/widgets$/, status: 403, headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000" } });
  await assert.rejects(gh.repoInfo(c, "acme", "widgets"), /レート制限.*制限が解けるのは/);
  mock.limits.push({ match: /^POST \/graphql$/, status: 200, headers: {}, graphql: true });
  await assert.rejects(gh.viewer(c), /RATE_LIMITED/);
  mock.limits.push({ match: /^POST \/graphql$/, status: 200, headers: { "retry-after": "1" }, graphql: true });
  assert.equal(await gh.viewer(c), "alice");
});

test("CX-T121 Approve は 100 件を超えるレビューも Link で読み切る。isTruncated の blob は読めないとする", async () => {
  const mock = new MockGitHub(fixture());
  const reviews = Array.from({ length: 150 }, (_, i) => ({ user: `u${i}`, state: "COMMENTED" }));
  reviews.push({ user: "bob", state: "APPROVED" });
  mock.pulls.i0001 = [{ number: 7, reviews }];
  assert.deepEqual(await gh.pullApprovals(client(mock), "acme", "widgets", "i0001"), [{ number: 7, approvals: 1 }]);
  const text = mock.files("main")[".ccnavi/common/phases.yml"];
  mock.truncatedBlobs.add(blobSha(text));
  await assert.rejects(gh.blobs(client(mock), "acme", "widgets", [blobSha(text)]), /isTruncated/);
});

test("CX-T122 置き場のシンボリックリンクは読まずに「決まらない」。承認も書かない", async () => {
  const mock = new MockGitHub(fixture());
  const d = depsFor(mock);
  const s = await shown(d);
  mock.linkPaths.add(".ccnavi/approved/done/i0005.md");
  const d2 = depsFor(mock);
  const b = await collectRepo(REPO, d2);
  assert.match(b.families.find((f) => f.family.name === "i0001")?.result?.undecided ?? "", /シンボリックリンク/);
  const out = await approveFamily(REPO, "i0001", s, d2);
  assert.equal(out.kind, "refused");
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T123 service worker が書く頼みを断ったら（登録していないリポジトリ）、先頭が動いたと取り違えずに原因を言う", async () => {
  const mock = new MockGitHub(fixture());
  const seen = await shown(depsFor(mock));
  const before = mock.calls.length;
  const d = depsFor(mock, []);
  const out = await approveFamily(REPO, "i0001", seen, d);
  assert.equal(out.kind, "failed");
  assert.match(out.kind === "failed" ? out.message : "", /登録していない/);
  // 読み取りも登録したリポジトリだけ受けるので、ホストに何も頼まない
  assert.equal(mock.calls.length, before);
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T124 コミットの見出しは先頭の数件と件数に畳んで 200 字に収め、全件は本文に書く", () => {
  const ids = Array.from({ length: 40 }, (_, i) => `i0001-${String(i + 1).padStart(2, "0")}`);
  const m = commitMessage(ids, "を承認", "承認した", "9.9.9");
  assert.equal(m.headline, "ccnavi: i0001-01, i0001-02, i0001-03 ほか 37 件 を承認（Chrome 拡張 9.9.9）");
  assert.ok(m.headline.length <= 200);
  for (const id of ids) assert.ok(m.body.includes(`- ${id}\n`), id);
  assert.deepEqual(commitMessage(["i0001"], "を承認", "承認した", "9.9.9"), { headline: "ccnavi: i0001 を承認（Chrome 拡張 9.9.9）", body: "" });
});

test("CX-T125 統合先の .claude/settings.json の env で置き場を動かしても、承認は既定の置き場に書く", async () => {
  const f = parentOnly();
  const env = JSON.stringify({ env: { CCNAVI_TICKETS_PROPOSAL: "wip/ws", CCNAVI_TICKETS_APPROVED: "/srv/approved" } });
  for (const b of Object.values(f)) {
    b.files[".claude/settings.json"] = env;
    // env の指す先にも同じ提案を置く。読むのも書くのも既定の置き場だけ
    for (const k of Object.keys(b.files)) {
      if (k.startsWith("wip/proposals/")) b.files[k.replace("wip/proposals/", "wip/ws/")] = b.files[k];
    }
  }
  const mock = new MockGitHub(f);
  const d = depsFor(mock);
  const out = await approveFamily(REPO, "i0001", await shown(d), d);
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.deepEqual(mock.commitCalls[0].deletions, [{ path: "wip/proposals/todo/i0001.md" }]);
  assert.ok(mock.commitCalls[0].additions.every((a: { path: string }) => a.path.startsWith(".ccnavi/approved/")), JSON.stringify(mock.commitCalls[0].additions));
});

test("CX-T126 承認の画面: ccnavi の本文を承認のボタンの上に開いた形で出し、Markdown の隠れる書き方は見える形にする（決定 A）", async () => {
  const f = parentOnly();
  const hidden = [
    "# 本文",
    '<span hidden>秘密1</span>',
    '<p style="display:none">秘密2</p>',
    '<p class="sr-only">秘密3</p>',
    '<font color="white">秘密4</font>',
    "<details><summary>畳む</summary>秘密5</details>",
    "<!-- 秘密6 -->",
    '<p id="result" aria-hidden="true">秘密7</p>',
    "<!-- 閉じない 秘密8",
    "",
  ].join("\n\n");
  f.i0001.files[TODO] = f.i0001.files[TODO].replace("## やること", hidden);
  const mock = new MockGitHub(f);
  const d = depsFor(mock);
  const b = await collectRepo(REPO, d);
  const dom = new JSDOM("<!doctype html><body></body>");
  const doc = dom.window.document;
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  doc.body.append(renderRepo(doc, md, b, { approve: () => undefined, withdraw: () => undefined }));
  const box = doc.querySelector('[data-family="i0001"]') as HTMLElement;
  const screen = box.querySelector("[data-testid=screen]") as HTMLElement;
  assert.ok(screen && !screen.closest("details"));
  assert.match(screen.querySelector("pre")?.textContent ?? "", /i0001/);
  const button = box.querySelector("button[data-action=approve]") as HTMLElement;
  assert.ok(screen.compareDocumentPosition(button) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING, "画面の本文はボタンの上");
  assert.ok(screen.compareDocumentPosition(box.querySelector(".entry") as Node) & dom.window.Node.DOCUMENT_POSITION_FOLLOWING, "範囲を最初に");
  const body = box.querySelector('.entry[data-ticket="i0001"] .markdown') as HTMLElement;
  for (let i = 1; i <= 8; i += 1) assert.ok(body.textContent?.includes(`秘密${i}`), `秘密${i}`);
  assert.match(body.textContent ?? "", /〈HTML コメント: 秘密6〉/);
  assert.equal(body.querySelectorAll("details, summary, font, [hidden], [class], [style], [id], [color]").length, 0);
});

test("CX-T127 PAT の期限を比べる alarm は、service worker が起き直しても作り直さない（1 日 1 回）", async () => {
  const made: string[] = [];
  let have: { name: string; periodInMinutes?: number } | undefined;
  const api = {
    get: async () => have,
    create: async (name: string, info: { periodInMinutes?: number }) => {
      made.push(name);
      have = { name, periodInMinutes: info.periodInMinutes };
    },
  };
  assert.equal(await ensureDailyAlarm(api), true);
  for (let i = 0; i < 5; i += 1) assert.equal(await ensureDailyAlarm(api), false);
  assert.deepEqual(made, ["pat-expiry"]);
  assert.equal(have?.periodInMinutes, PERIOD_MINUTES);
});

test("CX-T128 期限はミリ秒で比べ、7 日前ちょうどから知らせる。日数は切り上げて見せる", () => {
  const now = new Date("2026-09-30T00:00:00Z");
  const at = (h: number) => new Date(now.getTime() + h * 3600000).toISOString();
  assert.equal(expiryNotice("g", { host: at(7 * 24 + 1) }, now).level, "ok");
  const edge = expiryNotice("g", { host: at(7 * 24) }, now);
  assert.deepEqual([edge.level, edge.daysLeft], ["soon", 7]);
  assert.deepEqual([expiryNotice("g", { host: at(49) }, now).daysLeft], [3]);
  // 登録のときの日付はその日の終わり（UTC）まで使える
  const manual = expiryNotice("g", { manual: "2026-09-30" }, now);
  assert.deepEqual([manual.level, manual.daysLeft], ["soon", 1]);
});
