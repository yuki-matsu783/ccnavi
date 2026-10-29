/**
 * 模擬の GitLab（`MockGitLab`）が内部の状態から答える応答を、本物の GitLab から録った形（tests/contract/gitlab/recorded.json）と、
 * `reads` のキーで比べる契約テスト。Python の `tests/contract` の TypeScript 版で、規則は同じ（test/helpers/shape.ts）。
 *
 * - 規則の表（normalize-cases.json）を Python と同じように読む。同じ生の入力を Python と TS で正規化し、結果が一致することを確かめる（uv が無ければ飛ばす）
 * - 比べるのは、`used_by` に `chrome` を持つ要求だけ（`MockGitLab` が 200/201 で答えるもの。見本由来の応答は比べない）
 * - 変異テスト: 模擬の応答を 1 か所変えると、どのキーがどう違うかが出て落ちる
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";

import { REPO } from "./helpers/python.js";
import { MockGitLab } from "./helpers/mock-gitlab.js";
import { TOKEN, blobSha } from "./helpers/mock-github.js";
import {
  answerProblems,
  applyAllow,
  compare,
  compareAll,
  loadData,
  normalize,
  readsOf,
  readsProblems,
  selectReads,
  substitute,
  type Entry,
  type Recorded,
  type Req,
  type Shape,
} from "./helpers/shape.js";

type Answers = Record<string, [status: number, body: unknown]>;

const requests = (loadData("requests.json").requests as Req[]).filter((r) => r.used_by.includes("chrome"));
const recorded = loadData("recorded.json").requests as Record<string, Recorded>;
const allow = loadData("allow.json") as { chrome_entries?: Entry[] };

/**
 * 本物の側の場面（tests/manual/gitlab/record_contract.py の `Scene.build`）と同じ形のブランチ:
 * main に最初のコミット、feature・done・fresh は main から 1 コミット（`<名前>.txt`）、feature はさらに `docs/note.txt`
 */
function mock(): MockGitLab {
  const m = new MockGitLab({ main: { committedDate: "2026-09-20T00:00:00Z", files: { "README.md": "contract\n" } } });
  for (const b of ["feature", "done", "fresh"]) {
    m.branch(b, "main");
    m.push(b, { [`${b}.txt`]: b });
  }
  m.push("feature", { "docs/note.txt": "note\n" });
  // 本物のトークンは期限と api スコープを持つ
  m.glExpiry = "2027-01-01";
  m.glScopes = ["api"];
  m.mrs.push({ number: 2, branch: "done", base: "main", title: "scene merged", body: "", state: "merged", fork: false });
  return m;
}

/** 要求の列を `MockGitLab` に送り、応答の `[ステータス, 本文]` を名前で返す */
function send(): Answers {
  const m = mock();
  const context: Record<string, string> = {
    project: encodeURIComponent("acme/widgets"),
    "mr.iid": "1",
    "mr.branch": "feature",
    "mr.merged_branch": "done",
    "mr.new_branch": "fresh",
    "issue.iid": "1",
    "discussion.id": "d50",
    // 拡張だけの要求が使う名前（本物の側の Scene.build が積むものと同じ）
    "sha.main": m.head("main") as string,
    "sha.feature": m.head("feature") as string,
    "blob.sha": blobSha("feature"),
  };
  const out: Answers = {};
  for (const req of requests) {
    const query = new URLSearchParams(substitute(req.query ?? {}, context)).toString();
    const url = `/api/v4${substitute(req.path, context)}${query ? `?${query}` : ""}`;
    const headers: Record<string, string> = req.token === "main" ? { "private-token": TOKEN } : {};
    const body = req.body === undefined ? "" : JSON.stringify(substitute(req.body, context));
    const res = m.handle(req.method, url, headers, body);
    for (const [name, key] of Object.entries(req.save ?? {})) context[name] = String((res.json as Record<string, unknown>)[key]);
    out[req.name] = [res.status, JSON.parse(JSON.stringify(res.json))];
  }
  return out;
}

/** 応答の問題の一覧（空なら本物の記録と合っている） */
function problems(answers: Answers): string[] {
  const actual = Object.fromEntries(Object.entries(answers).map(([n, [status, body]]) => [n, { status, shape: normalize(body) }]));
  const want = Object.fromEntries(requests.map((r) => [r.name, recorded[r.name]]));
  const [left, allowProblems] = applyAllow(compareAll(want, actual, readsOf(requests)), allow.chrome_entries ?? []);
  return [
    ...answerProblems(requests, answers),
    ...readsProblems(requests, recorded),
    ...Object.entries(left).flatMap(([name, diffs]) => diffs.map(([key, kind]) => `${name}/${key}/${kind}`)),
    ...allowProblems,
  ];
}

test("CX-T386 契約: 規則の表（normalize-cases.json）の全件が、期待の形と一致する", () => {
  const cases = loadData("normalize-cases.json") as { name: string; input: unknown; expect: Shape }[];
  assert.ok(cases.length > 0);
  for (const c of cases) assert.deepEqual(normalize(c.input), c.expect, c.name);
});

const uv = spawnSync("uv", ["--version"], { stdio: "ignore" }).status === 0;
test("CX-T387 契約: 同じ生の入力を Python と TS で正規化して一致する", { skip: uv ? false : "uv が無いので飛ばす（Python と突き合わせていない）" }, () => {
  const answers = send();
  const input = JSON.stringify(Object.fromEntries(Object.entries(answers).map(([n, [, body]]) => [n, body])));
  const script = [
    "import json, sys",
    "from tests.contract import fakeside, shape",
    "raw = {}",
    "def tamper(name, body):",
    "    raw[name] = body",
    "    return body",
    "fake = fakeside.run(fakeside.load_requests(), tamper)",
    "mock = json.load(sys.stdin)",
    "print(json.dumps({'mock': {n: shape.normalize(b) for n, b in mock.items()}, 'raw': raw, 'fake': {n: v['shape'] for n, v in fake.items()}}))",
  ].join("\n");
  const r = spawnSync("uv", ["run", "--project", REPO, "--quiet", "python", "-c", script], {
    input,
    cwd: REPO,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: REPO, PYTHONUTF8: "1" },
  });
  assert.equal(r.status, 0, r.stderr);
  const py = JSON.parse(r.stdout.trim().split("\n").pop() as string) as { mock: Record<string, Shape>; raw: Record<string, unknown>; fake: Record<string, Shape> };
  assert.ok(Object.keys(py.raw).length > 0 && Object.keys(py.mock).length > 0);
  for (const [name, body] of Object.entries(JSON.parse(input))) assert.deepEqual(normalize(body), py.mock[name], `MockGitLab ${name}`);
  for (const [name, body] of Object.entries(py.raw)) assert.deepEqual(normalize(body), py.fake[name], `fakeside ${name}`);
});

test("CX-T388 契約: MockGitLab が内部の状態から答える応答は、本物の記録と reads の範囲で形が合う", () => {
  assert.deepEqual(requests.map((r) => r.name), [
    "user",
    "project",
    "mrs_merged",
    "branches_list",
    "branch_get",
    "branch_404",
    "tree_root",
    "tree_recursive",
    "blob",
    "commits_path",
    "commit_get",
    "merge_base",
    "compare",
    "file_get",
    "approvals",
    "token_self",
    "error_401",
    "error_404",
    "mr_create",
    "branch_create",
    "branch_exists",
    "commit_create",
  ]);
  assert.deepEqual(problems(send()), []);
});

test("CX-T389 契約の変異: 応答を 1 か所変えると、キーと違いの種類が出て落ちる（キーの削除・型の変更・ステータスの変更・空の配列）", () => {
  const mutate = (f: (a: Answers) => void): string[] => {
    const a = send();
    f(a);
    return problems(a);
  };
  const project = (a: Answers) => a.project[1] as Record<string, unknown>;
  const merged = (a: Answers) => a.mrs_merged[1] as Record<string, unknown>[];
  assert.ok(mutate((a) => delete project(a).archived).includes("project/archived/missing"));
  assert.ok(mutate((a) => (merged(a)[0].iid = "x")).includes("mrs_merged/[].iid/type"));
  assert.ok(mutate((a) => (a.mr_create[0] = 200)).includes("mr_create/$status/status"));
  assert.ok(mutate((a) => (a.mrs_merged[1] = [])).includes("mrs_merged: 配列が空（場面が組めていない）"));
  // 拡張だけの要求
  assert.ok(mutate((a) => (a.compare[1] as { diffs: Record<string, unknown>[] }).diffs.forEach((d) => delete d.new_path)).includes("compare/diffs[].new_path/missing"));
  assert.ok(mutate((a) => (a.branch_exists[0] = 201)).includes("branch_exists/$status/status"));
});

test("CX-T390 契約の比較器: extra・presence・reads 外の欠けの除外・許可リストの 3 つの検査が動く", () => {
  const s = (t: string, keys?: Record<string, Shape>, opt?: string[]): Shape => ({ t: [t], ...(keys ? { keys } : {}), ...(opt ? { opt } : {}) });
  const int = s("integer");
  const diffs = compare(s("object", { a: int, b: int }), s("object", { a: int, c: int }, ["a"]));
  assert.deepEqual(diffs, [["a", "presence"], ["b", "missing"], ["c", "extra"]]);
  // reads に無い欠けは落とし、reads の中の欠けと extra は残す
  assert.deepEqual(selectReads(diffs, ["b"]), diffs);
  assert.deepEqual(selectReads(diffs, ["a"]), [["a", "presence"], ["c", "extra"]]);
  const [left, probs] = applyAllow({ r: [["k", "type"]] }, [
    { request: "x", key: "y", kind: "type", reason: "" },
    { request: "x", key: "y", kind: "bogus", reason: "r" },
  ]);
  assert.deepEqual(left, { r: [["k", "type"]] });
  assert.equal(probs.filter((p) => p.includes("理由が空") || p.includes("違いの種類") || p.includes("古い許可")).length, 4);
});
