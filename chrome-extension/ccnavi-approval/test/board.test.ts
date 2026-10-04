/**
 * 読み取り専用ボードの組み立て（ADR-0093 段階 1）。模擬の GitHub と Node の上の Pyodide（拡張と同じ zip）で回す。
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import type { PyCall } from "../src/core/py.js";
import { renderRepo } from "../src/core/render.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, type BlobCache, type RepoBoard } from "../src/core/snapshot.js";
import { fixture, NOW, type FixtureBranch } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { COMPAT } from "./helpers/compat.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };

async function run(
  branches: Record<string, FixtureBranch> = fixture(),
  repo: Partial<RepoConfig> = {},
  cache: BlobCache = memoryCache(),
): Promise<{ board: RepoBoard; mock: MockGitHub; requests: Record<string, unknown>[] }> {
  const mock = new MockGitHub(branches);
  const stats = newStats();
  const requests: Record<string, unknown>[] = [];
  const spy: PyCall = (req) => {
    requests.push(req);
    return py(req);
  };
  const board = await collectRepo({ ...REPO, ...repo }, { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), py: spy, cache, now: () => new Date(NOW), stats });
  return { board, mock, requests };
}

const family = (b: RepoBoard, name: string) => b.families.find((f) => f.family.name === name);

test("CX-T040 直近のブランチから親のブランチを見分ける。コードだけのブランチと統合先は親のブランチにしない", async () => {
  const { board } = await run();
  assert.equal(board.error, "");
  assert.deepEqual(board.integration && { name: board.integration.name, source: board.integration.source }, { name: "main", source: "default" });
  assert.deepEqual(board.candidates, ["feature-x", "i0001", "i0002"]);
  assert.deepEqual(board.families.map((f) => f.family.name), ["i0001", "i0002"]);
  assert.equal(board.compat?.same, true);
});

test("CX-T041 先行の閉包の親のブランチ（直近の外）を読み足し、統合先で閉じた親のブランチは読まない", async () => {
  const { board, mock } = await run();
  const f = family(board, "i0001");
  assert.deepEqual(f?.result?.closure.families, ["i0001", "i0003"]);
  assert.ok(mock.calls.includes("GET /repos/acme/widgets/git/ref/heads/i0003"));
  assert.ok(!mock.calls.includes("GET /repos/acme/widgets/git/ref/heads/i0005"), "閉じた親のブランチは読まない");
  // 今の ccnavi の答え: 先行 i0003-01 が閉じていないので子は承認の対象にしない
  assert.deepEqual(f?.result?.batch?.map((e) => e.ticket), ["i0001"]);
  assert.equal(f?.result?.rejected?.[0].ticket, "i0001-01");
  assert.match(f?.result?.rejected?.[0].problems[0] ?? "", /先行 i0003-01 が閉じていない/);
  assert.equal(f?.result?.batch?.[0].path, "i0001:wip/proposals/todo/i0001.md");
});

test("CX-T042 判定の入力は統合先・P・閉包だけ。表示用のブランチを増やしても答えは変わらない（D2）", async () => {
  const narrow = await run();
  const boards = narrow.requests.filter((r) => r.op === "board" && r.family === "i0001");
  assert.equal(boards.length, 1);
  const input = boards[0].snapshot as { branches: Record<string, unknown> };
  assert.deepEqual(Object.keys(input.branches).sort(), ["i0001", "i0003", "main"]);

  const wide = await run(fixture(), { recentDays: 60, extraBranches: ["i0003"] });
  assert.deepEqual(wide.board.families.map((f) => f.family.name), ["i0001", "i0002", "i0003"]);
  const a = family(narrow.board, "i0001")?.result;
  const b = family(wide.board, "i0001")?.result;
  assert.deepEqual({ ...b, schema: 0 }, { ...a, schema: 0 });
});

test("CX-T043 先行の親のブランチが無いときは、今の ccnavi のとおり子を承認の対象にしない", async () => {
  const { board } = await run();
  const f = family(board, "i0002");
  assert.deepEqual(f?.result?.closure.absent, ["i0007"]);
  assert.equal(f?.result?.rejected?.[0].ticket, "i0002-01");
  assert.match(f?.result?.rejected?.[0].problems[0] ?? "", /i0007-01 がどの置き場/);
});

test("CX-T044 統合先の名前: 設定したブランチが無ければ止めて名前を出す。設定どおりなら「設定」と出す", async () => {
  const missing = await run(fixture(), { integration: "develop" });
  assert.equal(missing.board.error, "統合先 develop がリモートに無い。設定を直してください");
  assert.equal(missing.board.families.length, 0);
  const set = await run(fixture(), { integration: "main" });
  assert.equal(set.board.integration?.source, "setting");
});

test("CX-T045 互換のマーカーが違えば、どちらを更新するかを言う（7.3）", async () => {
  const newer = await run(fixture(COMPAT + 1));
  assert.equal(newer.board.compat?.same, false);
  assert.equal(newer.board.compat?.message, `拡張は互換 ${COMPAT}、リポジトリは互換 ${COMPAT + 1}。拡張を更新する`);
  const b = fixture();
  b.main.files[".ccnavi/scripts/ccnavi-common.sh"] = "#!/bin/sh\n";
  const none = await run(b);
  assert.match(none.board.compat?.message ?? "", /互換の版（CCNAVI_COMPAT）が書かれていない/);
});

test("CX-T046 blob は sha でキャッシュし、2 回目は tree だけを読む（8.2）", async () => {
  const cache = memoryCache();
  const first = await run(fixture(), {}, cache);
  assert.ok(first.board.stats.blobsFetched > 0);
  const second = await run(fixture(), {}, cache);
  assert.equal(second.board.stats.blobsFetched, 0);
  assert.ok(second.board.stats.graphql < first.board.stats.graphql);
  // 読み取りの回数は ADR の見積もりの桁（承認 1 回で 40 回ほど）に収まる
  assert.ok(first.board.stats.rest + first.board.stats.graphql < 40, JSON.stringify(first.board.stats));
});

test("CX-T047 置き場のパスは統合先の .claude/settings.json から読む", async () => {
  const b = fixture();
  b.main.files[".claude/settings.json"] = JSON.stringify({ env: { CCNAVI_TICKETS_PROPOSAL: "wip/tickets" } });
  const moved = b.i0001.files;
  for (const k of Object.keys(moved)) {
    if (k.startsWith("wip/proposals/")) {
      moved[k.replace("wip/proposals/", "wip/tickets/")] = moved[k];
      delete moved[k];
    }
  }
  b.i0001.files[".claude/settings.json"] = b.main.files[".claude/settings.json"];
  const { board } = await run(b);
  assert.deepEqual(family(board, "i0001")?.result?.batch?.map((e) => e.path), ["i0001:wip/tickets/todo/i0001.md"]);
});

test("CX-T048 置き場がリポジトリの外を指すワークスペースは読まない（3.1 の 12）", async () => {
  const b = fixture();
  b.main.files[".claude/settings.json"] = JSON.stringify({ env: { CCNAVI_TICKETS_APPROVED: "/srv/approved" } });
  const { board } = await run(b);
  assert.match(board.error, /リポジトリの外を指している/);
});

test("CX-T049 先行の閉包の親のブランチが 16 を超えたら決まらないで止める（3.3 の 5）", async () => {
  const b = fixture();
  const base = b.main.files;
  const chain = Array.from({ length: 17 }, (_, i) => `c${String(i + 1).padStart(2, "0")}x`);
  const text = (id: string, pred: string) =>
    `---\nversion: 1\nticket: ${id}\nparent: ${id.slice(0, -3)}\nphase: 1\npredecessors:\n  - ${pred}\nhuman_review:\n  required: false\n  reason: r\ntitle: t\nrationale: r\nallow:\n  - match: Write|Edit\n    glob: "wip/research/*"\n---\n\n本文\n`;
  b.i0001.files["wip/proposals/todo/i0001-01.md"] = text("i0001-01", `${chain[0]}-01`);
  chain.forEach((fam, i) => {
    b[fam] = { committedDate: "2026-09-01T00:00:00Z", files: { ...base, [`wip/proposals/todo/${fam}-01.md`]: text(`${fam}-01`, `${chain[i + 1] ?? "zz"}-01`) } };
  });
  const { board } = await run(b);
  const r = family(board, "i0001")?.result;
  assert.equal(r?.closure.over_limit, true);
  assert.match(r?.undecided ?? "", /16 を超える親のブランチ/);
});

test("CX-T050 ボードの DOM: 承認などのボタンを出さず、悪意のある本文は消毒して描く", async () => {
  const { board } = await run();
  const dom = new JSDOM("<!doctype html><body></body>");
  const doc = dom.window.document;
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  doc.body.append(renderRepo(doc, md, board));
  assert.equal(doc.querySelectorAll("button, form, input").length, 0);
  assert.match(doc.querySelector("[data-testid=integration]")?.textContent ?? "", /^統合先: main（ホストのデフォルトブランチ）/);
  const hostile = doc.querySelector('[data-ticket="i0002"] .markdown');
  assert.ok(hostile);
  assert.equal(hostile.querySelectorAll("script, img, svg, iframe, form").length, 0);
  assert.equal(hostile.querySelector("h1")?.textContent, "悪意のある本文");
  // 画面の本文（ccnavi が出したもの）は textContent で入る
  assert.equal(doc.querySelector('[data-family="i0001"] pre')?.children.length, 0);
});
