/**
 * GitHub の読み書き。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * 操作は名前で限る。画面から来るのは操作の名前と引数だけで、URL・クエリ・ヘッダは
 * ここで組む。GraphQL の問い合わせは固定の文で、引数は変数で渡す（文に継ぎ足さない）。
 * 書くのは `createCommitOnBranch`（`expectedHeadOid` つき）の 1 つだけ。
 */
import type { Host } from "./hosts.js";

export type Fetch = (url: string, init: { method: string; headers: Record<string, string>; body?: string; redirect?: "error" }) => Promise<{
  status: number;
  ok: boolean;
  json(): Promise<unknown>;
  /** 応答ヘッダ。PAT の期限（`github-authentication-token-expiration`）を読む */
  headers?: { get(name: string): string | null };
}>;

/** PAT の期限を返す応答ヘッダ（GitHub の fine-grained / classic のトークン） */
export const EXPIRATION_HEADER = "github-authentication-token-expiration";

/** 呼んだ回数。読み取り量の見積もりと突き合わせるために数える */
export interface Counter {
  rest: number;
  graphql: number;
}

export class HostError extends Error {
  constructor(
    message: string,
    readonly status = 0,
  ) {
    super(message);
  }
}

/** 1 回の GraphQL で取る blob の上限 */
export const BLOB_BATCH = 50;
/** 直近のブランチを読むページの上限。100 × 10 本を超えるリポジトリは古い方を諦める */
const REF_PAGES = 10;

const NAME = /^[A-Za-z0-9_.-]{1,100}$/;
const OID = /^[0-9a-f]{40}$/;
/** ブランチ名。git の ref の規則のうち、ここで困るものだけを見る */
const BRANCH = /^(?!\/)(?!.*\/\/)(?!.*\.\.)(?!.*[\s~^:?*[\\])(?!.*@\{)[^\x00-\x1f\x7f]{1,250}(?<![/.])$/;

export function checkName(value: unknown, what: string): string {
  if (typeof value !== "string" || !NAME.test(value) || value === "." || value === "..") {
    throw new HostError(`${what} の形が正しくありません：${String(value)}`);
  }
  return value;
}

export function checkBranch(value: unknown): string {
  if (typeof value !== "string" || !BRANCH.test(value) || value.endsWith(".lock")) {
    throw new HostError(`ブランチ名の形が正しくありません：${String(value)}`);
  }
  return value;
}

export function checkOid(value: unknown): string {
  if (typeof value !== "string" || !OID.test(value)) {
    throw new HostError(`sha を解釈できません：${String(value)}`);
  }
  return value;
}

export function checkPath(value: unknown): string {
  if (
    typeof value !== "string" ||
    value === "" ||
    value.startsWith("/") ||
    value.includes("\\") ||
    value.split("/").some((p) => p === "" || p === "." || p === "..")
  ) {
    throw new HostError(`パスを解釈できません：${String(value)}`);
  }
  return value;
}

export interface Client {
  readonly host: Host;
  readonly token: string;
  readonly fetch: Fetch;
  readonly counter: Counter;
  /** 応答から読んだもの（PAT の期限。読めなければ空）。呼び手が記録する */
  readonly seen?: { expiration: string };
  /** 待つ（レート制限の Retry-After）。試験は差し替える */
  readonly sleep?: (ms: number) => Promise<void>;
}

function note(client: Client, res: Awaited<ReturnType<Fetch>>): void {
  const exp = res.headers?.get(EXPIRATION_HEADER);
  if (client.seen && typeof exp === "string" && exp.trim() !== "") client.seen.expiration = exp.trim();
}

function headers(client: Client): Record<string, string> {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${client.token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

/** Retry-After を待ち直す上限（秒）。これより長ければ待たずに原因を言う */
export const RETRY_LIMIT_SECONDS = 60;

type Res = Awaited<ReturnType<Fetch>>;

function header(res: Res, name: string): string {
  return res.headers?.get(name)?.trim() ?? "";
}

/** 403/429 がレート制限なら、その説明と待つ秒数（待てなければ null）。レート制限でなければ null */
function rateLimit(res: Res): { text: string; wait: number | null } | null {
  const after = header(res, "retry-after");
  const remaining = header(res, "x-ratelimit-remaining");
  const reset = Number(header(res, "x-ratelimit-reset"));
  if (!(res.status === 429 || (res.status === 403 && (after !== "" || remaining === "0")))) return null;
  const seconds = after !== "" && /^\d+$/.test(after) ? Number(after) : null;
  const when = remaining === "0" && Number.isFinite(reset) && reset > 0 ? `。制限が解けるのは ${new Date(reset * 1000).toISOString()}` : "";
  const kind = after !== "" ? "二次のレート制限" : "レート制限";
  return {
    text: `GitHub の${kind}にかかりました（${res.status}${seconds !== null ? `、${seconds} 秒待つよう求められました` : ""}${when}）。少し待ってからボードを更新してください`,
    wait: seconds !== null && seconds <= RETRY_LIMIT_SECONDS ? seconds : null,
  };
}

/**
 * 転送を追わずに呼ぶ（`redirect: "error"`）。fetch そのものが失敗したら（転送された・通信が途切れた。ブラウザでは両者を
 * 見分けられない）、原因の分かる文面に包む。status は 0 のまま（書く流れは「届いたか分からない」として確かめる）
 */
export async function fetchNoRedirect(client: Pick<Client, "fetch">, url: string, init: Parameters<Fetch>[1], what: string): ReturnType<Fetch> {
  try {
    return await client.fetch(url, { ...init, redirect: "error" });
  } catch (err) {
    throw new HostError(
      `${what} が届きませんでした（通信が切れたか、別の場所へ転送されました。転送先へは送信しません）。通信先と、設定画面に登録したオーナー／リポジトリ（改名・移動していないか）を確認してください：${(err as Error).message ?? String(err)}`,
    );
  }
}

async function pause(client: Client, seconds: number): Promise<void> {
  const sleep = client.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  await sleep(seconds * 1000);
}

/**
 * 1 回の呼び出し。401 は差し替えを促し、レート制限（403・429、Retry-After）は原因を言う。
 * Retry-After が短ければ 1 回だけ待ち直す（15 の決定）。
 */
async function send(client: Client, url: string, init: Parameters<Fetch>[1], what: string): Promise<Res> {
  for (let attempt = 0; ; attempt += 1) {
    // 別のホストへの転送を追わない（PAT を載せた要求を通信先の一覧の外へ出さない）
    const res = await fetchNoRedirect(client, url, init, what);
    note(client, res);
    if (res.status === 401) {
      throw new HostError("PAT で認証できません（401）。設定画面で差し替えてください", 401);
    }
    const limited = rateLimit(res);
    if (limited) {
      if (limited.wait !== null && attempt === 0) {
        await pause(client, limited.wait);
        continue;
      }
      throw new HostError(limited.text, res.status);
    }
    if (res.status === 403) {
      throw new HostError(`GitHub が拒否しました（403）：${what}。PAT の権限（リポジトリ・Contents など）を見直してください`, 403);
    }
    return res;
  }
}

async function rest(client: Client, path: string): Promise<{ status: number; body: unknown; next: string }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "GET", headers: headers(client) }, `GET ${path}`);
  if (res.status === 404) {
    return { status: 404, body: null, next: "" };
  }
  if (!res.ok) {
    throw new HostError(`GitHub が ${res.status} を返しました：GET ${path}`, res.status);
  }
  return { status: res.status, body: await res.json(), next: nextPage(client, header(res, "link")) };
}

/** Link ヘッダの rel="next" を、同じ API の根からのパスにする。無い・根の外なら空 */
function nextPage(client: Client, link: string): string {
  const m = /<([^>]+)>\s*;\s*rel="next"/.exec(link);
  if (!m || !m[1].startsWith(`${client.host.api}/`)) return "";
  return m[1].slice(client.host.api.length);
}

/** 配列を返す REST をページごとに読み、`pages` まで足す。答えと、まだ続きがあるか */
async function restPages(client: Client, path: string, pages: number): Promise<{ items: unknown[]; more: boolean }> {
  const items: unknown[] = [];
  let next = path;
  for (let i = 0; i < pages && next; i += 1) {
    const { status, body, next: after } = await rest(client, next);
    if (status === 404 || !Array.isArray(body)) break;
    items.push(...body);
    next = after;
  }
  return { items, more: next !== "" && next !== path };
}

async function graphql(client: Client, query: string, variables: Record<string, unknown>): Promise<Record<string, unknown>> {
  for (let attempt = 0; ; attempt += 1) {
    client.counter.graphql += 1;
    const res = await send(
      client,
      client.host.graphql,
      { method: "POST", headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify({ query, variables }) },
      "GraphQL",
    );
    if (!res.ok) {
      throw new HostError(`GitHub の GraphQL が ${res.status} を返しました`, res.status);
    }
    const body = (await res.json()) as { data?: Record<string, unknown>; errors?: { type?: string; message?: string }[] };
    const errors = body.errors ?? [];
    if (errors.some((e) => e.type === "RATE_LIMITED")) {
      const after = header(res, "retry-after");
      const seconds = /^\d+$/.test(after) ? Number(after) : null;
      if (seconds !== null && seconds <= RETRY_LIMIT_SECONDS && attempt === 0) {
        await pause(client, seconds);
        continue;
      }
      throw new HostError("GitHub の GraphQL のレート制限にかかりました（RATE_LIMITED）。少し待ってからボードを更新してください", 429);
    }
    if (errors.length > 0) {
      throw new HostError(`GitHub の GraphQL が失敗しました：${errors.map((e) => e.message ?? "?").join(" / ")}`);
    }
    return body.data ?? {};
  }
}

function repoPath(owner: string, repo: string): string {
  return `/repos/${encodeURIComponent(checkName(owner, "owner"))}/${encodeURIComponent(checkName(repo, "repo"))}`;
}

/** リポジトリのデフォルトブランチ（統合先を設定していないときの既定） */
export async function repoInfo(client: Client, owner: string, repo: string): Promise<{ defaultBranch: string }> {
  const { status, body } = await rest(client, repoPath(owner, repo));
  if (status === 404) {
    throw new HostError(`リポジトリ ${owner}/${repo} が見つかりません（存在しないか、PAT の権限が不足しています）`, 404);
  }
  return { defaultBranch: checkBranch((body as { default_branch?: unknown }).default_branch) };
}

/** ブランチの先頭。無ければ null（404） */
export async function branchHead(client: Client, owner: string, repo: string, branch: string): Promise<string | null> {
  const ref = checkBranch(branch).split("/").map(encodeURIComponent).join("/");
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/git/ref/heads/${ref}`);
  if (status === 404) {
    return null;
  }
  const obj = (body as { ref?: unknown; object?: { sha?: unknown; type?: unknown } }) ?? {};
  if (obj.ref !== `refs/heads/${branch}`) {
    return null;
  }
  return checkOid(obj.object?.sha);
}

const REFS = `query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    refs(refPrefix: "refs/heads/", first: 100, after: $after, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { name target { oid ... on Commit { committedDate } } }
    }
  }
}`;

export interface RecentRef {
  readonly name: string;
  readonly head: string;
  readonly committedDate: string;
}

/** `since` より後に先頭が動いたブランチ（表示用） */
export async function recentRefs(client: Client, owner: string, repo: string, since: Date): Promise<RecentRef[]> {
  const out: RecentRef[] = [];
  let after: string | null = null;
  for (let page = 0; page < REF_PAGES; page += 1) {
    const data = await graphql(client, REFS, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), after });
    const refs = (data.repository as { refs?: { pageInfo: { hasNextPage: boolean; endCursor: string | null }; nodes: unknown[] } } | null)?.refs;
    if (!refs) {
      throw new HostError(`リポジトリ ${owner}/${repo} のブランチを取得できません`);
    }
    let older = false;
    for (const node of refs.nodes as { name?: unknown; target?: { oid?: unknown; committedDate?: unknown } }[]) {
      const date = typeof node.target?.committedDate === "string" ? node.target.committedDate : "";
      if (!date || new Date(date).getTime() < since.getTime()) {
        older = true;
        continue;
      }
      try {
        out.push({ name: checkBranch(node.name), head: checkOid(node.target?.oid), committedDate: date });
      } catch {
        // 読めない名前のブランチは候補にしない
      }
    }
    if (older || !refs.pageInfo.hasNextPage) {
      break;
    }
    after = refs.pageInfo.endCursor;
  }
  return out;
}

export interface PathObject {
  /** `link` はシンボリックリンク（GitLab は tree の mode 120000 で見分ける。GitHub のパスで引く GraphQL は見分けない） */
  readonly type: "tree" | "blob" | "link";
  readonly oid: string;
}

/** コミットの上のパスが指す tree / blob。無ければ null */
export async function pathObjects(
  client: Client,
  owner: string,
  repo: string,
  commit: string,
  paths: readonly string[],
): Promise<Record<string, PathObject | null>> {
  checkOid(commit);
  const vars: Record<string, unknown> = { owner: checkName(owner, "owner"), name: checkName(repo, "repo") };
  const decl: string[] = ["$owner: String!", "$name: String!"];
  const fields: string[] = [];
  paths.forEach((p, i) => {
    vars[`e${i}`] = `${commit}:${checkPath(p)}`;
    decl.push(`$e${i}: String!`);
    fields.push(`p${i}: object(expression: $e${i}) { __typename oid }`);
  });
  if (fields.length === 0) {
    return {};
  }
  const query = `query(${decl.join(", ")}) { repository(owner: $owner, name: $name) { ${fields.join(" ")} } }`;
  const data = await graphql(client, query, vars);
  const repoData = (data.repository ?? {}) as Record<string, { __typename?: string; oid?: string } | null>;
  const out: Record<string, PathObject | null> = {};
  paths.forEach((p, i) => {
    const obj = repoData[`p${i}`];
    if (obj && (obj.__typename === "Tree" || obj.__typename === "Blob")) {
      out[p] = { type: obj.__typename === "Tree" ? "tree" : "blob", oid: checkOid(obj.oid) };
    } else {
      out[p] = null;
    }
  });
  return out;
}

export interface TreeEntry {
  readonly path: string;
  readonly sha: string;
  /** git の mode（`100644`・`100755`・`120000` はシンボリックリンク） */
  readonly mode: string;
}

/** シンボリックリンクの mode。中身は指す先のパスなので、ファイルとして読まない（12） */
export const LINK_MODE = "120000";

/** tree を再帰で読む。`truncated` なら取り切れないので止める */
export async function tree(client: Client, owner: string, repo: string, oid: string): Promise<TreeEntry[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/git/trees/${checkOid(oid)}?recursive=1`);
  if (status === 404) {
    throw new HostError(`tree ${oid} が見つかりません`);
  }
  const b = body as { truncated?: boolean; tree?: { path?: unknown; type?: unknown; sha?: unknown; mode?: unknown }[] };
  if (b.truncated) {
    throw new HostError("置き場の tree が大きすぎて取得しきれません（truncated）。Chrome では読み取れません");
  }
  const out: TreeEntry[] = [];
  for (const e of b.tree ?? []) {
    if (e.type === "blob") {
      out.push({ path: checkPath(e.path), sha: checkOid(e.sha), mode: typeof e.mode === "string" ? e.mode : "" });
    }
  }
  return out;
}

export interface BlobText {
  readonly text: string | null;
  readonly binary: boolean;
}

/** blob を sha でまとめて取る（1 回に最大 50 件） */
export async function blobs(client: Client, owner: string, repo: string, oids: readonly string[]): Promise<Record<string, BlobText>> {
  if (oids.length > BLOB_BATCH) {
    throw new HostError(`blob は 1 回に ${BLOB_BATCH} 件まで`);
  }
  const vars: Record<string, unknown> = { owner: checkName(owner, "owner"), name: checkName(repo, "repo") };
  const decl: string[] = ["$owner: String!", "$name: String!"];
  const fields: string[] = [];
  oids.forEach((oid, i) => {
    vars[`o${i}`] = checkOid(oid);
    decl.push(`$o${i}: GitObjectID!`);
    fields.push(`b${i}: object(oid: $o${i}) { ... on Blob { text isBinary isTruncated byteSize } }`);
  });
  if (fields.length === 0) {
    return {};
  }
  const query = `query(${decl.join(", ")}) { repository(owner: $owner, name: $name) { ${fields.join(" ")} } }`;
  const data = await graphql(client, query, vars);
  const repoData = (data.repository ?? {}) as Record<string, { text?: string | null; isBinary?: boolean; isTruncated?: boolean; byteSize?: number } | null>;
  const out: Record<string, BlobText> = {};
  const encoder = new TextEncoder();
  oids.forEach((oid, i) => {
    const b = repoData[`b${i}`];
    if (!b) {
      throw new HostError(`blob ${oid} を取れませんでした`);
    }
    if (b.isTruncated) {
      throw new HostError(`blob ${oid} の本文が切られています（isTruncated）。取り切れていません`);
    }
    if (b.isBinary || typeof b.text !== "string") {
      out[oid] = { text: null, binary: true };
      return;
    }
    // 本文が途中で切られていたら「無い」とも「空」とも読ませない（取っていないファイルと同じ扱い）。
    if (typeof b.byteSize === "number" && encoder.encode(b.text).length !== b.byteSize) {
      throw new HostError(`blob ${oid} の本文が大きさと合いません（取り切れていません）`);
    }
    out[oid] = { text: b.text, binary: false };
  });
  return out;
}

// ---- 書き込みと、書いた後の確かめ・MR の Approve・PAT の期限 ----------------------

/** 期限のヘッダの表記（`2026-12-31 00:00:00 UTC`・`2026-12-31 09:00:00 +0900`）を ISO にする。読めなければ空 */
export function parseExpiration(text: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*(UTC|Z|[+-]\d{2}:?\d{2})?$/.exec(text.trim());
  if (!m) return "";
  const zone = !m[3] || m[3] === "UTC" || m[3] === "Z" ? "Z" : m[3].replace(/^([+-]\d{2}):?(\d{2})$/, "$1:$2");
  const t = Date.parse(`${m[1]}T${m[2].length === 5 ? `${m[2]}:00` : m[2]}${zone}`);
  return Number.isNaN(t) ? "" : new Date(t).toISOString();
}

const VIEWER = `query { viewer { login } }`;

/** PAT の持ち主のアカウント名（レビュー済みのマーカーの `actor`） */
export async function viewer(client: Client): Promise<string> {
  const data = await graphql(client, VIEWER, {});
  const login = (data.viewer as { login?: unknown } | undefined)?.login;
  if (typeof login !== "string" || !NAME.test(login)) {
    throw new HostError("PAT の所有者を取得できません");
  }
  return login;
}

const CREATE_COMMIT = `mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}`;

export interface FileAddition {
  readonly path: string;
  /** base64 */
  readonly contents: string;
}

/**
 * 親のブランチへ 1 コミットで書く。`expectedHeadOid` が先頭と違えば GitHub が断るので、
 * そのまま競合の検出になる。答えは新しいコミットの sha。
 */
export async function createCommit(
  client: Client,
  owner: string,
  repo: string,
  branch: string,
  expectedHeadOid: string,
  headline: string,
  body: string,
  additions: readonly FileAddition[],
  deletions: readonly string[],
): Promise<string> {
  const input = {
    branch: { repositoryNameWithOwner: `${checkName(owner, "owner")}/${checkName(repo, "repo")}`, branchName: checkBranch(branch) },
    expectedHeadOid: checkOid(expectedHeadOid),
    message: body ? { headline, body } : { headline },
    fileChanges: {
      additions: additions.map((a) => ({ path: checkPath(a.path), contents: a.contents })),
      deletions: deletions.map((p) => ({ path: checkPath(p) })),
    },
  };
  const data = await graphql(client, CREATE_COMMIT, { input });
  const oid = (data.createCommitOnBranch as { commit?: { oid?: unknown } } | undefined)?.commit?.oid;
  return checkOid(oid);
}

/** コミットの親（書いた後の確かめ。応答だけが届かなかったとき、新しい先頭が自分の書いたものかを見る） */
export async function commitParents(client: Client, owner: string, repo: string, sha: string): Promise<string[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/commits/${checkOid(sha)}`);
  if (status === 404) return [];
  const parents = (body as { parents?: { sha?: unknown }[] } | null)?.parents ?? [];
  return parents.map((p) => checkOid(p.sha));
}

/** 親のブランチの開いた MR に付いている Approve。付いていれば、書くと外れうることを出す */
export async function pullApprovals(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; approvals: number }[]> {
  const head = encodeURIComponent(`${checkName(owner, "owner")}:${checkBranch(branch)}`);
  const { items: pulls } = await restPages(client, `${repoPath(owner, repo)}/pulls?head=${head}&state=open&per_page=30`, 3);
  const out: { number: number; approvals: number }[] = [];
  for (const pr of pulls as { number?: unknown }[]) {
    if (typeof pr.number !== "number" || !Number.isInteger(pr.number)) continue;
    // 100 件を超えるレビューも Link で読み切る（古い側に Approve、新しい側に取り消しがありうる）
    const { items: reviews } = await restPages(client, `${repoPath(owner, repo)}/pulls/${pr.number}/reviews?per_page=100`, 10);
    const last = new Map<string, string>();
    for (const r of reviews as { user?: { login?: unknown }; state?: unknown }[]) {
      const who = typeof r.user?.login === "string" ? r.user.login : "";
      if (who && typeof r.state === "string" && r.state !== "COMMENTED") last.set(who, r.state);
    }
    const approvals = [...last.values()].filter((s) => s === "APPROVED").length;
    if (approvals > 0) out.push({ number: pr.number, approvals });
  }
  return out;
}

// ---- レビュー済み。MR のスレッドとレビューを取得した結果と、依頼の後の変更の一覧 --------------

/** スレッド 1 つ（`ccnavi-review.sh` の `threads` と同じ形） */
export interface ReviewThread {
  readonly id: string;
  readonly resolved: boolean;
  readonly url: string;
  readonly path: string;
  readonly line: number;
  readonly body: string;
  readonly created_at: string;
  /** 最後のコメントの id・時刻・作成者。再提起（受け入れたあとに付いたコメント）を見分ける。取れなければ空 */
  readonly last_id: string;
  readonly last_at: string;
  readonly last_author: string;
  /** 最初のコメントを書いたアカウント。GitLab から取得した結果だけが持ち、ccnavi の依頼のスレッドを見分けるのに使う（目印は誰でも書けるので書き手で見る） */
  readonly author?: string;
  /** スレッドのノートが全部 ccnavi の目印で始まるか。GitLab から取得した結果だけが持つ。ユーザが依頼コメントに返信したスレッドは false */
  readonly own?: boolean;
}

/** レビュー 1 つ（`ccnavi-review.sh` の `reviews` と同じ形） */
export interface PullReview {
  readonly state: string;
  readonly url: string;
  readonly submitted_at: string;
  readonly author: string;
}

/** ホストから取得した結果（`ccnavi-review.sh fetch` と同じ形。Python の `review_host.Result` が読む） */
export interface ReviewCopy {
  readonly host: "github" | "gitlab";
  readonly mr: { readonly number: number; readonly url: string };
  readonly threads: readonly ReviewThread[];
  readonly reviews: readonly PullReview[];
  readonly fetched_at: string;
}

/** sh の読みの上限と同じ（スレッドは 21 ページ目で、レビューは 20 ページを超えたら止める） */
const THREAD_PAGES = 20;
const REVIEW_PAGES = 20;
const PER_PAGE = 100;

/** jq の `a // b`。null と false のときだけ b（空文字や 0 は a のまま） */
function alt<T>(value: unknown, fallback: T): T {
  return value === null || value === undefined || value === false ? fallback : (value as T);
}

/** 親のブランチの開いた MR（sh の `find_mr` と同じ: `pulls?state=open&head=<owner>:<branch>` の先頭） */
export async function openPull(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; url: string } | null> {
  const head = encodeURIComponent(`${checkName(owner, "owner")}:${checkBranch(branch)}`);
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/pulls?state=open&head=${head}`);
  const first = status === 404 || !Array.isArray(body) ? undefined : (body[0] as { number?: unknown; html_url?: unknown } | undefined);
  if (!first) return null;
  if (typeof first.number !== "number" || !Number.isInteger(first.number)) {
    throw new HostError("マージリクエストの番号を取得できません");
  }
  return { number: first.number, url: String(alt(first.html_url, "")) };
}

const THREADS = `query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { url path line body createdAt } } last: comments(last: 1) { nodes { id createdAt author { login } } } }
      }
    }
  }
}`;

type ThreadNode = {
  id?: unknown;
  isResolved?: unknown;
  comments?: { nodes?: Record<string, unknown>[] } | null;
  last?: { nodes?: Record<string, unknown>[] } | null;
};

/** MR のスレッド（sh の `threads` と同じ読み方。最初のコメントの url・path・line・本文・時刻） */
export async function reviewThreads(client: Client, owner: string, repo: string, number: number): Promise<ReviewThread[]> {
  const out: ReviewThread[] = [];
  let after: string | null = null;
  for (let page = 0; ; page += 1) {
    const data = await graphql(client, THREADS, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), number, after });
    const threads = (data.repository as { pullRequest?: { reviewThreads?: { pageInfo: { hasNextPage: boolean; endCursor: string | null }; nodes: ThreadNode[] } } | null } | null)
      ?.pullRequest?.reviewThreads;
    if (!threads) throw new HostError(`マージリクエスト #${number} のスレッドを取得できません`);
    for (const t of threads.nodes) {
      const c = t.comments?.nodes?.[0] ?? {};
      const l = t.last?.nodes?.[0] ?? {};
      const lastAuthor = (l.author ?? {}) as { login?: unknown };
      out.push({
        id: String(t.id),
        resolved: t.isResolved === true,
        url: String(alt(c.url, "")),
        path: String(alt(c.path, "")),
        line: Number(alt(c.line, 0)),
        body: String(alt(c.body, "")),
        created_at: String(alt(c.createdAt, "")),
        last_id: String(alt(l.id, "")),
        last_at: String(alt(l.createdAt, "")),
        last_author: String(alt(lastAuthor.login, "")),
      });
    }
    if (!threads.pageInfo.hasNextPage) return out;
    if (page + 1 > THREAD_PAGES) throw new HostError("reviewThreads が多すぎて読み切れません");
    after = threads.pageInfo.endCursor;
  }
}

/** MR のレビュー（sh の `reviews` と同じ: 100 件ずつページの番号で読み、100 件に満たないページで終える） */
export async function pullReviews(client: Client, owner: string, repo: string, number: number): Promise<PullReview[]> {
  const out: PullReview[] = [];
  for (let page = 1; ; page += 1) {
    const { status, body } = await rest(client, `${repoPath(owner, repo)}/pulls/${number}/reviews?per_page=${PER_PAGE}&page=${page}`);
    // 404 や配列でない答えを「レビュー無し」と読むと、変更要求を見落として通してしまう
    if (status === 404) throw new HostError(`マージリクエスト #${number} のレビューを取得できません（404）`, 404);
    if (!Array.isArray(body)) throw new HostError(`マージリクエスト #${number} のレビューの応答が配列ではありません`);
    const chunk = body as Record<string, unknown>[];
    for (const r of chunk) {
      const user = (r.user ?? null) as { id?: unknown; login?: unknown } | null;
      out.push({
        state: String(alt(r.state, "")),
        url: String(alt(r.html_url, "")),
        submitted_at: String(alt(r.submitted_at, "")),
        author: String(alt(user?.id, alt(user?.login, ""))),
      });
    }
    if (chunk.length < PER_PAGE) return out;
    if (page + 1 > REVIEW_PAGES) throw new HostError("レビューが多すぎて読み切れません");
  }
}

/**
 * 親のブランチの MR のスレッドとレビューを取得した結果。`ccnavi-review.sh fetch` と同じ形で、
 * 同じ見本（test/fixtures/host/github/）から同じ結果になることを試験が見る。MR が無ければ投げる。
 */
export async function reviewCopy(client: Client, owner: string, repo: string, branch: string): Promise<ReviewCopy> {
  const mr = await openPull(client, owner, repo, branch);
  if (mr === null) throw new HostError(`親のブランチ ${branch} に対応する、オープン状態のマージリクエストがありません`);
  const threads = await reviewThreads(client, owner, repo, mr.number);
  const reviews = await pullReviews(client, owner, repo, mr.number);
  return { host: "github", mr, threads, reviews, fetched_at: new Date().toISOString() };
}

/** compare API が返す変更の一覧の上限（GitHub は 300 件で切る）。これに届けば打ち切られたとみなす */
export const COMPARE_FILES_LIMIT = 300;

/**
 * 依頼時の先頭 `base` から今の先頭 `head` までに変わったパス。改名は元と先の両方を入れる（手元の
 * `--no-renames` と同じ）。読めない・打ち切られた・`base` が祖先でない（`ahead`・`identical` でない）なら
 * `files` は null で、Python が「動いた」と数える。
 */
export async function compareFiles(client: Client, owner: string, repo: string, base: string, head: string): Promise<{ base: string; head: string; files: string[] | null }> {
  const b = checkOid(base);
  const h = checkOid(head);
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/compare/${b}...${h}`);
  const res = (body ?? {}) as { status?: unknown; files?: { filename?: unknown; previous_filename?: unknown }[] };
  if (status === 404 || (res.status !== "ahead" && res.status !== "identical")) return { base: b, head: h, files: null };
  // 一覧が無い・配列でない答えは「変わっていない」と読まない（動いたと数える）
  if (!Array.isArray(res.files)) return { base: b, head: h, files: null };
  const list = res.files;
  if (list.length >= COMPARE_FILES_LIMIT) return { base: b, head: h, files: null };
  const files: string[] = [];
  for (const f of list) {
    if (typeof f.filename === "string") files.push(f.filename);
    if (typeof f.previous_filename === "string") files.push(f.previous_filename);
  }
  return { base: b, head: h, files };
}

/** パスの履歴を読む件数の上限。届けば履歴が途切れていて、最初のコミットを言えない */
export const PATH_HISTORY_LIMIT = 100;

/**
 * そのパスを最初に加えたコミットの時刻（epoch 秒）。手で `done/` に動かした子の移動の時刻に使う。
 * ブランチ `ref` でそのパスに触れたコミットのうち最も古いものを取る。履歴が上限に届いた・読めない・
 * 日付を読めないときは null（呼び手は確かめられないものとして確定しない）。
 */
export async function pathCommitTime(client: Client, owner: string, repo: string, ref: string, path: string): Promise<{ time: number | null }> {
  const r = checkBranch(ref);
  const p = checkPath(path);
  const { status, body } = await rest(
    client,
    `${repoPath(owner, repo)}/commits?sha=${encodeURIComponent(r)}&path=${encodeURIComponent(p)}&per_page=${PATH_HISTORY_LIMIT}`,
  );
  if (status === 404 || !Array.isArray(body) || body.length === 0 || body.length >= PATH_HISTORY_LIMIT) return { time: null };
  const oldest = body[body.length - 1] as { commit?: { committer?: { date?: unknown } } };
  const date = oldest?.commit?.committer?.date;
  const ms = typeof date === "string" ? Date.parse(date) : Number.NaN;
  return { time: Number.isFinite(ms) ? Math.floor(ms / 1000) : null };
}

// ---- 「始める」。issue の一覧と、親のブランチを作る -------------------------------

/** 全部のブランチの名前を読むページの上限（100 × 50）。超えたら読み切れないので止める（「始める」の重なりの検査） */
export const BRANCH_PAGES = 50;

/** 全部のブランチの名前（「始める」が大文字小文字をそろえて重なりを見る。直近 N 日の上限を掛けない） */
export async function branchNames(client: Client, owner: string, repo: string): Promise<string[]> {
  const { items, more } = await restPages(client, `${repoPath(owner, repo)}/branches?per_page=100`, BRANCH_PAGES);
  if (more) throw new HostError("ブランチが多すぎて読み切れません。「始める」は手元で行ってください");
  return (items as { name?: unknown }[]).map((b) => b.name).filter((n): n is string => typeof n === "string");
}

export interface Issue {
  readonly number: number;
  readonly title: string;
  readonly url: string;
}

/** 開いた issue（新しい順に 50 件。PR は除く）。「始める」の一覧 */
export async function issues(client: Client, owner: string, repo: string): Promise<Issue[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/issues?state=open&sort=created&direction=desc&per_page=50`);
  if (status === 404 || !Array.isArray(body)) throw new HostError(`リポジトリ ${owner}/${repo} の Issue を取得できません`, status);
  const out: Issue[] = [];
  for (const i of body as { number?: unknown; title?: unknown; html_url?: unknown; pull_request?: unknown }[]) {
    if (i.pull_request !== undefined && i.pull_request !== null) continue;
    if (typeof i.number !== "number" || !Number.isInteger(i.number) || i.number <= 0) continue;
    out.push({ number: i.number, title: String(alt(i.title, "")), url: String(alt(i.html_url, "")) });
  }
  return out;
}

/** ブランチを `sha` から作る（`POST /git/refs`。Contents の書き込みの権限）。既にあれば断られる（422） */
export async function createBranch(client: Client, owner: string, repo: string, name: string, sha: string): Promise<string> {
  const path = `${repoPath(owner, repo)}/git/refs`;
  client.counter.rest += 1;
  const res = await send(
    client,
    `${client.host.api}${path}`,
    { method: "POST", headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify({ ref: `refs/heads/${checkBranch(name)}`, sha: checkOid(sha) }) },
    `POST ${path}`,
  );
  if (!res.ok) throw new HostError(`GitHub がブランチを作成しませんでした（${res.status}。同じ名前のブランチが既にあるか、権限が不足しています）`, res.status);
  const made = (await res.json()) as { ref?: unknown; object?: { sha?: unknown } };
  if (made.ref !== `refs/heads/${name}`) throw new HostError("作ったブランチの名前が、頼んだ名前と違います");
  return checkOid(made.object?.sha);
}

// ---- 「始める」。既存の MR の採用と、新しい MR の作成 ----------------------------------

/** 開いた MR を読むページの上限（100 × 20）。超えたら読み切れないので止める（既存の MR を見落として重複を作らない） */
export const MR_PAGES = 20;

/** 採用を考える MR 1 本。拡張が見るのはこの欄だけ（`ccnavi-branches.sh` の `mrs` と同じ形） */
export interface MrInfo {
  readonly number: number;
  readonly branch: string;
  readonly state: string;
  readonly url: string;
  readonly title: string;
  readonly body: string;
  readonly base: string;
  readonly fork: boolean;
}

/** 開いた MR（PR）の全部。issue を閉じる MR の探索と、採用の確認に使う。読むだけ */
export async function listOpenMrs(client: Client, owner: string, repo: string): Promise<MrInfo[]> {
  const { items, more } = await restPages(client, `${repoPath(owner, repo)}/pulls?state=open&per_page=100`, MR_PAGES);
  if (more) throw new HostError("開いたマージリクエストが多すぎて読み切れません。既存の MR を見落とさないよう、「始める」は手元で行ってください");
  const own = `${owner}/${repo}`.toLowerCase();
  const out: MrInfo[] = [];
  for (const p of items as { number?: unknown; title?: unknown; body?: unknown; html_url?: unknown; head?: { ref?: unknown; repo?: { full_name?: unknown } | null }; base?: { ref?: unknown } }[]) {
    if (typeof p.number !== "number" || typeof p.head?.ref !== "string") continue;
    out.push({
      number: p.number,
      branch: p.head.ref,
      state: "open",
      url: String(alt(p.html_url, "")),
      title: String(alt(p.title, "")),
      body: String(alt(p.body, "")),
      base: String(alt(p.base?.ref, "")),
      // フォークから出た MR は、元ブランチがこのリポジトリに無い
      fork: String(alt(p.head.repo?.full_name, "")).toLowerCase() !== own,
    });
  }
  return out;
}

/** 同じブランチ名のマージ済み MR（`head=<owner>:<branch>` の閉じた PR のうち `merged_at` があるもの）。読むだけ */
export async function listMergedMrs(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; url: string }[]> {
  const head = encodeURIComponent(`${checkName(owner, "owner")}:${checkBranch(branch)}`);
  const { items, more } = await restPages(client, `${repoPath(owner, repo)}/pulls?state=closed&head=${head}&per_page=100`, MR_PAGES);
  if (more) throw new HostError("閉じたマージリクエストが多すぎて読み切れません");
  const out: { number: number; url: string }[] = [];
  for (const p of items as { number?: unknown; html_url?: unknown; merged_at?: unknown }[]) {
    if (typeof p.number === "number" && p.merged_at !== null && p.merged_at !== undefined) out.push({ number: p.number, url: String(alt(p.html_url, "")) });
  }
  return out;
}

/**
 * 作ったブランチに空の初期コミットを積む（`ccnavi-start.sh` の初期コミットと同じ）。GitHub は差分のない
 * ブランチからは PR を作れないので、MR を作る前に要る。親のコミットの tree をそのまま使う新しいコミットを作り
 * （`POST /git/commits`）、ブランチをそこへ進める（`PATCH /git/refs/heads/<name>`）。新しい先頭の sha を返す。
 */
export async function startCommit(client: Client, owner: string, repo: string, name: string, message: string): Promise<string> {
  const refName = checkBranch(name).split("/").map(encodeURIComponent).join("/");
  const refPath = `${repoPath(owner, repo)}/git/refs/heads/${refName}`;
  const ref = await rest(client, `${repoPath(owner, repo)}/git/ref/heads/${refName}`);
  const parent = checkOid((ref.body as { object?: { sha?: unknown } } | null)?.object?.sha);
  const commit = await rest(client, `${repoPath(owner, repo)}/git/commits/${parent}`);
  const tree = checkOid((commit.body as { tree?: { sha?: unknown } } | null)?.tree?.sha);
  const post = async (path: string, method: string, body: unknown): Promise<unknown> => {
    client.counter.rest += 1;
    const res = await send(client, `${client.host.api}${path}`, { method, headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify(body) }, `${method} ${path}`);
    if (!res.ok) throw new HostError(`GitHub が ${res.status} を返しました：${method} ${path}`, res.status);
    return res.json();
  };
  const made = (await post(`${repoPath(owner, repo)}/git/commits`, "POST", { message, tree, parents: [parent] })) as { sha?: unknown };
  const sha = checkOid(made.sha);
  await post(refPath, "PATCH", { sha, force: false });
  return sha;
}

/** Draft の MR（PR）を作る。`head` は同じリポジトリのブランチ。番号と URL を返す */
export async function createDraftMr(client: Client, owner: string, repo: string, head: string, base: string, title: string, body: string): Promise<{ number: number; url: string }> {
  const path = `${repoPath(owner, repo)}/pulls`;
  client.counter.rest += 1;
  const res = await send(
    client,
    `${client.host.api}${path}`,
    { method: "POST", headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify({ head: checkBranch(head), base: checkBranch(base), title, body, draft: true }) },
    `POST ${path}`,
  );
  if (!res.ok) throw new HostError(`GitHub が Draft のマージリクエストを作りませんでした（${res.status}）`, res.status);
  const made = (await res.json()) as { number?: unknown; html_url?: unknown };
  if (typeof made.number !== "number") throw new HostError("作ったマージリクエストの番号を取得できません");
  return { number: made.number, url: String(alt(made.html_url, "")) };
}
