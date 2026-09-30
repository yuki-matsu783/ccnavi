/**
 * 模擬の GitHub。見本のリポジトリ（test/fixtures/repo.ts）を、拡張が使う REST と GraphQL の形で返す。
 * sha は git と同じ作り方（blob は `blob <大きさ>\0<中身>` の sha1）で、tree は中身の並びから作る。
 *
 * 段階 3 から書ける: `createCommitOnBranch`（`expectedHeadOid` が先頭と違えば断る）でコミットを積み、
 * コミットの履歴（`GET /commits?sha=&path=`・`GET /commits/<sha>`）、開いた MR と Approve、
 * PAT の期限のヘッダを返す。試験は `push`・`merge` で他の書き手を、`beforeCommit` で書く直前の
 * 割り込みを作る。
 *
 * 単体試験は `fetch` の代わりに `mockFetch` を渡し、実機の試験は `serve` で HTTP に出す。
 */
import { createHash } from "node:crypto";
import http from "node:http";
import { NOW, type FixtureBranch } from "../fixtures/repo.js";

export const TOKEN = "test-token-0123456789";
export const LOGIN = "alice";
export const EXPIRATION_HEADER = "github-authentication-token-expiration";

const sha1 = (s: string | Buffer) => createHash("sha1").update(s).digest("hex");

export function blobSha(text: string): string {
  const buf = Buffer.from(text, "utf8");
  return sha1(Buffer.concat([Buffer.from(`blob ${buf.length}\0`), buf]));
}

interface Tree {
  readonly entries: { path: string; sha: string }[];
}

export interface Commit {
  readonly sha: string;
  readonly parents: readonly string[];
  readonly files: Record<string, string>;
  readonly date: string;
  readonly message: string;
}

export interface CommitCall {
  readonly branch: string;
  readonly expected: string;
  readonly headline: string;
  readonly additions: { path: string; contents: string }[];
  readonly deletions: { path: string }[];
  readonly result: "written" | "stale" | "error";
  readonly oid?: string;
}

export class MockGitHub {
  readonly owner = "acme";
  readonly repo = "widgets";
  readonly calls: string[] = [];
  readonly commits = new Map<string, Commit>();
  readonly commitCalls: CommitCall[] = [];
  private readonly heads = new Map<string, string>();
  private readonly blobs = new Map<string, string>();
  private readonly trees = new Map<string, Tree>();
  /** 応答ヘッダに載せる PAT の期限（空なら載せない） */
  expiration = "";
  /** createCommitOnBranch を受けたとき、比べる前に 1 度だけ呼ぶ（割り込みの書き手） */
  beforeCommit: ((branch: string) => void) | null = null;
  /** 書いた後の応答を 502 にする（応答だけが落ちた形） */
  loseCommitResponse = false;
  /** 本文を返さない（バイナリとして返す）blob の sha */
  readonly binaryBlobs = new Set<string>();
  /** 開いた MR（ブランチ → 番号と Approve） */
  readonly pulls: Record<string, { number: number; reviews: { user: string; state: string }[] }[]> = {};

  /** 見本の日時（`NOW` 基準）を、実機の試験では今の時刻へずらす */
  private readonly shift: number;
  private seq = 0;

  constructor(
    readonly branches: Record<string, FixtureBranch>,
    readonly defaultBranch = "main",
    private readonly now: Date = new Date(NOW),
  ) {
    this.shift = now.getTime() - Date.parse(NOW);
    for (const [name, b] of Object.entries(branches)) {
      const date = new Date(Date.parse(b.committedDate) + this.shift).toISOString();
      const sha = this.store({ parents: [], files: { ...b.files }, date, message: `init ${name}` }, name);
      this.heads.set(name, sha);
    }
  }

  private store(c: Omit<Commit, "sha">, salt = ""): string {
    const sha = sha1(`commit ${salt}\n${this.seq++}\n${c.parents.join(",")}\n${JSON.stringify(c.files)}`);
    for (const text of Object.values(c.files)) this.blobs.set(blobSha(text), text);
    this.commits.set(sha, { sha, ...c });
    return sha;
  }

  head(name: string): string | undefined {
    return this.heads.get(name);
  }

  /** ブランチの先頭の中身 */
  files(name: string): Record<string, string> {
    return this.commits.get(this.heads.get(name) ?? "")?.files ?? {};
  }

  /** 他の書き手が普通の push をする。`null` の中身は消す */
  push(branch: string, change: Record<string, string | null>, message = "push"): string {
    const parent = this.heads.get(branch) as string;
    const files = { ...this.files(branch) };
    for (const [p, v] of Object.entries(change)) {
      if (v === null) delete files[p];
      else files[p] = v;
    }
    const sha = this.store({ parents: [parent], files, date: this.now.toISOString(), message });
    this.heads.set(branch, sha);
    return sha;
  }

  /** `from` を `branch` に merge する（中身は両方を足したもの。`from` が勝つ） */
  merge(branch: string, from: string): string {
    const parents = [this.heads.get(branch) as string, this.heads.get(from) as string];
    const files = { ...this.files(branch), ...this.files(from) };
    const sha = this.store({ parents, files, date: this.now.toISOString(), message: `merge ${from}` });
    this.heads.set(branch, sha);
    return sha;
  }

  /** ディレクトリの tree。無ければ登録して oid を返す */
  private treeOf(commit: string, dir: string): string | null {
    const files = this.commits.get(commit)?.files ?? {};
    const entries = Object.keys(files)
      .filter((p) => p.startsWith(`${dir}/`))
      .sort()
      .map((p) => ({ path: p.slice(dir.length + 1), sha: blobSha(files[p]) }));
    if (entries.length === 0) return null;
    const oid = sha1(`tree\n${JSON.stringify(entries)}`);
    this.trees.set(oid, { entries });
    return oid;
  }

  /** 最初の親を辿った履歴（新しい順） */
  private history(sha: string): Commit[] {
    const out: Commit[] = [];
    let c = this.commits.get(sha);
    while (c) {
      out.push(c);
      c = this.commits.get(c.parents[0] ?? "");
    }
    return out;
  }

  private changed(c: Commit): { filename: string; status: string }[] {
    const before = this.commits.get(c.parents[0] ?? "")?.files ?? {};
    const out: { filename: string; status: string }[] = [];
    for (const p of new Set([...Object.keys(before), ...Object.keys(c.files)])) {
      if (!(p in before)) out.push({ filename: p, status: "added" });
      else if (!(p in c.files)) out.push({ filename: p, status: "removed" });
      else if (before[p] !== c.files[p]) out.push({ filename: p, status: "modified" });
    }
    return out.sort((a, b) => (a.filename < b.filename ? -1 : 1));
  }

  handle(method: string, url: string, headers: Record<string, string>, body: string): { status: number; json: unknown } {
    const u = new URL(url, "http://mock");
    this.calls.push(`${method} ${u.pathname}`);
    if (headers.authorization !== `Bearer ${TOKEN}`) return { status: 401, json: { message: "Bad credentials" } };
    const base = `/repos/${this.owner}/${this.repo}`;
    if (method === "GET" && u.pathname === base) return { status: 200, json: { default_branch: this.defaultBranch } };
    if (method === "GET" && u.pathname.startsWith(`${base}/git/ref/heads/`)) {
      const name = decodeURIComponent(u.pathname.slice(`${base}/git/ref/heads/`.length));
      const sha = this.heads.get(name);
      return sha ? { status: 200, json: { ref: `refs/heads/${name}`, object: { sha, type: "commit" } } } : { status: 404, json: { message: "Not Found" } };
    }
    if (method === "GET" && u.pathname.startsWith(`${base}/git/trees/`)) {
      const tree = this.trees.get(u.pathname.slice(`${base}/git/trees/`.length));
      if (!tree) return { status: 404, json: { message: "Not Found" } };
      return { status: 200, json: { truncated: false, tree: tree.entries.map((e) => ({ path: e.path, type: "blob", sha: e.sha })) } };
    }
    if (method === "GET" && u.pathname === `${base}/commits`) {
      const start = u.searchParams.get("sha") ?? "";
      const path = u.searchParams.get("path") ?? "";
      const per = Number(u.searchParams.get("per_page") ?? "30");
      if (!this.commits.has(start)) return { status: 404, json: { message: "Not Found" } };
      const list = this.history(start)
        .filter((c) => !path || this.changed(c).some((f) => f.filename === path))
        .slice(0, per)
        .map((c) => ({ sha: c.sha, parents: c.parents.map((p) => ({ sha: p })) }));
      return { status: 200, json: list };
    }
    if (method === "GET" && u.pathname.startsWith(`${base}/commits/`)) {
      const c = this.commits.get(u.pathname.slice(`${base}/commits/`.length));
      if (!c) return { status: 404, json: { message: "Not Found" } };
      return { status: 200, json: { sha: c.sha, parents: c.parents.map((p) => ({ sha: p })), files: this.changed(c) } };
    }
    if (method === "GET" && u.pathname === `${base}/pulls`) {
      const head = u.searchParams.get("head") ?? "";
      const branch = head.startsWith(`${this.owner}:`) ? head.slice(this.owner.length + 1) : "";
      return { status: 200, json: (this.pulls[branch] ?? []).map((p) => ({ number: p.number })) };
    }
    const reviews = new RegExp(`^${base}/pulls/(\\d+)/reviews$`).exec(u.pathname);
    if (method === "GET" && reviews) {
      const pr = Object.values(this.pulls)
        .flat()
        .find((p) => p.number === Number(reviews[1]));
      return { status: 200, json: (pr?.reviews ?? []).map((r) => ({ user: { login: r.user }, state: r.state })) };
    }
    if (method === "POST" && u.pathname.endsWith("/graphql")) return this.graphql(JSON.parse(body));
    return { status: 404, json: { message: "Not Found" } };
  }

  private graphql(req: { query: string; variables: Record<string, unknown> }): { status: number; json: unknown } {
    const { query, variables } = req;
    if (query.includes("createCommitOnBranch")) return this.createCommit(variables.input as Record<string, unknown>);
    if (query.includes("viewer")) return { status: 200, json: { data: { viewer: { login: LOGIN } } } };
    if (variables.owner !== this.owner || variables.name !== this.repo) return { status: 200, json: { data: { repository: null } } };
    if (query.includes("refs(refPrefix")) {
      const nodes = [...this.heads.entries()]
        .map(([name, sha]) => ({ name, target: { oid: sha, committedDate: (this.commits.get(sha) as Commit).date } }))
        .sort((a, b) => (a.target.committedDate < b.target.committedDate ? 1 : -1));
      return { status: 200, json: { data: { repository: { refs: { pageInfo: { hasNextPage: false, endCursor: null }, nodes } } } } };
    }
    const repository: Record<string, unknown> = {};
    for (const m of query.matchAll(/(p\d+): object\(expression: \$(e\d+)\)/g)) {
      const [commit, ...rest] = String(variables[m[2]]).split(":");
      const path = rest.join(":");
      const files = this.commits.get(commit)?.files;
      if (!files) {
        repository[m[1]] = null;
        continue;
      }
      if (path in files) {
        repository[m[1]] = { __typename: "Blob", oid: blobSha(files[path]) };
      } else {
        const oid = this.treeOf(commit, path);
        repository[m[1]] = oid ? { __typename: "Tree", oid } : null;
      }
    }
    for (const m of query.matchAll(/(b\d+): object\(oid: \$(o\d+)\)/g)) {
      const oid = String(variables[m[2]]);
      const text = this.blobs.get(oid);
      repository[m[1]] =
        text === undefined
          ? null
          : this.binaryBlobs.has(oid)
            ? { text: null, isBinary: true, byteSize: Buffer.byteLength(text) }
            : { text, isBinary: false, byteSize: Buffer.byteLength(text) };
    }
    return { status: 200, json: { data: { repository } } };
  }

  private createCommit(input: Record<string, unknown>): { status: number; json: unknown } {
    const target = input.branch as { repositoryNameWithOwner?: string; branchName?: string };
    const branch = String(target?.branchName ?? "");
    const expected = String(input.expectedHeadOid ?? "");
    const changes = (input.fileChanges ?? {}) as { additions?: { path: string; contents: string }[]; deletions?: { path: string }[] };
    const call = {
      branch,
      expected,
      headline: String((input.message as { headline?: string })?.headline ?? ""),
      additions: changes.additions ?? [],
      deletions: changes.deletions ?? [],
    };
    if (target?.repositoryNameWithOwner !== `${this.owner}/${this.repo}` || !this.heads.has(branch)) {
      this.commitCalls.push({ ...call, result: "error" });
      return { status: 200, json: { data: { createCommitOnBranch: null }, errors: [{ type: "NOT_FOUND", message: "branch not found" }] } };
    }
    const hook = this.beforeCommit;
    this.beforeCommit = null;
    hook?.(branch);
    if (this.heads.get(branch) !== expected) {
      this.commitCalls.push({ ...call, result: "stale" });
      return {
        status: 200,
        json: {
          data: { createCommitOnBranch: null },
          errors: [{ type: "STALE_DATA", message: `Expected branch to point to "${expected}" but it did not. Pull and try again.` }],
        },
      };
    }
    const files = { ...this.files(branch) };
    for (const d of call.deletions) {
      if (!(d.path in files)) {
        this.commitCalls.push({ ...call, result: "error" });
        return { status: 200, json: { data: { createCommitOnBranch: null }, errors: [{ message: `A path was requested for deletion which does not exist: ${d.path}` }] } };
      }
      delete files[d.path];
    }
    for (const a of call.additions) files[a.path] = Buffer.from(a.contents, "base64").toString("utf8");
    const oid = this.store({ parents: [expected], files, date: this.now.toISOString(), message: call.headline });
    this.heads.set(branch, oid);
    this.commitCalls.push({ ...call, result: "written", oid });
    if (this.loseCommitResponse) {
      this.loseCommitResponse = false;
      return { status: 502, json: { message: "Bad Gateway" } };
    }
    return { status: 200, json: { data: { createCommitOnBranch: { commit: { oid } } } } };
  }

  private responseHeaders(): Record<string, string> {
    return this.expiration ? { [EXPIRATION_HEADER]: this.expiration } : {};
  }

  /** `fetch` の代わり（単体試験） */
  fetch = async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const lower: Record<string, string> = {};
    for (const [k, v] of Object.entries(init.headers)) lower[k.toLowerCase()] = v;
    const r = this.handle(init.method, url, lower, init.body ?? "");
    const h = this.responseHeaders();
    return { status: r.status, ok: r.status >= 200 && r.status < 300, json: async () => r.json, headers: { get: (n: string) => h[n.toLowerCase()] ?? null } };
  };

  /**
   * HTTP に出す（実機の試験）。閉じる関数を返す。
   * PAT の期限のヘッダは `Access-Control-Expose-Headers` に載せない（拡張の fetch が CORS の制限を
   * 受けずに読めるか、を実機で確かめるため。10.2 の確認事項 5）。
   */
  serve(port: number): Promise<() => Promise<void>> {
    const server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        const headers: Record<string, string> = {};
        for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers[k] = v;
        const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type, accept, x-github-api-version" };
        if (req.method === "OPTIONS") {
          res.writeHead(204, cors).end();
          return;
        }
        const r = this.handle(req.method ?? "GET", req.url ?? "/", headers, Buffer.concat(chunks).toString("utf8"));
        res.writeHead(r.status, { ...cors, ...this.responseHeaders(), "Content-Type": "application/json" }).end(JSON.stringify(r.json));
      });
    });
    return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(() => new Promise((r) => server.close(() => r())))));
  }
}
