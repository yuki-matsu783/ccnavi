/**
 * 実リポジトリの状態を模擬ホストに載せて Chromium に描かせる開発用スクリプト（scripts/render-scene.js）の実機の試験。
 * ブラウザと組み立て（dist-e2e/）が無いときは終了コード 3 で止まり、成功に数えない（この試験は失敗になる）。
 *
 * `node scripts/test.js --e2e` で回す（拡張の実機の試験が通信先のポートを解放した後に回る。ファイル名の順）。
 */
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { fixture, reviewFamilyFiles, requestedMark } from "../fixtures/repo.js";
import { makeRepo } from "../helpers/git-repo.js";
import { TOKEN } from "../helpers/mock-github.js";
import { HERE } from "../helpers/python.js";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-render-e2e-"));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

const SECRET_ENV = "ENV-SECRET-VALUE-91827";
const SECRET_REMOTE = "REMOTE-CRED-55731";

function realisticRepo(name: string): string {
  const day = (n: number) => new Date(Date.now() - n * 86400000).toISOString();
  const f = fixture();
  const branches: Parameters<typeof makeRepo>[1] = {};
  for (const [name, b] of Object.entries(f)) {
    branches[name] = { files: { ...b.files, ".env": `API_KEY=${SECRET_ENV}\n` }, date: name === "main" ? day(20) : name === "i0003" ? day(25) : day(1) };
  }
  branches.i0004 = { files: { ...f.main.files, ...reviewFamilyFiles("i0004"), ".ccnavi/approved/phases/i0004/1.requested": requestedMark("a".repeat(40), 7) }, date: day(1) };
  const dir = path.join(tmp, name);
  makeRepo(dir, branches);
  spawnSync("git", ["-C", dir, "remote", "add", "origin", `https://user:${SECRET_REMOTE}@example.invalid/acme/widgets.git`]);
  return dir;
}

function render(args: string[]): { code: number | null; stdout: string; stderr: string } {
  const r = spawnSync(process.execPath, [path.join(HERE, "scripts", "render-scene.js"), ...args], { cwd: HERE, encoding: "utf8", timeout: 240_000 });
  return { code: r.status, stdout: r.stdout, stderr: r.stderr };
}

test("CX-T382 render-scene: 実リポジトリのブランチを載せて描き、PNG・テキスト・DOM 要約・ログを出す。承認待ちとレビュー待ちが状態どおりに出る。PAT と .env と .git/config の認証情報はどの出力にも出ない", () => {
  const repo = realisticRepo("repo-main");
  const out = path.join(tmp, "out");
  const r = render(["--repo", repo, "--out", out]);
  assert.equal(r.code, 0, `${r.stdout}\n${r.stderr}`);
  assert.deepEqual(fs.readdirSync(out).sort(), ["board.dom.json", "board.log", "board.png", "board.txt"]);
  assert.deepEqual([...fs.readFileSync(path.join(out, "board.png")).subarray(0, 4)], [0x89, 0x50, 0x4e, 0x47]);

  const dom = JSON.parse(fs.readFileSync(path.join(out, "board.dom.json"), "utf8"));
  assert.equal(dom.schema, 1);
  assert.equal(dom.state, "done");
  assert.deepEqual(dom.families.map((x: { name: string }) => x.name), ["i0001", "i0002", "i0004"]);
  const [i1, , i4] = dom.families;
  assert.deepEqual(i1.entries.map((e: { ticket: string; waiting: string }) => [e.ticket, e.waiting]), [["i0001", "approval"]]);
  assert.deepEqual(i1.actions, ["承認する（i0001）"]);
  assert.deepEqual(i4.entries, []);
  assert.deepEqual(i4.reviews.map((x: { phase: string; waiting: string }) => [x.phase, x.waiting]), [["1", "review"]]);

  const text = fs.readFileSync(path.join(out, "board.txt"), "utf8");
  assert.match(text, /i0001 見本の親 i0001/);
  assert.match(text, /承認する（i0001）/);
  assert.match(text, /フェーズ 1 のレビュー/);

  // 出力ファイル全体（PNG のバイト列を含む）に、PAT・.env の値・リモートの認証情報が無い
  for (const name of fs.readdirSync(out)) {
    const bytes = fs.readFileSync(path.join(out, name)).toString("latin1");
    for (const secret of [TOKEN, SECRET_ENV, SECRET_REMOTE]) assert.ok(!bytes.includes(secret), `${name} に ${secret} が出ている`);
  }
  assert.ok(!r.stdout.includes(TOKEN) && !r.stderr.includes(TOKEN));
});

test("CX-T383 render-scene: --out が --repo の下なら 2 で止まり、何も書かない。--repo が無ければ 2", () => {
  const repo = realisticRepo("repo-inside");
  const inside = path.join(repo, "out");
  const r = render(["--repo", repo, "--out", inside]);
  assert.equal(r.code, 2, r.stderr);
  assert.ok(!fs.existsSync(inside));
  assert.equal(render(["--out", path.join(tmp, "o")]).code, 2);
});

test("CX-T384 render-scene: git のリポジトリでない --repo は 1 で止まり、前回の出力は残らない。同名のタグがあるブランチも載せられる", () => {
  const out = path.join(tmp, "out-stale");
  fs.mkdirSync(out);
  for (const name of ["board.png", "board.txt", "board.dom.json", "board.log"]) fs.writeFileSync(path.join(out, name), "古い出力");
  const plain = path.join(tmp, "not-a-repo");
  fs.mkdirSync(plain);
  const r = render(["--repo", plain, "--out", out]);
  assert.equal(r.code, 1, `${r.stdout}\n${r.stderr}`);
  assert.deepEqual(fs.readdirSync(out), []);

  // ブランチと同名のタグ（`%(refname:short)` だと heads/main に短縮されて読めなくなる）
  const tagged = realisticRepo("repo-tagged");
  spawnSync("git", ["-C", tagged, "tag", "main"]);
  spawnSync("git", ["-C", tagged, "tag", "i0001"]);
  const r2 = render(["--repo", tagged, "--out", path.join(tmp, "out-tagged")]);
  assert.equal(r2.code, 0, `${r2.stdout}\n${r2.stderr}`);
});
