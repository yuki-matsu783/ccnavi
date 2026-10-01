/**
 * 模擬の GitLab（段階 5）。見本のリポジトリ（test/fixtures/repo.ts）を、拡張が使う GitLab の REST API（v4）の形で返す。
 * コミットの置き場（sha・tree・履歴・push・merge）は模擬の GitHub と同じものを使う（`MockGitHub` を継ぐ）。
 *
 * 書き込みは Commits API（`POST repository/commits` の `actions`）。本物と同じく「先頭がこの sha のときだけ」の指定は
 * 無く、今の先頭に積む（作るのに既にある・書き換えるのに無い・消すのに無いは 400）。試験は `beforeCommit` で、
 * 拡張が先頭を読んだ後・書く前に他の書き手を割り込ませ、事後確認と打ち消し（8.4 の 1 段目）を見る。
 *
 * MR のスレッドとレビューは、録ったホストの応答の見本（test/fixtures/host/gitlab/。`gitlab-fixture.ts`）を付けると
 * そのとおりに返す（`attachGitLabScene`）。
 */
import http from "node:http";
import { MockGitHub, TOKEN, blobSha, type Commit } from "./mock-github.js";
import { gitlabSceneAnswer, type GitLabScene } from "./gitlab-fixture.js";

export const GL_LOGIN = "lab-approver";

export interface GitLabCommitCall {
  readonly branch: string;
  readonly message: string;
  readonly actions: { action: string; file_path: string; content?: string; encoding?: string; last_commit_id?: string }[];
  readonly result: "written" | "error";
  readonly oid?: string;
  readonly parent?: string;
}

export class MockGitLab extends MockGitHub {
  readonly glCommits: GitLabCommitCall[] = [];
  /** `GET /personal_access_tokens/self` の `expires_at`（空なら null） */
  glExpiry = "";
  /** 付けた見本（GitLab の MR・スレッド・レビュアー） */
  readonly glScenes = new Map<string, GitLabScene>();
  /** compare の変更の一覧を切る件数（拡張は 450 件以上で打ち切られたとみなす） */
  compareLimit = 1000;
  /** compare の答えに足す欄（`compare_timeout`、各 diff の `collapsed`・`too_large`） */
  compareExtra: { timeout?: boolean; collapsed?: boolean; tooLarge?: boolean } = {};
  /** フォーク（source_project_id 99）の同じ名前のブランチの開いた MR */
  readonly forkMrs: { branch: string; number: number }[] = [];

  /** `ref` の上でそのファイルを最後に変えたコミット（`git log -1 -- path`） */
  lastChange(ref: string, path: string): string {
    return this.history(ref, path)[0]?.sha ?? "";
  }

  attachGitLabScene(branch: string, scene: GitLabScene): void {
    this.glScenes.clear();
    scene.branch = branch;
    this.glScenes.set(branch, scene);
  }

  private get project(): string {
    return `/projects/${this.owner}%2F${this.repo}`;
  }

  private list(u: URL, items: unknown[]): { status: number; json: unknown } {
    const per = Number(u.searchParams.get("per_page") ?? "20");
    const page = Number(u.searchParams.get("page") ?? "1");
    return { status: 200, json: items.slice((page - 1) * per, page * per) };
  }

  private branchJson(name: string): Record<string, unknown> {
    const sha = this.heads.get(name) as string;
    return { name, commit: { id: sha, committed_date: (this.commits.get(sha) as Commit).date } };
  }

  /** GitLab の repository/tree の答え（`path` の下。再帰なら全部の段、でなければ直下だけ） */
  private treeItems(ref: string, path: string, recursive: boolean): unknown[] | null {
    const files = this.commits.get(ref)?.files;
    if (!files) return null;
    const prefix = path ? `${path}/` : "";
    const under = Object.keys(files)
      .filter((p) => p.startsWith(prefix))
      .sort();
    if (path && under.length === 0) return null;
    const out: unknown[] = [];
    const dirs = new Set<string>();
    for (const p of under) {
      const rest = p.slice(prefix.length);
      const parts = rest.split("/");
      for (let i = 1; i < parts.length; i += 1) {
        if (!recursive && i > 1) break;
        dirs.add(prefix + parts.slice(0, i).join("/"));
      }
      if (!recursive && parts.length > 1) continue;
      out.push({ id: blobSha(files[p]), name: parts[parts.length - 1], type: "blob", path: p, mode: this.linkPaths.has(p) ? "120000" : "100644" });
    }
    for (const d of [...dirs].sort()) {
      out.push({ id: this.treeOf(ref, d), name: d.slice(d.lastIndexOf("/") + 1), type: "tree", path: d, mode: "040000" });
    }
    return out;
  }

  private mergeBase(a: string, b: string): string | null {
    const mine = new Set(this.ancestors(a).map((c) => c.sha));
    return this.ancestors(b).find((c) => mine.has(c.sha))?.sha ?? null;
  }

  override handle(method: string, url: string, headers: Record<string, string>, body: string): { status: number; json: unknown; headers?: Record<string, string> } {
    const u = new URL(url, "http://mock");
    const p = u.pathname.replace(/^.*?\/api\/v4/, "");
    this.calls.push(`${method} ${p}`);
    this.onRequest?.(method, u);
    if (headers["private-token"] !== TOKEN) return { status: 401, json: { message: "401 Unauthorized" } };
    for (const scene of this.glScenes.values()) {
      const answered = gitlabSceneAnswer(scene, method, u);
      if (answered) return answered;
    }
    if (method === "GET" && p === "/user") return { status: 200, json: { id: 7, username: GL_LOGIN } };
    if (method === "GET" && p === "/personal_access_tokens/self") return { status: 200, json: { id: 1, expires_at: this.glExpiry || null } };
    const base = this.project;
    if (method === "GET" && p === base) return { status: 200, json: { id: 42, default_branch: this.defaultBranch } };
    if (!p.startsWith(`${base}/`)) return { status: 404, json: { message: "404 Project Not Found" } };
    const rest = p.slice(base.length);
    if (method === "GET" && rest.startsWith("/repository/branches/")) {
      const name = decodeURIComponent(rest.slice("/repository/branches/".length));
      return this.heads.has(name) ? { status: 200, json: this.branchJson(name) } : { status: 404, json: { message: "404 Branch Not Found" } };
    }
    if (method === "GET" && rest === "/repository/branches") {
      return this.list(u, [...this.heads.keys()].sort().map((n) => this.branchJson(n)));
    }
    if (method === "POST" && rest === "/repository/branches") {
      const name = u.searchParams.get("branch") ?? "";
      const ref = u.searchParams.get("ref") ?? "";
      if (!name || this.heads.has(name)) return { status: 400, json: { message: "Branch already exists" } };
      if (!this.commits.has(ref)) return { status: 400, json: { message: "Invalid reference name" } };
      this.heads.set(name, ref);
      this.createdBranches.push({ name, sha: ref });
      return { status: 201, json: this.branchJson(name) };
    }
    if (method === "GET" && rest === "/repository/tree") {
      const items = this.treeItems(u.searchParams.get("ref") ?? "", u.searchParams.get("path") ?? "", u.searchParams.get("recursive") === "true");
      return items === null ? { status: 404, json: { message: "404 Tree Not Found" } } : this.list(u, items);
    }
    if (method === "GET" && rest.startsWith("/repository/blobs/")) {
      const oid = rest.slice("/repository/blobs/".length);
      const text = this.blobs.get(oid);
      if (text === undefined) return { status: 404, json: { message: "404 Blob Not Found" } };
      const bytes = Buffer.from(text, "utf8");
      return { status: 200, json: { size: bytes.length, encoding: "base64", content: bytes.toString("base64"), sha: oid } };
    }
    if (method === "GET" && rest === "/repository/commits") {
      const start = u.searchParams.get("ref_name") ?? "";
      if (!this.commits.has(start)) return { status: 404, json: { message: "404 Commit Not Found" } };
      let list: Commit[];
      if (u.searchParams.get("first_parent") === "true") {
        list = [];
        for (let at: string | undefined = start; at; at = this.commits.get(at)?.parents[0]) list.push(this.commits.get(at) as Commit);
      } else {
        list = this.history(start, u.searchParams.get("path") ?? "");
      }
      return this.list(u, list.map((c) => ({ id: c.sha, parent_ids: [...c.parents] })));
    }
    if (method === "GET" && rest.startsWith("/repository/commits/")) {
      const c = this.commits.get(rest.slice("/repository/commits/".length));
      return c ? { status: 200, json: { id: c.sha, parent_ids: [...c.parents] } } : { status: 404, json: { message: "404 Commit Not Found" } };
    }
    if (method === "GET" && rest === "/repository/merge_base") {
      const refs = u.searchParams.getAll("refs[]");
      const found = refs.length === 2 ? this.mergeBase(refs[0], refs[1]) : null;
      return found ? { status: 200, json: { id: found } } : { status: 404, json: { message: "404 Not Found" } };
    }
    if (method === "GET" && rest === "/repository/compare") {
      const from = this.commits.get(u.searchParams.get("from") ?? "");
      const to = this.commits.get(u.searchParams.get("to") ?? "");
      if (!from || !to) return { status: 404, json: { message: "404 Not Found" } };
      const diffs = [...new Set([...Object.keys(from.files), ...Object.keys(to.files)])]
        .filter((q) => from.files[q] !== to.files[q])
        .sort()
        .map((q) => ({
          old_path: q,
          new_path: q,
          new_file: !(q in from.files),
          deleted_file: !(q in to.files),
          renamed_file: false,
          ...(this.compareExtra.collapsed ? { collapsed: true } : {}),
          ...(this.compareExtra.tooLarge ? { too_large: true } : {}),
        }));
      return { status: 200, json: { commits: [], diffs: diffs.slice(0, this.compareLimit), compare_timeout: this.compareExtra.timeout === true, compare_same_ref: false } };
    }
    if (method === "GET" && rest.startsWith("/repository/files/")) {
      const path = decodeURIComponent(rest.slice("/repository/files/".length));
      const ref = u.searchParams.get("ref") ?? "";
      if (!this.commits.get(ref)?.files[path]) return { status: 404, json: { message: "404 File Not Found" } };
      return { status: 200, json: { file_path: path, last_commit_id: this.lastChange(ref, path) } };
    }
    if (method === "POST" && rest === "/repository/commits") return this.commitActions(JSON.parse(body || "{}"));
    if (method === "GET" && rest === "/merge_requests") {
      const branch = u.searchParams.get("source_branch") ?? "";
      // フォークの MR を先に並べる（1 ページ目がフォークで埋まる形を作れるように）。ページは per_page と page で切る
      return this.list(u, [
        ...this.forkMrs.filter((m) => m.branch === branch).map((m) => ({ iid: m.number, source_project_id: 99, web_url: `https://gitlab.com/fork/${this.repo}/-/merge_requests/${m.number}` })),
        ...(this.pulls[branch] ?? []).map((m) => ({ iid: m.number, source_project_id: 42, web_url: `https://gitlab.com/${this.owner}/${this.repo}/-/merge_requests/${m.number}` })),
      ]);
    }
    const approvals = /^\/merge_requests\/(\d+)\/approvals$/.exec(rest);
    if (method === "GET" && approvals) {
      const mr = Object.values(this.pulls)
        .flat()
        .find((m) => m.number === Number(approvals[1]));
      return { status: 200, json: { approved_by: (mr?.reviews ?? []).filter((r) => r.state === "APPROVED").map((r) => ({ user: { username: r.user } })) } };
    }
    if (method === "GET" && rest === "/issues") {
      return { status: 200, json: this.issues.map((i) => ({ iid: i.number, title: i.title, web_url: `https://gitlab.com/${this.owner}/${this.repo}/-/issues/${i.number}` })) };
    }
    return { status: 404, json: { message: "404 Not Found" } };
  }

  /** Commits API。今の先頭に積む（比べる指定は無い）。作る・書き換える・消すの前提が外れたら 400 */
  private commitActions(req: { branch?: string; commit_message?: string; actions?: GitLabCommitCall["actions"] }): { status: number; json: unknown } {
    const branch = String(req.branch ?? "");
    const call = { branch, message: String(req.commit_message ?? ""), actions: req.actions ?? [] };
    if (!this.heads.has(branch)) {
      this.glCommits.push({ ...call, result: "error" });
      return { status: 400, json: { message: "You can only create or edit files when you are on a branch" } };
    }
    const hook = this.beforeCommit;
    this.beforeCommit = null;
    hook?.(branch);
    const parent = this.heads.get(branch) as string;
    const files = { ...this.files(branch) };
    for (const a of call.actions) {
      const exists = a.file_path in files;
      if ((a.action === "create" && exists) || (a.action !== "create" && !exists)) {
        this.glCommits.push({ ...call, result: "error" });
        return { status: 400, json: { message: a.action === "create" ? "A file with this name already exists" : "A file with this name doesn't exist" } };
      }
      // 本物と同じく、last_commit_id がそのファイルを最後に変えたコミットでなければ断る（決定 A）
      if (a.action !== "create" && a.last_commit_id !== undefined && a.last_commit_id !== this.lastChange(parent, a.file_path)) {
        this.glCommits.push({ ...call, result: "error" });
        return { status: 400, json: { message: "You are attempting to update a file that has changed since you started editing it." } };
      }
      if (a.action === "delete") delete files[a.file_path];
      else files[a.file_path] = a.encoding === "base64" ? Buffer.from(a.content ?? "", "base64").toString("utf8") : (a.content ?? "");
    }
    const oid = this.store({ parents: [parent], files, date: this.now.toISOString(), message: call.message });
    this.heads.set(branch, oid);
    this.glCommits.push({ ...call, result: "written", oid, parent });
    if (this.loseCommitResponse) {
      this.loseCommitResponse = false;
      return { status: 502, json: { message: "502 Bad Gateway" } };
    }
    return { status: 201, json: { id: oid, parent_ids: [parent], message: call.message } };
  }

  /** HTTP に出す（実機の試験） */
  override serve(port: number): Promise<() => Promise<void>> {
    const server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        const headers: Record<string, string> = {};
        for (const [k, v] of Object.entries(req.headers)) if (typeof v === "string") headers[k] = v;
        const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "private-token, content-type, accept" };
        if (req.method === "OPTIONS") {
          res.writeHead(204, cors).end();
          return;
        }
        const r = this.handle(req.method ?? "GET", req.url ?? "/", headers, Buffer.concat(chunks).toString("utf8"));
        res.writeHead(r.status, { ...cors, ...(r.headers ?? {}), "Content-Type": "application/json" }).end(JSON.stringify(r.json));
      });
    });
    return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(() => new Promise((r) => server.close(() => r())))));
  }
}
