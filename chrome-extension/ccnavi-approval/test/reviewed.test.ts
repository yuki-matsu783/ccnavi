/**
 * Chrome の「レビュー済み」（ADR-0093 の 8.9。段階 4）。模擬の GitHub と Node の上の Pyodide（拡張と同じ zip）で回す。
 *
 * - 録ったホストの応答の見本（test/fixtures/host/github/）から TS が組む写しが、sh が組んだ期待値と同じ
 * - 依頼の後の変更の一覧（compare API）は、打ち切り・祖先でない・無い、のどれでも null（動いたと数える）
 * - ボードは依頼済みのフェーズにスレッドと通らない理由を出し、通るときだけ「レビュー済みにする」を出す
 * - 押すと読み直して Python の confirm が通したときだけ 1 コミットで書く。印は PAT の持ち主（actor）と
 *   経路（chrome）を持つ。未解決・変更要求・依頼の後のコードの変更があれば書かない
 * - スレッドの本文は承認の画面と同じ規則で描く（実行されない・隠れない）
 * - PAT は画面に渡らない
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { JSDOM } from "jsdom";

import * as gh from "../src/core/github.js";
import type { PyCall } from "../src/core/py.js";
import { renderRepo } from "../src/core/render.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo } from "../src/core/snapshot.js";
import { confirmPhase, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, requestedMark, reviewFamilyFiles } from "./fixtures/repo.js";
import { BOARD, deps, hostCall, HOSTS, memoryCache, newStats } from "./helpers/host.js";
import { loadScene, SCENES, sceneAnswer, sceneFetch, sceneNames } from "./helpers/host-fixture.js";
import { dispatch } from "../src/core/protocol.js";
import { LOGIN, MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { nativePy, pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});
const native = nativePy();
after(() => native?.close());

const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [] };
const FAMILY = "i0004";
const MARK = `.ccnavi/approved/phases/${FAMILY}/1.reviewed`;
const REQUESTED = `.ccnavi/approved/phases/${FAMILY}/1.requested`;
const noWait = async () => undefined;

function client(fetch: gh.Fetch) {
  return { host: HOSTS[0], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait };
}

/** 依頼を済ませた家族 i0004 を積み、場面の見本を付けた模擬の GitHub */
function reviewing(scene: string): MockGitHub {
  const mock = new MockGitHub(fixture());
  mock.branch(FAMILY, "main");
  const at = mock.push(FAMILY, reviewFamilyFiles(FAMILY), "作業とレビュー待ちの子");
  mock.push(FAMILY, { [REQUESTED]: requestedMark(at) }, "ccnavi: レビューを依頼した");
  mock.attachScene(FAMILY, loadScene(scene));
  return mock;
}

function depsFor(mock: MockGitHub, pyCall: PyCall = py): WriteDeps {
  const stats = newStats();
  const d = { ...deps(mock, new Map([["github.com", TOKEN]])), sleep: noWait };
  return { call: hostCall(d, stats), py: pyCall, cache: memoryCache(), now: () => new Date(NOW), stats, version: "9.9.9", sleep: noWait };
}

async function panelOf(mock: MockGitHub) {
  const board = await collectRepo(REPO, depsFor(mock));
  const fam = board.families.find((f) => f.family.name === FAMILY);
  assert.ok(fam, board.error || JSON.stringify(board.families.map((f) => f.family.name)));
  return { board, fam, panel: fam.reviews?.[0] };
}

test("CX-T129 録ったホストの応答の見本ごとに、TS が組む写しは sh が組んだ期待値（expected.json）と同じ", async () => {
  assert.deepEqual(sceneNames(), ["changes-requested", "cr-commented", "full-page", "hostile", "paged", "pending", "resolved"]);
  for (const name of sceneNames()) {
    const scene = loadScene(name);
    const copy = (await gh.reviewCopy(client(sceneFetch(scene)), scene.owner, scene.repo, scene.branch)) as unknown as Record<string, unknown>;
    assert.match(String(copy.fetched_at), /^\d{4}-\d\d-\d\dT/);
    delete copy.fetched_at;
    const expected = JSON.parse(fs.readFileSync(path.join(SCENES, name, "expected.json"), "utf8"));
    assert.deepEqual(copy, expected, name);
  }
});

test("CX-T130 変更の一覧（compare）: 祖先から進んだぶんだけを返し、打ち切り・分かれた・無いときは null", async () => {
  const mock = reviewing("resolved");
  const c = client(mock.fetch);
  const at = JSON.parse(mock.files(FAMILY)[REQUESTED]).head as string;
  const head = mock.head(FAMILY) as string;
  assert.deepEqual(await gh.compareFiles(c, "acme", "widgets", at, head), { base: at, head, files: [REQUESTED] });
  // GitHub は一覧を 300 件で切る。300 件あれば打ち切られたとみなす
  const many: Record<string, string> = {};
  for (let i = 0; i < gh.COMPARE_FILES_LIMIT; i += 1) many[`.ccnavi/approved/events/x${i}.ndjson`] = `${i}\n`;
  mock.push(FAMILY, many);
  assert.equal((await gh.compareFiles(c, "acme", "widgets", at, mock.head(FAMILY) as string)).files, null);
  mock.branch("other", "main");
  const other = mock.push("other", { "src/x.py": "y\n" });
  assert.equal((await gh.compareFiles(c, "acme", "widgets", at, other)).files, null);
  assert.equal((await gh.compareFiles(c, "acme", "widgets", "f".repeat(40), other)).files, null);
  // 改名は元と先の両方を入れる（手元の --no-renames と同じ）
  const renamed = async () => ({ status: 200, ok: true, json: async () => ({ status: "ahead", files: [{ filename: "b.py", previous_filename: "a.py", status: "renamed" }] }), headers: { get: () => null } });
  assert.deepEqual((await gh.compareFiles(client(renamed), "acme", "widgets", at, head)).files, ["b.py", "a.py"]);
});

test("CX-T131 ボード: 依頼済みのフェーズにスレッドを出し、通るとき（解決済み・Approve）だけ「レビュー済みにする」を出す", async () => {
  const dom = new JSDOM("<!doctype html><body></body>");
  const md = createRenderer(dom.window as unknown as Window & typeof globalThis);
  const noop = { approve: () => undefined, withdraw: () => undefined, review: () => undefined };
  for (const [scene, button, why] of [
    ["resolved", 1, null],
    ["full-page", 1, null],
    // GitHub では目印で始まるスレッドも数える（11.8.1 の決定 C）
    ["paged", 0, /未解決のスレッドが 4 件残っている/],
    ["changes-requested", 0, /変更要求のレビューが立っている/],
    // 変更要求の後のコメントだけ・書きかけのレビューは変更要求を消さない（決定 A）
    ["cr-commented", 0, /変更要求のレビューが立っている/],
    ["pending", 0, /変更要求のレビューが立っている/],
  ] as const) {
    const mock = reviewing(scene);
    const { board, panel } = await panelOf(mock);
    assert.ok(panel, scene);
    assert.equal(panel.error, "", scene);
    assert.equal(panel.phase, 1);
    assert.deepEqual(panel.children, [`${FAMILY}-01`]);
    if (why) assert.match(panel.problems.join("\n"), why, scene);
    else assert.deepEqual(panel.problems, [], scene);
    const html = renderRepo(dom.window.document, md, board, noop);
    const box = html.querySelector(`[data-family="${FAMILY}"] .review[data-phase="1"]`);
    assert.ok(box, scene);
    assert.equal(box.querySelectorAll("button[data-action=review]").length, button, scene);
    assert.equal(box.querySelectorAll(".thread").length, panel.copy?.threads.length);
    // 開いただけでは何も書かない
    assert.equal(mock.commitCalls.length, 0);
  }
});

test("CX-T132 レビュー済みにする: 子を done/ へ動かし、印（actor = PAT の持ち主・via chrome）を 1 コミットで書く", async () => {
  const mock = reviewing("resolved");
  const before = mock.head(FAMILY);
  const out = await confirmPhase(REPO, FAMILY, 1, depsFor(mock));
  assert.equal(out.kind, "written", JSON.stringify(out));
  assert.equal(mock.commitCalls.length, 1);
  const call = mock.commitCalls[0];
  assert.deepEqual([call.branch, call.expected, call.result], [FAMILY, before, "written"]);
  assert.equal(call.headline, `ccnavi: ${FAMILY} のフェーズ 1 のレビュー済みを置いた（Chrome 拡張 9.9.9）`);
  const files = mock.files(FAMILY);
  const mark = JSON.parse(files[MARK]);
  assert.deepEqual(Object.keys(mark), ["mr", "accepted", "actor", "via", "at"]);
  assert.deepEqual([mark.mr, mark.accepted, mark.actor, mark.via], [42, [], LOGIN, "chrome"]);
  assert.ok(`.ccnavi/approved/done/${FAMILY}-01.md` in files);
  assert.ok(!(`wip/proposals/review/${FAMILY}-01.md` in files));
  const last = JSON.parse(files[`.ccnavi/approved/events/${FAMILY}.ndjson`].trim().split("\n").pop() as string);
  assert.deepEqual([last.kind, last.mark, last.via, last.actor, last.version], ["phase-mark", "reviewed", "chrome", LOGIN, "9.9.9"]);
  // 書いた後のボードからは候補が消える
  const { fam } = await panelOf(mock);
  assert.deepEqual(fam.reviews, []);
});

test("CX-T133 未解決・変更要求が残れば書かない。ボードを開いた後に未解決が増えても、押したときに読み直して止める", async () => {
  for (const scene of ["paged", "changes-requested", "cr-commented", "pending", "hostile"]) {
    const mock = reviewing(scene);
    const out = await confirmPhase(REPO, FAMILY, 1, depsFor(mock));
    assert.equal(out.kind, "refused", scene);
    assert.equal(mock.commitCalls.length, 0, scene);
  }
  const mock = reviewing("resolved");
  const { panel } = await panelOf(mock);
  assert.deepEqual(panel?.problems, []);
  mock.attachScene(FAMILY, loadScene("paged"));
  const out = await confirmPhase(REPO, FAMILY, 1, depsFor(mock));
  assert.equal(out.kind, "refused");
  assert.match(out.message, /未解決のスレッドが 4 件残っている/);
  assert.equal(mock.commitCalls.length, 0);
});

test("CX-T134 依頼の後に人が見るもの（置き場の外）が動いたら書かない。置き場だけの変更なら書く", async () => {
  const mock = reviewing("resolved");
  mock.push(FAMILY, { "src/late.py": "print(1)\n" }, "依頼の後のコード");
  const out = await confirmPhase(REPO, FAMILY, 1, depsFor(mock));
  assert.equal(out.kind, "refused");
  assert.match(out.message, /依頼の後に親の HEAD が動いている/);
  assert.equal(mock.commitCalls.length, 0);

  const ok = reviewing("resolved");
  ok.push(FAMILY, { [`.ccnavi/approved/events/${FAMILY}.ndjson`]: '{"at": "2026-09-29T00:00:00Z", "ticket": "i0004", "kind": "phase-mark", "via": "hook", "phase": 1, "mark": "pending"}\n' });
  assert.equal((await confirmPhase(REPO, FAMILY, 1, depsFor(ok))).kind, "written");
});

test("CX-T135 スレッドの悪意のある本文は描いても実行されず、隠れる書き方は見える形になる", async () => {
  const mock = reviewing("hostile");
  const { board, panel } = await panelOf(mock);
  assert.equal(panel?.copy?.threads.length, 8);
  const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only" });
  const md = createRenderer(dom.window as unknown as Window & typeof globalThis);
  const html = renderRepo(dom.window.document, md, board, { approve: () => undefined, withdraw: () => undefined, review: () => undefined });
  dom.window.document.body.append(html);
  const box = dom.window.document.querySelector(`[data-family="${FAMILY}"] .review`) as HTMLElement;
  assert.equal(box.querySelectorAll("script, img, svg, iframe, form, style, details, summary, font").length, 0);
  const attrs = [...box.querySelectorAll("*")].flatMap((e) => [...e.attributes].map((a) => a.name));
  assert.ok(!attrs.some((n) => /^on/i.test(n) || n === "style" || n === "hidden"), attrs.join(","));
  for (const e of box.querySelectorAll(".markdown *")) {
    for (const a of e.attributes) assert.ok(!/^on/i.test(a.name) && !["style", "hidden", "class", "color", "open"].includes(a.name), `${e.tagName} ${a.name}`);
  }
  const hrefs = [...box.querySelectorAll(".markdown a")].map((a) => a.getAttribute("href")).filter((h) => h !== null);
  assert.deepEqual(hrefs, []);
  const text = box.textContent ?? "";
  for (const seen of ["畳んで隠した指摘", "〈HTML コメント: 隠したつもりの指摘〉", "hidden で隠した指摘", "白文字の指摘", "style で隠した指摘"]) {
    assert.ok(text.includes(seen), seen);
  }
  for (const a of box.querySelectorAll("a")) (a as HTMLElement).click();
  assert.equal((dom.window as unknown as { __pwned?: string }).__pwned, undefined);
  // 未解決が残るのでボタンは出さない
  assert.equal(box.querySelectorAll("button").length, 0);
});

test("CX-T136 service worker の読み取り（スレッドの写し・変更の一覧）の答えに PAT は入らない。MR の無いブランチは断る", async () => {
  const mock = reviewing("resolved");
  const d = deps(mock, new Map([["github.com", TOKEN]]));
  const at = JSON.parse(mock.files(FAMILY)[REQUESTED]).head as string;
  const answers = [
    await dispatch({ kind: "host", host: "github.com", op: "reviewCopy", args: ["acme", "widgets", FAMILY] }, BOARD, d),
    await dispatch({ kind: "host", host: "github.com", op: "compareFiles", args: ["acme", "widgets", at, mock.head(FAMILY)] }, BOARD, d),
  ];
  for (const a of answers) assert.ok(a.ok, JSON.stringify(a));
  assert.ok(!JSON.stringify(answers).includes(TOKEN));
  const none = await dispatch({ kind: "host", host: "github.com", op: "reviewCopy", args: ["acme", "widgets", "feature-x"] }, BOARD, d);
  assert.equal(none.ok, false);
  assert.match((none as { error: string }).error, /開いた MR が無い/);
});

test("CX-T137 レビュー済みで Python に投げた要求（board・confirm）すべてに、Pyodide と手元の CPython が同じ答えを返す", { skip: native === null ? "uv が無い" : false }, async () => {
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: PyCall = async (req) => {
    const res = await py(req);
    log.push({ req, res });
    return res;
  };
  const mock = reviewing("resolved");
  await collectRepo(REPO, depsFor(mock, spy));
  assert.equal((await confirmPhase(REPO, FAMILY, 1, depsFor(mock, spy))).kind, "written");
  const confirms = log.filter((l) => l.req.op === "confirm");
  assert.ok(confirms.some((l) => l.res.need_compare), "compare を求めた回がある");
  assert.ok(confirms.some((l) => l.req.compare && l.res.changes), "compare を渡して書くものを出した回がある");
  for (const { req, res } of log) {
    assert.deepEqual(await native!.call(req), res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});

test("CX-T139 レビューの一覧が 404・並びでないなら投げ（レビュー無しと読まない）、compare の一覧が並びでないなら null", async () => {
  const answer = (status: number, json: unknown) => async () => ({ status, ok: status < 300, json: async () => json, headers: { get: () => null } });
  await assert.rejects(gh.pullReviews(client(answer(404, { message: "Not Found" })), "acme", "widgets", 42), /404/);
  await assert.rejects(gh.pullReviews(client(answer(200, { message: "?" })), "acme", "widgets", 42), /並びでない/);
  const a = "a".repeat(40);
  const b = "b".repeat(40);
  assert.equal((await gh.compareFiles(client(answer(200, { status: "ahead" })), "acme", "widgets", a, b)).files, null);
  assert.equal((await gh.compareFiles(client(answer(200, { status: "ahead", files: "x" })), "acme", "widgets", a, b)).files, null);
  assert.deepEqual((await gh.compareFiles(client(answer(200, { status: "identical", files: [] })), "acme", "widgets", a, b)).files, []);
});

test("CX-T140 レビュー済みの読み取りの受け口も、設定画面で登録したリポジトリだけ受ける", async () => {
  const mock = reviewing("resolved");
  const d = deps(mock, new Map([["github.com", TOKEN]]), new Map(), () => new Date(), []);
  const at = JSON.parse(mock.files(FAMILY)[REQUESTED]).head as string;
  for (const [op, args] of [
    ["reviewCopy", ["acme", "widgets", FAMILY]],
    ["compareFiles", ["acme", "widgets", at, mock.head(FAMILY)]],
  ] as const) {
    const res = await dispatch({ kind: "host", host: "github.com", op, args }, BOARD, d);
    assert.equal(res.ok, false, op);
    assert.match((res as { error: string }).error, /登録していないリポジトリ/);
  }
  assert.ok(!mock.calls.some((c) => c.includes("/pulls")), mock.calls.join("\n"));
});

test("CX-T141 同じ家族の依頼済みのフェーズが 2 つでも、MR・スレッド・レビューは 1 度だけ読む", async () => {
  const mock = new MockGitHub(fixture());
  mock.branch(FAMILY, "main");
  const at = mock.push(FAMILY, reviewFamilyFiles(FAMILY, 2), "2 フェーズぶんのレビュー待ち");
  mock.push(FAMILY, { [REQUESTED]: requestedMark(at), [`.ccnavi/approved/phases/${FAMILY}/2.requested`]: requestedMark(at) }, "依頼");
  mock.attachScene(FAMILY, loadScene("resolved"));
  const { fam } = await panelOf(mock);
  assert.deepEqual(fam.reviews?.map((p) => [p.phase, p.error, p.problems.length]), [
    [1, "", 0],
    [2, "", 0],
  ]);
  const count = (re: RegExp) => mock.calls.filter((c) => re.test(c)).length;
  assert.equal(count(/^GET \/repos\/acme\/widgets\/pulls$/), 1);
  assert.equal(count(/^GET \/repos\/acme\/widgets\/pulls\/42\/reviews$/), 1);
});

test("CX-T142 レビュー済みのフェーズに Chrome から重ねて書かない（押した時点で読み直して止める）", async () => {
  const mock = reviewing("resolved");
  assert.equal((await confirmPhase(REPO, FAMILY, 1, depsFor(mock))).kind, "written");
  const again = await confirmPhase(REPO, FAMILY, 1, depsFor(mock));
  assert.equal(again.kind, "refused");
  assert.match(again.message, /フェーズ 1 はレビュー済み/);
  assert.equal(mock.commitCalls.length, 1);
});

test("CX-T143 見本の代役は、問い合わせが見本の欄を落としていれば答えない（欄を削っても試験が通らないように）", () => {
  const scene = loadScene("resolved");
  const full = "query { repository { pullRequest { reviewThreads { pageInfo { hasNextPage endCursor } nodes { id isResolved comments { nodes { url path line body createdAt } } } } } } }";
  const ask = (q: string) => sceneAnswer(scene, "POST", new URL("https://api.github.com/graphql"), JSON.stringify({ query: q, variables: { number: 42, after: null } }));
  assert.ok((ask(full)?.json as { data?: unknown }).data);
  const without = ask(full.replace(" isResolved", ""))?.json as { errors?: { message: string }[] };
  assert.match(without.errors?.[0].message ?? "", /isResolved/);
});
