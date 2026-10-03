/**
 * 同じ要求に、Pyodide（拡張と同じ zip）と手元の CPython が同じ答えを返すか（ADR-0093 の 6.2・7.2）。
 * ボードを 1 回組むあいだに Python へ投げた要求を全部控え、手元の CPython にも投げて比べる。
 *
 * 判定のコア（段階 2a）の見本は `test/fixtures/core-scenarios.json`。手元の試験
 * （`tests/ticket/test_core.py`）が、手元で実際に書いたバイト列と同じ答えになることを確かめて
 * 書き出したもので、ここでは Pyodide と手元の CPython がそのとおりの答えを返すことを見る。
 */
import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import type { PyCall } from "../src/core/py.js";
import { collectRepo } from "../src/core/snapshot.js";
import { approveFamily, withdrawTicket } from "../src/core/write.js";
import { fixture, NOW } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { HERE, nativePy, pyodidePy } from "./helpers/python.js";

const native = nativePy();
after(() => native?.close());

test("CX-T060 ボード 1 回ぶんの要求すべてに、Pyodide と手元の CPython が同じ答えを返す", { skip: native === null ? "uv が無い" : false }, async () => {
  const pyodide = await pyodidePy();
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: PyCall = async (req) => {
    const res = await pyodide.call(req);
    log.push({ req, res });
    return res;
  };
  const mock = new MockGitHub(fixture());
  const stats = newStats();
  await collectRepo(
    { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 60, extraBranches: [], project: "", workspace: "" },
    { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), py: spy, cache: memoryCache(), now: () => new Date(NOW), stats },
  );
  const ops = new Set(log.map((l) => l.req.op));
  for (const op of ["placement", "compat", "families", "closure", "board"]) assert.ok(ops.has(op), op);
  assert.ok(log.filter((l) => l.req.op === "board").length >= 3);
  for (const { req, res } of log) {
    const theirs = await native!.call(req);
    assert.deepEqual(theirs, res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});

test("CX-T061 判定のコアの見本（承認・マーカーの消去・改版・フィードバック計画・多段の先行・取り下げ・レビュー済み）に、Pyodide が手元の書いたとおりのバイト列を返す（uv があれば手元の CPython も）", async () => {
  // Pyodide の側は見本と比べるだけなので、uv が無くても回す。
  const pyodide = await pyodidePy();
  const file = path.join(HERE, "test", "fixtures", "core-scenarios.json");
  const scenarios = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, { request: Record<string, unknown>; answer: Record<string, unknown> }>;
  assert.deepEqual(Object.keys(scenarios).sort(), ["approve-new", "approve-reopen", "confirm", "feedback-plan", "predecessors", "revise", "withdraw"]);
  for (const [name, { request, answer }] of Object.entries(scenarios)) {
    assert.deepEqual(await pyodide.call(request), answer, `Pyodide ${name}`);
    if (native !== null) assert.deepEqual(await native.call(request), answer, `CPython ${name}`);
  }
});

test("CX-T062 承認と取り下げで Python に投げた要求（plan・withdraw を含む）すべてに、Pyodide と手元の CPython が同じ答えを返す", { skip: native === null ? "uv が無い" : false }, async () => {
  const pyodide = await pyodidePy();
  const log: { req: Record<string, unknown>; res: Record<string, unknown> }[] = [];
  const spy: PyCall = async (req) => {
    const res = await pyodide.call(req);
    log.push({ req, res });
    return res;
  };
  const branches = fixture();
  delete branches.i0001.files["wip/proposals/todo/i0001-01.md"];
  const mock = new MockGitHub(branches);
  const stats = newStats();
  const repo = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };
  const d = { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), py: spy, cache: memoryCache(), now: () => new Date(NOW), stats, version: "9.9.9" };
  const board = await collectRepo(repo, d);
  const r = board.families.find((f) => f.family.name === "i0001")?.result;
  const shown = { ids: (r?.batch ?? []).map((e) => e.ticket), digest: r?.digest ?? "", only: r?.only ?? null };
  assert.equal((await approveFamily(repo, "i0001", shown, d)).kind, "written");
  assert.equal((await withdrawTicket(repo, "i0001", "i0001", "押し間違い", d)).kind, "written");
  const ops = new Set(log.map((l) => l.req.op));
  for (const op of ["board", "plan", "withdraw"]) assert.ok(ops.has(op), op);
  for (const { req, res } of log) {
    assert.deepEqual(await native!.call(req), res, `${String(req.op)} ${String(req.family ?? "")}`);
  }
});
