/**
 * 模擬の GitHub。見本のリポジトリ（test/fixtures/repo.ts）を、拡張が使う REST と GraphQL の形で返す。
 * sha は git と同じ作り方（blob は `blob <大きさ>\0<中身>` の sha1）で、tree は中身のリストから作る。
 *
 * 段階 3 から書ける: `createCommitOnBranch`（`expectedHeadOid` が先頭と違えば断る）でコミットを積み、
 * コミットの履歴（`GET /commits?sha=&path=`・`GET /commits/<sha>`）、開いた MR と Approve、
 * PAT の期限のヘッダを返す。試験は `push`・`merge` で他の書き手を、`beforeCommit` で書く直前の
 * 割り込みを作る。
 *
 * 段階 4 から、録ったホストの応答の見本（test/fixtures/host/github/。`host-fixture.ts`）をブランチに付けると、
 * その MR・スレッド・レビューを見本のとおりに返す（`attachScene`）。依頼の後の変更の一覧（`GET /compare/<b>...<h>`）も返す。
 *
 * 単体試験は `fetch` の代わりに `mockFetch` を渡し、実機の試験は `serve` で HTTP に出す。
 */
import { createHash } from "node:crypto";
import http from "node:http";
import { NOW, type FixtureBranch } from "../fixtures/repo.js";
import { sceneAnswer, type Scene } from "./host-fixture.js";

export const TOKEN = "test-token-0123456789";
export const LOGIN = "alice";
export const EXPIRATION_HEADER = "github-authentication-token-expiration";

const sha1 = (s: string | Buffer) => createHash("sha1").update(s).digest("hex");

/** 中身が似ているか（rename 検出の代わり。行の半分以上が同じ） */
function similar(a: string, b: string): boolean {
  const x = a.split("\n");
  const y = new Set(b.split("\n"));
  return x.filter((l) => y.has(l)).length * 2 >= Math.max(x.length, y.size);
}

export function blobSha(text: string): string {
  const buf = Buffer.from(text, "utf8");
  return sha1(Buffer.concat([Buffer.from(`blob ${buf.length}\0`), buf]));
}

interface Tree {
  readonly entries: { path: string; sha: string; mode: string }[];
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
  readonly body: string;
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
  protected readonly heads = new Map<string, string>();
  protected readonly blobs = new Map<string, string>();
  protected readonly trees = new Map<string, Tree>();
  /** 応答ヘッダに載せる PAT の期限（空なら載せない） */
  expiration = "";
  /** createCommitOnBranch を受けたとき、比べる前に 1 度だけ呼ぶ（割り込みの書き手） */
  beforeCommit: ((branch: string) => void) | null = null;
  /** 書いた後の応答を 502 にする（応答だけが落ちた形） */
  loseCommitResponse = false;
  /** 本文を返さない（バイナリとして返す）blob の sha */
  readonly binaryBlobs = new Set<string>();
  /** 本文を切って返す（isTruncated）blob の sha */
  readonly truncatedBlobs = new Set<string>();
  /** シンボリックリンク（tree の mode 120000）として返すパス */
  readonly linkPaths = new Set<string>();
  /** 次の要求（`match` に当たるもの）をレート制限で断る（1 回ずつ消える） */
  readonly limits: { match: RegExp; status: number; headers: Record<string, string>; graphql?: boolean }[] = [];
  /** 要求を受けるたびに呼ぶ（試験が割り込みの書き手を作る） */
  onRequest: ((method: string, url: URL) => void) | null = null;
  /** コミットの変更の一覧（`GET /commits/<sha>` の files）を切る件数（本物は 300） */
  filesLimit = 300;
  /** 付けた見本（ブランチ → 場面）。その MR・スレッド・レビューを見本のとおりに返す */
  readonly scenes = new Map<string, Scene>();
  /** 開いた MR（ブランチ → 番号と Approve） */
  readonly pulls: Record<string, { number: number; reviews: { user: string; state: string }[] }[]> = {};
  /** 開いた issue（「始める」。段階 5） */
  readonly issues: { number: number; title: string; pull?: boolean }[] = [];
  /** 作ったブランチ（「始める」の頼み。名前と元の sha） */
  readonly createdBranches: { name: string; sha: string }[] = [];

  /** 見本の日時（`NOW` 基準）を、実機の試験では今の時刻へずらす */
  protected readonly shift: number;
  protected seq = 0;

  constructor(
    readonly branches: Record<string, FixtureBranch>,
    readonly defaultBranch = "main",
    protected readonly now: Date = new Date(NOW),
  ) {
    this.shift = now.getTime() - Date.parse(NOW);
    for (const [name, b] of Object.entries(branches)) {
      const date = new Date(Date.parse(b.committedDate) + this.shift).toISOString();
      const sha = this.store({ parents: [], files: { ...b.files }, date, message: `init ${name}` }, name);
      this.heads.set(name, sha);
    }
  }

  protected store(c: Omit<Commit, "sha">, salt = ""): string {
    const sha = sha1(`commit ${salt}\n${this.seq++}\n${c.parents.join(",")}\n${JSON.stringify(c.files)}`);
    for (const text of Object.values(c.files)) this.blobs.set(blobSha(text), text);
    this.commits.set(sha, { sha, ...c });
    return sha;
  }

  /** 見本の場面をブランチに付ける（同じ MR 番号の場面は 1 つずつ付ける） */
  attachScene(branch: string, scene: Scene): void {
    this.scenes.clear();
    scene.branch = branch;
    this.scenes.set(branch, scene);
  }

  head(name: string): string | undefined {
    return this.heads.get(name);
  }

  /** ブランチの先頭の中身 */
  files(name: string): Record<string, string> {
    return this.commits.get(this.heads.get(name) ?? "")?.files ?? {};
  }

  /** `from` の先頭から新しいブランチを作る */
  branch(name: string, from: string): void {
    this.heads.set(name, this.heads.get(from) as string);
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

  /** `from` を `branch` に merge する。共通の祖先から `from` が変えたパス（消したものを含む）は `from` を採る */
  merge(branch: string, from: string): string {
    const ours = this.heads.get(branch) as string;
    const theirs = this.heads.get(from) as string;
    const mine = new Set(this.ancestors(ours).map((c) => c.sha));
    const base = this.ancestors(theirs).find((c) => mine.has(c.sha))?.files ?? {};
    const files = { ...this.files(branch) };
    const other = this.files(from);
    for (const p of new Set([...Object.keys(base), ...Object.keys(other)])) {
      if (base[p] === other[p]) continue;
      if (p in other) files[p] = other[p];
      else delete files[p];
    }
    const sha = this.store({ parents: [ours, theirs], files, date: this.now.toISOString(), message: `merge ${from}` });
    this.heads.set(branch, sha);
    return sha;
  }

  /** ディレクトリの tree。無ければ登録して oid を返す */
  protected treeOf(commit: string, dir: string): string | null {
    const files = this.commits.get(commit)?.files ?? {};
    const entries = Object.keys(files)
      .filter((p) => p.startsWith(`${dir}/`))
      .sort()
      .map((p) => ({ path: p.slice(dir.length + 1), sha: blobSha(files[p]), mode: this.linkPaths.has(p) ? "120000" : "100644" }));
    if (entries.length === 0) return null;
    const oid = sha1(`tree\n${JSON.stringify(entries)}`);
    this.trees.set(oid, { entries });
    return oid;
  }

  /** 祖先の全部（日付の新しい順） */
  protected ancestors(sha: string): Commit[] {
    const seen = new Map<string, Commit>();
    const stack = [sha];
    while (stack.length > 0) {
      const c = this.commits.get(stack.pop() as string);
      if (!c || seen.has(c.sha)) continue;
      seen.set(c.sha, c);
      stack.push(...c.parents);
    }
    return [...seen.values()].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  }

  /**
   * `git log <sha> -- <path>` の既定の簡略化にそろえた履歴（新しい順）。path を変えたコミットを出す。
   * merge コミットは、path がどれかの親と同じなら出さずにその親だけを辿り（別の枝に入ることもある）、
   * どの親とも違えば出して全部の親を辿る。path が無ければ最初の親の鎖を全部出す。
   */
  protected history(sha: string, path = ""): Commit[] {
    const out: Commit[] = [];
    const seen = new Set<string>();
    const stack = [sha];
    while (stack.length > 0) {
      const c = this.commits.get(stack.pop() as string);
      if (!c || seen.has(c.sha)) continue;
      seen.add(c.sha);
      if (!path) {
        out.push(c);
        if (c.parents[0]) stack.push(c.parents[0]);
        continue;
      }
      const mine = c.files[path];
      const same = c.parents.find((p) => this.commits.get(p)?.files[path] === mine);
      if (c.parents.length > 1 && same !== undefined) {
        stack.push(same);
        continue;
      }
      if (c.parents.length === 0 ? mine !== undefined : same === undefined) out.push(c);
      for (const p of [...c.parents].reverse()) stack.push(p);
    }
    return out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  }

  /** コミットの変更の一覧。消えたファイルと同じ中身で足されたファイルは renamed（本物の rename 検出にそろえる） */
  protected changed(c: Commit): { filename: string; status: string; previous_filename?: string }[] {
    const before = this.commits.get(c.parents[0] ?? "")?.files ?? {};
    const added = Object.keys(c.files).filter((p) => !(p in before));
    const removed = Object.keys(before).filter((p) => !(p in c.files));
    const out: { filename: string; status: string; previous_filename?: string }[] = [];
    const renamedFrom = new Set<string>();
    for (const p of added) {
      const from = removed.find((r) => !renamedFrom.has(r) && similar(before[r], c.files[p]));
      if (from) {
        renamedFrom.add(from);
        out.push({ filename: p, status: "renamed", previous_filename: from });
      } else {
        out.push({ filename: p, status: "added" });
      }
    }
    for (const p of removed) if (!renamedFrom.has(p)) out.push({ filename: p, status: "removed" });
    for (const p of Object.keys(c.files)) if (p in before && before[p] !== c.files[p]) out.push({ filename: p, status: "modified" });
    return out.sort((a, b) => (a.filename < b.filename ? -1 : 1)).slice(0, this.filesLimit);
  }

  /** 配列を per_page・page で切り、続きがあれば Link を付ける */
  protected paged(u: URL, items: unknown[]): { status: number; json: unknown; headers?: Record<string, string> } {
    const per = Number(u.searchParams.get("per_page") ?? "30");
    const page = Number(u.searchParams.get("page") ?? "1");
    const slice = items.slice((page - 1) * per, page * per);
    if (page * per >= items.length) return { status: 200, json: slice };
    const next = new URL(u.toString());
    next.searchParams.set("page", String(page + 1));
    return { status: 200, json: slice, headers: { link: `<${this.apiBase}${next.pathname}${next.search}>; rel="next"` } };
  }

  /** Link に書く API の根（単体試験は api.github.com、実機は serve の根） */
  apiBase = "https://api.github.com";

  handle(method: string, url: string, headers: Record<string, string>, body: string): { status: number; json: unknown; headers?: Record<string, string> } {
    const u = new URL(url, "http://mock");
    this.calls.push(`${method} ${u.pathname}`);
    this.onRequest?.(method, u);
    if (headers.authorization !== `Bearer ${TOKEN}`) return { status: 401, json: { message: "Bad credentials" } };
    const limit = this.limits.findIndex((l) => l.match.test(`${method} ${u.pathname}${u.search}`));
    if (limit >= 0) {
      const l = this.limits.splice(limit, 1)[0];
      if (l.graphql) return { status: 200, json: { data: null, errors: [{ type: "RATE_LIMITED", message: "API rate limit exceeded" }] }, headers: l.headers };
      return { status: l.status, json: { message: "You have exceeded a secondary rate limit" }, headers: l.headers };
    }
    for (const scene of this.scenes.values()) {
      const answered = sceneAnswer(scene, method, u, body);
      if (answered) return answered;
    }
    const base = `/repos/${this.owner}/${this.repo}`;
    if (method === "GET" && u.pathname === base) return { status: 200, json: { default_branch: this.defaultBranch } };
    const compare = new RegExp(`^${base}/compare/([0-9a-f]{40})\\.\\.\\.([0-9a-f]{40})$`).exec(u.pathname);
    if (method === "GET" && compare) return this.compare(compare[1], compare[2]);
    if (method === "GET" && u.pathname.startsWith(`${base}/git/ref/heads/`)) {
      const name = decodeURIComponent(u.pathname.slice(`${base}/git/ref/heads/`.length));
      const sha = this.heads.get(name);
      return sha ? { status: 200, json: { ref: `refs/heads/${name}`, object: { sha, type: "commit" } } } : { status: 404, json: { message: "Not Found" } };
    }
    if (method === "GET" && u.pathname.startsWith(`${base}/git/trees/`)) {
      const tree = this.trees.get(u.pathname.slice(`${base}/git/trees/`.length));
      if (!tree) return { status: 404, json: { message: "Not Found" } };
      return { status: 200, json: { truncated: false, tree: tree.entries.map((e) => ({ path: e.path, type: "blob", mode: e.mode, sha: e.sha })) } };
    }
    if (method === "GET" && u.pathname === `${base}/commits`) {
      const start = u.searchParams.get("sha") ?? "";
      const path = u.searchParams.get("path") ?? "";
      const per = Number(u.searchParams.get("per_page") ?? "30");
      if (!this.commits.has(start)) return { status: 404, json: { message: "Not Found" } };
      const list = this.history(start, path).map((c) => ({ sha: c.sha, parents: c.parents.map((p) => ({ sha: p })) }));
      return this.paged(u, list);
    }
    if (method === "GET" && u.pathname.startsWith(`${base}/commits/`)) {
      const c = this.commits.get(u.pathname.slice(`${base}/commits/`.length));
      if (!c) return { status: 404, json: { message: "Not Found" } };
      return { status: 200, json: { sha: c.sha, parents: c.parents.map((p) => ({ sha: p })), files: this.changed(c) } };
    }
    if (method === "GET" && u.pathname === `${base}/pulls`) {
      const head = u.searchParams.get("head") ?? "";
      const branch = head.startsWith(`${this.owner}:`) ? head.slice(this.owner.length + 1) : "";
      return this.paged(u, (this.pulls[branch] ?? []).map((p) => ({ number: p.number })));
    }
    const reviews = new RegExp(`^${base}/pulls/(\\d+)/reviews$`).exec(u.pathname);
    if (method === "GET" && reviews) {
      const pr = Object.values(this.pulls)
        .flat()
        .find((p) => p.number === Number(reviews[1]));
      return this.paged(u, (pr?.reviews ?? []).map((r) => ({ user: { login: r.user }, state: r.state })));
    }
    if (method === "GET" && u.pathname === `${base}/branches`) {
      return this.paged(u, [...this.heads.keys()].sort().map((name) => ({ name, commit: { sha: this.heads.get(name) } })));
    }
    if (method === "GET" && u.pathname === `${base}/issues`) {
      const list = this.issues.map((i) => ({ number: i.number, title: i.title, html_url: `https://github.com/${this.owner}/${this.repo}/issues/${i.number}`, ...(i.pull ? { pull_request: {} } : {}) }));
      return { status: 200, json: list };
    }
    if (method === "POST" && u.pathname === `${base}/git/refs`) {
      const req = JSON.parse(body || "{}") as { ref?: string; sha?: string };
      const name = String(req.ref ?? "").replace(/^refs\/heads\//, "");
      if (!name || this.heads.has(name)) return { status: 422, json: { message: "Reference already exists" } };
      if (!this.commits.has(String(req.sha))) return { status: 422, json: { message: "Object does not exist" } };
      this.heads.set(name, String(req.sha));
      this.createdBranches.push({ name, sha: String(req.sha) });
      return { status: 201, json: { ref: `refs/heads/${name}`, object: { sha: req.sha, type: "commit" } } };
    }
    if (method === "POST" && u.pathname.endsWith("/graphql")) return this.graphql(JSON.parse(body));
    return { status: 404, json: { message: "Not Found" } };
  }

  /** `GET /compare/<base>...<head>`。`base` が祖先なら ahead、同じなら identical、ほかは diverged。一覧は filesLimit で切る */
  protected compare(b: string, h: string): { status: number; json: unknown } {
    const from = this.commits.get(b);
    const to = this.commits.get(h);
    if (!from || !to) return { status: 404, json: { message: "Not Found" } };
    const status = b === h ? "identical" : this.ancestors(h).some((c) => c.sha === b) ? "ahead" : "diverged";
    const files = [...new Set([...Object.keys(from.files), ...Object.keys(to.files)])]
      .filter((p) => from.files[p] !== to.files[p])
      .sort()
      .map((p) => ({ filename: p, status: !(p in from.files) ? "added" : !(p in to.files) ? "removed" : "modified" }));
    return { status: 200, json: { status, files: files.slice(0, this.filesLimit) } };
  }

  protected graphql(req: { query: string; variables: Record<string, unknown> }): { status: number; json: unknown } {
    const { query, variables } = req;
    if (query.includes("history(first:")) {
      // 本物の history は first-parent に限らず祖先を日付順に返す。ここでも全部の祖先を返す
      const all = this.ancestors(String(variables.oid)).map((c) => ({ oid: c.sha, parents: { nodes: c.parents.slice(0, 1).map((p) => ({ oid: p })) } }));
      return { status: 200, json: { data: { repository: { object: { history: { nodes: all.slice(0, 100) } } } } } };
    }
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
            ? { text: null, isBinary: true, isTruncated: false, byteSize: Buffer.byteLength(text) }
            : this.truncatedBlobs.has(oid)
              ? { text: text.slice(0, 1), isBinary: false, isTruncated: true, byteSize: Buffer.byteLength(text) }
              : { text, isBinary: false, isTruncated: false, byteSize: Buffer.byteLength(text) };
    }
    return { status: 200, json: { data: { repository } } };
  }

  protected createCommit(input: Record<string, unknown>): { status: number; json: unknown } {
    const target = input.branch as { repositoryNameWithOwner?: string; branchName?: string };
    const branch = String(target?.branchName ?? "");
    const expected = String(input.expectedHeadOid ?? "");
    const changes = (input.fileChanges ?? {}) as { additions?: { path: string; contents: string }[]; deletions?: { path: string }[] };
    const call = {
      branch,
      expected,
      headline: String((input.message as { headline?: string })?.headline ?? ""),
      body: String((input.message as { body?: string })?.body ?? ""),
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

  protected responseHeaders(): Record<string, string> {
    return this.expiration ? { [EXPIRATION_HEADER]: this.expiration } : {};
  }

  /** `fetch` の代わり（単体試験） */
  fetch = async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    const lower: Record<string, string> = {};
    for (const [k, v] of Object.entries(init.headers)) lower[k.toLowerCase()] = v;
    const r = this.handle(init.method, url, lower, init.body ?? "");
    const h: Record<string, string> = { ...this.responseHeaders(), ...(r.headers ?? {}) };
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
        const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type, accept, x-github-api-version, private-token" };
        if (req.method === "OPTIONS") {
          res.writeHead(204, cors).end();
          return;
        }
        const r = this.handle(req.method ?? "GET", req.url ?? "/", headers, Buffer.concat(chunks).toString("utf8"));
        res.writeHead(r.status, { ...cors, ...this.responseHeaders(), ...(r.headers ?? {}), "Content-Type": "application/json" }).end(JSON.stringify(r.json));
      });
    });
    return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(() => new Promise((r) => server.close(() => r())))));
  }
}
