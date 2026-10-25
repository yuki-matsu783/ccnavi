/**
 * `scripts/render-scene.js` の部品（引数・出力先の判定・失敗の条件・ログ）と、描いたボードの DOM の要約。
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { JSDOM } from "jsdom";

import { summarizeBoard } from "../src/core/dom-summary.js";
import { createRenderer } from "../src/core/sanitize.js";
import { collectRepo, type RepoBoard } from "../src/core/snapshot.js";
import type { PyCall } from "../src/core/py.js";
import type { RepoConfig } from "../src/core/settings.js";
import { fixture, NOW, reviewFamilyFiles, requestedMark } from "./fixtures/repo.js";
import { renderBoard } from "./helpers/board-dom.js";
import { COMPAT } from "./helpers/compat.js";
import { deps, hostCall, memoryCache, newStats } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";
import { pyodidePy } from "./helpers/python.js";
import { DEFAULT_WIDTH, failuresOf, foldPath, isInside, logLine, parseArgs, redact } from "./helpers/render-scene-lib.js";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-scene-test-"));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

test("CX-T378 render-scene の引数: 既定・値・誤り（知らない引数・2 度・値なし・範囲外・--repo と --out の欠け）", () => {
  const ok = parseArgs(["--repo", "r", "--out", "o"]);
  assert.deepEqual(ok, { ok: true, options: { repo: "r", out: "o", host: "github", width: DEFAULT_WIDTH } });
  assert.deepEqual(parseArgs(["--repo", "r", "--out", "o", "--host", "gitlab", "--width", "800"]), { ok: true, options: { repo: "r", out: "o", host: "gitlab", width: 800 } });
  for (const bad of [
    ["--out", "o"],
    ["--repo", "r"],
    ["--repo", "r", "--out", "o", "--x", "1"],
    ["--repo", "r", "--repo", "q", "--out", "o"],
    ["--repo", "r", "--out"],
    ["--repo", "r", "--out", "--host"],
    ["--repo", "r", "--out", "o", "--host", "bitbucket"],
    ["--repo", "r", "--out", "o", "--width", "100"],
    ["--repo", "r", "--out", "o", "--width", "9999"],
    ["--repo", "r", "--out", "o", "--width", "12.5"],
  ]) assert.equal(parseArgs(bad).ok, false, bad.join(" "));
});

test("CX-T379 --out の判定: --repo と同じか下は不可。大文字小文字・区切り・シンボリックリンクを越えて比べる。まだ無い出力先も判定する", () => {
  const repo = path.join(tmp, "Repo");
  fs.mkdirSync(path.join(repo, "sub"), { recursive: true });
  fs.mkdirSync(path.join(tmp, "elsewhere"));
  assert.equal(isInside(repo, repo), true);
  assert.equal(isInside(path.join(repo, "sub", "new", "deeper"), repo), true);
  assert.equal(isInside(path.join(tmp, "REPO", "x"), repo), true, "大文字小文字だけ違うパス");
  assert.equal(isInside(path.join(tmp, "elsewhere", "x"), repo), false);
  assert.equal(isInside(`${repo}-sibling`, repo), false, "名前の頭が同じだけの別のディレクトリ");
  fs.symlinkSync(repo, path.join(tmp, "link"));
  assert.equal(isInside(path.join(tmp, "link", "out"), repo), true, "シンボリックリンク越し");
  assert.equal(foldPath("Ａ".normalize("NFD")), foldPath("Ａ"), "NFC にそろえる");
  assert.equal(foldPath("ABC"), "abc");
});

test("CX-T380 失敗の条件と記録: ページエラー・コンソールの error・service worker の error は失敗。favicon・warn・log は失敗にしない。PAT は記録に出さない", () => {
  const entries = [
    { kind: "console", type: "log", text: "x" },
    { kind: "console", type: "warning", text: "w" },
    { kind: "console", type: "error", text: "Failed to load /favicon.ico" },
    { kind: "console", type: "error", text: "boom" },
    { kind: "pageerror", text: "oops" },
    { kind: "worker-console", type: "error", text: "sw boom" },
    { kind: "worker-console", type: "log", text: "sw log" },
    { kind: "worker-error", text: "sw throw" },
  ] as const;
  assert.deepEqual(failuresOf(entries).map((e) => e.text), ["boom", "oops", "sw boom", "sw throw"]);
  assert.equal(redact(`x ${TOKEN} y Authorization: Bearer abc.def z`, [TOKEN]), "x [PAT] y Authorization: Bearer [PAT] z");
  assert.equal(logLine({ kind: "worker-console", type: "error", text: `a\n${TOKEN}` }, [TOKEN]), "[worker.error] a\\n[PAT]");
});

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});
const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 3, extraBranches: [], project: "", workspace: "" };

test("CX-T381 DOM の要約: 親子のチケットごとに承認待ち（バッジ・操作）・レビュー待ち・承認対象外を取り出す。押せないボタンは操作に含めない", async () => {
  const branches = fixture();
  const mock = new MockGitHub(branches);
  mock.branch("i0004", "main");
  const at = mock.push("i0004", reviewFamilyFiles("i0004"), "作業とレビュー待ちの子");
  mock.push("i0004", { ".ccnavi/approved/phases/i0004/1.requested": requestedMark(at, 7) }, "依頼");
  const stats = newStats();
  const board: RepoBoard = await collectRepo(REPO, { call: hostCall(deps(mock, new Map([["github.com", TOKEN]])), stats), py, cache: memoryCache(), now: () => new Date(NOW), stats });
  const dom = new JSDOM("<!doctype html><body data-state='done'><p id='status'>完了</p></body>");
  const doc = dom.window.document;
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  renderBoard(doc, md, board, { approve() {}, review() {} });
  const s = summarizeBoard(doc);
  assert.equal(s.schema, 1);
  assert.equal(s.state, "done");
  assert.deepEqual(s.families.map((f) => f.name), ["i0001", "i0002", "i0004"]);
  const i1 = s.families[0];
  assert.deepEqual([i1.repo, i1.title], ["github.com/acme/widgets", "見本の親 i0001"]);
  assert.deepEqual(i1.entries.map((e) => [e.ticket, e.title, e.waiting, e.badges, e.actions]), [["i0001", "見本の親 i0001", "approval", [], []]]);
  assert.deepEqual(i1.actions, ["承認する（i0001）"]);
  assert.equal(i1.rejected[0].ticket, "i0001-01-01");
  assert.match(i1.rejected[0].reason, /先行 i0003-01-01 が完了していない/);
  const i4 = s.families[2];
  assert.deepEqual(i4.entries, []);
  assert.equal(i4.reviews.length, 1);
  assert.deepEqual([i4.reviews[0].phase, i4.reviews[0].waiting], ["1", "review"]);
  // 読み取りだけ（actions を渡さない）なら、承認の操作は無い
  renderBoard(doc, md, board);
  assert.deepEqual(summarizeBoard(doc).families[0].actions, []);
  // 自己完結（`fn.toString()` でページへ渡せる）: 外の値を使わない
  const detached = new Function(`return (${summarizeBoard.toString()})`)() as typeof summarizeBoard;
  assert.deepEqual(detached(doc), summarizeBoard(doc));
});
