/**
 * decide のホスト書き込みの模擬ホスト。模擬の GitHub・GitLab に、スレッドへの返信と解決、issue の作成と検索、
 * MR へのコメント、書く前の確かめ（権限・スレッドの種類）を足す。書いた順は `log` に残るので、試験が順序を見る。
 *
 * 失敗は `failures` に積む（`match` に当たる書き込みを `status` で断る。1 回ずつ消える）。権限の足りない PAT は
 * `scopes`（GitHub の classic の `x-oauth-scopes`）や、スレッドの `canReply`・`canResolve`、GitLab のロールで作る。
 */
import { MockGitLab } from "./mock-gitlab.js";
import { GL_LOGIN } from "./mock-gitlab.js";
import { LOGIN, MockGitHub, TOKEN } from "./mock-github.js";

export interface WriteFailure {
  /** `log` に積む前の操作の名前（`issue`・`issueComment`・`reply`・`resolve`・`comment`・`find`） */
  readonly op: string;
  readonly status: number;
  readonly message?: string;
  /** 何回目の当たりから断るか（既定 1 回目） */
  readonly nth?: number;
}

export interface MockIssue {
  readonly number: number;
  readonly title: string;
  readonly body: string;
  readonly author: string;
  readonly comments: string[];
  readonly raw: Record<string, unknown>;
  /** 閉じた issue（一覧の state で絞る） */
  closed?: boolean;
}

export interface MockThread {
  readonly id: string;
  resolved: boolean;
  canReply: boolean;
  canResolve: boolean;
  pending?: boolean;
  /** GitLab: 差分以外の discussion（位置が無い） */
  nonDiff?: boolean;
  /** GitLab: 解決できないノート */
  notResolvable?: boolean;
  readonly replies: string[];
  /** 最初の指摘の書き手・場所・本文（決める流れの試験が、取得したスレッドとして返す） */
  url?: string;
  path?: string;
  line?: number;
  body?: string;
  /** 解決した後、取得したスレッド（reviewThreads・discussions）にまだ映らない回数（ホストの読み取りの遅れ） */
  stale?: number;
  /** GitLab: 代表のノートの id（url のキーになる）。既定は 100 */
  noteId?: number;
  /** GitHub: スレッドが属するリポジトリと PR の番号（既定は acme/widgets の 7） */
  repo?: string;
  number?: number;
}

export function thread(id: string, extra: Partial<MockThread> = {}): MockThread {
  return { id, resolved: false, canReply: true, canResolve: true, replies: [], ...extra };
}

/** 書き込みの共通の記録と失敗の仕掛け */
export class WriteState {
  /** 書いた順（`issue <タイトル>`・`issueComment <番号>`・`reply <id>`・`resolve <id>`・`comment`） */
  readonly log: string[] = [];
  /** 本文つきの記録（`{op, ...}`） */
  readonly writes: { op: string; id?: string; body?: string; title?: string; number?: number; raw?: unknown }[] = [];
  readonly failures: WriteFailure[] = [];
  readonly issues: MockIssue[] = [];
  threads: MockThread[] = [];
  nextIssue = 500;
  /** 解決が次の取得に映らない回数（解決のたびに、そのスレッドに付ける） */
  resolveLag = 0;
  /** MR へのコメント */
  readonly comments: string[] = [];
  private readonly seen = new Map<string, number>();

  /** 取得したスレッドに映る解決済みか。遅れの回数が残っていれば、1 回分減らして未解決に見せる */
  seenResolved(t: MockThread): boolean {
    if ((t.stale ?? 0) > 0) {
      t.stale = (t.stale ?? 0) - 1;
      return false;
    }
    return t.resolved;
  }

  /** この操作を断る失敗があれば返す */
  fail(op: string): WriteFailure | null {
    const n = (this.seen.get(op) ?? 0) + 1;
    this.seen.set(op, n);
    const i = this.failures.findIndex((f) => f.op === op && n >= (f.nth ?? 1));
    return i >= 0 ? this.failures.splice(i, 1)[0] : null;
  }
}

type Answer = { status: number; json: unknown; headers?: Record<string, string> };

export class WriteGitHub extends MockGitHub {
  readonly w = new WriteState();
  /** classic の PAT の `x-oauth-scopes`。null なら付けない（fine-grained） */
  scopes: string | null = null;
  hasIssues = true;
  archived = false;
  /** 作る issue の画面の根 */
  web = "https://github.com";
  /** 返信・解決・コメントを付ける MR の番号 */
  readonly mrNumber = 7;

  override handle(method: string, url: string, headers: Record<string, string>, body: string): Answer {
    const out = this.writeHandle(method, url, headers, body);
    const r = out ?? super.handle(method, url, headers, body);
    return this.scopes === null ? r : { ...r, headers: { ...(r.headers ?? {}), "x-oauth-scopes": this.scopes } };
  }

  private writeHandle(method: string, url: string, headers: Record<string, string>, body: string): Answer | null {
    const u = new URL(url, "http://mock");
    const base = `/repos/${this.owner}/${this.repo}`;
    const issuesPath = `${base}/issues`;
    const comment = new RegExp(`^${issuesPath}/(\\d+)/comments$`).exec(u.pathname);
    const mine = method === "POST" && (u.pathname === issuesPath || comment !== null);
    const find = method === "GET" && u.pathname === issuesPath && u.searchParams.has("state") && u.searchParams.has("per_page") && u.searchParams.get("per_page") === "100";
    const list = method === "GET" && comment !== null;
    if (!mine && !find && !list) return null;
    this.calls.push(`${method} ${u.pathname}`);
    if (headers.authorization !== `Bearer ${TOKEN}`) return { status: 401, json: { message: "Bad credentials" } };
    if (list) {
      const failure = this.w.fail("listComments");
      if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
      const issue = this.w.issues.find((i) => i.number === Number((comment as RegExpExecArray)[1]));
      const page = Number(u.searchParams.get("page") ?? "1");
      return issue ? { status: 200, json: issue.comments.map((b) => ({ body: b })).slice((page - 1) * 100, page * 100) } : { status: 404, json: { message: "Not Found" } };
    }
    const op = find ? "find" : comment ? (this.w.issues.some((i) => i.number === Number(comment[1])) ? "issueComment" : "comment") : "issue";
    const failure = this.w.fail(op);
    if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
    if (find) {
      const page = Number(u.searchParams.get("page") ?? "1");
      const state = u.searchParams.get("state");
      const all = [...this.w.issues].reverse().filter((i) => state === "all" || (state === "closed") === (i.closed === true)).map((i) => i.raw);
      return { status: 200, json: all.slice((page - 1) * 100, page * 100) };
    }
    const req = JSON.parse(body || "{}") as { title?: string; body?: string };
    if (comment) {
      const n = Number(comment[1]);
      const issue = this.w.issues.find((i) => i.number === n);
      if (issue) {
        issue.comments.push(String(req.body));
        this.w.log.push(`issueComment ${n}`);
        this.w.writes.push({ op: "issueComment", number: n, body: String(req.body) });
      } else {
        this.w.comments.push(String(req.body));
        this.w.log.push("comment");
        this.w.writes.push({ op: "comment", number: n, body: String(req.body) });
      }
      return { status: 201, json: { id: 1, html_url: `${this.web}/${this.owner}/${this.repo}/pull/${n}#issuecomment-1` } };
    }
    if (!this.hasIssues) return { status: 410, json: { message: "Issues are disabled for this repo" } };
    const number = this.w.nextIssue++;
    const raw = {
      number,
      title: req.title,
      body: req.body,
      html_url: `${this.web}/${this.owner}/${this.repo}/issues/${number}`,
      user: { login: LOGIN },
      // 作るときに送ったものをそのまま残す（assignee などが入っていないことを試験が見る）
      sent: JSON.parse(body || "{}"),
    };
    this.w.issues.push({ number, title: String(req.title), body: String(req.body), author: LOGIN, comments: [], raw });
    this.w.log.push(`issue ${req.title}`);
    this.w.writes.push({ op: "issue", title: String(req.title), body: String(req.body), number, raw: JSON.parse(body || "{}") });
    return { status: 201, json: raw };
  }

  /** PR（一覧に `pull_request` つきで混ざる）。本文に印を持っていても issue ではない */
  seedPr(body: string, author = LOGIN): void {
    const number = this.w.nextIssue++;
    const raw = { number, title: "PR", body, html_url: `${this.web}/${this.owner}/${this.repo}/pull/${number}`, user: { login: author }, pull_request: { url: "x" } };
    this.w.issues.push({ number, title: "PR", body, author, comments: [], raw });
  }

  /** 他人が作った issue（印だけ書いた偽物）や、前の回に作った issue を置く */
  seedIssue(body: string, author = LOGIN): MockIssue {
    const number = this.w.nextIssue++;
    const raw = { number, title: "既にある", body, html_url: `${this.web}/${this.owner}/${this.repo}/issues/${number}`, user: { login: author } };
    const made: MockIssue = { number, title: "既にある", body, author, comments: [], raw };
    this.w.issues.push(made);
    return made;
  }

  protected override graphql(req: { query: string; variables: Record<string, unknown> }): { status: number; json: unknown } {
    const { query, variables } = req;
    if (query.includes("addPullRequestReviewThreadReply")) {
      const input = variables.input as { pullRequestReviewThreadId: string; body: string };
      const failure = this.w.fail("reply");
      if (failure) return this.error(failure);
      const t = this.w.threads.find((x) => x.id === input.pullRequestReviewThreadId);
      if (!t) return { status: 200, json: { data: null, errors: [{ type: "NOT_FOUND", message: "Could not resolve to a node" }] } };
      if (!t.canReply) return { status: 200, json: { data: null, errors: [{ type: "FORBIDDEN", message: "Resource not accessible by personal access token" }] } };
      t.replies.push(input.body);
      this.w.log.push(`reply ${t.id}`);
      this.w.writes.push({ op: "reply", id: t.id, body: input.body });
      return { status: 200, json: { data: { addPullRequestReviewThreadReply: { comment: { id: `C_${t.replies.length}`, url: `${this.web}/x#r${t.replies.length}` } } } } };
    }
    if (query.includes("resolveReviewThread")) {
      const input = variables.input as { threadId: string };
      const failure = this.w.fail("resolve");
      if (failure) return this.error(failure);
      const t = this.w.threads.find((x) => x.id === input.threadId);
      if (!t) return { status: 200, json: { data: null, errors: [{ type: "NOT_FOUND", message: "Could not resolve to a node" }] } };
      if (!t.canResolve) return { status: 200, json: { data: null, errors: [{ type: "FORBIDDEN", message: "Resource not accessible by personal access token" }] } };
      t.resolved = true;
      t.stale = this.w.resolveLag;
      this.w.log.push(`resolve ${t.id}`);
      this.w.writes.push({ op: "resolve", id: t.id });
      return { status: 200, json: { data: { resolveReviewThread: { thread: { id: t.id, isResolved: true } } } } };
    }
    if (query.includes("PullRequestReviewThread {") && query.includes("node(id")) {
      const t = this.w.threads.find((x) => x.id === variables.id);
      if (!t) return { status: 200, json: { data: { node: null } } };
      return { status: 200, json: { data: { node: { pullRequest: { number: t.number ?? this.mrNumber, repository: { nameWithOwner: t.repo ?? `${this.owner}/${this.repo}` } } } } } };
    }
    if (query.includes("last: comments")) {
      // 取得したスレッド（reviewThreads）: 書いた返信と解決が次の取得に映る
      const nodes = this.w.threads.map((t, i) => ({
        id: t.id,
        isResolved: this.w.seenResolved(t),
        comments: { nodes: [{ url: t.url ?? `${this.web}/${this.owner}/${this.repo}/pull/${this.mrNumber}#discussion_r${1000 + i}`, path: t.path ?? "src/app.py", line: t.line ?? 3, body: t.body ?? `指摘 ${i + 1}`, createdAt: "2026-09-29T00:00:00Z" }] },
        last: { nodes: [{ id: `C_last_${i}_${t.replies.length}`, createdAt: "2026-09-29T00:00:00Z", author: { login: "reviewer" } }] },
      }));
      return { status: 200, json: { data: { repository: { pullRequest: { reviewThreads: { pageInfo: { hasNextPage: false, endCursor: null }, nodes } } } } } };
    }
    if (query.includes("viewerCanReply")) {
      if (variables.owner !== this.owner || variables.name !== this.repo) return { status: 200, json: { data: { repository: null } } };
      const nodes = this.w.threads.map((t) => ({
        id: t.id,
        isResolved: t.resolved,
        viewerCanReply: t.canReply,
        viewerCanResolve: t.canResolve,
        comments: { nodes: [{ state: t.pending ? "PENDING" : "SUBMITTED" }] },
        recent: { nodes: [{ body: "指摘" }, ...t.replies.map((body) => ({ body }))] },
      }));
      return {
        status: 200,
        json: {
          data: {
            repository: {
              hasIssuesEnabled: this.hasIssues,
              isArchived: this.archived,
              viewerPermission: "WRITE",
              pullRequest: { reviewThreads: { pageInfo: { hasNextPage: false, endCursor: null }, nodes } },
            },
          },
        },
      };
    }
    return super.graphql(req);
  }

  private error(f: WriteFailure): { status: number; json: unknown } {
    return f.status >= 500 || f.status === 403 ? { status: f.status, json: { message: f.message ?? "refused" } } : { status: 200, json: { data: null, errors: [{ type: "ERR", message: f.message ?? "refused" }] } };
  }
}

export class WriteGitLab extends MockGitLab {
  readonly w = new WriteState();
  /** PAT のスコープ。null なら `personal_access_tokens/self` を 404 にする（プロジェクトのトークンなど） */
  scopes: string[] | null = ["api"];
  /** プロジェクトのロール（Developer = 30）。0 なら permissions を返さない */
  role = 30;
  issuesEnabled = true;
  readonly web = "https://gitlab.com";
  readonly mrNumber = 7;

  private get proj(): string {
    return `/projects/${this.owner}%2F${this.repo}`;
  }

  override handle(method: string, url: string, headers: Record<string, string>, body: string): Answer {
    const u = new URL(url, "http://mock");
    const p = u.pathname.replace(/^.*?\/api\/v4/, "");
    const rest = p.startsWith(`${this.proj}/`) ? p.slice(this.proj.length) : "";
    const route = this.writeRoute(method, p, rest, u, body);
    if (route === null) return super.handle(method, url, headers, body);
    this.calls.push(`${method} ${p}`);
    if (headers["private-token"] !== TOKEN) return { status: 401, json: { message: "401 Unauthorized" } };
    return route();
  }

  /** 書き込みの経路。当たらなければ null（ふだんの模擬へ） */
  private writeRoute(method: string, p: string, rest: string, u: URL, body: string): (() => Answer) | null {
    const discussions = new RegExp("^/merge_requests/(\\d+)/discussions$").exec(rest);
    const note = new RegExp("^/merge_requests/(\\d+)/discussions/([^/]+)/notes$").exec(rest);
    const one = new RegExp("^/merge_requests/(\\d+)/discussions/([^/]+)$").exec(rest);
    const mrNote = new RegExp("^/merge_requests/(\\d+)/notes$").exec(rest);
    const issueNote = new RegExp("^/issues/(\\d+)/notes$").exec(rest);
    if (method === "GET" && p === "/personal_access_tokens/self") {
      return () => (this.scopes === null ? { status: 404, json: { message: "404 Not Found" } } : { status: 200, json: { id: 1, scopes: this.scopes, expires_at: null } });
    }
    if (method === "GET" && p === this.proj) {
      return () => ({
        status: 200,
        json: {
          id: 42,
          default_branch: this.defaultBranch,
          issues_enabled: this.issuesEnabled,
          ...(this.role > 0 ? { permissions: { project_access: { access_level: this.role }, group_access: null } } : { permissions: { project_access: null, group_access: null } }),
        },
      });
    }
    if (method === "GET" && new RegExp("^/merge_requests/(\\d+)/reviewers$").test(rest)) return () => ({ status: 200, json: [] });
    if (method === "GET" && discussions) return () => ({ status: 200, json: this.w.threads.map((t) => this.discussion(t, true)).slice((Number(u.searchParams.get("page") ?? "1") - 1) * 100, Number(u.searchParams.get("page") ?? "1") * 100) });
    if (method === "POST" && note) {
      return () => {
        const failure = this.w.fail("reply");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        const t = this.w.threads.find((x) => x.id === note[2]);
        if (!t) return { status: 404, json: { message: "404 Discussion Not Found" } };
        const req = JSON.parse(body || "{}") as { body?: string };
        t.replies.push(String(req.body));
        this.w.log.push(`reply ${t.id}`);
        this.w.writes.push({ op: "reply", id: t.id, body: String(req.body) });
        return { status: 201, json: { id: 9000 + t.replies.length, body: req.body } };
      };
    }
    if (method === "PUT" && one) {
      return () => {
        const failure = this.w.fail("resolve");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        const t = this.w.threads.find((x) => x.id === one[2]);
        if (!t) return { status: 404, json: { message: "404 Discussion Not Found" } };
        if (!t.canResolve) return { status: 403, json: { message: "403 Forbidden" } };
        t.resolved = JSON.parse(body || "{}").resolved === true;
        t.stale = this.w.resolveLag;
        this.w.log.push(`resolve ${t.id}`);
        this.w.writes.push({ op: "resolve", id: t.id });
        return { status: 200, json: this.discussion(t) };
      };
    }
    if (method === "GET" && issueNote) {
      return () => {
        const failure = this.w.fail("listComments");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        const issue = this.w.issues.find((i) => i.number === Number(issueNote[1]));
        return issue ? { status: 200, json: issue.comments.map((b) => ({ body: b })) } : { status: 404, json: { message: "404 Not Found" } };
      };
    }
    if (method === "POST" && (mrNote || issueNote)) {
      return () => {
        const isIssue = issueNote !== null;
        const failure = this.w.fail(isIssue ? "issueComment" : "comment");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        const req = JSON.parse(body || "{}") as { body?: string };
        if (isIssue) {
          const n = Number(issueNote[1]);
          this.w.issues.find((i) => i.number === n)?.comments.push(String(req.body));
          this.w.log.push(`issueComment ${n}`);
          this.w.writes.push({ op: "issueComment", number: n, body: String(req.body) });
        } else {
          this.w.comments.push(String(req.body));
          this.w.log.push("comment");
          this.w.writes.push({ op: "comment", body: String(req.body) });
        }
        return { status: 201, json: { id: 8000, body: req.body } };
      };
    }
    if (method === "POST" && rest === "/issues") {
      return () => {
        const failure = this.w.fail("issue");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        if (!this.issuesEnabled) return { status: 404, json: { message: "404 Not Found" } };
        const req = JSON.parse(body || "{}") as { title?: string; description?: string };
        const iid = this.w.nextIssue++;
        const raw = { iid, title: req.title, description: req.description, web_url: `${this.web}/${this.owner}/${this.repo}/-/issues/${iid}`, author: { id: 7, username: GL_LOGIN } };
        this.w.issues.push({ number: iid, title: String(req.title), body: String(req.description), author: GL_LOGIN, comments: [], raw });
        this.w.log.push(`issue ${req.title}`);
        this.w.writes.push({ op: "issue", title: String(req.title), body: String(req.description), number: iid, raw: JSON.parse(body || "{}") });
        return { status: 201, json: raw };
      };
    }
    if (method === "GET" && rest === "/issues" && u.searchParams.get("state") === "all") {
      return () => {
        const failure = this.w.fail("find");
        if (failure) return { status: failure.status, json: { message: failure.message ?? "refused" } };
        const page = Number(u.searchParams.get("page") ?? "1");
        return { status: 200, json: [...this.w.issues].reverse().filter((i) => u.searchParams.get("state") === "all" || i.closed !== true).map((i) => i.raw).slice((page - 1) * 100, page * 100) };
      };
    }
    return null;
  }

  seedIssue(body: string, authorId = 7): MockIssue {
    const iid = this.w.nextIssue++;
    const raw = { iid, title: "既にある", description: body, web_url: `${this.web}/${this.owner}/${this.repo}/-/issues/${iid}`, author: { id: authorId, username: "someone" } };
    const made: MockIssue = { number: iid, title: "既にある", body, author: String(authorId), comments: [], raw };
    this.w.issues.push(made);
    return made;
  }

  private discussion(t: MockThread, read = false): Record<string, unknown> {
    const resolvable = t.notResolvable !== true;
    const resolved = read ? this.w.seenResolved(t) : t.resolved;
    const diff = t.nonDiff !== true;
    return {
      id: t.id,
      individual_note: false,
      notes: [
        {
          id: t.noteId ?? 100,
          type: diff ? "DiffNote" : "DiscussionNote",
          system: false,
          body: t.body ?? "指摘",
          resolvable,
          resolved: resolvable ? resolved : false,
          author: { id: 202, username: "reviewer" },
          position: diff ? { new_path: "src/app.py", new_line: 3 } : null,
        },
        ...t.replies.map((body, i) => ({ id: 200 + i, type: "DiscussionNote", system: false, body, resolvable, resolved: resolvable ? resolved : false, author: { id: 7, username: "bot" }, position: null })),
      ],
    };
  }
}
