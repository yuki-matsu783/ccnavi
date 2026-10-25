/**
 * GitHub の読み書き。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * 操作は名前で限る。画面から来るのは操作の名前と引数だけで、URL・クエリ・ヘッダは
 * ここで組む。GraphQL の問い合わせは固定の文で、引数は変数で渡す（文に継ぎ足さない）。
 * 書くのは `createCommitOnBranch`（`expectedHeadOid` つき）、ブランチの作成・初期コミット・Draft の MR、
 * レビューのスレッドへの返信と解決、issue の作成、MR へのコメントに限る。
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
  readonly seen?: { expiration: string; scopes?: string };
  /** 待つ（レート制限の Retry-After）。試験は差し替える */
  readonly sleep?: (ms: number) => Promise<void>;
}

function note(client: Client, res: Awaited<ReturnType<Fetch>>): void {
  const exp = res.headers?.get(EXPIRATION_HEADER);
  if (client.seen && typeof exp === "string" && exp.trim() !== "") client.seen.expiration = exp.trim();
  // classic の PAT だけが付ける。fine-grained には無い（権限を書く前に読み取れない）
  const scopes = res.headers?.get("x-oauth-scopes");
  if (client.seen && typeof scopes === "string") client.seen.scopes = scopes.trim();
}

function headers(client: Client): Record<string, string> {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${client.token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

/** 書き込みに要る PAT の権限（fine-grained の名前）。無ければ読み取り */
export type Need = "Contents" | "Issues" | "Pull requests";

const NEED_TEXT: Record<Need, string> = {
  Contents: "Contents: Read and write",
  Issues: "Issues: Read and write",
  "Pull requests": "Pull requests: Read and write",
};

/** 権限が足りないときの文面。書く操作ごとに、要る権限の名前を言う */
export function lacking(need?: Need): string {
  if (!need) return "PAT の権限（リポジトリ・Contents など）を見直してください";
  return `PAT に ${NEED_TEXT[need]} の権限が要ります（fine-grained token は対象のリポジトリも選びます。classic は repo スコープ）`;
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
async function send(client: Client, url: string, init: Parameters<Fetch>[1], what: string, need?: Need): Promise<Res> {
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
      throw new HostError(`GitHub が拒否しました（403）：${what}。${lacking(need)}`, 403);
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

/** GraphQL の errors が権限の不足を言っているか */
function forbidden(errors: { type?: string; message?: string }[]): boolean {
  return errors.some((e) => e.type === "FORBIDDEN" || e.type === "INSUFFICIENT_SCOPES" || /resource not accessible|must have .*permission|not permitted/i.test(e.message ?? ""));
}

async function graphql(client: Client, query: string, variables: Record<string, unknown>, need?: Need): Promise<Record<string, unknown>> {
  for (let attempt = 0; ; attempt += 1) {
    client.counter.graphql += 1;
    const res = await send(
      client,
      client.host.graphql,
      { method: "POST", headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify({ query, variables }) },
      "GraphQL",
      need,
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
      const text = errors.map((e) => e.message ?? "?").join(" / ");
      if (need && forbidden(errors)) throw new HostError(`GitHub が拒否しました：${text}。${lacking(need)}`, 403);
      throw new HostError(`GitHub の GraphQL が失敗しました：${text}`);
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

// ---- decide のホスト書き込み。スレッドへの返信と解決、issue の作成と検索、MR へのコメント ------

/** issue のタイトル・返信とコメントと issue の本文の字数の上限（GitHub の本文の上限は 65,536 字） */
export const MAX_TITLE = 255;
export const MAX_WRITE_TEXT = 65000;

const THREAD_ID = /^[A-Za-z0-9_=-]{1,200}$/;
/** issue の操作 ID の印。本文に入れ、作る前に同じ印の issue を探す */
export const ISSUE_MARKER = /^<!-- ccnavi:issue op=[A-Za-z0-9._:-]{1,100} -->$/;

/** GraphQL の node id（スレッドの id）。経路にも変数にも使える字に限る */
export function checkThreadId(value: unknown): string {
  if (typeof value !== "string" || !THREAD_ID.test(value)) throw new HostError(`スレッドの id の形が正しくありません: ${String(value)}`);
  return value;
}

/** PR・MR の番号 */
export function checkNumber(value: unknown, what = "番号"): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1 || value > 999_999_999) throw new HostError(`${what} の形が正しくありません: ${String(value)}`);
  return value;
}

/** 返信・コメント・issue の本文。空でなく、上限以内で、NUL を含まない */
export function checkWriteText(value: unknown, what: string): string {
  if (typeof value !== "string" || value.trim() === "" || value.length > MAX_WRITE_TEXT || value.includes("\0")) {
    throw new HostError(`${what} は ${MAX_WRITE_TEXT} 字までの空でない文字列にしてください（NUL は含められません）`);
  }
  return value;
}

/** issue のタイトル。1 行で、制御文字を含まない */
export function checkTitle(value: unknown): string {
  if (typeof value !== "string" || value.trim() === "" || value.length > MAX_TITLE || /[\x00-\x1f\x7f]/.test(value)) {
    throw new HostError(`issue のタイトルは ${MAX_TITLE} 字までの 1 行の文字列にしてください（改行・制御文字は含められません）`);
  }
  return value;
}

/** 本文の 1 行目だけを印として読む。印でなければ空 */
export function markerLine(body: unknown): string {
  if (typeof body !== "string") return "";
  const first = body.split("\n", 1)[0].replace(/\r$/, "");
  return ISSUE_MARKER.test(first) ? first : "";
}

/**
 * ホストへ送る本文の無害化。コードフェンスの外だけを直す: GitLab は行頭の `/`（quick action）の前に、
 * 両ホストとも `@` の直後（メンション）に、ゼロ幅の空白を入れる。フェンスの中は変えない
 */
export function neutralize(text: string, gitlab: boolean): string {
  const ZW = "\u200b";
  let fence: { ch: string; len: number } | null = null;
  return text
    .split("\n")
    .map((line) => {
      const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line.replace(/\r$/, ""));
      if (fence) {
        if (m && m[1][0] === fence.ch && m[1].length >= fence.len && m[2].trim() === "") fence = null;
        return line;
      }
      if (m) {
        fence = { ch: m[1][0], len: m[1].length };
        return line;
      }
      let out = line.replace(/(^|[^\w`])@(?=[\w])/g, `$1@${ZW}`);
      if (gitlab && /^\s*\//.test(out)) out = ZW + out;
      return out;
    })
    .join("\n");
}

export function checkMarker(value: unknown): string {
  if (typeof value !== "string" || !ISSUE_MARKER.test(value)) throw new HostError("issue の操作 ID の印の形が正しくありません");
  return value;
}

/** ホストが返した画面の URL。通信先の画面の根の下に限る（返信の本文に入れるので、別の場所を指させない） */
export function checkWebUrl(client: Pick<Client, "host">, value: unknown): string {
  let url: URL;
  try {
    url = new URL(String(value));
  } catch {
    throw new HostError(`ホストが返した URL を読めません: ${String(value)}`);
  }
  const root = new URL(client.host.web);
  if (url.origin !== root.origin || url.username || url.password || /[\s`<>]/.test(String(value))) {
    throw new HostError(`ホストが返した URL が通信先の画面の外を指しています: ${String(value)}`);
  }
  return url.toString();
}

export interface IssueRef {
  readonly number: number;
  readonly url: string;
}

/** 書く前に確かめる 1 つのスレッドの状態 */
export interface ThreadAbility {
  readonly id: string;
  /** ok: 扱える。pending: 保留中のレビューのスレッド（GitHub）。not-resolvable: 解決できない種類。non-diff: 差分以外の discussion（GitLab） */
  readonly kind: "ok" | "pending" | "not-resolvable" | "non-diff";
  readonly canReply: boolean;
  readonly canResolve: boolean;
  /** スレッドの既存のコメントにある、返信の印（`<!-- ccnavi:reply op=… -->`）の操作 ID。同じ返信を二度付けない */
  readonly replyOps: readonly string[];
}

/** コメントの本文の並びから、返信の印の操作 ID を集める */
export function replyOpsOf(bodies: readonly string[]): string[] {
  const out: string[] = [];
  for (const b of bodies) for (const m of b.matchAll(/<!-- ccnavi:reply op=([0-9a-f]{32}) key=\S* -->/g)) out.push(m[1]);
  return out;
}

/** 足りないと分かった権限 1 つ。`for` は、その権限が要る操作（`all` はどれにも要る） */
export interface Missing {
  readonly need: string;
  readonly for: "all" | "issue" | "reply" | "resolve" | "comment";
}

/** 書く前の確かめの材料（`precheckHostPlan` が計画と突き合わせる） */
export interface WriteInfo {
  readonly host: "github" | "gitlab";
  readonly threads: readonly ThreadAbility[];
  /** PAT かロールに足りないと分かったもの */
  readonly missing: readonly Missing[];
  /** 書く前には確かめられなかった権限の名前（実際に書くまで分からない） */
  readonly unknown: readonly string[];
}

async function writeJson(client: Client, path: string, method: string, body: unknown, need: Need): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method, headers: { ...headers(client), "Content-Type": "application/json" }, body: JSON.stringify(body) }, `${method} ${path}`, need);
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { status: res.status, body: parsed };
}

const REPLY_THREAD = `mutation($input: AddPullRequestReviewThreadReplyInput!) {
  addPullRequestReviewThreadReply(input: $input) { comment { id url } }
}`;

/** レビューのスレッドへ返信する（`addPullRequestReviewThreadReply`。Pull requests の書き込み） */
export async function replyThread(client: Client, threadId: string, body: string): Promise<{ url: string }> {
  const data = await graphql(client, REPLY_THREAD, { input: { pullRequestReviewThreadId: checkThreadId(threadId), body: checkWriteText(body, "返信の本文") } }, "Pull requests");
  const comment = (data.addPullRequestReviewThreadReply as { comment?: { id?: unknown; url?: unknown } | null } | null)?.comment;
  if (!comment || typeof comment.id !== "string") throw new HostError("GitHub がスレッドへの返信を受け付けませんでした（返信が作られていません。保留中のレビューのスレッドは返信できないことがあります）");
  return { url: typeof comment.url === "string" ? comment.url : "" };
}

const RESOLVE_THREAD = `mutation($input: ResolveReviewThreadInput!) {
  resolveReviewThread(input: $input) { thread { id isResolved } }
}`;

/** レビューのスレッドを解決済みにする（`resolveReviewThread`）。解決済みになったことまで確かめる */
export async function resolveThread(client: Client, threadId: string): Promise<void> {
  const data = await graphql(client, RESOLVE_THREAD, { input: { threadId: checkThreadId(threadId) } }, "Pull requests");
  const thread = (data.resolveReviewThread as { thread?: { isResolved?: unknown } | null } | null)?.thread;
  if (!thread || thread.isResolved !== true) throw new HostError("GitHub がスレッドを解決済みにしませんでした（応答が解決済みを示していません）");
}

/**
 * issue を作る（`POST /repos/{o}/{r}/issues`）。タイトルと本文だけを送る。assignee・labels・milestone は入れない
 * （入れると、権限のないユーザには黙って無視されるか、割り当てが通知になる）。
 */
export async function createIssue(client: Client, owner: string, repo: string, title: string, body: string): Promise<IssueRef> {
  const path = `${repoPath(owner, repo)}/issues`;
  const res = await writeJson(client, path, "POST", { title: checkTitle(title), body: checkWriteText(body, "issue の本文") }, "Issues");
  if (res.status === 410) throw new HostError("このリポジトリは Issues が無効です（410）。設定で Issues を有効にしてください", 410);
  if (res.status === 404) throw new HostError(`issue を作れませんでした（404）。リポジトリ ${owner}/${repo} が見えないか、PAT に ${NEED_TEXT.Issues} の権限がありません`, 404);
  if (res.status < 200 || res.status >= 300) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitHub が issue を作りませんでした（${res.status}）: ${typeof why === "string" ? why : "?"}`, res.status);
  }
  const made = (res.body ?? {}) as { number?: unknown; html_url?: unknown };
  if (typeof made.number !== "number" || !Number.isInteger(made.number) || made.number <= 0) throw new HostError("作った issue の番号を取得できません");
  return { number: made.number, url: checkWebUrl(client, made.html_url) };
}

/** 検索で見る issue の上限（100 件 × 3 ページ。新しい順）。届いても探し切れたとは言わない（検索は best effort） */
export const FIND_PAGES = 3;

/** 探した結果。`truncated` は、上限まで読んでも見つからず、探し切れていない */
export interface FindResult {
  readonly issue: IssueRef | null;
  readonly truncated: boolean;
}

/**
 * 操作 ID の印を本文の 1 行目に持つ issue を探す。PAT の持ち主が作ったものだけを拾う（他人が印を書いて、返信の
 * リンク先を差し替えさせない）。404 は「無い」。5xx・429・通信の失敗は投げる（作り直さずに止める）。
 * 上限（100 件 × 3 ページ、新しい順）まで読んで見つからなければ `truncated` を立てる。
 */
export async function findIssueByMarker(client: Client, owner: string, repo: string, marker: string): Promise<FindResult> {
  const m = checkMarker(marker);
  const me = (await viewer(client)).toLowerCase();
  for (let page = 1; page <= FIND_PAGES; page += 1) {
    const { status, body } = await rest(client, `${repoPath(owner, repo)}/issues?state=all&sort=created&direction=desc&per_page=100&page=${page}`);
    if (status === 404) return { issue: null, truncated: false };
    if (!Array.isArray(body)) throw new HostError(`リポジトリ ${owner}/${repo} の Issue の一覧の応答が配列ではありません`);
    for (const i of body as { number?: unknown; html_url?: unknown; body?: unknown; pull_request?: unknown; user?: { login?: unknown } | null }[]) {
      if (i.pull_request !== undefined && i.pull_request !== null) continue;
      if (markerLine(i.body) !== m) continue;
      if (typeof i.user?.login !== "string" || i.user.login.toLowerCase() !== me) continue;
      if (typeof i.number !== "number" || !Number.isInteger(i.number) || i.number <= 0) continue;
      return { issue: { number: i.number, url: checkWebUrl(client, i.html_url) }, truncated: false };
    }
    if (body.length < 100) return { issue: null, truncated: false };
  }
  return { issue: null, truncated: true };
}

/** issue のコメントの本文（続きのコメントが付いているかを見る）。500 件を超えたら読み切れないので投げる */
export async function issueComments(client: Client, owner: string, repo: string, number: number): Promise<string[]> {
  const out: string[] = [];
  for (let page = 1; page <= 5; page += 1) {
    const { status, body } = await rest(client, `${repoPath(owner, repo)}/issues/${checkNumber(number, "issue の番号")}/comments?per_page=100&page=${page}`);
    if (status === 404 || !Array.isArray(body)) throw new HostError(`issue #${number} のコメントを読めません`, status === 404 ? 404 : 0);
    for (const c of body as { body?: unknown }[]) out.push(typeof c.body === "string" ? c.body : "");
    if (body.length < 100) return out;
  }
  throw new HostError(`issue #${number} のコメントが多すぎて読み切れません`);
}

const THREAD_OWNER = `query($id: ID!) {
  node(id: $id) { ... on PullRequestReviewThread { pullRequest { number repository { nameWithOwner } } } }
}`;

/** スレッドが、登録したリポジトリの、その PR のものか（別のリポジトリ・別の PR のスレッドに書かせない） */
export async function threadBelongs(client: Client, threadId: string, owner: string, repo: string, number: number): Promise<boolean> {
  const data = await graphql(client, THREAD_OWNER, { id: checkThreadId(threadId) });
  const pr = (data.node as { pullRequest?: { number?: unknown; repository?: { nameWithOwner?: unknown } } | null } | null)?.pullRequest;
  return pr?.number === number && typeof pr.repository?.nameWithOwner === "string" && pr.repository.nameWithOwner.toLowerCase() === `${owner}/${repo}`.toLowerCase();
}

/**
 * PR か issue にコメントを付ける（`POST /issues/{n}/comments`。PR は issue の一種）。
 * 要る権限は、PR なら Pull requests の書き込み、issue なら Issues の書き込み
 */
export async function issueComment(client: Client, owner: string, repo: string, number: number, body: string, target: "pr" | "issue" = "pr"): Promise<{ url: string }> {
  const path = `${repoPath(owner, repo)}/issues/${checkNumber(number, target === "pr" ? "PR の番号" : "issue の番号")}/comments`;
  const need: Need = target === "pr" ? "Pull requests" : "Issues";
  const res = await writeJson(client, path, "POST", { body: checkWriteText(body, "コメントの本文") }, need);
  if (res.status === 404) throw new HostError(`コメントを付けられませんでした（404）。${target === "pr" ? "PR" : "issue"} #${number} が見えないか、PAT に ${NEED_TEXT[need]} の権限がありません`, 404);
  if (res.status < 200 || res.status >= 300) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitHub がコメントを受け付けませんでした（${res.status}）: ${typeof why === "string" ? why : "?"}`, res.status);
  }
  const url = (res.body as { html_url?: unknown } | null)?.html_url;
  return { url: typeof url === "string" ? checkWebUrl(client, url) : "" };
}

const WRITE_THREADS = `query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    hasIssuesEnabled
    isArchived
    viewerPermission
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved viewerCanReply viewerCanResolve comments(first: 1) { nodes { state } } recent: comments(last: 30) { nodes { body } } }
      }
    }
  }
}`;

/**
 * 書く前の確かめの材料。スレッドごとに返信・解決できるか（`viewerCanReply`・`viewerCanResolve`）と、保留中のレビューか
 * （最初のコメントの state が PENDING）を読み、Issues が使えるか・アーカイブされていないか・classic の PAT のスコープを見る。
 * fine-grained の PAT の権限は、書く前には読み取れない（`unknown` に入れる）。
 */
export async function writeInfo(client: Client, owner: string, repo: string, number: number): Promise<WriteInfo> {
  const n = checkNumber(number, "PR の番号");
  const threads: ThreadAbility[] = [];
  const missing: Missing[] = [];
  let after: string | null = null;
  for (let page = 0; ; page += 1) {
    const data = await graphql(client, WRITE_THREADS, { owner: checkName(owner, "owner"), name: checkName(repo, "repo"), number: n, after });
    const r = data.repository as {
      hasIssuesEnabled?: unknown;
      isArchived?: unknown;
      viewerPermission?: unknown;
      pullRequest?: { reviewThreads?: { pageInfo: { hasNextPage: boolean; endCursor: string | null }; nodes: Record<string, unknown>[] } } | null;
    } | null;
    const conn = r?.pullRequest?.reviewThreads;
    if (!r || !conn) throw new HostError(`マージリクエスト #${n} のスレッドを取得できません`);
    if (page === 0) {
      if (r.isArchived === true) missing.push({ need: "リポジトリのアーカイブの解除（読み取り専用です）", for: "all" });
      if (r.hasIssuesEnabled === false) missing.push({ need: "リポジトリの Issues の有効化", for: "issue" });
    }
    for (const t of conn.nodes) {
      const first = ((t.comments as { nodes?: { state?: unknown }[] } | null)?.nodes ?? [])[0];
      const pending = first?.state === "PENDING";
      const recent = ((t.recent as { nodes?: { body?: unknown }[] } | null)?.nodes ?? []).map((c) => (typeof c.body === "string" ? c.body : ""));
      threads.push({ id: String(t.id), kind: pending ? "pending" : "ok", canReply: t.viewerCanReply === true, canResolve: t.viewerCanResolve === true, replyOps: replyOpsOf(recent) });
    }
    if (!conn.pageInfo.hasNextPage) break;
    if (page + 1 > THREAD_PAGES) throw new HostError("reviewThreads が多すぎて読み切れません");
    after = conn.pageInfo.endCursor;
  }
  const scopes = client.seen?.scopes;
  const unknown: string[] = [];
  if (typeof scopes === "string") {
    // classic の PAT: repo（公開リポジトリだけなら public_repo）が無ければ、issue もコメントも書けない
    const have = scopes.split(",").map((x) => x.trim());
    if (!have.includes("repo") && !have.includes("public_repo")) missing.push({ need: "repo スコープ（classic の PAT）", for: "all" });
  } else {
    unknown.push(NEED_TEXT.Issues, NEED_TEXT["Pull requests"]);
  }
  return { host: "github", threads, missing, unknown };
}
