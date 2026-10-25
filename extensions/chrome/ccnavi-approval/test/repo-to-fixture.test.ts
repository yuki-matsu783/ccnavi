/**
 * 実リポジトリを模擬ホストの入力にする（`repoToFixture`）。許可リスト・サイズの上限・秘密情報の除外・読むだけであること。
 */
import { after, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { makeRepo } from "./helpers/git-repo.js";
import { FixtureLimitError, MAX_FILE_BYTES, repoToFixture } from "./helpers/repo-to-fixture.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-fixture-test-"));
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

const SECRET = "SECRET-VALUE-0123";
const git = (dir: string, ...args: string[]) => execFileSync("git", ["-C", dir, ...args], { encoding: "utf8" });

function sample(name: string, extra: Record<string, string | Buffer> = {}): string {
  const dir = path.join(tmp, name);
  makeRepo(dir, {
    main: { date: "2026-10-01T00:00:00Z", files: { "README.md": "見本\n", ".ccnavi/scripts/ccnavi-common.sh": "CCNAVI_COMPAT=1\n", ".env": `KEY=${SECRET}\n` } },
    i0001: {
      date: "2026-10-24T09:00:00Z",
      files: {
        "README.md": "見本\n",
        ".env": `KEY=${SECRET}\n`,
        "src/app.py": "print('x')\n",
        "wip/proposals/todo/i0001.md": "親の提案\n",
        ".ccnavi/approved/doing/i0001.md": "作業中\n",
        ".ccnavi/config/common/rules.yml": "version: 1\n",
        ".ccnavi/config/project/.env": `KEY=${SECRET}\n`,
        ".ccnavi/approved/id_rsa": `KEY=${SECRET}\n`,
        "wip/dump.bin": Buffer.from([1, 2, 0, 3]),
        ".claude/settings.json": JSON.stringify({ env: { CCNAVI_TICKET_CONTROL: "enable", ANTHROPIC_API_KEY: SECRET }, hooks: { x: SECRET } }),
        ...extra,
      },
    },
  });
  git(dir, "remote", "add", "origin", `https://user:${SECRET}@example.invalid/acme/widgets.git`);
  return dir;
}

test("CX-T371 repoToFixture: ブランチごとに許可リストのファイルと先頭の日付だけを載せる。.env・鍵・コード・バイナリは載せない", () => {
  const dir = sample("allow");
  const fx = repoToFixture(dir);
  assert.equal(fx.defaultBranch, "main");
  assert.deepEqual(Object.keys(fx.branches).sort(), ["i0001", "main"]);
  assert.equal(fx.branches.i0001.committedDate, "2026-10-24T09:00:00.000Z");
  assert.deepEqual(Object.keys(fx.branches.i0001.files).sort(), [
    ".ccnavi/approved/doing/i0001.md",
    ".ccnavi/config/common/rules.yml",
    ".claude/settings.json",
    "wip/proposals/todo/i0001.md",
  ]);
  assert.deepEqual(Object.keys(fx.branches.main.files), [".ccnavi/scripts/ccnavi-common.sh"]);
  assert.deepEqual(fx.skipped["バイナリ"], ["wip/dump.bin"]);
  assert.deepEqual((fx.skipped["秘密情報の名前"] ?? []).sort(), [".ccnavi/approved/id_rsa", ".ccnavi/config/project/.env"]);
  // 秘密は、どのブランチのどのファイルにも、模擬の応答にも入らない（.git/config のリモートの認証情報も）
  assert.ok(!JSON.stringify(fx).includes(SECRET));
});

test("CX-T372 repoToFixture: .claude/settings.json は env の CCNAVI_ だけを残す", () => {
  const fx = repoToFixture(sample("settings"));
  assert.deepEqual(JSON.parse(fx.branches.i0001.files[".claude/settings.json"]), { env: { CCNAVI_TICKET_CONTROL: "enable" } });
});

test("CX-T373 repoToFixture: 1 ファイルの上限を超えたら止まる。上限ちょうどは通る", () => {
  assert.doesNotThrow(() => repoToFixture(sample("exact", { "wip/big.md": "a".repeat(MAX_FILE_BYTES) })));
  assert.throws(() => repoToFixture(sample("over", { "wip/big.md": "a".repeat(MAX_FILE_BYTES + 1) })), (e: unknown) => e instanceof FixtureLimitError && /1 ファイルの上限/.test(e.message));
});

test("CX-T374 repoToFixture: 全体の上限を超えたら止まる", () => {
  const files: Record<string, string> = {};
  for (let i = 0; i < 40; i += 1) files[`wip/part${i}.md`] = `${i % 10}`.repeat(MAX_FILE_BYTES - 10);
  assert.throws(() => repoToFixture(sample("total", files)), (e: unknown) => e instanceof FixtureLimitError && /全体の上限/.test(e.message));
});

test("CX-T375 repoToFixture: リポジトリに何も書かない（ブランチ・参照・作業ツリー・.git の設定が変わらない）", () => {
  const dir = sample("readonly");
  const before = [git(dir, "for-each-ref"), git(dir, "status", "--porcelain=v1", "--ignored"), fs.readFileSync(path.join(dir, ".git", "config"), "utf8"), fs.readFileSync(path.join(dir, ".git", "HEAD"), "utf8")];
  repoToFixture(dir);
  const after = [git(dir, "for-each-ref"), git(dir, "status", "--porcelain=v1", "--ignored"), fs.readFileSync(path.join(dir, ".git", "config"), "utf8"), fs.readFileSync(path.join(dir, ".git", "HEAD"), "utf8")];
  assert.deepEqual(after, before);
});

test("CX-T376 repoToFixture: 出力は模擬の GitHub がそのまま受け、40 桁の sha で先頭を返す。日付は実際のまま（ずらさない）", () => {
  const fx = repoToFixture(sample("mock"));
  const mock = new MockGitHub(fx.branches, fx.defaultBranch, fx.now);
  assert.match(mock.head("i0001") ?? "", /^[0-9a-f]{40}$/);
  const res = mock.handle("GET", "/repos/acme/widgets", { authorization: `Bearer ${TOKEN}` }, "");
  assert.deepEqual(res.json, { default_branch: "main" });
  assert.equal(mock.commits.get(mock.head("i0001") as string)?.date, "2026-10-24T09:00:00.000Z");
});

test("CX-T377 repoToFixture: 統合先は main か master、無ければ HEAD。どれも無ければ止まる", () => {
  const dir = path.join(tmp, "master");
  makeRepo(dir, { main: { date: "2026-10-01T00:00:00Z", files: { "README.md": "x\n" } } });
  git(dir, "branch", "-m", "main", "master");
  assert.equal(repoToFixture(dir).defaultBranch, "master");
  git(dir, "branch", "-m", "master", "trunk");
  assert.equal(repoToFixture(dir).defaultBranch, "trunk");
  const empty = path.join(tmp, "empty");
  fs.mkdirSync(empty);
  git(empty, "init", "-q");
  assert.throws(() => repoToFixture(empty), FixtureLimitError);
});
