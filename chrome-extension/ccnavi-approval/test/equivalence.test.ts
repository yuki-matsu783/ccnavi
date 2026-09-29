/**
 * 同じ要求に、Pyodide（拡張と同じ zip）と手元の CPython が同じ答えを返すか（ADR-0093 の 6.2・7.2）。
 * ボードを 1 回組むあいだに Python へ投げた要求を全部控え、手元の CPython にも投げて比べる。
 */
import { after, test } from "node:test";
import assert from "node:assert/strict";

import type { PyCall } from "../src/core/py.js";
import { collectRepo } from "../src/core/snapshot.js";
import { fixture, NOW } from "./fixtures/repo.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { nativePy, pyodidePy } from "./helpers/python.js";

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
    { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 60, extraBranches: [] },
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
