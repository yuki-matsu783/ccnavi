/**
 * GitLab の読み書き（ADR-0093 の段階 5。8.2・8.4・8.8・8.9）。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * GitHub（`github.ts`）と同じ名前の操作を、GitLab の REST API（v4）で組む。操作は名前で限り（5.5 の 4）、
 * URL・クエリ・ヘッダはここで組む。書くのは Commits API（`POST repository/commits` の `actions`）の 1 コミットと、
 * 「始める」のブランチの作成だけ。
 *
 * GitLab の Commits API には「先頭がこの sha のときだけ」の指定が無い（8.4）。書く直前に先頭を読み、違えば書かずに
 * 「動いた」（409）で返す。それでも間に入った書き込みは防げないので、答えにコミットの親（`parent_ids[0]`）を返し、
 * 書く流れ（`write.ts`）が事後に確かめて、違えば判定し直し、打ち消す（D21 の 1 段目）。`seq` は書かない（2 段目）。
 *
 * MR のスレッドとレビューの写し（`reviewCopy`）は、手元の `ccnavi-review.sh` の `find_mr`・`threads`・`reviews`
 * （GitLab の枝）と同じ問い合わせ・同じページの切り方・同じ欄の落とし方（jq の `//`・`tostring`、型は jq と同じく
 * そのまま写す）で組む。同じ見本（test/fixtures/host/gitlab/。手で組んだもの）から同じ写しになることを試験が見る。
 *
 * レビューの後（11.9.1）: 書き込みの update・delete には `last_commit_id`（そのファイルを最後に変えたコミット）を付け、
 * 同じファイルを他人が変えていれば GitLab が断る（決定 A。ファイル単位の比較つきの書き込み）。MR は同じプロジェクトから
 * 出たもの（`source_project_id`）だけを拾う（フォークの MR を拾わない）。転送は追わない（`redirect: "error"`）。
 */
import type { Host } from "./hosts.js";
import {
  checkBranch,
  checkOid,
  checkPath,
  fetchNoRedirect,
  HostError,
  RETRY_LIMIT_SECONDS,
  type ApprovalCommit,
  type BlobText,
  type Client,
  type Fetch,
  type PathObject,
  type PullReview,
  type RecentRef,
  type ReviewThread,
  type TreeEntry,
  BLOB_BATCH,
} from "./github.js";

export type { Client };

/** 名前空間（グループ。入れ子の `group/sub` も）とプロジェクトの名前の 1 段 */
const SEGMENT = /^[A-Za-z0-9_.-]{1,100}$/;
const USERNAME = /^[A-Za-z0-9_.-]{1,100}$/;

/** GitLab の owner（名前空間）。`group` か入れ子の `group/sub`。どの段も `.`・`..` でない */
export function checkNamespace(value: unknown, what = "owner"): string {
  if (typeof value !== "string" || value.length > 255 || value.split("/").some((p) => !SEGMENT.test(p) || p === "." || p === "..")) {
    throw new HostError(`${what} の形が正しくない: ${String(value)}`);
  }
  return value;
}

function project(owner: string, repo: string): string {
  const full = `${checkNamespace(owner)}/${checkNamespace(repo, "repo")}`;
  if (repo.includes("/")) throw new HostError(`repo の形が正しくない: ${repo}`);
  return `/projects/${encodeURIComponent(full)}`;
}

function headers(client: Client, json = false): Record<string, string> {
  return { Accept: "application/json", "PRIVATE-TOKEN": client.token, ...(json ? { "Content-Type": "application/json" } : {}) };
}

type Res = Awaited<ReturnType<Fetch>>;

function header(res: Res, name: string): string {
  return res.headers?.get(name)?.trim() ?? "";
}

async function pause(client: Client, seconds: number): Promise<void> {
  const sleep = client.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  await sleep(seconds * 1000);
}

/** 1 回の呼び出し。401 は差し替えを促し、429（レート制限）は Retry-After が短ければ 1 回だけ待ち直す */
async function send(client: Client, url: string, init: Parameters<Fetch>[1], what: string): Promise<Res> {
  for (let attempt = 0; ; attempt += 1) {
    // 別のホストへの転送を追わない（PAT を載せた要求を埋め込んだ通信先の外へ出さない）
    const res = await fetchNoRedirect(client, url, init, what);
    if (res.status === 401) throw new HostError("PAT で認証できない（401）。設定画面で差し替えてください", 401);
    if (res.status === 429) {
      const after = header(res, "retry-after");
      const seconds = /^\d+$/.test(after) ? Number(after) : null;
      if (seconds !== null && seconds <= RETRY_LIMIT_SECONDS && attempt === 0) {
        await pause(client, seconds);
        continue;
      }
      throw new HostError(`GitLab のレート制限にかかった（429${seconds !== null ? `、${seconds} 秒待つよう求められた` : ""}）。少し待ってからボードを更新してください`, 429);
    }
    if (res.status === 403) {
      throw new HostError(`GitLab が断った（403）: ${what}。PAT の権限（api スコープ・プロジェクトのメンバー）を見直してください`, 403);
    }
    return res;
  }
}

async function get(client: Client, path: string): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "GET", headers: headers(client) }, `GET ${path}`);
  if (res.status === 404) return { status: 404, body: null };
  if (!res.ok) throw new HostError(`GitLab が ${res.status} を返した: GET ${path}`, res.status);
  return { status: res.status, body: await res.json() };
}

async function post(client: Client, path: string, body: unknown): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "POST", headers: headers(client, true), body: JSON.stringify(body) }, `POST ${path}`);
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { status: res.status, body: parsed };
}

/** jq の `a // b`。null と false のときだけ b（空文字や 0 は a のまま） */
function alt<T>(value: unknown, fallback: T): T {
  return value === null || value === undefined || value === false ? fallback : (value as T);
}

/** jq の `tostring`（数は 10 進、文字列はそのまま、ほかは JSON） */
function tostring(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "null";
}

/** jq の `ascii_upcase`（ASCII の英小文字だけを大文字に） */
function asciiUpcase(value: string): string {
  return value.replace(/[a-z]/g, (c) => c.toUpperCase());
}

/** 100 件ずつページの番号で読み、100 件に満たないページで終える。`pages` を超えたら止める（sh の `pages` と同じ） */
async function pages(client: Client, path: string, limit: number, what: string): Promise<unknown[]> {
  const sep = path.includes("?") ? "&" : "?";
  const all: unknown[] = [];
  for (let page = 1; ; page += 1) {
    const { status, body } = await get(client, `${path}${sep}per_page=100&page=${page}`);
    // 404 や並びでない答えを「無い」と読むと、見落として通してしまう
    if (status === 404) throw new HostError(`${what} を読めない（404）`, 404);
    if (!Array.isArray(body)) throw new HostError(`${what} の応答が並びでない`);
    all.push(...body);
    if (body.length < 100) return all;
    if (page + 1 > limit) throw new HostError(`${what} が多すぎて読み切れない`);
  }
}

// ---- 読み取り（8.2） ----------------------------------------------------------------------

/** プロジェクトのデフォルトブランチ（3.3 の統合先の既定） */
export async function repoInfo(client: Client, owner: string, repo: string): Promise<{ defaultBranch: string }> {
  const { status, body } = await get(client, project(owner, repo));
  if (status === 404) throw new HostError(`プロジェクト ${owner}/${repo} が見つからない（存在しないか、PAT の権限が及ばない）`, 404);
  return { defaultBranch: checkBranch((body as { default_branch?: unknown }).default_branch) };
}

/** ブランチの先頭。無ければ null（404） */
export async function branchHead(client: Client, owner: string, repo: string, branch: string): Promise<string | null> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/branches/${encodeURIComponent(checkBranch(branch))}`);
  if (status === 404) return null;
  const b = (body ?? {}) as { name?: unknown; commit?: { id?: unknown } };
  if (b.name !== branch) return null;
  return checkOid(b.commit?.id);
}

/** ブランチを読むページの上限。100 × 10 本を超えるプロジェクトは残りを諦める（表示用） */
const REF_PAGES = 10;

/** `since` より後に先頭が動いたブランチ（表示用。D2）。GitLab の `repository/branches` を読む（8.2） */
export async function recentRefs(client: Client, owner: string, repo: string, since: Date): Promise<RecentRef[]> {
  const out: RecentRef[] = [];
  for (let page = 1; page <= REF_PAGES; page += 1) {
    const { status, body } = await get(client, `${project(owner, repo)}/repository/branches?per_page=100&page=${page}`);
    if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} のブランチを読めない`);
    for (const b of body as { name?: unknown; commit?: { id?: unknown; committed_date?: unknown } }[]) {
      const date = typeof b.commit?.committed_date === "string" ? b.commit.committed_date : "";
      if (!date || new Date(date).getTime() < since.getTime()) continue;
      try {
        out.push({ name: checkBranch(b.name), head: checkOid(b.commit?.id), committedDate: new Date(date).toISOString() });
      } catch {
        // 読めない名前のブランチは候補にしない
      }
    }
    if (body.length < 100) break;
  }
  return out;
}

/** tree を読むページの上限（100 × 50 = 5,000 件）。超えたら取り切れないので止める（8.2） */
export const TREE_PAGES = 50;

type TreeItem = { id?: unknown; name?: unknown; type?: unknown; path?: unknown; mode?: unknown };

async function listTree(client: Client, owner: string, repo: string, commit: string, path: string, recursive: boolean): Promise<TreeItem[]> {
  const q = new URLSearchParams({ ref: checkOid(commit) });
  if (path) q.set("path", checkPath(path));
  if (recursive) q.set("recursive", "true");
  const base = `${project(owner, repo)}/repository/tree?${q.toString()}`;
  const all: TreeItem[] = [];
  for (let page = 1; ; page += 1) {
    const { status, body } = await get(client, `${base}&per_page=100&page=${page}`);
    if (status === 404) return [];
    if (!Array.isArray(body)) throw new HostError("tree の応答が並びでない");
    all.push(...(body as TreeItem[]));
    if (body.length < 100) return all;
    if (page >= TREE_PAGES) throw new HostError("置き場の tree が大きすぎて取り切れない。Chrome では読めない");
  }
}

/** コミットの上のパスが指す tree / blob。無ければ null（親のディレクトリを 1 段読んで名前で引く） */
export async function pathObjects(client: Client, owner: string, repo: string, commit: string, paths: readonly string[]): Promise<Record<string, PathObject | null>> {
  checkOid(commit);
  const dirs = new Map<string, TreeItem[]>();
  const out: Record<string, PathObject | null> = {};
  for (const p of paths) {
    const path = checkPath(p);
    const cut = path.lastIndexOf("/");
    const dir = cut < 0 ? "" : path.slice(0, cut);
    const name = cut < 0 ? path : path.slice(cut + 1);
    if (!dirs.has(dir)) dirs.set(dir, await listTree(client, owner, repo, commit, dir, false));
    const hit = (dirs.get(dir) ?? []).find((e) => e.name === name);
    // シンボリックリンク（mode 120000）は中身を読まず、読む側が「決まらない」にする
    const type = hit?.type === "tree" ? "tree" : hit?.type === "blob" ? (hit.mode === "120000" ? "link" : "blob") : null;
    out[p] = hit && type ? { type, oid: checkOid(hit.id) } : null;
  }
  return out;
}

/** 置き場のディレクトリを再帰で読む。GitLab は tree の sha でなく、コミットとパスで引く */
export async function tree(client: Client, owner: string, repo: string, _oid: string, commit: string, path: string): Promise<TreeEntry[]> {
  const items = await listTree(client, owner, repo, commit, path, true);
  const out: TreeEntry[] = [];
  const prefix = `${checkPath(path)}/`;
  for (const e of items) {
    if (e.type !== "blob") continue;
    const full = checkPath(e.path);
    if (!full.startsWith(prefix)) throw new HostError(`tree の応答に置き場の外のパスがある: ${full}`);
    out.push({ path: full.slice(prefix.length), sha: checkOid(e.id), mode: typeof e.mode === "string" ? e.mode : "" });
  }
  return out;
}

/** BOM を落とさない（落とすと、打ち消しで戻すバイト列が元と変わる） */
const DECODER = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

function fromBase64(text: string): Uint8Array {
  const bin = atob(text.replace(/\s+/g, ""));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/**
 * blob を sha で 1 件ずつ取る（`repository/blobs/:sha`）。8.2 の束の取り方は確認事項 2。
 * NUL を含むか UTF-8 として読めなければバイナリ。大きさが `size` と合わなければ止める
 */
export async function blobs(client: Client, owner: string, repo: string, oids: readonly string[]): Promise<Record<string, BlobText>> {
  if (oids.length > BLOB_BATCH) throw new HostError(`blob は 1 回に ${BLOB_BATCH} 件まで`);
  const out: Record<string, BlobText> = {};
  for (const oid of oids) {
    const { status, body } = await get(client, `${project(owner, repo)}/repository/blobs/${checkOid(oid)}`);
    if (status === 404) throw new HostError(`blob ${oid} を取れなかった`);
    const b = (body ?? {}) as { content?: unknown; encoding?: unknown; size?: unknown };
    if (b.encoding !== "base64" || typeof b.content !== "string") throw new HostError(`blob ${oid} の応答が読めない`);
    const bytes = fromBase64(b.content);
    if (typeof b.size === "number" && bytes.length !== b.size) throw new HostError(`blob ${oid} の本文が大きさと合わない（取り切れていない）`);
    if (bytes.includes(0)) {
      out[oid] = { text: null, binary: true };
      continue;
    }
    try {
      out[oid] = { text: DECODER.decode(bytes), binary: false };
    } catch {
      out[oid] = { text: null, binary: true };
    }
  }
  return out;
}

/** PAT の持ち主のアカウント名（跡と印の `actor`。8.8・8.9） */
export async function viewer(client: Client): Promise<string> {
  const { status, body } = await get(client, "/user");
  const name = (body as { username?: unknown } | null)?.username;
  if (status === 404 || typeof name !== "string" || !USERNAME.test(name)) throw new HostError("PAT の持ち主を読めない");
  return name;
}

/**
 * PAT の期限（D25。`GET /personal_access_tokens/self` の `expires_at`、`YYYY-MM-DD`）。その日の終わり（UTC）を ISO で返す。
 * 読めなければ空（project access token で返るかは確認事項 3）
 */
export async function tokenExpiry(client: Client): Promise<string> {
  const { status, body } = await get(client, "/personal_access_tokens/self");
  const at = (body as { expires_at?: unknown } | null)?.expires_at;
  if (status !== 200 || typeof at !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(at)) return "";
  const t = Date.parse(`${at}T23:59:59Z`);
  return Number.isNaN(t) ? "" : new Date(t).toISOString();
}

// ---- 取り下げ（8.8）と書いた後の確かめ ---------------------------------------------------------

const HISTORY_PAGE = 30;
const CHAIN_DEPTH = 100;

type CommitItem = { id?: unknown; parent_ids?: unknown };

/** `sha` から最初の親を辿った鎖（`first_parent=true` の一覧の `CHAIN_DEPTH` 件） */
export async function firstParentChain(client: Client, owner: string, repo: string, sha: string): Promise<Set<string>> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/commits?ref_name=${checkOid(sha)}&first_parent=true&per_page=${CHAIN_DEPTH}`);
  const chain = new Set<string>();
  if (status === 404 || !Array.isArray(body)) return chain;
  for (const c of body as CommitItem[]) if (typeof c.id === "string") chain.add(c.id);
  return chain;
}

/**
 * 承認コミット（8.8 の 2）。GitHub の `approvalCommit` と同じ規則: `sha` から遡って `path` を最後に変えたコミットが、
 * 親が 1 つで、親に `path` が無く自分には在り（足した）、`sha` の first-parent の鎖の上にあるときだけ返す
 */
export async function approvalCommit(client: Client, owner: string, repo: string, sha: string, path: string): Promise<ApprovalCommit | null> {
  const q = `?ref_name=${checkOid(sha)}&path=${encodeURIComponent(checkPath(path))}&per_page=${HISTORY_PAGE}`;
  const { status, body } = await get(client, `${project(owner, repo)}/repository/commits${q}`);
  const newest = status === 404 || !Array.isArray(body) ? undefined : (body[0] as CommitItem | undefined);
  if (!newest) return null;
  const commit = checkOid(newest.id);
  const parents = Array.isArray(newest.parent_ids) ? newest.parent_ids : [];
  if (parents.length !== 1) return null;
  const parent = checkOid(parents[0]);
  const here = await pathObjects(client, owner, repo, commit, [path]);
  const before = await pathObjects(client, owner, repo, parent, [path]);
  if (here[path]?.type !== "blob" || before[path] !== null) return null;
  const chain = await firstParentChain(client, owner, repo, sha);
  return chain.has(commit) ? { commit, parent } : null;
}

/** コミットの親（書いた後の確かめ） */
export async function commitParents(client: Client, owner: string, repo: string, sha: string): Promise<string[]> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/commits/${checkOid(sha)}`);
  if (status === 404) return [];
  const parents = (body as { parent_ids?: unknown } | null)?.parent_ids;
  return Array.isArray(parents) ? parents.map((p) => checkOid(p)) : [];
}

// ---- MR（8.9・8.10） ------------------------------------------------------------------------

/** プロジェクトの数の id（MR が同じプロジェクトから出たかを見る） */
async function projectId(client: Client, owner: string, repo: string): Promise<number> {
  const { status, body } = await get(client, project(owner, repo));
  const id = (body as { id?: unknown } | null)?.id;
  if (status === 404 || typeof id !== "number") throw new HostError(`プロジェクト ${owner}/${repo} の id を読めない`, status);
  return id;
}

type MrItem = { iid?: unknown; web_url?: unknown; source_project_id?: unknown };

/** 開いた MR のうち、このプロジェクトのブランチから出たもの（フォークの同じ名前のブランチの MR を拾わない） */
async function openMrs(client: Client, owner: string, repo: string, branch: string): Promise<MrItem[]> {
  const pid = await projectId(client, owner, repo);
  // API は source_project_id で絞れないので、全ページ（20 ページまで）を読んでから絞る（1 ページ目がフォークで埋まっても
  // 本物を外さない。sh の find_mr と同じ。11.9.3 の 6）
  const all = await pages(client, `${project(owner, repo)}/merge_requests?state=opened&source_branch=${encodeURIComponent(checkBranch(branch))}`, THREAD_PAGES, `${branch} のマージリクエストの一覧`);
  return (all as MrItem[]).filter((m) => m && m.source_project_id === pid);
}

/**
 * 親のブランチの開いた MR（sh の `find_mr` と同じ: `merge_requests?state=opened&source_branch=<branch>` のうち、
 * `source_project_id` がこのプロジェクトのものの先頭）
 */
export async function openMr(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; url: string } | null> {
  const first = (await openMrs(client, owner, repo, branch))[0];
  if (!first) return null;
  if (typeof first.iid !== "number" || !Number.isInteger(first.iid)) throw new HostError("マージリクエストの番号を読めない");
  return { number: first.iid, url: String(alt(first.web_url, "")) };
}

/** 親のブランチの開いた MR に付いている Approve（8.10。`merge_requests/:iid/approvals` の `approved_by`） */
export async function pullApprovals(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; approvals: number }[]> {
  const out: { number: number; approvals: number }[] = [];
  for (const mr of await openMrs(client, owner, repo, branch)) {
    if (typeof mr.iid !== "number" || !Number.isInteger(mr.iid)) continue;
    const got = await get(client, `${project(owner, repo)}/merge_requests/${mr.iid}/approvals`);
    const by = (got.body as { approved_by?: unknown } | null)?.approved_by;
    const approvals = Array.isArray(by) ? by.length : 0;
    if (approvals > 0) out.push({ number: mr.iid, approvals });
  }
  return out;
}

/** sh の読みの上限と同じ（20 ページを超えたら止める） */
const THREAD_PAGES = 20;

type Note = { id?: unknown; body?: unknown; created_at?: unknown; resolvable?: unknown; resolved?: unknown; author?: { id?: unknown; username?: unknown } | null; position?: { new_path?: unknown; new_line?: unknown } | null };

/**
 * MR のスレッド（sh の `threads` の GitLab の枝と同じ読み方）。最初のノートが解決できる discussion だけ。
 * 解決済みは、解決できるノートが全部解決しているとき。url は MR の URL + `#note_<最初のノートの id>`
 */
export async function discussions(client: Client, owner: string, repo: string, number: number, mrUrl: string): Promise<ReviewThread[]> {
  const all = await pages(client, `${project(owner, repo)}/merge_requests/${number}/discussions`, THREAD_PAGES, `マージリクエスト !${number} のスレッド`);
  const out: ReviewThread[] = [];
  for (const d of all as { id?: unknown; notes?: unknown }[]) {
    const notes = (Array.isArray(alt(d.notes, [])) ? (alt(d.notes, []) as Note[]) : []) ?? [];
    if (notes.length === 0) continue;
    const first = notes[0] ?? {};
    if (alt(first.resolvable, false) === false) continue;
    const resolvable = notes.filter((n) => alt(n?.resolvable, false) !== false);
    const pos = first.position ?? null;
    // jq と同じく、欄の型はそのまま写す（文字列の行番号を数に直さない）。書き手は username でなく id（11.9.1 の 15）
    out.push({
      id: tostring(d.id),
      resolved: resolvable.every((n) => alt(n.resolved, false) !== false),
      url: `${mrUrl}#note_${tostring(first.id)}`,
      path: alt(pos?.new_path, "") as string,
      line: alt(pos?.new_line, 0) as number,
      body: alt(first.body, "") as string,
      created_at: alt(first.created_at, "") as string,
      author: tostring(alt(first.author?.id, "")),
    });
  }
  return out;
}

/** MR のレビュアーの状態（sh の `reviews` の GitLab の枝と同じ）。`requested_changes` を CHANGES_REQUESTED にそろえる */
export async function reviewers(client: Client, owner: string, repo: string, number: number, mrUrl: string): Promise<PullReview[]> {
  const all = await pages(client, `${project(owner, repo)}/merge_requests/${number}/reviewers`, THREAD_PAGES, `マージリクエスト !${number} のレビュアー`);
  return (all as { state?: unknown; updated_at?: unknown; created_at?: unknown; user?: { id?: unknown; username?: unknown } | null }[]).map((r) => {
    const raw = alt(r.state, "");
    // jq の ascii_upcase は文字列でなければ落ちる（sh は写しを組めずに止まる）。同じく止める
    if (r.state !== "requested_changes" && typeof raw !== "string") throw new HostError(`マージリクエスト !${number} のレビュアーの state が文字列でない`);
    return {
      state: r.state === "requested_changes" ? "CHANGES_REQUESTED" : asciiUpcase(raw as string),
      url: mrUrl,
      submitted_at: alt(r.updated_at, alt(r.created_at, "")) as string,
      author: tostring(alt(r.user?.id, alt(r.user?.username, ""))),
    };
  });
}

export interface GitLabReviewCopy {
  readonly host: "gitlab";
  readonly mr: { readonly number: number; readonly url: string };
  readonly threads: readonly ReviewThread[];
  readonly reviews: readonly PullReview[];
  readonly fetched_at: string;
}

/** 親のブランチの MR のスレッドとレビューの写し（8.9。`ccnavi-review.sh fetch` の GitLab の枝と同じ形） */
export async function reviewCopy(client: Client, owner: string, repo: string, branch: string): Promise<GitLabReviewCopy> {
  const mr = await openMr(client, owner, repo, branch);
  if (mr === null) throw new HostError(`親のブランチ ${branch} に対応する、開いているマージリクエストが無い`);
  const threads = await discussions(client, owner, repo, mr.number, mr.url);
  const reviews = await reviewers(client, owner, repo, mr.number, mr.url);
  return { host: "gitlab", mr, threads, reviews, fetched_at: new Date().toISOString() };
}

/**
 * compare の変更の一覧を打ち切られたとみなす件数。GitLab の差分の件数の上限（`diff_max_files`）は既定が 1000 で、
 * インスタンスの管理者が 500 まで下げられる。その最小値より下（450）から打ち切られたとみなす（11.9.3 の 3。確認事項 9 で
 * インスタンスの値を確かめる）
 */
export const COMPARE_FILES_LIMIT = 1000;
export const COMPARE_FILES_NEAR = 450;

/**
 * 依頼時の先頭 `base` から今の先頭 `head` までに変わったパス（8.9）。`base` が `head` の祖先でなければ
 * （`merge_base` が `base` でない）、読めない・時間切れ・打ち切りのどれでも `files` は null（動いたと数える）
 */
export async function compareFiles(client: Client, owner: string, repo: string, base: string, head: string): Promise<{ base: string; head: string; files: string[] | null }> {
  const b = checkOid(base);
  const h = checkOid(head);
  if (b === h) return { base: b, head: h, files: [] };
  const mb = await get(client, `${project(owner, repo)}/repository/merge_base?refs[]=${b}&refs[]=${h}`);
  if (mb.status === 404 || (mb.body as { id?: unknown } | null)?.id !== b) return { base: b, head: h, files: null };
  const { status, body } = await get(client, `${project(owner, repo)}/repository/compare?from=${b}&to=${h}`);
  const res = (body ?? {}) as { diffs?: unknown; compare_timeout?: unknown };
  if (status === 404 || res.compare_timeout === true || !Array.isArray(res.diffs)) return { base: b, head: h, files: null };
  if (res.diffs.length >= COMPARE_FILES_NEAR) return { base: b, head: h, files: null };
  // 行数などで折りたたまれた・大きすぎる差分があれば、一覧が揃っていると言えない（動いたと数える。11.9.1 の 9）
  const diffs = res.diffs as { old_path?: unknown; new_path?: unknown; collapsed?: unknown; too_large?: unknown }[];
  if (diffs.some((d) => d?.collapsed === true || d?.too_large === true)) return { base: b, head: h, files: null };
  const files: string[] = [];
  for (const d of diffs) {
    if (typeof d.new_path === "string") files.push(d.new_path);
    if (typeof d.old_path === "string" && d.old_path !== d.new_path) files.push(d.old_path);
  }
  return { base: b, head: h, files };
}

// ---- 書き込み（8.4 の 1 段目） -----------------------------------------------------------------

/** GitLab が書き込みを断った（`last_commit_id` が違う・書き換えるものが無い など）。何も書いていない。打ち消しならユーザに回す */
export const HOST_REFUSED = 409;
/** 送る前の確認で先頭が読んだものと違った（何も送っていない）。読み直して試し直してよい */
export const HOST_MOVED = 412;

/** 書くもの 1 つ（Commits API の action）。中身は base64。`last` はそのファイルを最後に変えたと呼び手が知っているコミット */
export interface GitLabAction {
  readonly op: "create" | "update" | "delete";
  readonly path: string;
  readonly contents?: string;
  readonly last?: string;
}

/** `ref` の上でそのファイルを最後に変えたコミット（`repository/files/:path?ref=` の `last_commit_id`）。無ければ投げる */
export async function lastCommit(client: Client, owner: string, repo: string, ref: string, path: string): Promise<string> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/files/${encodeURIComponent(checkPath(path))}?ref=${checkOid(ref)}`);
  if (status === 404) throw new HostError(`${path} が ${ref.slice(0, 7)} に無い（書き換える・消すものが無い）`, HOST_REFUSED);
  return checkOid((body as { last_commit_id?: unknown } | null)?.last_commit_id);
}

/** 書いた答え。`parent` は GitLab が実際に積んだ先（読んだ先頭と違えば、間に別の書き込みが入った） */
export interface Written {
  readonly oid: string;
  readonly parent: string;
}

/**
 * 親のブランチへ 1 コミットで書く（Commits API の `actions`）。書く直前に先頭を読み、`expected` と違えば書かずに
 * 409（動いた）で返す。GitLab が断った（ファイルが既にある・無い、など 400）も 409 にする（書く流れが読み直す）。
 * `force` は付けない。答えのコミットの親を返し、書く流れが事後に確かめる（8.4 の 1 段目）。
 */
export async function createCommit(
  client: Client,
  owner: string,
  repo: string,
  branch: string,
  expected: string,
  headline: string,
  body: string,
  actions: readonly GitLabAction[],
): Promise<Written> {
  const name = checkBranch(branch);
  const now = await branchHead(client, owner, repo, name);
  if (now !== checkOid(expected)) throw new HostError(`親のブランチ ${name} の先頭が読んだものと違う（書く前に動いた）`, HOST_MOVED);
  // update・delete には、そのファイルを最後に変えたコミットを付ける（決定 A）。呼び手が知っていればそれ（打ち消しは自分の
  // コミット）、無ければ読んだ先頭の上の値。その後に他人が変えていれば GitLab が断る（400。ここでは 409 にする）
  const out: Record<string, unknown>[] = [];
  for (const a of actions) {
    const file = checkPath(a.path);
    if (a.op === "create") {
      out.push({ action: "create", file_path: file, content: a.contents ?? "", encoding: "base64" });
      continue;
    }
    const last = a.last ? checkOid(a.last) : await lastCommit(client, owner, repo, expected, file);
    out.push(
      a.op === "delete"
        ? { action: "delete", file_path: file, last_commit_id: last }
        : { action: "update", file_path: file, content: a.contents ?? "", encoding: "base64", last_commit_id: last },
    );
  }
  const payload = { branch: name, commit_message: body ? `${headline}\n\n${body}` : headline, actions: out };
  const res = await post(client, `${project(owner, repo)}/repository/commits`, payload);
  if (res.status === 400 || res.status === 409 || res.status === 422) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitLab が書き込みを断った（${res.status}）: ${typeof why === "string" ? why : "?"}`, HOST_REFUSED);
  }
  if (res.status < 200 || res.status >= 300) throw new HostError(`GitLab が ${res.status} を返した: POST repository/commits`, res.status);
  const c = (res.body ?? {}) as { id?: unknown; parent_ids?: unknown };
  const parents = Array.isArray(c.parent_ids) ? c.parent_ids : [];
  return { oid: checkOid(c.id), parent: parents.length > 0 ? checkOid(parents[0]) : "" };
}

// ---- 「始める」（8.6） ----------------------------------------------------------------------

export interface Issue {
  readonly number: number;
  readonly title: string;
  readonly url: string;
}

/** 開いた issue（新しい順に 50 件）。「始める」の一覧 */
export async function issues(client: Client, owner: string, repo: string): Promise<Issue[]> {
  const { status, body } = await get(client, `${project(owner, repo)}/issues?state=opened&order_by=created_at&sort=desc&per_page=50`);
  if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} の issue を読めない`, status);
  const out: Issue[] = [];
  for (const i of body as { iid?: unknown; title?: unknown; web_url?: unknown }[]) {
    if (typeof i.iid !== "number" || !Number.isInteger(i.iid) || i.iid <= 0) continue;
    out.push({ number: i.iid, title: String(alt(i.title, "")), url: String(alt(i.web_url, "")) });
  }
  return out;
}

/** 全部のブランチの名前を読むページの上限（100 × 50）。超えたら読み切れないので止める（「始める」の重なりの検査） */
export const BRANCH_PAGES = 50;

/** 全部のブランチの名前（「始める」が大文字小文字をそろえて重なりを見る） */
export async function branchNames(client: Client, owner: string, repo: string): Promise<string[]> {
  const all: string[] = [];
  for (let page = 1; ; page += 1) {
    const { status, body } = await get(client, `${project(owner, repo)}/repository/branches?per_page=100&page=${page}`);
    if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} のブランチを読めない`);
    for (const b of body as { name?: unknown }[]) if (typeof b.name === "string") all.push(b.name);
    if (body.length < 100) return all;
    if (page >= BRANCH_PAGES) throw new HostError("ブランチが多すぎて読み切れない。「始める」は手元で行ってください");
  }
}

/** ブランチを `sha` から作る（`POST repository/branches`）。既にあれば断られる（400） */
export async function createBranch(client: Client, owner: string, repo: string, name: string, sha: string): Promise<string> {
  const q = new URLSearchParams({ branch: checkBranch(name), ref: checkOid(sha) });
  const res = await post(client, `${project(owner, repo)}/repository/branches?${q.toString()}`, {});
  if (res.status < 200 || res.status >= 300) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitLab がブランチを作らなかった（${res.status}）: ${typeof why === "string" ? why : "?"}`, res.status);
  }
  const made = (res.body ?? {}) as { name?: unknown; commit?: { id?: unknown } };
  if (made.name !== name) throw new HostError("作ったブランチの名前が、頼んだ名前と違う");
  return checkOid(made.commit?.id);
}

export type { Host };
