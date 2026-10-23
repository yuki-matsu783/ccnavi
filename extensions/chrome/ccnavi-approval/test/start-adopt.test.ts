/**
 * 「始める」は `ccnavi-start.sh` と同じ手順にそろえる（既存の MR を探して採用する、無ければブランチと Draft の MR を作る）。
 * 模擬の GitHub・GitLab と Node の上の Pyodide で回す。
 *
 * - issue を閉じる開いた MR が 1 本あれば、その元ブランチを採用する。ブランチも MR も作らない
 * - 採用の確認: open・フォークでない・マージ先が統合先・ブランチ名が保護されていない・識別子が使える・
 *   同じ識別子になる別のブランチが無い・同じブランチ名のマージ済み MR が無い。引っかかったら何も作らずに止める
 * - 2 本以上あれば、何も作らずに一覧を出して止める
 * - 新規: ブランチ・初期コミット・Draft の MR。MR を作れなかったときは、ブランチを作ったことと続きの手を言う
 * - 書く操作（startCommit・createDraftMr）は、ボードからだけ・登録したリポジトリだけ・issue から決める形の名前だけ・
 *   統合先へ向けた本文 `Closes #<番号>` だけ受ける
 * - 識別子（ブランチ名の `/` を `-` に置き換えたもの）は、共通の表（tests/fixtures/branch-to-id.json）と一致する
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { dispatch, type Deps } from "../src/core/protocol.js";
import { py as pyOps, type PyCall } from "../src/core/py.js";
import type { RepoConfig } from "../src/core/settings.js";
import { collectRepo, Reader, readIntegration } from "../src/core/snapshot.js";
import { closesIssue, startIssue, type StartOutcome } from "../src/core/start.js";
import type { WriteDeps } from "../src/core/write.js";
import { fixture, NOW } from "./fixtures/repo.js";
import { BOARD, deps, GITLAB_REPO, hostCall, HOSTS, memoryCache, newStats, OPTIONS } from "./helpers/host.js";
import { MockGitHub, TOKEN, type MockMr } from "./helpers/mock-github.js";
import { MockGitLab } from "./helpers/mock-gitlab.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const GH_REPO: RepoConfig = { ...GITLAB_REPO, host: "github.com" };
void HOSTS;
const noWait = async () => undefined;
const TOKENS = new Map([
  ["github.com", TOKEN],
  ["gitlab.com", TOKEN],
]);

function wdeps(mock: MockGitHub, host: string): { d: WriteDeps; repo: RepoConfig } {
  const stats = newStats();
  const base = { ...deps(mock, TOKENS, new Map(), () => new Date(NOW), [GITLAB_REPO, GH_REPO]), sleep: noWait };
  return {
    repo: host === "github.com" ? GH_REPO : GITLAB_REPO,
    d: {
      call: hostCall(base, stats, host),
      py,
      cache: memoryCache(),
      now: () => new Date(NOW),
      stats,
      version: "9.9.9",
      sleep: noWait,
      kind: host === "gitlab.com" ? "gitlab" : "github",
    },
  };
}

const HOSTS_UNDER_TEST = [
  ["github.com", () => new MockGitHub(fixture())],
  ["gitlab.com", () => new MockGitLab(fixture())],
] as const;

/** issue 20 を閉じる開いた MR を 1 本置く（元ブランチも作る）。over で上書き */
function withMr(mock: MockGitHub, over: Partial<MockMr> = {}): MockMr {
  const mr: MockMr = { number: 5, branch: "feature/20-login", base: "main", title: "ログイン", body: "Closes #20", state: "open", fork: false, ...over };
  if (!mock.head(mr.branch)) mock.branch(mr.branch, "main");
  mock.mrs.push(mr);
  return mr;
}

async function start(mock: MockGitHub, host: string, over: { title?: string } = {}): Promise<StartOutcome> {
  const { d, repo } = wdeps(mock, host);
  const board = await collectRepo(repo, d);
  const taken = [...board.candidates, ...board.families.map((f) => f.family.name)];
  return startIssue(repo, { number: 20, title: over.title ?? "ログイン" }, board.seen ?? null, taken, d);
}

function message(out: StartOutcome): string {
  return out.kind === "refused" || out.kind === "failed" ? out.message : "";
}

test("CX-T251 issue を閉じる MR は、閉じる語に続くこのリポジトリの #番号 か issues/番号 で見つける。参照だけ・別のリポジトリ・コードや引用の中・否定・番号が長い別の issue は拾わない", () => {
  const mr = (title: string, body = "") => ({ title, body });
  const ok = (m: { title: string; body: string }) => closesIssue(m, 20, "acme/widgets");
  assert.equal(ok(mr("直す Closes #20")), true);
  assert.equal(ok(mr("直す", "closes #20\n")), true);
  assert.equal(ok(mr("直す", "Fixes: #20")), true);
  assert.equal(ok(mr("直す", "Resolved ACME/Widgets#20")), true);
  assert.equal(ok(mr("直す", "Closes https://github.com/acme/widgets/issues/20")), true);
  assert.equal(ok(mr("直す", "**Closes** #20")), true);
  assert.equal(ok(mr("直す", "Closes #20.")), true);
  assert.equal(ok(mr("直す #20")), false);
  assert.equal(ok(mr("直す", "See #20")), false);
  assert.equal(ok(mr("直す", "https://github.com/acme/widgets/issues/20")), false);
  assert.equal(ok(mr("直す", "Closes #200")), false);
  assert.equal(ok(mr("直す", "Closes #20abc")), false);
  assert.equal(ok(mr("直す", "Closes a#20")), false);
  assert.equal(ok(mr("直す", "Enclosed #20")), false);
  // 別のリポジトリの issue を閉じる MR は、この issue の MR ではない
  assert.equal(ok(mr("直す", "Closes other/repo#20")), false);
  assert.equal(ok(mr("直す", "Closes: other/repo#20")), false);
  assert.equal(ok(mr("直す", "Closes https://github.com/other/repo/issues/20")), false);
  // コードブロック・インラインコード・引用の中、否定は閉じない
  assert.equal(ok(mr("直す", "```\nCloses #20\n```")), false);
  assert.equal(ok(mr("直す", "`Closes #20`")), false);
  assert.equal(ok(mr("直す", "> Closes #20")), false);
  assert.equal(ok(mr("直す", "Does not fix #20")), false);
  assert.equal(ok(mr("直す", "This doesn't close #20")), false);
  // 閉じる語 1 つにつき、直後の指定だけ
  assert.equal(ok(mr("直す", "Closes #21, #20")), false);
  assert.equal(ok(mr("直す", "Closes #21, closes #20")), true);
  // GitLab の入れ子のグループ
  assert.equal(closesIssue(mr("直す", "Closes group/sub/proj#20"), 20, "group/sub/proj"), true);
});

test("CX-T251b 参照しているだけの MR（本文が #番号 の言及だけ・元ブランチの名前に番号がある）は採用せず、新しく作る", async () => {
  for (const [host, make] of HOSTS_UNDER_TEST) {
    const mock = make();
    withMr(mock, { title: "ログイン #20", body: "see #20", branch: "feature/20-login" });
    const out = await start(mock, host);
    assert.equal(out.kind, "started", host);
    assert.equal(mock.createdMrs.length, 1, host);
  }
});

test("CX-T252 issue を閉じる開いた MR が 1 本あれば、その元ブランチを採用する。ブランチも MR も作らない（GitHub と GitLab）", async () => {
  for (const [host, make] of HOSTS_UNDER_TEST) {
    const mock = make();
    withMr(mock);
    const out = await start(mock, host);
    assert.deepEqual(out, { kind: "adopted", name: "feature/20-login", identifier: "feature-20-login", mr: { number: 5, url: out.kind === "adopted" ? out.mr.url : "" } }, host);
    assert.equal(mock.createdBranches.length, 0, host);
    assert.equal(mock.createdMrs.length, 0, host);
    assert.equal(mock.startCommits.length, 0, host);
  }
});

test("CX-T253 採用の確認に引っかかったら、何も作らずに止める（open でない・フォーク・マージ先・保護されたブランチ・識別子・識別子の重なり・マージ済み）", async () => {
  const cases: [string, Partial<MockMr>, (m: MockGitHub) => void, RegExp][] = [
    ["フォーク", { fork: true }, () => undefined, /フォークから出た MR/],
    ["マージ先", { base: "develop" }, () => undefined, /マージ先が develop で、統合先 main ではありません/],
    ["保護されたブランチ", { branch: "release/1.0" }, () => undefined, /採用できません/],
    ["識別子が長い", { branch: `feature/${"a".repeat(60)}` }, () => undefined, /識別子 .* は使えません/],
    ["識別子の末尾が -2 桁", { branch: "feature/20-login-05" }, () => undefined, /末尾が `-<2 桁>`/],
    ["識別子の重なり", {}, (m) => m.branch("feature-20-login", "main"), /どちらも識別子 feature-20-login になります/],
  ];
  for (const [host, make] of HOSTS_UNDER_TEST) {
    for (const [name, over, setup, why] of cases) {
      const mock = make();
      setup(mock);
      withMr(mock, over);
      const out = await start(mock, host);
      assert.equal(out.kind, "refused", `${host} ${name}: ${JSON.stringify(out)}`);
      assert.match(message(out), why, `${host} ${name}`);
      assert.equal(mock.createdBranches.length + mock.createdMrs.length, 0, `${host} ${name}`);
    }
    // 同じブランチ名のマージ済み MR（手元の退避には頼らず、ホストに聞く）
    const mock = make();
    withMr(mock);
    mock.mrs.push({ number: 2, branch: "feature/20-login", base: "main", title: "前の", body: "", state: "merged", fork: false });
    const out = await start(mock, host);
    assert.equal(out.kind, "refused", host);
    assert.match(message(out), /マージ済み MR（!2）/, host);
    assert.equal(mock.createdBranches.length + mock.createdMrs.length, 0, host);
  }
});

test("CX-T254 issue を閉じる開いた MR が複数あれば、何も作らずに一覧を出して止める", async () => {
  for (const [host, make] of HOSTS_UNDER_TEST) {
    const mock = make();
    withMr(mock);
    withMr(mock, { number: 6, branch: "fix/20-other", title: "別の #20" });
    const out = await start(mock, host);
    assert.equal(out.kind, "refused", host);
    assert.match(message(out), /開いたマージリクエストが 2 本あります（!5 feature\/20-login、!6 fix\/20-other）/, host);
    assert.equal(mock.createdBranches.length + mock.createdMrs.length, 0, host);
  }
});

test("CX-T255 MR を作れなかったときは、ブランチを作ったことと続きの手を言う", async () => {
  for (const [host, make] of HOSTS_UNDER_TEST) {
    const mock = make();
    const { d, repo } = wdeps(mock, host);
    const board = await collectRepo(repo, d);
    const failing: WriteDeps = {
      ...d,
      call: async (op, args) => {
        if (op === "createDraftMr") throw new Error("boom");
        return d.call(op, args);
      },
    };
    const out = await startIssue(repo, { number: 20, title: "ログイン" }, board.seen ?? null, [], failing);
    assert.equal(out.kind, "failed", host);
    assert.match(message(out), /ブランチ feature-20-ログイン は作成しましたが、続きの処理に失敗しました（boom）[\s\S]*ccnavi-start\.sh --issue 20/, host);
    assert.equal(mock.createdBranches.length, 1, host);
  }
});

test("CX-T256 書く操作（startCommit・createDraftMr）の保護: ボードからだけ、登録したリポジトリだけ、issue から決める形の名前だけ、統合先へだけ、本文は Closes #番号 だけ", async () => {
  const mock = new MockGitLab(fixture());
  const d: Deps = deps(mock, TOKENS, new Map(), () => new Date(NOW), [GITLAB_REPO]);
  const head = mock.head("main") as string;
  const made = await dispatch({ kind: "host", host: "gitlab.com", op: "createBranch", args: ["acme", "widgets", "feature-20-x", head] }, BOARD, d);
  assert.ok(made.ok, JSON.stringify(made));
  const ask = (op: string, args: unknown[], sender = BOARD) => dispatch({ kind: "host", host: "gitlab.com", op, args }, sender, d);
  const refused: [string, unknown[], typeof BOARD, RegExp][] = [
    ["startCommit", ["acme", "widgets", "feature-20-x", head], OPTIONS, /ボードからのみ/],
    ["startCommit", ["acme", "other", "feature-20-x", head], BOARD, /登録されていない/],
    ["startCommit", ["acme", "widgets", "main", head], BOARD, /issue から作る/],
    ["startCommit", ["acme", "widgets", "feature-20-x", "f".repeat(40)], BOARD, /動いているため/],
    ["createDraftMr", ["acme", "widgets", "feature-20-x", "main", "題", "Closes #20"], OPTIONS, /ボードからのみ/],
    ["createDraftMr", ["acme", "other", "feature-20-x", "main", "題", "Closes #20"], BOARD, /登録されていない/],
    ["createDraftMr", ["acme", "widgets", "main", "main", "題", "Closes #20"], BOARD, /issue から作る/],
    ["createDraftMr", ["acme", "widgets", "feature-20-x", "develop", "題", "Closes #20"], BOARD, /統合先 main に限ります/],
    ["createDraftMr", ["acme", "widgets", "feature-20-x", "main", "題", "任意の本文"], BOARD, /Closes #<issue の番号>/],
  ];
  for (const [op, args, sender, why] of refused) {
    const res = await ask(op, args, sender);
    assert.equal(res.ok, false, `${op} ${JSON.stringify(args)}`);
    assert.match((res as { error: string }).error, why, `${op} ${JSON.stringify(args)}`);
  }
  assert.equal(mock.createdMrs.length, 0);
  const ok = await ask("createDraftMr", ["acme", "widgets", "feature-20-x", "main", "題", "Closes #20"]);
  assert.ok(ok.ok, JSON.stringify(ok));
  assert.equal(mock.createdMrs.length, 1);
  // 読み取り（listOpenMrs・listMergedMrs）は登録したリポジトリだけ
  const unregistered = await ask("listOpenMrs", ["acme", "other"]);
  assert.match((unregistered as { error: string }).error, /登録されていない/);
  assert.ok(!JSON.stringify([made, ok, unregistered]).includes(TOKEN));
});

test("CX-T257 採用の識別子はブランチ名の / を - に置き換えたもの。Python・sh・TS 共通の表（tests/fixtures/branch-to-id.json）と一致する", async () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const table = path.join(here, "..", "..", "..", "..", "..", "tests", "fixtures", "branch-to-id.json");
  const cases = JSON.parse(fs.readFileSync(table, "utf8")) as { convert: { branch: string; id: string }[]; adopt: { branch: string; ok: boolean }[] };
  const mock = new MockGitHub(fixture());
  const { d, repo } = wdeps(mock, "github.com");
  const board = await collectRepo(repo, d);
  const reader = new Reader(repo, d);
  const base = await readIntegration(repo, reader, d);
  const fresh = reader.snapshot(base.integration);
  const snapshot = { ...fresh, branches: { ...(board.seen?.branches ?? {}), ...fresh.branches }, absent: [] };
  assert.ok(cases.convert.length > 0 && cases.adopt.length > 0);
  for (const c of cases.convert) {
    const res = await pyOps.adopt(d.py, { snapshot, branch: c.branch, names: [] });
    assert.equal(res.identifier, c.id, c.branch);
  }
  // 採用できるかの表（識別子の字・長さ・末尾）。保護された名前や統合先の done/ などは別の理由なので、表の ok だけを見る
  for (const c of cases.adopt) {
    const res = await pyOps.adopt(d.py, { snapshot, branch: c.branch, names: [] });
    assert.equal(res.problems.length === 0, c.ok, `${c.branch}: ${res.problems.join(" / ")}`);
  }
});
