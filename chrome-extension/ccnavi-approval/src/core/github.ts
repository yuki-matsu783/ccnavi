/**
 * GitHub の読み書き（ADR-0093 の 8.2・8.4・8.8）。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * 操作は名前で限る（5.5 の 4）。画面から来るのは操作の名前と引数だけで、URL・クエリ・ヘッダは
 * ここで組む。GraphQL の問い合わせは固定の文で、引数は変数で渡す（文に継ぎ足さない）。
 * 書くのは `createCommitOnBranch`（`expectedHeadOid` つき）の 1 つだけ（段階 3）。
 */
import type { Host } from "./hosts.js";

export type Fetch = (url: string, init: { method: string; headers: Record<string, string>; body?: string; redirect?: "error" }) => Promise<{
  status: number;
  ok: boolean;
  json(): Promise<unknown>;
  /** 応答ヘッダ。PAT の期限（`github-authentication-token-expiration`）を読む（5.5・D25） */
  headers?: { get(name: string): string | null };
}>;

/** PAT の期限を返す応答ヘッダ（GitHub の fine-grained / classic のトークン） */
export const EXPIRATION_HEADER = "github-authentication-token-expiration";

/** 呼んだ回数。読み取り量の見積もり（8.2）と突き合わせるために数える */
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

/** 1 回の GraphQL で取る blob の上限（8.2） */
export const BLOB_BATCH = 50;
/** 直近のブランチを読むページの上限。100 × 10 本を超えるリポジトリは古い方を諦める */
const REF_PAGES = 10;

const NAME = /^[A-Za-z0-9_.-]{1,100}$/;
const OID = /^[0-9a-f]{40}$/;
/** ブランチ名。git の ref の規則のうち、ここで困るものだけを見る */
const BRANCH = /^(?!\/)(?!.*\/\/)(?!.*\.\.)(?!.*[\s~^:?*[\\])(?!.*@\{)[^\x00-\x1f\x7f]{1,250}(?<![/.])$/;

export function checkName(value: unknown, what: string): string {
  if (typeof value !== "string" || !NAME.test(value) || value === "." || value === "..") {
    throw new HostError(`${what} が読めない: ${String(value)}`);
  }
  return value;
}

export function checkBranch(value: unknown): string {
  if (typeof value !== "string" || !BRANCH.test(value) || value.endsWith(".lock")) {
    throw new HostError(`ブランチ名が読めない: ${String(value)}`);
  }
  return value;
}

export function checkOid(value: unknown): string {
  if (typeof value !== "string" || !OID.test(value)) {
    throw new HostError(`sha が読めない: ${String(value)}`);
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
    throw new HostError(`パスが読めない: ${String(value)}`);
  }
  return value;
}

export interface Client {
  readonly host: Host;
  readonly token: string;
  readonly fetch: Fetch;
  readonly counter: Counter;
  /** 応答から読んだもの（PAT の期限。読めなければ空）。呼び手が控える */
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
  const when = remaining === "0" && Number.isFinite(reset) && reset > 0 ? `。回復は ${new Date(reset * 1000).toISOString()}` : "";
  const kind = after !== "" ? "二次のレート制限" : "レート制限";
  return {
    text: `GitHub の${kind}に当たった（${res.status}${seconds !== null ? `、${seconds} 秒待つよう言われた` : ""}${when}）。少し待ってからボードを更新する`,
    wait: seconds !== null && seconds <= RETRY_LIMIT_SECONDS ? seconds : null,
  };
}

/**
 * 転送を追わずに呼ぶ（`redirect: "error"`）。fetch そのものが落ちたら（転送された・通信が落ちた。ブラウザは両者を
 * 見分けさせない）、原因の分かる文面に包む。status は 0 のまま（書く流れは「届いたか分からない」として確かめる）
 */
export async function fetchNoRedirect(client: Pick<Client, "fetch">, url: string, init: Parameters<Fetch>[1], what: string): ReturnType<Fetch> {
  try {
    return await client.fetch(url, { ...init, redirect: "error" });
  } catch (err) {
    throw new HostError(
      `${what} が届かなかった（通信が落ちたか、別の場所へ転送された。転送は追わない）。通信先と、設定画面に登録した owner/repo（改名・移動していないか）を見直す: ${(err as Error).message ?? String(err)}`,
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
    // 別のホストへの転送を追わない（PAT を載せた要求を焼き込んだ通信先の外へ出さない）
    const res = await fetchNoRedirect(client, url, init, what);
    note(client, res);
    if (res.status === 401) {
      throw new HostError("PAT が通らない（401）。設定画面で差し替える", 401);
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
      throw new HostError(`GitHub が断った（403）: ${what}。PAT の権限（リポジトリ・Contents など）を見直す`, 403);
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
    throw new HostError(`GitHub が ${res.status} を返した: GET ${path}`, res.status);
  }
  return { status: res.status, body: await res.json(), next: nextPage(client, header(res, "link")) };
}

/** Link ヘッダの rel="next" を、同じ API の根からのパスにする。無い・根の外なら空 */
function nextPage(client: Client, link: string): string {
  const m = /<([^>]+)>\s*;\s*rel="next"/.exec(link);
  if (!m || !m[1].startsWith(`${client.host.api}/`)) return "";
  return m[1].slice(client.host.api.length);
}

/** 並びを返す REST をページごとに読み、`pages` まで足す。答えと、まだ続きがあるか */
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
      throw new HostError(`GitHub の GraphQL が ${res.status} を返した`, res.status);
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
      throw new HostError("GitHub の GraphQL のレート制限に当たった（RATE_LIMITED）。少し待ってからボードを更新する", 429);
    }
    if (errors.length > 0) {
      throw new HostError(`GitHub の GraphQL が失敗した: ${errors.map((e) => e.message ?? "?").join(" / ")}`);
    }
    return body.data ?? {};
  }
}

function repoPath(owner: string, repo: string): string {
  return `/repos/${encodeURIComponent(checkName(owner, "owner"))}/${encodeURIComponent(checkName(repo, "repo"))}`;
}

/** リポジトリのデフォルトブランチ（3.3 の統合先の既定） */
export async function repoInfo(client: Client, owner: string, repo: string): Promise<{ defaultBranch: string }> {
  const { status, body } = await rest(client, repoPath(owner, repo));
  if (status === 404) {
    throw new HostError(`リポジトリ ${owner}/${repo} が見えない（無いか、PAT の権限の外）`, 404);
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

/** `since` より後に先頭が動いたブランチ（表示用。D2） */
export async function recentRefs(client: Client, owner: string, repo: string, since: Date): Promise<RecentRef[]> {
  const out: RecentRef[] = [];
  let after: string | null = null;
  for (let page = 0; page < REF_PAGES; page += 1) {
    const data = await graphql(client, REFS, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), after });
    const refs = (data.repository as { refs?: { pageInfo: { hasNextPage: boolean; endCursor: string | null }; nodes: unknown[] } } | null)?.refs;
    if (!refs) {
      throw new HostError(`リポジトリ ${owner}/${repo} のブランチを読めない`);
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

/** シンボリックリンクの mode。中身は指す先の綴りなので、ファイルとして読まない（12） */
export const LINK_MODE = "120000";

/** tree を再帰で読む。`truncated` なら取り切れないので止める（8.2） */
export async function tree(client: Client, owner: string, repo: string, oid: string): Promise<TreeEntry[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/git/trees/${checkOid(oid)}?recursive=1`);
  if (status === 404) {
    throw new HostError(`tree ${oid} が無い`);
  }
  const b = body as { truncated?: boolean; tree?: { path?: unknown; type?: unknown; sha?: unknown; mode?: unknown }[] };
  if (b.truncated) {
    throw new HostError("置き場の tree が大きすぎて取り切れない（truncated）。Chrome では読めない");
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

/** blob を sha でまとめて取る（8.2。1 回に最大 50 件） */
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
      throw new HostError(`blob ${oid} を取れなかった`);
    }
    if (b.isTruncated) {
      throw new HostError(`blob ${oid} の本文が切られている（isTruncated）。取り切れていない`);
    }
    if (b.isBinary || typeof b.text !== "string") {
      out[oid] = { text: null, binary: true };
      return;
    }
    // 本文が途中で切られていたら「無い」とも「空」とも読ませない（6.2 の NOT_FETCHED と同じ考え）。
    if (typeof b.byteSize === "number" && encoder.encode(b.text).length !== b.byteSize) {
      throw new HostError(`blob ${oid} の本文が大きさと合わない（取り切れていない）`);
    }
    out[oid] = { text: b.text, binary: false };
  });
  return out;
}

// ---- 段階 3: 書き込みと、取り下げのための履歴・MR の Approve・PAT の期限 ----------------------

/** 期限のヘッダの綴り（`2026-12-31 00:00:00 UTC`・`2026-12-31 09:00:00 +0900`）を ISO にする。読めなければ空 */
export function parseExpiration(text: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*(UTC|Z|[+-]\d{2}:?\d{2})?$/.exec(text.trim());
  if (!m) return "";
  const zone = !m[3] || m[3] === "UTC" || m[3] === "Z" ? "Z" : m[3].replace(/^([+-]\d{2}):?(\d{2})$/, "$1:$2");
  const t = Date.parse(`${m[1]}T${m[2].length === 5 ? `${m[2]}:00` : m[2]}${zone}`);
  return Number.isNaN(t) ? "" : new Date(t).toISOString();
}

const VIEWER = `query { viewer { login } }`;

/** PAT の持ち主のアカウント名（跡の `actor`。8.8・8.9） */
export async function viewer(client: Client): Promise<string> {
  const data = await graphql(client, VIEWER, {});
  const login = (data.viewer as { login?: unknown } | undefined)?.login;
  if (typeof login !== "string" || !NAME.test(login)) {
    throw new HostError("PAT の持ち主を読めない");
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
 * 親のブランチへ 1 コミットで書く（8.4）。`expectedHeadOid` が先頭と違えば GitHub が断るので、
 * そのまま競合の検出になる（Git Data API + PATCH refs は使わない）。答えは新しいコミットの sha。
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

export interface ApprovalCommit {
  readonly commit: string;
  readonly parent: string;
}

/** 1 回の取り下げで履歴を遡る上限（1 ページのコミットの数とページの数） */
const HISTORY_PAGE = 30;
const HISTORY_PAGES = 3;
/** first-parent の鎖を確かめるのに読む祖先の数 */
const CHAIN_DEPTH = 100;

const HISTORY = `query($owner: String!, $name: String!, $oid: GitObjectID!) {
  repository(owner: $owner, name: $name) {
    object(oid: $oid) { ... on Commit { history(first: ${CHAIN_DEPTH}) { nodes { oid parents(first: 1) { nodes { oid } } } } } }
  }
}`;

/** `sha` から最初の親を辿った鎖（`CHAIN_DEPTH` の祖先の中で辿れたところまで） */
export async function firstParentChain(client: Client, owner: string, repo: string, sha: string): Promise<Set<string>> {
  const data = await graphql(client, HISTORY, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), oid: checkOid(sha) });
  const nodes =
    ((data.repository as { object?: { history?: { nodes?: { oid?: unknown; parents?: { nodes?: { oid?: unknown }[] } }[] } } } | null)?.object?.history
      ?.nodes ?? []);
  const first = new Map<string, string>();
  for (const n of nodes) {
    if (typeof n.oid !== "string") continue;
    const p = n.parents?.nodes?.[0]?.oid;
    first.set(n.oid, typeof p === "string" ? p : "");
  }
  const chain = new Set<string>();
  let at = sha;
  while (at && first.has(at) && !chain.has(at)) {
    chain.add(at);
    at = first.get(at) as string;
  }
  return chain;
}

/**
 * 承認コミット（8.8 の 2）: `sha` から遡って `path`（`doing/<識別子>.md`）を最後に変えたコミットが、
 * 最初の親に `path` が無く自分には在る（足した）コミットで、親が 1 つで、`sha` の first-parent の鎖の上に
 * あるときだけ、そのコミットと親を返す（決定 B）。どれかを確かめられなければ null（取り下げを出さない）。
 *
 * 「足した」はコミットの変更の一覧（`files` の `status`）に頼らない。GitHub は承認コミット（提案の削除と
 * 写しの追加）を `renamed` と返すことがあり、一覧は 300 件で切れるため。両方の木で `path` を引いて比べる。
 * 一覧（`GET /commits?path=`）は `git log -- path` の簡略化で、merge や別の枝のコミットも出うる。
 */
export async function approvalCommit(client: Client, owner: string, repo: string, sha: string, path: string): Promise<ApprovalCommit | null> {
  const q = `?sha=${checkOid(sha)}&path=${encodeURIComponent(checkPath(path))}&per_page=${HISTORY_PAGE}`;
  const { items } = await restPages(client, `${repoPath(owner, repo)}/commits${q}`, HISTORY_PAGES);
  const newest = items[0] as { sha?: unknown; parents?: { sha?: unknown }[] } | undefined;
  if (!newest) return null;
  const commit = checkOid(newest.sha);
  const parents = Array.isArray(newest.parents) ? newest.parents : [];
  if (parents.length !== 1) return null;
  const parent = checkOid(parents[0].sha);
  const here = await pathObjects(client, owner, repo, commit, [path]);
  const before = await pathObjects(client, owner, repo, parent, [path]);
  if (here[path]?.type !== "blob" || before[path] !== null) return null;
  const chain = await firstParentChain(client, owner, repo, sha);
  return chain.has(commit) ? { commit, parent } : null;
}

/** コミットの親（書いた後の確かめ。応答だけが落ちたとき、新しい先頭が自分の書いたものかを見る） */
export async function commitParents(client: Client, owner: string, repo: string, sha: string): Promise<string[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/commits/${checkOid(sha)}`);
  if (status === 404) return [];
  const parents = (body as { parents?: { sha?: unknown }[] } | null)?.parents ?? [];
  return parents.map((p) => checkOid(p.sha));
}

/** 親のブランチの開いた MR に付いている Approve（8.10）。付いていれば、書くと外れうることを出す */
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

// ---- 段階 4: レビュー済み（8.9）。MR のスレッドとレビューの写しと、依頼の後の変更の一覧 --------------

/** スレッド 1 つ（`ccnavi-review.sh` の `threads` と同じ形） */
export interface ReviewThread {
  readonly id: string;
  readonly resolved: boolean;
  readonly url: string;
  readonly path: string;
  readonly line: number;
  readonly body: string;
  readonly created_at: string;
  /** 最初のコメントを書いたアカウント（GitLab の写しだけ。ccnavi の依頼のスレッドを見分ける。11.8.1 の決定 C） */
  readonly author?: string;
}

/** レビュー 1 つ（`ccnavi-review.sh` の `reviews` と同じ形） */
export interface PullReview {
  readonly state: string;
  readonly url: string;
  readonly submitted_at: string;
  readonly author: string;
}

/** ホストの写し（`ccnavi-review.sh fetch` と同じ形。Python の `review.Result` が読む） */
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
    throw new HostError("MR の番号を読めない");
  }
  return { number: first.number, url: String(alt(first.html_url, "")) };
}

const THREADS = `query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved comments(first: 1) { nodes { url path line body createdAt } } }
      }
    }
  }
}`;

type ThreadNode = { id?: unknown; isResolved?: unknown; comments?: { nodes?: Record<string, unknown>[] } | null };

/** MR のスレッド（sh の `threads` と同じ読み方。最初のコメントの url・path・line・本文・時刻） */
export async function reviewThreads(client: Client, owner: string, repo: string, number: number): Promise<ReviewThread[]> {
  const out: ReviewThread[] = [];
  let after: string | null = null;
  for (let page = 0; ; page += 1) {
    const data = await graphql(client, THREADS, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), number, after });
    const threads = (data.repository as { pullRequest?: { reviewThreads?: { pageInfo: { hasNextPage: boolean; endCursor: string | null }; nodes: ThreadNode[] } } | null } | null)
      ?.pullRequest?.reviewThreads;
    if (!threads) throw new HostError(`MR #${number} のスレッドを読めない`);
    for (const t of threads.nodes) {
      const c = t.comments?.nodes?.[0] ?? {};
      out.push({
        id: String(t.id),
        resolved: t.isResolved === true,
        url: String(alt(c.url, "")),
        path: String(alt(c.path, "")),
        line: Number(alt(c.line, 0)),
        body: String(alt(c.body, "")),
        created_at: String(alt(c.createdAt, "")),
      });
    }
    if (!threads.pageInfo.hasNextPage) return out;
    if (page + 1 > THREAD_PAGES) throw new HostError("reviewThreads が多すぎて読み切れない");
    after = threads.pageInfo.endCursor;
  }
}

/** MR のレビュー（sh の `reviews` と同じ: 100 件ずつページの番号で読み、100 件に満たないページで終える） */
export async function pullReviews(client: Client, owner: string, repo: string, number: number): Promise<PullReview[]> {
  const out: PullReview[] = [];
  for (let page = 1; ; page += 1) {
    const { status, body } = await rest(client, `${repoPath(owner, repo)}/pulls/${number}/reviews?per_page=${PER_PAGE}&page=${page}`);
    // 404 や並びでない答えを「レビュー無し」と読むと、変更要求を見落として通してしまう
    if (status === 404) throw new HostError(`MR #${number} のレビューを読めない（404）`, 404);
    if (!Array.isArray(body)) throw new HostError(`MR #${number} のレビューの答えが並びでない`);
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
    if (page + 1 > REVIEW_PAGES) throw new HostError("レビューが多すぎて読み切れない");
  }
}

/**
 * 親のブランチの MR のスレッドとレビューの写し（8.9）。`ccnavi-review.sh fetch` と同じ形で、
 * 同じ見本（test/fixtures/host/github/）から同じ写しになることを試験が見る。MR が無ければ投げる。
 */
export async function reviewCopy(client: Client, owner: string, repo: string, branch: string): Promise<ReviewCopy> {
  const mr = await openPull(client, owner, repo, branch);
  if (mr === null) throw new HostError(`親のブランチ ${branch} に対応する開いた MR が無い`);
  const threads = await reviewThreads(client, owner, repo, mr.number);
  const reviews = await pullReviews(client, owner, repo, mr.number);
  return { host: "github", mr, threads, reviews, fetched_at: new Date().toISOString() };
}

/** compare API が返す変更の一覧の上限（GitHub は 300 件で切る）。これに届けば打ち切られたとみなす */
export const COMPARE_FILES_LIMIT = 300;

/**
 * 依頼時の先頭 `base` から今の先頭 `head` までに変わったパス（8.9）。改名は元と先の両方を入れる（手元の
 * `--no-renames` と同じ）。読めない・打ち切られた・`base` が祖先でない（`ahead`・`identical` でない）なら
 * `files` は null で、Python が「動いた」と数える。
 */
export async function compareFiles(client: Client, owner: string, repo: string, base: string, head: string): Promise<{ base: string; head: string; files: string[] | null }> {
  const b = checkOid(base);
  const h = checkOid(head);
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/compare/${b}...${h}`);
  const res = (body ?? {}) as { status?: unknown; files?: { filename?: unknown; previous_filename?: unknown }[] };
  if (status === 404 || (res.status !== "ahead" && res.status !== "identical")) return { base: b, head: h, files: null };
  // 一覧が無い・並びでない答えは「変わっていない」と読まない（動いたと数える）
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

// ---- 段階 5: 「始める」（8.6）。issue の一覧と、親のブランチを作る -------------------------------

/** 全部のブランチの名前を読むページの上限（100 × 50）。超えたら読み切れないので止める（「始める」の重なりの検査） */
export const BRANCH_PAGES = 50;

/** 全部のブランチの名前（「始める」が大文字小文字を畳んで重なりを見る。直近 N 日の上限を掛けない） */
export async function branchNames(client: Client, owner: string, repo: string): Promise<string[]> {
  const { items, more } = await restPages(client, `${repoPath(owner, repo)}/branches?per_page=100`, BRANCH_PAGES);
  if (more) throw new HostError("ブランチが多すぎて読み切れない。「始める」は手元で行う");
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
  if (status === 404 || !Array.isArray(body)) throw new HostError(`リポジトリ ${owner}/${repo} の issue を読めない`, status);
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
  if (!res.ok) throw new HostError(`GitHub がブランチを作らなかった（${res.status}。同じ名前のブランチが既にあるか、権限が無い）`, res.status);
  const made = (await res.json()) as { ref?: unknown; object?: { sha?: unknown } };
  if (made.ref !== `refs/heads/${name}`) throw new HostError("作ったブランチの名前が違う");
  return checkOid(made.object?.sha);
}
