/**
 * 模擬の GitHub。見本のリポジトリ（test/fixtures/repo.ts）を、拡張が使う REST と GraphQL の形で返す。
 * sha は git と同じ作り方（blob は `blob <大きさ>\0<中身>` の sha1）で、tree は中身の並びから作る。
 *
 * 単体試験は `fetch` の代わりに `mockFetch` を渡し、実機の試験は `serve` で HTTP に出す。
 */
import { createHash } from "node:crypto";
import http from "node:http";
import { NOW, type FixtureBranch } from "../fixtures/repo.js";

export const TOKEN = "test-token-0123456789";

const sha1 = (s: string | Buffer) => createHash("sha1").update(s).digest("hex");

export function blobSha(text: string): string {
  const buf = Buffer.from(text, "utf8");
  return sha1(Buffer.concat([Buffer.from(`blob ${buf.length}\0`), buf]));
}

interface Tree {
  readonly entries: { path: string; sha: string }[];
}

export class MockGitHub {
  readonly owner = "acme";
  readonly repo = "widgets";
  readonly calls: string[] = [];
  private readonly heads = new Map<string, string>();
  private readonly byCommit = new Map<string, string>();
  private readonly blobs = new Map<string, string>();
  private readonly trees = new Map<string, Tree>();

  /** 見本の日時（`NOW` 基準）を、実機の試験では今の時刻へずらす */
  private readonly shift: number;

  constructor(
    readonly branches: Record<string, FixtureBranch>,
    readonly defaultBranch = "main",
    now: Date = new Date(NOW),
  ) {
    this.shift = now.getTime() - Date.parse(NOW);
    for (const [name, b] of Object.entries(branches)) {
      for (const text of Object.values(b.files)) this.blobs.set(blobSha(text), text);
      const commit = sha1(`commit ${name}\n${JSON.stringify(b.files)}`);
      this.heads.set(name, commit);
      this.byCommit.set(commit, name);
    }
  }

  head(name: string): string | undefined {
    return this.heads.get(name);
  }

  /** ディレクトリの tree。無ければ登録して oid を返す */
  private treeOf(branch: string, dir: string): string | null {
    const files = this.branches[branch].files;
    const entries = Object.keys(files)
      .filter((p) => p.startsWith(`${dir}/`))
      .sort()
      .map((p) => ({ path: p.slice(dir.length + 1), sha: blobSha(files[p]) }));
    if (entries.length === 0) return null;
    const oid = sha1(`tree\n${JSON.stringify(entries)}`);
    this.trees.set(oid, { entries });
    return oid;
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
    if (method === "POST" && u.pathname.endsWith("/graphql")) return { status: 200, json: this.graphql(JSON.parse(body)) };
    return { status: 404, json: { message: "Not Found" } };
  }

  private graphql(req: { query: string; variables: Record<string, unknown> }): unknown {
    const { query, variables } = req;
    if (variables.owner !== this.owner || variables.name !== this.repo) return { data: { repository: null } };
    if (query.includes("refs(refPrefix")) {
      const nodes = Object.entries(this.branches)
        .sort((a, b) => (a[1].committedDate < b[1].committedDate ? 1 : -1))
        .map(([name, b]) => ({
          name,
          target: { oid: this.heads.get(name), committedDate: new Date(Date.parse(b.committedDate) + this.shift).toISOString() },
        }));
      return { data: { repository: { refs: { pageInfo: { hasNextPage: false, endCursor: null }, nodes } } } };
    }
    const repository: Record<string, unknown> = {};
    for (const m of query.matchAll(/(p\d+): object\(expression: \$(e\d+)\)/g)) {
      const [commit, ...rest] = String(variables[m[2]]).split(":");
      const path = rest.join(":");
      const branch = this.byCommit.get(commit);
      if (!branch) {
        repository[m[1]] = null;
        continue;
      }
      const files = this.branches[branch].files;
      if (path in files) {
        repository[m[1]] = { __typename: "Blob", oid: blobSha(files[path]) };
      } else {
        const oid = this.treeOf(branch, path);
        repository[m[1]] = oid ? { __typename: "Tree", oid } : null;
      }
    }
    for (const m of query.matchAll(/(b\d+): object\(oid: \$(o\d+)\)/g)) {
      const text = this.blobs.get(String(variables[m[2]]));
      repository[m[1]] = text === undefined ? null : { text, isBinary: false, byteSize: Buffer.byteLength(text) };
    }
    return { data: { repository } };
  }

  /** `fetch` の代わり（単体試験） */
  fetch = async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const lower: Record<string, string> = {};
    for (const [k, v] of Object.entries(init.headers)) lower[k.toLowerCase()] = v;
    const r = this.handle(init.method, url, lower, init.body ?? "");
    return { status: r.status, ok: r.status >= 200 && r.status < 300, json: async () => r.json };
  };

  /** HTTP に出す（実機の試験）。閉じる関数を返す */
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
        res.writeHead(r.status, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify(r.json));
      });
    });
    return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(() => new Promise((r) => server.close(() => r())))));
  }
}
