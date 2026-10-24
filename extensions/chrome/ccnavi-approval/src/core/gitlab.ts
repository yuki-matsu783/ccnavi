/**
 * GitLab の読み書き。service worker だけが呼ぶ。PAT は引数で受け、外へ返さない。
 *
 * GitHub（`github.ts`）と同じ名前の操作を、GitLab の REST API（v4）で組む。操作は名前で限り、
 * URL・クエリ・ヘッダはここで組む。書くのは Commits API（`POST repository/commits` の `actions`）の 1 コミットと、
 * 「始める」のブランチの作成・Draft の MR、discussion への返信と解決、issue の作成、MR へのメモだけ。
 *
 * GitLab の Commits API には「先頭がこの sha のときだけ」の指定が無い。書く直前に先頭を読み、違えば書かずに
 * 「動いた」（409）で返す。それでも間に入った書き込みは防げないので、答えにコミットの親（`parent_ids[0]`）を返し、
 * 書く流れ（`write.ts`）が事後に確かめて、違えば判定し直し、元に戻す。ccnavi の書き込みどうしの競合の検出は、実際の GitLab で確かめるまで未実装。
 *
 * MR のスレッドとレビュー（`reviewCopy`）は、手元の `ccnavi-review.sh` の `find_mr`・`threads`・`reviews`
 * （GitLab の枝）と同じ問い合わせ・同じページの切り方・同じ欄の省き方（jq の `//`・`tostring`、型は jq と同じく
 * そのまま残す）で組む。同じ見本（test/fixtures/host/gitlab/。手で組んだもの）から同じ結果になることを試験が見る。
 *
 * 書き込みの update・delete には `last_commit_id`（そのファイルを最後に変えたコミット）を付け、
 * 同じファイルを他人が変えていれば GitLab が断る（ファイル単位の比較つきの書き込み）。MR は同じプロジェクトから
 * 出たもの（`source_project_id`）だけを拾う。転送は追わない（`redirect: "error"`）。
 */
import type { Host } from "./hosts.js";
import {
  checkBranch,
  checkMarker,
  checkNumber,
  checkOid,
  checkTitle,
  checkWebUrl,
  checkWriteText,
  markerLine,
  replyOpsOf,
  checkPath,
  fetchNoRedirect,
  HostError,
  RETRY_LIMIT_SECONDS,
  type BlobText,
  type Client,
  type Fetch,
  type FindResult,
  type IssueRef,
  type Missing,
  type PathObject,
  type PullReview,
  type RecentRef,
  type ReviewThread,
  type ThreadAbility,
  type TreeEntry,
  type WriteInfo,
  BLOB_BATCH,
} from "./github.js";

export type { Client };

/** 名前空間（グループ。入れ子の `group/sub` も）とプロジェクトの名前の 1 段 */
const SEGMENT = /^[A-Za-z0-9_.-]{1,100}$/;
const USERNAME = /^[A-Za-z0-9_.-]{1,100}$/;

/** GitLab の owner（名前空間）。`group` か入れ子の `group/sub`。どの段も `.`・`..` でない */
export function checkNamespace(value: unknown, what = "owner"): string {
  if (typeof value !== "string" || value.length > 255 || value.split("/").some((p) => !SEGMENT.test(p) || p === "." || p === "..")) {
    throw new HostError(`${what} の形が正しくありません：${String(value)}`);
  }
  return value;
}

function project(owner: string, repo: string): string {
  const full = `${checkNamespace(owner)}/${checkNamespace(repo, "repo")}`;
  if (repo.includes("/")) throw new HostError(`repo の形が正しくありません：${repo}`);
  return `/projects/${encodeURIComponent(full)}`;
}

function headers(client: Client, json = false): Record<string, string> {
  return { Accept: "application/json", "PRIVATE-TOKEN": client.token, ...(json ? { "Content-Type": "application/json" } : {}) };
}

type Res = Awaited<ReturnType<Fetch>>;

/** 権限が足りないときの文面。decide のホスト書き込みは api スコープ（read_api では書けない）と、書けるロールを言う */
export function lacking(decide: boolean): string {
  return decide
    ? "PAT に api スコープ（read_api では書けません）が要ります。プロジェクトのロールも、返信・解決・issue の作成ができる Developer 以上にしてください"
    : "PAT の権限（api スコープ・プロジェクトのメンバー）を見直してください";
}

function header(res: Res, name: string): string {
  return res.headers?.get(name)?.trim() ?? "";
}

async function pause(client: Client, seconds: number): Promise<void> {
  const sleep = client.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  await sleep(seconds * 1000);
}

/** 1 回の呼び出し。401 は差し替えを促し、429（レート制限）は Retry-After が短ければ 1 回だけ待ち直す */
async function send(client: Client, url: string, init: Parameters<Fetch>[1], what: string, decide = false): Promise<Res> {
  for (let attempt = 0; ; attempt += 1) {
    // 別のホストへの転送を追わない（PAT を載せた要求を通信先の一覧の外へ出さない）
    const res = await fetchNoRedirect(client, url, init, what);
    if (res.status === 401) throw new HostError("PAT で認証できません（401）。設定画面で差し替えてください", 401);
    if (res.status === 429) {
      const after = header(res, "retry-after");
      const seconds = /^\d+$/.test(after) ? Number(after) : null;
      if (seconds !== null && seconds <= RETRY_LIMIT_SECONDS && attempt === 0) {
        await pause(client, seconds);
        continue;
      }
      throw new HostError(`GitLab のレート制限にかかりました（429${seconds !== null ? `、${seconds} 秒待つよう求められました` : ""}）。少し待ってからボードを更新してください`, 429);
    }
    if (res.status === 403) {
      throw new HostError(`GitLab が拒否しました（403）：${what}。${lacking(decide)}`, 403);
    }
    return res;
  }
}

async function get(client: Client, path: string): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "GET", headers: headers(client) }, `GET ${path}`);
  if (res.status === 404) return { status: 404, body: null };
  if (!res.ok) throw new HostError(`GitLab が ${res.status} を返しました：GET ${path}`, res.status);
  return { status: res.status, body: await res.json() };
}

async function post(client: Client, path: string, body: unknown, decide = false): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "POST", headers: headers(client, true), body: JSON.stringify(body) }, `POST ${path}`, decide);
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { status: res.status, body: parsed };
}

async function put(client: Client, path: string, body: unknown): Promise<{ status: number; body: unknown }> {
  client.counter.rest += 1;
  const res = await send(client, `${client.host.api}${path}`, { method: "PUT", headers: headers(client, true), body: JSON.stringify(body) }, `PUT ${path}`, true);
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
    // 404 や配列でない答えを「無い」と読むと、見落として通してしまう
    if (status === 404) throw new HostError(`${what} を取得できません（404）`, 404);
    if (!Array.isArray(body)) throw new HostError(`${what} の応答が配列ではありません`);
    all.push(...body);
    if (body.length < 100) return all;
    if (page + 1 > limit) throw new HostError(`${what} が多すぎて読み切れません`);
  }
}

// ---- 読み取り ----------------------------------------------------------------------

/** プロジェクトのデフォルトブランチ（統合先を設定していないときの既定） */
export async function repoInfo(client: Client, owner: string, repo: string): Promise<{ defaultBranch: string }> {
  const { status, body } = await get(client, project(owner, repo));
  if (status === 404) throw new HostError(`プロジェクト ${owner}/${repo} が見つかりません（存在しないか、PAT の権限が不足しています）`, 404);
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

/** `since` より後に先頭が動いたブランチ（表示用）。GitLab の `repository/branches` を読む */
export async function recentRefs(client: Client, owner: string, repo: string, since: Date): Promise<RecentRef[]> {
  const out: RecentRef[] = [];
  for (let page = 1; page <= REF_PAGES; page += 1) {
    const { status, body } = await get(client, `${project(owner, repo)}/repository/branches?per_page=100&page=${page}`);
    if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} のブランチを取得できません`);
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

/** tree を読むページの上限（100 × 50 = 5,000 件）。超えたら取り切れないので止める */
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
    if (!Array.isArray(body)) throw new HostError("tree の応答が配列ではありません");
    all.push(...(body as TreeItem[]));
    if (body.length < 100) return all;
    if (page >= TREE_PAGES) throw new HostError("置き場の tree が大きすぎて取得しきれません。Chrome では読み取れません");
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
    if (!full.startsWith(prefix)) throw new HostError(`tree の応答に置き場の外のパスが含まれています：${full}`);
    out.push({ path: full.slice(prefix.length), sha: checkOid(e.id), mode: typeof e.mode === "string" ? e.mode : "" });
  }
  return out;
}

/** BOM を除かない（除くと、元に戻すコミットで戻すバイト列が元と変わる） */
const DECODER = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

function fromBase64(text: string): Uint8Array {
  const bin = atob(text.replace(/\s+/g, ""));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/**
 * blob を sha で 1 件ずつ取る（`repository/blobs/:sha`。1 件ずつ。GraphQL でまとめて取れるかは実際の GitLab で確かめていない）。
 * NUL を含むか UTF-8 として読めなければバイナリ。大きさが `size` と合わなければ止める
 */
export async function blobs(client: Client, owner: string, repo: string, oids: readonly string[]): Promise<Record<string, BlobText>> {
  if (oids.length > BLOB_BATCH) throw new HostError(`blob は 1 回に ${BLOB_BATCH} 件まで`);
  const out: Record<string, BlobText> = {};
  for (const oid of oids) {
    const { status, body } = await get(client, `${project(owner, repo)}/repository/blobs/${checkOid(oid)}`);
    if (status === 404) throw new HostError(`blob ${oid} を取れませんでした`);
    const b = (body ?? {}) as { content?: unknown; encoding?: unknown; size?: unknown };
    if (b.encoding !== "base64" || typeof b.content !== "string") throw new HostError(`blob ${oid} の応答を解釈できません`);
    const bytes = fromBase64(b.content);
    if (typeof b.size === "number" && bytes.length !== b.size) throw new HostError(`blob ${oid} の本文が大きさと合いません（取り切れていません）`);
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

/** PAT の持ち主のアカウント名（レビュー済みのマーカーの `actor`） */
export async function viewer(client: Client): Promise<string> {
  const { status, body } = await get(client, "/user");
  const name = (body as { username?: unknown } | null)?.username;
  if (status === 404 || typeof name !== "string" || !USERNAME.test(name)) throw new HostError("PAT の所有者を取得できません");
  return name;
}

/**
 * PAT の期限（`GET /personal_access_tokens/self` の `expires_at`、`YYYY-MM-DD`）。その日の終わり（UTC）を ISO で返す。
 * 読めなければ空（project access token で返るかは実際の GitLab で確かめていない）
 */
export async function tokenExpiry(client: Client): Promise<string> {
  const { status, body } = await get(client, "/personal_access_tokens/self");
  const at = (body as { expires_at?: unknown } | null)?.expires_at;
  if (status !== 200 || typeof at !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(at)) return "";
  const t = Date.parse(`${at}T23:59:59Z`);
  return Number.isNaN(t) ? "" : new Date(t).toISOString();
}

// ---- 書いた後の確かめ ---------------------------------------------------------

/** コミットの親（書いた後の確かめ） */
export async function commitParents(client: Client, owner: string, repo: string, sha: string): Promise<string[]> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/commits/${checkOid(sha)}`);
  if (status === 404) return [];
  const parents = (body as { parent_ids?: unknown } | null)?.parent_ids;
  return Array.isArray(parents) ? parents.map((p) => checkOid(p)) : [];
}

// ---- MR ------------------------------------------------------------------------

/** プロジェクトの数の id（MR が同じプロジェクトから出たかを見る） */
async function projectId(client: Client, owner: string, repo: string): Promise<number> {
  const { status, body } = await get(client, project(owner, repo));
  const id = (body as { id?: unknown } | null)?.id;
  if (status === 404 || typeof id !== "number") throw new HostError(`プロジェクト ${owner}/${repo} の id を取得できません`, status);
  return id;
}

type MrItem = { iid?: unknown; web_url?: unknown; source_project_id?: unknown };

/** 開いた MR のうち、このプロジェクトのブランチから出たもの（フォークの同じ名前のブランチの MR を拾わない） */
async function openMrs(client: Client, owner: string, repo: string, branch: string): Promise<MrItem[]> {
  const pid = await projectId(client, owner, repo);
  // API は source_project_id で絞れないので、全ページ（20 ページまで）を読んでから絞る（1 ページ目がフォークで埋まっても
  // このプロジェクトの MR を外さない。sh の find_mr と同じ）
  const all = await pages(client, `${project(owner, repo)}/merge_requests?state=opened&source_branch=${encodeURIComponent(checkBranch(branch))}`, THREAD_PAGES, `${branch} のマージリクエスト一覧`);
  return (all as MrItem[]).filter((m) => m && m.source_project_id === pid);
}

/**
 * 親のブランチの開いた MR（sh の `find_mr` と同じ: `merge_requests?state=opened&source_branch=<branch>` のうち、
 * `source_project_id` がこのプロジェクトのものの先頭）
 */
export async function openMr(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; url: string } | null> {
  const first = (await openMrs(client, owner, repo, branch))[0];
  if (!first) return null;
  if (typeof first.iid !== "number" || !Number.isInteger(first.iid)) throw new HostError("マージリクエストの番号を取得できません");
  return { number: first.iid, url: String(alt(first.web_url, "")) };
}

/** 親のブランチの開いた MR に付いている Approve（`merge_requests/:iid/approvals` の `approved_by`） */
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

type Note = { id?: unknown; type?: unknown; system?: unknown; body?: unknown; created_at?: unknown; resolvable?: unknown; resolved?: unknown; author?: { id?: unknown; username?: unknown } | null; position?: { new_path?: unknown; new_line?: unknown } | null };

/** 本文が ccnavi の目印で始まるか（jq の `marked` と同じ。文字列でない本文は目印ではない） */
function marked(n: Note | undefined): boolean {
  const body = alt(n?.body, "");
  return typeof body === "string" && body.startsWith("<!-- ccnavi:");
}

/** ノートの書き手の id（取れなければ空） */
function writer(n: Note | undefined): string {
  return tostring(alt(n?.author?.id, ""));
}

/**
 * 機構自身のスレッドと見てよいノートの並びか。目印で始まるだけでは足りず、書き手が代表のノートの書き手と同じで
 * なければならない（依頼の投稿者のスレッドに、他人が目印で始まる返信を足して、未解決を隠せない）。
 * 代表の書き手が依頼の投稿者かどうかは、呼ぶ側（Python）が依頼の記録と比べる
 */
function ownThread(notes: Note[], first: Note): boolean {
  const by = writer(first);
  return by !== "" && notes.every((n) => marked(n) && writer(n) === by);
}

/**
 * MR のスレッド（sh の `threads` の GitLab の枝と同じ読み方）。判定に使うノートは、discussion のノートから
 * `system: true` を除いたもの（空なら discussion ごと除く）。対象は、そのノートが 1 つでも解決できる discussion。
 * 解決済みは、解決できるノートが全部解決しているとき。代表のノート（本文・位置・書き手・url）は、本文が
 * ccnavi の目印で始まらない最初のノート（無ければ先頭）。ユーザが依頼コメントに返信したスレッドは返信が代表になる。
 * url は MR の URL + `#note_<代表のノートの id>`。`own` は、ノートの本文が全部目印で始まり、書き手が全部代表と同じときだけ真
 */
export async function discussions(client: Client, owner: string, repo: string, number: number, mrUrl: string): Promise<ReviewThread[]> {
  const all = await pages(client, `${project(owner, repo)}/merge_requests/${number}/discussions`, THREAD_PAGES, `マージリクエスト !${number} のスレッド`);
  const out: ReviewThread[] = [];
  for (const d of all as { id?: unknown; notes?: unknown }[]) {
    const given = (Array.isArray(alt(d.notes, [])) ? (alt(d.notes, []) as Note[]) : []) ?? [];
    const notes = given.filter((n) => n?.system !== true);
    if (notes.length === 0) continue;
    const resolvable = notes.filter((n) => alt(n?.resolvable, false) !== false);
    if (resolvable.length === 0) continue;
    const first = notes.find((n) => !marked(n)) ?? notes[0] ?? {};
    const pos = first.position ?? null;
    const last = notes[notes.length - 1] ?? {};
    // jq と同じく、欄の型はそのまま残す（文字列の行番号を数に直さない）。書き手は username でなく id
    out.push({
      id: tostring(d.id),
      resolved: resolvable.every((n) => alt(n.resolved, false) !== false),
      url: `${mrUrl}#note_${tostring(first.id)}`,
      path: alt(pos?.new_path, "") as string,
      line: alt(pos?.new_line, 0) as number,
      body: alt(first.body, "") as string,
      created_at: alt(first.created_at, "") as string,
      author: tostring(alt(first.author?.id, "")),
      own: ownThread(notes, first),
      last_id: tostring(alt(last.id, "")),
      last_at: alt(last.created_at, "") as string,
      last_author: String(alt(last.author?.username, "")),
    });
  }
  return out;
}

/** MR のレビュアーの状態（sh の `reviews` の GitLab の枝と同じ）。`requested_changes` を CHANGES_REQUESTED にそろえる */
export async function reviewers(client: Client, owner: string, repo: string, number: number, mrUrl: string): Promise<PullReview[]> {
  const all = await pages(client, `${project(owner, repo)}/merge_requests/${number}/reviewers`, THREAD_PAGES, `マージリクエスト !${number} のレビュアー`);
  return (all as { state?: unknown; updated_at?: unknown; created_at?: unknown; user?: { id?: unknown; username?: unknown } | null }[]).map((r) => {
    const raw = alt(r.state, "");
    // jq の ascii_upcase は文字列でなければ失敗する（sh は取得した結果を組めずに止まる）。同じく止める
    if (r.state !== "requested_changes" && typeof raw !== "string") throw new HostError(`マージリクエスト !${number} のレビュアーの state が文字列ではありません`);
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

/** 親のブランチの MR のスレッドとレビューを取得した結果（`ccnavi-review.sh fetch` の GitLab の枝と同じ形） */
export async function reviewCopy(client: Client, owner: string, repo: string, branch: string): Promise<GitLabReviewCopy> {
  const mr = await openMr(client, owner, repo, branch);
  if (mr === null) throw new HostError(`親のブランチ ${branch} に対応する、オープン状態のマージリクエストがありません`);
  const threads = await discussions(client, owner, repo, mr.number, mr.url);
  const reviews = await reviewers(client, owner, repo, mr.number, mr.url);
  return { host: "gitlab", mr, threads, reviews, fetched_at: new Date().toISOString() };
}

/**
 * compare の変更の一覧を打ち切られたとみなす件数。GitLab の差分の件数の上限（`diff_max_files`）は既定が 1000 で、
 * インスタンスの管理者が 500 まで下げられる。その最小値より下の 450 件以上で、打ち切られたとみなす（実際の
 * インスタンスの値は確かめていない）
 */
export const COMPARE_FILES_NEAR = 450;

/**
 * 依頼時の先頭 `base` から今の先頭 `head` までに変わったパス。`base` が `head` の祖先でなければ
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
  // 行数などで折りたたまれた・大きすぎる差分があれば、一覧が揃っていると言えない（動いたと数える）
  const diffs = res.diffs as { old_path?: unknown; new_path?: unknown; collapsed?: unknown; too_large?: unknown }[];
  if (diffs.some((d) => d?.collapsed === true || d?.too_large === true)) return { base: b, head: h, files: null };
  const files: string[] = [];
  for (const d of diffs) {
    if (typeof d.new_path === "string") files.push(d.new_path);
    if (typeof d.old_path === "string" && d.old_path !== d.new_path) files.push(d.old_path);
  }
  return { base: b, head: h, files };
}

/** パスの履歴を読む件数の上限。届けば履歴が途切れていて、最初のコミットを言えない */
export const PATH_HISTORY_LIMIT = 100;

/**
 * そのパスを最初に加えたコミットの時刻（epoch 秒）。ブランチ `ref` でそのパスに触れたコミットのうち
 * 最も古いものを取る。履歴が上限に届いた・読めない・日付を読めないときは null（確定しない）。
 */
export async function pathCommitTime(client: Client, owner: string, repo: string, ref: string, path: string): Promise<{ time: number | null }> {
  const r = checkBranch(ref);
  const p = checkPath(path);
  const { status, body } = await get(
    client,
    `${project(owner, repo)}/repository/commits?ref_name=${encodeURIComponent(r)}&path=${encodeURIComponent(p)}&per_page=${PATH_HISTORY_LIMIT}`,
  );
  if (status === 404 || !Array.isArray(body) || body.length === 0 || body.length >= PATH_HISTORY_LIMIT) return { time: null };
  const oldest = body[body.length - 1] as { committed_date?: unknown };
  const ms = typeof oldest?.committed_date === "string" ? Date.parse(oldest.committed_date) : Number.NaN;
  return { time: Number.isFinite(ms) ? Math.floor(ms / 1000) : null };
}

// ---- 書き込み -----------------------------------------------------------------

/** GitLab が書き込みを断った（`last_commit_id` が違う・書き換えるものが無い など）。何も書いていない。元に戻すコミットならユーザの対応に切り替える */
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
  if (status === 404) throw new HostError(`${path} が ${ref.slice(0, 7)} に存在しません（書き換え・削除の対象がありません）`, HOST_REFUSED);
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
 * `force` は付けない。答えのコミットの親を返し、書く流れが事後に確かめる。
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
  if (now !== checkOid(expected)) throw new HostError(`親のブランチ ${name} の先頭が、取得した時点から変わっています（反映前に更新されました）`, HOST_MOVED);
  // update・delete には、そのファイルを最後に変えたコミットを付ける。呼び手が知っていればそれ（元に戻すコミットでは自分の
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
    throw new HostError(`GitLab が書き込みを拒否しました（${res.status}）：${typeof why === "string" ? why : "?"}`, HOST_REFUSED);
  }
  if (res.status < 200 || res.status >= 300) throw new HostError(`GitLab が ${res.status} を返しました：POST repository/commits`, res.status);
  const c = (res.body ?? {}) as { id?: unknown; parent_ids?: unknown };
  const parents = Array.isArray(c.parent_ids) ? c.parent_ids : [];
  return { oid: checkOid(c.id), parent: parents.length > 0 ? checkOid(parents[0]) : "" };
}

// ---- 「始める」 ----------------------------------------------------------------------

export interface Issue {
  readonly number: number;
  readonly title: string;
  readonly url: string;
}

/** 開いた issue（新しい順に 50 件）。「始める」の一覧 */
export async function issues(client: Client, owner: string, repo: string): Promise<Issue[]> {
  const { status, body } = await get(client, `${project(owner, repo)}/issues?state=opened&order_by=created_at&sort=desc&per_page=50`);
  if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} の Issue を取得できません`, status);
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
    if (status === 404 || !Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} のブランチを取得できません`);
    for (const b of body as { name?: unknown }[]) if (typeof b.name === "string") all.push(b.name);
    if (body.length < 100) return all;
    if (page >= BRANCH_PAGES) throw new HostError("ブランチが多すぎて読み切れません。「始める」は手元で行ってください");
  }
}

/** ブランチを `sha` から作る（`POST repository/branches`）。既にあれば断られる（400） */
export async function createBranch(client: Client, owner: string, repo: string, name: string, sha: string): Promise<string> {
  const q = new URLSearchParams({ branch: checkBranch(name), ref: checkOid(sha) });
  const res = await post(client, `${project(owner, repo)}/repository/branches?${q.toString()}`, {});
  if (res.status < 200 || res.status >= 300) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitLab がブランチを作りませんでした（${res.status}）：${typeof why === "string" ? why : "?"}`, res.status);
  }
  const made = (res.body ?? {}) as { name?: unknown; commit?: { id?: unknown } };
  if (made.name !== name) throw new HostError("作ったブランチの名前が、頼んだ名前と違います");
  return checkOid(made.commit?.id);
}

export type { Host };

// ---- 「始める」。既存の MR の採用と、新しい MR の作成 ----------------------------------

/** 開いた MR を読むページの上限（100 × 20）。超えたら読み切れないので止める */
export const MR_PAGES = 20;

/** 採用を考える MR 1 本（`github.MrInfo` と同じ形） */
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

/** 開いた MR の全部。フォークから出たものは `fork` を立てる（元ブランチがこのプロジェクトに無い）。読むだけ */
export async function listOpenMrs(client: Client, owner: string, repo: string): Promise<MrInfo[]> {
  const pid = await projectId(client, owner, repo);
  const all = await pages(client, `${project(owner, repo)}/merge_requests?state=opened`, MR_PAGES, "開いたマージリクエスト一覧");
  const out: MrInfo[] = [];
  for (const m of all as { iid?: unknown; source_branch?: unknown; target_branch?: unknown; title?: unknown; description?: unknown; web_url?: unknown; source_project_id?: unknown }[]) {
    if (!m || typeof m.iid !== "number" || typeof m.source_branch !== "string") continue;
    out.push({
      number: m.iid,
      branch: m.source_branch,
      state: "open",
      url: String(alt(m.web_url, "")),
      title: String(alt(m.title, "")),
      body: String(alt(m.description, "")),
      base: String(alt(m.target_branch, "")),
      fork: m.source_project_id !== pid,
    });
  }
  return out;
}

/** 同じブランチ名のマージ済み MR（`state=merged&source_branch=<branch>`）。読むだけ */
export async function listMergedMrs(client: Client, owner: string, repo: string, branch: string): Promise<{ number: number; url: string }[]> {
  const all = await pages(client, `${project(owner, repo)}/merge_requests?state=merged&source_branch=${encodeURIComponent(checkBranch(branch))}`, MR_PAGES, `${branch} のマージ済みマージリクエスト一覧`);
  const out: { number: number; url: string }[] = [];
  for (const m of all as { iid?: unknown; web_url?: unknown }[]) {
    if (m && typeof m.iid === "number") out.push({ number: m.iid, url: String(alt(m.web_url, "")) });
  }
  return out;
}

/**
 * GitLab は差分のないブランチからも MR を作れるので、初期コミットは積まない（ブランチを作った先頭のまま返す）。
 * `github.startCommit` と呼び方を揃えるための関数。
 */
export async function startCommit(client: Client, owner: string, repo: string, name: string, _message: string): Promise<string> {
  const { status, body } = await get(client, `${project(owner, repo)}/repository/branches/${encodeURIComponent(checkBranch(name))}`);
  const id = (body as { commit?: { id?: unknown } } | null)?.commit?.id;
  if (status === 404) throw new HostError(`ブランチ ${name} が見つかりません`, 404);
  return checkOid(id);
}

/** Draft の MR を作る（題の頭に `Draft: ` を付ける）。番号（iid）と URL を返す */
export async function createDraftMr(client: Client, owner: string, repo: string, head: string, base: string, title: string, body: string): Promise<{ number: number; url: string }> {
  const res = await post(client, `${project(owner, repo)}/merge_requests`, { source_branch: checkBranch(head), target_branch: checkBranch(base), title: `Draft: ${title}`, description: body });
  if (res.status < 200 || res.status >= 300) {
    const why = (res.body as { message?: unknown } | null)?.message;
    throw new HostError(`GitLab が Draft のマージリクエストを作りませんでした（${res.status}）: ${typeof why === "string" ? why : "?"}`, res.status);
  }
  const made = (res.body ?? {}) as { iid?: unknown; web_url?: unknown };
  if (typeof made.iid !== "number") throw new HostError("作ったマージリクエストの番号を取得できません");
  return { number: made.iid, url: String(alt(made.web_url, "")) };
}

// ---- decide のホスト書き込み。discussion への返信と解決、issue の作成と検索、MR へのメモ -----------

const DISCUSSION_ID = /^[0-9a-f]{8,64}$/;

/** discussion の id（GitLab は 40 桁の 16 進）。経路に使うので、この字に限る */
export function checkDiscussionId(value: unknown): string {
  if (typeof value !== "string" || !DISCUSSION_ID.test(value)) throw new HostError(`discussion の id の形が正しくない: ${String(value)}`);
  return value;
}

function mrPath(owner: string, repo: string, number: number): string {
  return `${project(owner, repo)}/merge_requests/${checkNumber(number, "MR の番号")}`;
}

function failure(what: string, res: { status: number; body: unknown }): HostError {
  const why = (res.body as { message?: unknown; error?: unknown } | null)?.message ?? (res.body as { error?: unknown } | null)?.error;
  return new HostError(`${what}（${res.status}）: ${typeof why === "string" ? why : "?"}`, res.status);
}

/** discussion へ返信する（`POST …/discussions/:id/notes`） */
export async function replyDiscussion(client: Client, owner: string, repo: string, number: number, discussion: string, body: string): Promise<{ url: string }> {
  const res = await post(client, `${mrPath(owner, repo, number)}/discussions/${checkDiscussionId(discussion)}/notes`, { body: checkWriteText(body, "返信の本文") }, true);
  if (res.status === 404) throw new HostError("返信できませんでした（404）。discussion が見えないか、PAT のロールが足りません（Developer 以上・api スコープ）", 404);
  if (res.status < 200 || res.status >= 300) throw failure("GitLab が返信を受け付けなかった", res);
  const id = (res.body as { id?: unknown } | null)?.id;
  if (typeof id !== "number") throw new HostError("GitLab が作ったノートの id を返さなかった");
  return { url: "" };
}

/** discussion を解決済みにする（`PUT …/discussions/:id` の `resolved=true`）。解決済みになったことまで確かめる */
export async function resolveDiscussion(client: Client, owner: string, repo: string, number: number, discussion: string): Promise<void> {
  const res = await put(client, `${mrPath(owner, repo, number)}/discussions/${checkDiscussionId(discussion)}`, { resolved: true });
  if (res.status === 404) throw new HostError("解決できませんでした（404）。discussion が見えないか、PAT のロールが足りません（Developer 以上・api スコープ）", 404);
  if (res.status < 200 || res.status >= 300) throw failure("GitLab が discussion を解決しなかった", res);
  const notes = (res.body as { notes?: unknown } | null)?.notes;
  const resolvable = (Array.isArray(notes) ? (notes as Note[]) : []).filter((n) => n?.system !== true && alt(n?.resolvable, false) !== false);
  if (resolvable.length === 0 || !resolvable.every((n) => alt(n.resolved, false) !== false)) {
    throw new HostError("GitLab の応答が、discussion の解決済みを示していない");
  }
}

/** issue を作る（`POST projects/:id/issues`）。題と本文だけを送る。assignee・labels・milestone は入れない */
export async function createIssue(client: Client, owner: string, repo: string, title: string, body: string): Promise<IssueRef> {
  const res = await post(client, `${project(owner, repo)}/issues`, { title: checkTitle(title), description: checkWriteText(body, "issue の本文") }, true);
  if (res.status === 404) throw new HostError(`issue を作れませんでした（404）。プロジェクト ${owner}/${repo} が見えないか、PAT のロールが足りません（Reporter 以上・api スコープ）`, 404);
  if (res.status < 200 || res.status >= 300) throw failure("GitLab が issue を作らなかった", res);
  const made = (res.body ?? {}) as { iid?: unknown; web_url?: unknown };
  if (typeof made.iid !== "number" || !Number.isInteger(made.iid) || made.iid <= 0) throw new HostError("作った issue の番号を取得できません");
  return { number: made.iid, url: checkWebUrl(client, made.web_url) };
}

/** 検索で見る issue のページ数（100 件ずつ、新しい順）。検索は best effort */
export const FIND_PAGES = 3;

/** 操作 ID の印を本文の 1 行目に持つ issue を探す（`github.findIssueByMarker` と同じ約束。404 は「無い」、ほかの失敗は投げる） */
export async function findIssueByMarker(client: Client, owner: string, repo: string, marker: string): Promise<FindResult> {
  const m = checkMarker(marker);
  const me = await viewerId(client);
  for (let page = 1; page <= FIND_PAGES; page += 1) {
    const { status, body } = await get(client, `${project(owner, repo)}/issues?state=all&order_by=created_at&sort=desc&per_page=100&page=${page}`);
    if (status === 404) return { issue: null, truncated: false };
    if (!Array.isArray(body)) throw new HostError(`プロジェクト ${owner}/${repo} の Issue の一覧の応答が配列でない`);
    for (const i of body as { iid?: unknown; web_url?: unknown; description?: unknown; author?: { id?: unknown } | null }[]) {
      if (markerLine(i.description) !== m) continue;
      if (typeof i.author?.id !== "number" || i.author.id !== me) continue;
      if (typeof i.iid !== "number" || !Number.isInteger(i.iid) || i.iid <= 0) continue;
      return { issue: { number: i.iid, url: checkWebUrl(client, i.web_url) }, truncated: false };
    }
    if (body.length < 100) return { issue: null, truncated: false };
  }
  return { issue: null, truncated: true };
}

/** issue のメモの本文（続きのコメントが付いているかを見る）。500 件を超えたら読み切れないので投げる */
export async function issueComments(client: Client, owner: string, repo: string, number: number): Promise<string[]> {
  const out: string[] = [];
  for (let page = 1; page <= 5; page += 1) {
    const { status, body } = await get(client, `${project(owner, repo)}/issues/${checkNumber(number, "issue の番号")}/notes?sort=asc&per_page=100&page=${page}`);
    if (status === 404 || !Array.isArray(body)) throw new HostError(`issue !${number} のメモを読めません`, status === 404 ? 404 : 0);
    for (const n of body as { body?: unknown }[]) out.push(typeof n.body === "string" ? n.body : "");
    if (body.length < 100) return out;
  }
  throw new HostError(`issue #${number} のメモが多すぎて読み切れません`);
}

/** PAT の持ち主の数の id */
async function viewerId(client: Client): Promise<number> {
  const { status, body } = await get(client, "/user");
  const id = (body as { id?: unknown } | null)?.id;
  if (status === 404 || typeof id !== "number") throw new HostError("PAT の所有者を取得できません");
  return id;
}

/** MR にメモ（コメント）を付ける（`POST …/merge_requests/:iid/notes`） */
export async function mrNote(client: Client, owner: string, repo: string, number: number, body: string): Promise<{ url: string }> {
  const res = await post(client, `${mrPath(owner, repo, number)}/notes`, { body: checkWriteText(body, "コメントの本文") }, true);
  if (res.status === 404) throw new HostError("コメントを付けられませんでした（404）。MR が見えないか、PAT のロールが足りません（api スコープ）", 404);
  if (res.status < 200 || res.status >= 300) throw failure("GitLab がコメントを受け付けなかった", res);
  return { url: "" };
}

/** issue にメモ（コメント）を付ける（`POST …/issues/:iid/notes`。issue の続きのコメント） */
export async function issueNote(client: Client, owner: string, repo: string, number: number, body: string): Promise<{ url: string }> {
  const res = await post(client, `${project(owner, repo)}/issues/${checkNumber(number, "issue の番号")}/notes`, { body: checkWriteText(body, "コメントの本文") }, true);
  if (res.status === 404) throw new HostError("コメントを付けられませんでした（404）。issue が見えないか、PAT のロールが足りません（api スコープ）", 404);
  if (res.status < 200 || res.status >= 300) throw failure("GitLab がコメントを受け付けなかった", res);
  return { url: "" };
}

/** ロールの数値（Developer = 30。Reporter = 20） */
const DEVELOPER = 30;
const REPORTER = 20;

/**
 * 書く前の確かめの材料。discussion ごとに、解決できる種類か（`resolvable` なノートがあるか、差分への指摘か）を読み、
 * PAT のスコープ（`personal_access_tokens/self` の `scopes` に `api` があるか。プロジェクトのトークンなどで読めなければ unknown）、
 * プロジェクトのロール（Developer 未満は解決できない）、Issues が使えるかを見る。
 */
export async function writeInfo(client: Client, owner: string, repo: string, number: number): Promise<WriteInfo> {
  const all = await pages(client, `${mrPath(owner, repo, number)}/discussions`, THREAD_PAGES, `マージリクエスト !${number} のスレッド`);
  const threads: ThreadAbility[] = [];
  for (const d of all as { id?: unknown; notes?: unknown }[]) {
    const notes = (Array.isArray(d.notes) ? (d.notes as Note[]) : []).filter((n) => n?.system !== true);
    if (notes.length === 0) continue;
    const replyOps = replyOpsOf(notes.map((n) => (typeof n?.body === "string" ? n.body : "")));
    const resolvable = notes.filter((n) => alt(n?.resolvable, false) !== false);
    if (resolvable.length === 0) {
      threads.push({ id: tostring(d.id), kind: "not-resolvable", canReply: true, canResolve: false, replyOps });
      continue;
    }
    const onDiff = resolvable.some((n) => n.type === "DiffNote" || (n.position !== null && n.position !== undefined));
    threads.push({ id: tostring(d.id), kind: onDiff ? "ok" : "non-diff", canReply: true, canResolve: onDiff, replyOps });
  }
  const missing: Missing[] = [];
  const unknown: string[] = [];
  const self = await get(client, "/personal_access_tokens/self");
  const scopes = (self.body as { scopes?: unknown } | null)?.scopes;
  if (self.status === 200 && Array.isArray(scopes)) {
    if (!scopes.includes("api")) missing.push({ need: "api スコープ（read_api では書けません）", for: "all" });
  } else {
    unknown.push("api スコープ");
  }
  const proj = await get(client, project(owner, repo));
  const p = (proj.body ?? {}) as {
    permissions?: { project_access?: { access_level?: unknown } | null; group_access?: { access_level?: unknown } | null } | null;
    issues_enabled?: unknown;
    issues_access_level?: unknown;
    archived?: unknown;
  };
  const level = Math.max(...[p.permissions?.project_access?.access_level, p.permissions?.group_access?.access_level].map((x) => (typeof x === "number" ? x : 0)));
  if (level === 0) unknown.push("プロジェクトのロール（Developer 以上）");
  else if (level < REPORTER) missing.push({ need: "プロジェクトのロール（Reporter 以上。スレッドの解決は Developer 以上）", for: "all" });
  else if (level < DEVELOPER) missing.push({ need: "プロジェクトのロール（スレッドの解決は Developer 以上）", for: "resolve" });
  if (p.issues_enabled === false || p.issues_access_level === "disabled") missing.push({ need: "プロジェクトの Issues の有効化", for: "issue" });
  if (p.archived === true) missing.push({ need: "プロジェクトのアーカイブの解除（読み取り専用です）", for: "all" });
  return { host: "gitlab", threads: level > 0 && level < DEVELOPER ? threads.map((t) => ({ ...t, canResolve: false })) : threads, missing, unknown };
}
