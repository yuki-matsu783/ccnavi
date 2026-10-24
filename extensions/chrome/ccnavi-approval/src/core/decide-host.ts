/**
 * decide のホスト書き込みを、順序を守って実行する。ネットワークには `HostOps` を通してしか出ない。
 * 画面は service worker 経由の `callOps` を渡す。PAT はここに来ない。
 *
 * 実行の順序は固定である。
 * (1) 全 issue の作成。作る前に、同じ操作 ID の印を本文の 1 行目に持つ issue を探す。あれば作らず、それを使う。
 *     探すのが失敗したら、作り直さずに止める。続きのコメントには `part=<i>` の印を付け、使い回した issue でも足りない分だけ付ける。
 * (2) 全返信。`{issue_url}` は作った issue の URL に置き換える。末尾に操作 ID の印を付け、同じ印の返信が既にあるスレッドには付けない。
 * (3) 全解決。
 * (4) MR へのコメント。計画の `comment` があるときだけ行い、`{issue_links}` は作った issue の URL の一覧に置き換える。
 *     `decidePhase` は `comment` を null で渡し、親のブランチへのコミットが済んでから自分で投稿する。
 * どこかで失敗したら、その時点で止めて、済ませたことと失敗の理由を返す。例外にはしない。同じキーは二度書かない。
 *
 * 呼ぶ側は、1 回の確定につき 1 度だけ呼ぶ。`writeLoop` の周の中では呼ばない。操作の記録は持たない。
 * 止まったときは、未解決のスレッドを読み直し、決め直して押せば続きが進む。
 * 書く前に `precheckHostPlan` で権限と、Chrome で扱えない種類のスレッドを見分け、足りなければ何も書かずに止める。
 */
import type { HostCall } from "./snapshot.js";
import type { FindResult, ThreadAbility, WriteInfo } from "./github.js";
import type { IssueRef } from "./github.js";

/** Python の `decide` が返す、ホストへ書く計画（`host_plan`） */
export interface HostPlan {
  readonly issues: readonly PlanIssue[];
  readonly replies: readonly PlanReply[];
  readonly resolves: readonly PlanResolve[];
  /** 決めた内容のコメント。無ければ null */
  readonly comment: string | null;
}

export interface PlanIssue {
  readonly key: string;
  readonly title: string;
  /** 先頭の近くに `<!-- ccnavi:issue op=… -->` の印を含む */
  readonly body: string;
  /** 操作 ID。あれば本文の印と同じでなければならない */
  readonly op?: string;
  /** 本文が長いときの続き。作った issue にコメントとして付ける */
  readonly comments?: readonly string[];
}

export interface PlanReply {
  readonly key: string;
  readonly thread_id: string;
  /** `{issue_url}` を含みうる（同じキーの issue を作った後に置き換える） */
  readonly body: string;
}

export interface PlanResolve {
  readonly key: string;
  readonly thread_id: string;
}

/** 返信の本文の、issue の URL を入れる枠 */
export const ISSUE_URL_SLOT = "{issue_url}";
/** コメントの、作った issue の一覧を入れる枠 */
export const ISSUE_LINKS_SLOT = "{issue_links}";

export interface RepoRef {
  readonly owner: string;
  readonly repo: string;
}

/** ホストへ書く操作（owner と repo は呼び出しごとに渡す）。画面側の実装は `callOps` */
export interface HostOps {
  writeInfo(repo: RepoRef, mr: number): Promise<WriteInfo>;
  findIssue(repo: RepoRef, marker: string): Promise<FindResult>;
  listIssueComments(repo: RepoRef, issue: number): Promise<string[]>;
  createIssue(repo: RepoRef, title: string, body: string): Promise<IssueRef>;
  commentIssue(repo: RepoRef, issue: number, body: string): Promise<void>;
  replyThread(repo: RepoRef, mr: number, threadId: string, body: string): Promise<void>;
  resolveThread(repo: RepoRef, mr: number, threadId: string): Promise<void>;
  postComment(repo: RepoRef, mr: number, body: string): Promise<void>;
}

/** service worker 経由の `HostOps`（画面から使う） */
export function callOps(call: HostCall): HostOps {
  return {
    writeInfo: async (r, mr) => (await call("writeInfo", [r.owner, r.repo, mr])) as WriteInfo,
    findIssue: async (r, marker) => (await call("findIssue", [r.owner, r.repo, marker])) as FindResult,
    listIssueComments: async (r, issue) => (await call("issueComments", [r.owner, r.repo, issue])) as string[],
    createIssue: async (r, title, body) => (await call("createIssue", [r.owner, r.repo, title, body])) as IssueRef,
    commentIssue: async (r, issue, body) => void (await call("commentIssue", [r.owner, r.repo, issue, body])),
    replyThread: async (r, mr, id, body) => void (await call("replyThread", [r.owner, r.repo, mr, id, body])),
    resolveThread: async (r, mr, id) => void (await call("resolveThread", [r.owner, r.repo, mr, id])),
    postComment: async (r, mr, body) => void (await call("postComment", [r.owner, r.repo, mr, body])),
  };
}

// ---- 計画の検査 ------------------------------------------------------------------

const MARKER_LINE = /^<!-- ccnavi:issue op=([A-Za-z0-9._:-]{1,100}) -->$/;
const THREAD_ID = /^[A-Za-z0-9_=-]{1,200}$/;
/** 本文の字数の上限（`github.MAX_WRITE_TEXT` と同じ。画面に github.ts を持ち込まないため写している） */
const MAX_TEXT = 60000;
const MAX_TITLE = 255;
const MAX_KEY = 1000;
/** 返信・コメントの本文の上限（URL・印・一覧を足しても本文の上限に収まるように） */
const MAX_REPLY = 55000;

function plainKey(key: unknown): key is string {
  return typeof key === "string" && key !== "" && key.length <= MAX_KEY && !/[\x00-\x1f\x7f]/.test(key);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "" && value.length <= MAX_TEXT && !value.includes("\0");
}

/** issue の題を 1 行にする（改行・制御文字は空白にし、上限で切る） */
export function oneLine(title: string): string {
  const flat = title.replace(/[\x00-\x1f\x7f\u2028\u2029]+/g, " ").replace(/\s+/g, " ").trim();
  return [...flat].slice(0, MAX_TITLE).join("");
}

/** 本文の 1 行目だけを印として読み、操作 ID を取り出す（無ければ空） */
export function markerOf(body: string): string {
  return MARKER_LINE.exec(body.split("\n", 1)[0].replace(/\r$/, ""))?.[1] ?? "";
}

/** 続きのコメントの印（先頭の 1 行） */
export function partMarker(op: string, part: number): string {
  return `<!-- ccnavi:issue op=${op} part=${part} -->`;
}

/** 返信の印の操作 ID（キー・スレッド・送る本文から決まる。同じ返信を打ち直しても同じ） */
async function replyOp(key: string, threadId: string, body: string): Promise<string> {
  const bytes = new TextEncoder().encode(`reply\0${key}\0${threadId}\0${body}`);
  const digest = new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

/** 実行中の計画（同じリポジトリの同じ操作 ID・スレッドを二重に動かさない） */
const running = new Set<string>();

/** 計画の形の問題を並べる（空なら書いてよい形）。ホストには触れない */
export function planProblems(plan: unknown): string[] {
  const out: string[] = [];
  const p = plan as Partial<Record<keyof HostPlan, unknown>> | null;
  if (!p || typeof p !== "object") return ["計画がオブジェクトでない"];
  if (!Array.isArray(p.issues) || !Array.isArray(p.replies) || !Array.isArray(p.resolves)) return ["計画の issues・replies・resolves が配列でない"];
  if (p.comment !== null && p.comment !== undefined && (!text(p.comment) || p.comment.length > MAX_REPLY)) out.push("コメントが空か長すぎる（null か本文にしてください）");
  const issueKeys = new Set<string>();
  const ops = new Set<string>();
  for (const i of p.issues as Partial<PlanIssue>[]) {
    if (!plainKey(i?.key)) {
      out.push("issue のキーが文字列ではありません");
      continue;
    }
    if (issueKeys.has(i.key)) out.push(`issue のキーが重なっています: ${i.key}`);
    issueKeys.add(i.key);
    if (typeof i.title !== "string" || oneLine(i.title) === "") out.push(`issue の題が空です: ${i.key}`);
    if (!text(i.body)) {
      out.push(`issue の本文が空か長すぎます: ${i.key}`);
      continue;
    }
    const op = markerOf(i.body);
    if (op === "") out.push(`issue の本文の 1 行目に操作 ID の印（<!-- ccnavi:issue op=… -->）がありません: ${i.key}`);
    else if (i.op !== undefined && i.op !== op) out.push(`issue の操作 ID が本文の印と違います: ${i.key}`);
    else if (ops.has(op)) out.push(`issue の操作 ID が重なっています: ${i.key}`);
    ops.add(op);
    if (i.comments !== undefined && (!Array.isArray(i.comments) || !i.comments.every(text))) out.push(`issue の続きのコメントが文字列の配列ではありません: ${i.key}`);
  }
  const replied = new Set<string>();
  const repliedThreads = new Set<string>();
  for (const r of p.replies as Partial<PlanReply>[]) {
    if (!plainKey(r?.key) || typeof r.thread_id !== "string" || !THREAD_ID.test(r.thread_id)) {
      out.push(`返信のキーかスレッドの id の形が正しくありません: ${String(r?.key)}`);
      continue;
    }
    if (replied.has(r.key) || repliedThreads.has(r.thread_id)) out.push(`返信が重なっています: ${r.key}`);
    replied.add(r.key);
    repliedThreads.add(r.thread_id);
    if (!text(r.body) || r.body.length > MAX_REPLY) out.push(`返信の本文が空か長すぎます: ${r.key}`);
    else if (r.body.includes(ISSUE_URL_SLOT) && !issueKeys.has(r.key)) out.push(`返信が issue の URL を求めているが、同じキーの issue が計画にありません: ${r.key}`);
  }
  const resolved = new Set<string>();
  const resolvedThreads = new Set<string>();
  for (const r of p.resolves as Partial<PlanResolve>[]) {
    if (!plainKey(r?.key) || typeof r.thread_id !== "string" || !THREAD_ID.test(r.thread_id)) {
      out.push(`解決のキーかスレッドの id の形が正しくありません: ${String(r?.key)}`);
      continue;
    }
    if (resolved.has(r.key) || resolvedThreads.has(r.thread_id)) out.push(`解決が重なっています: ${r.key}`);
    resolved.add(r.key);
    resolvedThreads.add(r.thread_id);
  }
  return out;
}

// ---- 書く前の確かめ ----------------------------------------------------------------

export type Precheck =
  | { readonly ok: true; readonly unknown: readonly string[] }
  | {
      readonly ok: false;
      /** plan: 計画の形が悪い。unsupported: Chrome で扱えない種類のスレッド。permission: 権限が足りない。unreadable: 確かめられなかった */
      readonly kind: "plan" | "unsupported" | "permission" | "unreadable";
      /** 日本語の理由（そのまま画面に出せる） */
      readonly reason: string;
      /** 足りない権限の名前（permission のとき） */
      readonly missing: readonly string[];
      /** 問題のあったキー */
      readonly keys: readonly string[];
    };

function reasonOf(err: unknown): string {
  return (err as { message?: string })?.message ?? String(err);
}

/** Chrome で扱えない種類のスレッドの名前（`ok` 以外） */
export function unsupportedReason(kind: Exclude<ThreadAbility["kind"], "ok">): string {
  return kind === "pending" ? "保留中のレビューのスレッド" : kind === "non-diff" ? "差分以外の discussion" : "解決できない種類のスレッド";
}

/**
 * 書く前に、PAT の権限とスレッドの種類を確かめる。足りなければ何も書かない。
 * - Chrome で扱えない種類のスレッド（GitLab の差分以外の discussion、解決できないスレッド、GitHub の保留中のレビュー）は、
 *   `unsupported` とし、VS Code へ誘導する文面を出す。
 * - GitHub は、スレッドの `viewerCanReply`・`viewerCanResolve` で見る。GitLab は、note の `resolvable` とロール・スコープで見る。
 * - fine-grained の PAT の権限は、書く前には読めない。確かめられなかったものは、`ok` の `unknown` に残す。
 */
export async function precheckHostPlan(ops: HostOps, repo: RepoRef, mr: number, plan: HostPlan): Promise<Precheck> {
  const problems = planProblems(plan);
  if (problems.length > 0) return { ok: false, kind: "plan", reason: `ホストへ書く計画の形が正しくないため、何も書きませんでした: ${problems.join(" / ")}`, missing: [], keys: [] };
  let info: WriteInfo;
  try {
    info = await ops.writeInfo(repo, mr);
  } catch (err) {
    return { ok: false, kind: "unreadable", reason: `書く前の確認ができなかったため、何も書きませんでした: ${reasonOf(err)}`, missing: [], keys: [] };
  }
  const lab = info.host === "gitlab";
  const threads = new Map(info.threads.map((t) => [t.id, t]));
  const unsupported: string[] = [];
  const why: string[] = [];
  const gone: string[] = [];
  const lacking = new Set<string>();
  const keys = new Set<string>();
  const flag = (key: string, id: string, need: "reply" | "resolve") => {
    const t = threads.get(id);
    if (!t) {
      gone.push(key);
      return;
    }
    if (t.kind !== "ok") {
      unsupported.push(key);
      why.push(unsupportedReason(t.kind));
      return;
    }
    if ((need === "reply" && !t.canReply) || (need === "resolve" && !t.canResolve)) {
      lacking.add(lab ? "api スコープと、Developer 以上のロール" : "Pull requests: Read and write");
      keys.add(key);
    }
  };
  for (const r of plan.replies) flag(r.key, r.thread_id, "reply");
  for (const r of plan.resolves) flag(r.key, r.thread_id, "resolve");
  if (unsupported.length > 0) {
    const kinds = [...new Set(why)].join("・");
    return {
      ok: false,
      kind: "unsupported",
      reason: `Chrome では扱えない。VS Code で行ってください（${kinds}があります。何も書きませんでした）`,
      missing: [],
      keys: [...new Set(unsupported)],
    };
  }
  if (gone.length > 0) {
    return {
      ok: false,
      kind: "unreadable",
      reason: "決めたスレッドがホストに見つかりませんでした（消えたか、別の MR のものです）。MR を読み直してください（何も書きませんでした）",
      missing: [],
      keys: [...new Set(gone)],
    };
  }
  const needs = new Set<string>(["all"]);
  if (plan.issues.length > 0) needs.add("issue");
  if (plan.replies.length > 0) needs.add("reply");
  if (plan.resolves.length > 0) needs.add("resolve");
  if (plan.comment) needs.add("comment");
  for (const m of info.missing) if (needs.has(m.for)) lacking.add(m.need);
  if (lacking.size > 0) {
    const names = [...lacking];
    return {
      ok: false,
      kind: "permission",
      reason: `PAT の権限が足りないため、何も書きませんでした。必要な権限: ${names.join("、")}。設定画面で PAT を作り直してください`,
      missing: names,
      keys: [...keys],
    };
  }
  return { ok: true, unknown: info.unknown };
}

// ---- 実行 -----------------------------------------------------------------------

export interface HostStepIssue {
  readonly key: string;
  readonly number: number;
  readonly url: string;
  /** 同じ印の issue が既にあったので作らなかった */
  readonly reused: boolean;
}

export type HostStepName = "plan" | "issue" | "issue-comment" | "reply" | "resolve" | "comment";

export interface HostStepFailure {
  readonly step: HostStepName;
  /** 失敗した対象のキー（コメントや計画全体なら null） */
  readonly key: string | null;
  readonly reason: string;
  readonly status?: number;
}

/** 実行の結果。止まったときは、そこまでに済ませたことが入る */
export interface HostStepResult {
  readonly ok: boolean;
  readonly issues: readonly HostStepIssue[];
  /** 返信の済んだキー（同じ返信の印が既にあって飛ばした分も含む） */
  readonly replied: readonly string[];
  /** 解決の済んだキー */
  readonly resolved: readonly string[];
  readonly commented: boolean;
  /** issue の検索が上限まで読んでも見つからず、探し切れていない（既に作った issue があれば重複する） */
  readonly searchTruncated: boolean;
  readonly failed?: HostStepFailure;
}

export interface HostStepEvent {
  readonly step: HostStepName;
  readonly key: string | null;
  readonly state: "start" | "done" | "reused" | "failed";
}

/** ホストが返した URL を、本文にそのまま入れてよい形に限る（空白・引用符・括弧・< > ` ・`-->` を含まない https/http） */
function safeUrl(url: unknown): string {
  if (typeof url !== "string" || !/^https?:\/\/[^\s`<>"'()\[\]{}\\]{1,2000}$/.test(url) || url.includes("-->")) {
    throw new Error(`ホストが返した URL を本文に入れられません: ${String(url).slice(0, 100)}`);
  }
  return url;
}

/**
 * 計画をホストへ書く。順序は issue → 返信 → 解決 → コメント。失敗したらそこで止め、例外にせず結果に入れる。
 * `onStep` は進み具合の通知（例外は無視する）。同じリポジトリの同じ操作 ID・スレッドを含む計画が実行中なら、
 * 何も書かずに断る（連打の二重実行を防ぐ）。
 */
export async function runHostPlan(ops: HostOps, repo: RepoRef, mr: number, plan: HostPlan, onStep?: (event: HostStepEvent) => void): Promise<HostStepResult> {
  const issues: HostStepIssue[] = [];
  const replied: string[] = [];
  const resolved: string[] = [];
  let commented = false;
  let searchTruncated = false;
  const notify = (step: HostStepName, key: string | null, state: HostStepEvent["state"]) => {
    try {
      onStep?.({ step, key, state });
    } catch {
      // 通知の失敗で書き込みを止めない
    }
  };
  const result = (failed?: HostStepFailure): HostStepResult => ({ ok: failed === undefined, issues, replied, resolved, commented, searchTruncated, ...(failed ? { failed } : {}) });
  const stop = (step: HostStepName, key: string | null, err: unknown): HostStepResult => {
    notify(step, key, "failed");
    const status = (err as { status?: number })?.status;
    return result({ step, key, reason: reasonOf(err), ...(typeof status === "number" && status > 0 ? { status } : {}) });
  };

  const problems = planProblems(plan);
  if (problems.length > 0) return stop("plan", null, new Error(`ホストへ書く計画の形が正しくないため、何も書きませんでした: ${problems.join(" / ")}`));

  // 同じ計画の再入を断る
  const where = `${repo.owner}/${repo.repo}`.toLowerCase();
  const claims = [...plan.issues.map((i) => `${where}#op:${markerOf(i.body)}`), ...plan.replies.map((r) => `${where}#thread:${r.thread_id}`), ...plan.resolves.map((r) => `${where}#thread:${r.thread_id}`)];
  if (claims.some((c) => running.has(c))) return stop("plan", null, new Error("同じ計画を実行中です。終わるまで待ってください（何も書きませんでした）"));
  for (const c of claims) running.add(c);
  try {
    return await execute();
  } finally {
    for (const c of claims) running.delete(c);
  }

  async function execute(): Promise<HostStepResult> {
    // (1) issue。作る前に同じ印の issue を探す。探すのが失敗したら、作り直さずに止める
    const urlOf = new Map<string, string>();
    for (const issue of plan.issues) {
      notify("issue", issue.key, "start");
      const op = markerOf(issue.body);
      let made: IssueRef;
      let reused = false;
      try {
        const found = await ops.findIssue(repo, `<!-- ccnavi:issue op=${op} -->`);
        if (found.truncated) searchTruncated = true;
        reused = found.issue !== null;
        made = found.issue ?? (await ops.createIssue(repo, oneLine(issue.title), issue.body));
        urlOf.set(issue.key, safeUrl(made.url));
      } catch (err) {
        return stop("issue", issue.key, err);
      }
      issues.push({ key: issue.key, number: made.number, url: made.url, reused });
      notify("issue", issue.key, reused ? "reused" : "done");
      // 続きのコメント: 印（part=<i>）が無い分だけ付ける。使い回した issue では、コメントを読めなければ止める
      const parts = issue.comments ?? [];
      if (parts.length > 0) {
        try {
          const have = reused ? await ops.listIssueComments(repo, made.number) : [];
          for (const [i, part] of parts.entries()) {
            const mark = partMarker(op, i + 1);
            if (have.some((c) => c.split("\n", 1)[0].replace(/\r$/, "") === mark)) continue;
            await ops.commentIssue(repo, made.number, `${mark}\n${part}`);
          }
        } catch (err) {
          return stop("issue-comment", issue.key, err);
        }
      }
    }

    // (2) 返信（issue の URL を置き換え、末尾に操作 ID の印を付ける。同じ印が既にあれば飛ばす）
    let known = new Map<string, readonly string[]>();
    if (plan.replies.length > 0) {
      try {
        known = new Map((await ops.writeInfo(repo, mr)).threads.map((t) => [t.id, t.replyOps ?? []]));
      } catch (err) {
        return stop("reply", null, new Error(`既存の返信を確かめられないため、返信を付けません: ${reasonOf(err)}`));
      }
    }
    for (const reply of plan.replies) {
      notify("reply", reply.key, "start");
      try {
        const url = urlOf.get(reply.key);
        if (reply.body.includes(ISSUE_URL_SLOT) && url === undefined) throw new Error("返信に入れる issue の URL がありません");
        const base = reply.body.split(ISSUE_URL_SLOT).join(url ?? "").trimEnd();
        const op = await replyOp(reply.key, reply.thread_id, base);
        if (!(known.get(reply.thread_id) ?? []).includes(op)) {
          await ops.replyThread(repo, mr, reply.thread_id, `${base}\n\n<!-- ccnavi:reply op=${op} key=${encodeURIComponent(reply.key).slice(0, 200)} -->\n`);
        }
      } catch (err) {
        return stop("reply", reply.key, err);
      }
      replied.push(reply.key);
      notify("reply", reply.key, "done");
    }

    // (3) 解決
    for (const r of plan.resolves) {
      notify("resolve", r.key, "start");
      try {
        await ops.resolveThread(repo, mr, r.thread_id);
      } catch (err) {
        return stop("resolve", r.key, err);
      }
      resolved.push(r.key);
      notify("resolve", r.key, "done");
    }

    // (4) MR へのコメント（作った issue の一覧を入れる）
    if (plan.comment) {
      notify("comment", null, "start");
      try {
        const links = issues.map((i) => `- ${i.url}`).join("\n");
        await ops.postComment(repo, mr, plan.comment.split(ISSUE_LINKS_SLOT).join(links));
      } catch (err) {
        return stop("comment", null, err);
      }
      commented = true;
      notify("comment", null, "done");
    }
    return result();
  }
}

const STEP_LABEL: Record<HostStepName, string> = {
  plan: "計画の検査",
  issue: "issue の作成",
  "issue-comment": "issue の続きのコメント",
  reply: "スレッドへの返信",
  resolve: "スレッドの解決",
  comment: "MR へのコメント",
};

/** 止まった理由だけを日本語にする（済んだ分は呼び手が別に出す） */
export function describeHostFailure(f: HostStepFailure): string {
  return `${STEP_LABEL[f.step]}で止まりました${f.key ? `（${f.key}）` : ""}: ${f.reason}`;
}

/** 結果を画面に出す日本語にする（止まったときは、済んだことと、続きの進め方まで言う） */
export function describeHostResult(r: HostStepResult): string {
  const done: string[] = [];
  if (r.issues.length > 0) done.push(`issue ${r.issues.map((i) => i.url).join(" ")}（${r.issues.length} 件${r.issues.some((i) => i.reused) ? "。うち既にあったものを使用" : ""}）`);
  if (r.replied.length > 0) done.push(`返信 ${r.replied.length} 件`);
  if (r.resolved.length > 0) done.push(`解決 ${r.resolved.length} 件`);
  if (r.commented) done.push("MR へのコメント");
  const summary = (done.length > 0 ? `済んだこと: ${done.join("、")}` : "ホストには何も書いていません") + (r.searchTruncated ? "。issue の検索が上限まで読んでも見つからず、探し切れていません（既に作った issue があれば重複します）" : "");
  if (r.ok || !r.failed) return summary;
  const f = r.failed;
  const retry = f.step === "plan" ? "" : "。MR を読み直して残った指摘を決め直せば、続きから進みます（同じ印の issue は作り直さず、返信も同じ印があれば付け直しません）";
  return `${STEP_LABEL[f.step]}で止まりました${f.key ? `（${f.key}）` : ""}: ${f.reason}。${summary}${retry}`;
}
