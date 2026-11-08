/**
 * 同じ要求に、WASM（拡張と同じ dist/ccnavi.wasm。仮のツリーはメモリの上の fs）と、同じ入口を手元向けに組んだもの
 * （手元のファイルシステム）が同じ答えを返すか。ボードを 1 回組むあいだに ccnavi へ投げた要求を全部記録し、手元向けの
 * ものにも投げて比べる。
 *
 * 判定のコアの見本は `test/fixtures/core-scenarios.json`。手元の試験
 * （`tests/ticket/test_swap_points.py`）が、手元で実際に書いたバイト列と同じ答えになることを確かめて
 * 書き出したもので、ここでは WASM と手元向けのものがそのとおりの答えを返すことを見る。
 */
import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import type { CcnaviCall } from "../src/core/ccnavi.js";
import { collectRepo } from "../src/core/snapshot.js";
import { approveFamily } from "../src/core/write.js";
import { fixture, NOW } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { HERE, nativeCcnavi, wasmCcnavi } from "./helpers/ccnavi.js";

const native = nativeCcnavi();
after(() => native.close());

test("CX-T060 ボード 1 回ぶんの要求すべてに、WASM と手元向けの ccnavi が同じ答えを返す", async () => {
  const wasm = await wasmCcnavi();
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: CcnaviCall = async (req) => {
    const res = await wasm.call(req);
    log.push({ req, res });
    return res;
  };
  const mock = new MockGitHub(fixture());
  const stats = newStats();
  await collectRepo(
    { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 60, extraBranches: [], project: "", workspace: "" },
    { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), ccnavi: spy, cache: memoryCache(), now: () => new Date(NOW), stats },
  );
  const ops = new Set(log.map((l) => l.req.op));
  for (const op of ["placement", "compat", "families", "closure", "board"]) assert.ok(ops.has(op), op);
  assert.ok(log.filter((l) => l.req.op === "board").length >= 3);
  for (const { req, res } of log) {
    const theirs = await native.call(req);
    assert.deepEqual(theirs, res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});

test("CX-T061 判定のコアの見本（承認・マーカーの消去・改版・フィードバック計画・多段の先行・レビュー済み）に、WASM と手元向けの ccnavi が手元の書いたとおりのバイト列を返す", async () => {
  const wasm = await wasmCcnavi();
  const file = path.join(HERE, "test", "fixtures", "core-scenarios.json");
  const scenarios = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, { request: Record<string, unknown>; answer: Record<string, unknown> }>;
  assert.deepEqual(Object.keys(scenarios).sort(), ["approve-new", "approve-reopen", "confirm", "feedback-plan", "predecessors", "revise"]);
  for (const [name, { request, answer }] of Object.entries(scenarios)) {
    assert.deepEqual(await wasm.call(request), answer, `WASM ${name}`);
    assert.deepEqual(await native.call(request), answer, `手元 ${name}`);
  }
});

test("CX-T062 承認で ccnavi に投げた要求（plan を含む）すべてに、WASM と手元向けの ccnavi が同じ答えを返す", async () => {
  const wasm = await wasmCcnavi();
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: CcnaviCall = async (req) => {
    const res = await wasm.call(req);
    log.push({ req, res });
    return res;
  };
  const branches = fixture();
  delete branches.i0001.files["wip/proposals/todo/i0001-01-01.md"];
  const mock = new MockGitHub(branches);
  const stats = newStats();
  const repo = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };
  const d = { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), ccnavi: spy, cache: memoryCache(), now: () => new Date(NOW), stats, version: "9.9.9" };
  const board = await collectRepo(repo, d);
  const r = board.families.find((f) => f.family.name === "i0001")?.result;
  const shown = { ids: (r?.batch ?? []).map((e) => e.ticket), digest: r?.digest ?? "", only: r?.only ?? null };
  assert.equal((await approveFamily(repo, "i0001", shown, d)).kind, "written");
  const ops = new Set(log.map((l) => l.req.op));
  for (const op of ["board", "plan"]) assert.ok(ops.has(op), op);
  for (const { req, res } of log) {
    assert.deepEqual(await native.call(req), res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});
