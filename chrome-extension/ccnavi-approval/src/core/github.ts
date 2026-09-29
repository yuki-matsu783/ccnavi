/**
 * GitHub の読み取り（ADR-0093 の 8.2）。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * 操作は名前で限る（5.5 の 4）。画面から来るのは操作の名前と引数だけで、URL・クエリ・ヘッダは
 * ここで組む。GraphQL の問い合わせは固定の文で、引数は変数で渡す（文に継ぎ足さない）。
 * 段階 1 は読み取りだけで、mutation は持たない。
 */
import type { Host } from "./hosts.js";

export type Fetch = (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => Promise<{
  status: number;
  ok: boolean;
  json(): Promise<unknown>;
}>;

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
}

function headers(client: Client): Record<string, string> {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${client.token}`,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

async function rest(client: Client, path: string): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await client.fetch(`${client.host.api}${path}`, { method: "GET", headers: headers(client) });
  if (res.status === 401) {
    throw new HostError("PAT が通らない（401）。設定画面で差し替える", 401);
  }
  if (res.status === 404) {
    return { status: 404, body: null };
  }
  if (!res.ok) {
    throw new HostError(`GitHub が ${res.status} を返した: GET ${path}`, res.status);
  }
  return { status: res.status, body: await res.json() };
}

async function graphql(client: Client, query: string, variables: Record<string, unknown>): Promise<Record<string, unknown>> {
  client.counter.graphql += 1;
  const res = await client.fetch(client.host.graphql, {
    method: "POST",
    headers: { ...headers(client), "Content-Type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (res.status === 401) {
    throw new HostError("PAT が通らない（401）。設定画面で差し替える", 401);
  }
  if (!res.ok) {
    throw new HostError(`GitHub の GraphQL が ${res.status} を返した`, res.status);
  }
  const body = (await res.json()) as { data?: Record<string, unknown>; errors?: { message?: string }[] };
  if (body.errors && body.errors.length > 0) {
    throw new HostError(`GitHub の GraphQL が失敗した: ${body.errors.map((e) => e.message ?? "?").join(" / ")}`);
  }
  return body.data ?? {};
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
  readonly type: "tree" | "blob";
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
}

/** tree を再帰で読む。`truncated` なら取り切れないので止める（8.2） */
export async function tree(client: Client, owner: string, repo: string, oid: string): Promise<TreeEntry[]> {
  const { status, body } = await rest(client, `${repoPath(owner, repo)}/git/trees/${checkOid(oid)}?recursive=1`);
  if (status === 404) {
    throw new HostError(`tree ${oid} が無い`);
  }
  const b = body as { truncated?: boolean; tree?: { path?: unknown; type?: unknown; sha?: unknown }[] };
  if (b.truncated) {
    throw new HostError("置き場の tree が大きすぎて取り切れない（truncated）。Chrome では読めない");
  }
  const out: TreeEntry[] = [];
  for (const e of b.tree ?? []) {
    if (e.type === "blob") {
      out.push({ path: checkPath(e.path), sha: checkOid(e.sha) });
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
    fields.push(`b${i}: object(oid: $o${i}) { ... on Blob { text isBinary byteSize } }`);
  });
  if (fields.length === 0) {
    return {};
  }
  const query = `query(${decl.join(", ")}) { repository(owner: $owner, name: $name) { ${fields.join(" ")} } }`;
  const data = await graphql(client, query, vars);
  const repoData = (data.repository ?? {}) as Record<string, { text?: string | null; isBinary?: boolean; byteSize?: number } | null>;
  const out: Record<string, BlobText> = {};
  const encoder = new TextEncoder();
  oids.forEach((oid, i) => {
    const b = repoData[`b${i}`];
    if (!b) {
      throw new HostError(`blob ${oid} を取れなかった`);
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
