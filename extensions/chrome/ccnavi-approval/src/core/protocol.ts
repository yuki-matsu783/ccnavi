/**
 * 画面と service worker のあいだの約束。
 *
 * PAT を読むのは service worker だけ。画面は PAT を受け取らず、ホストの API は名前で限った
 * 操作を頼む。PAT を書く・消すのは設定画面からだけ受ける。送り手は拡張の自分のページに限り、
 * 他の拡張・ウェブページからの呼び出し（`onMessageExternal`）は受けない。
 *
 * レビュー済みのために MR のスレッドとレビュー（`reviewCopy`）と、依頼の後の変更の
 * 一覧（`compareFiles`）を読む。どちらも読むだけ。
 *
 * 書く操作 `commit` を受ける。受けるのはボードからだけで、設定画面で登録したリポジトリだけに
 * 書く。書く先が予約の名前（`main`・`master`・`develop`・`release*`）か統合先の名前（ボードの値を信頼せず
 * 自分で引く）なら断り、書くパスは置き場（既定に固定。統合先の `.claude/settings.json` は読まない）の下に限る
 * （Python も同じ検査をする。画面で XSS が起きても書く先を広げないための二重の確認）。PAT の期限は、
 * GitHub なら応答ヘッダから、GitLab なら `GET /personal_access_tokens/self` から読んで記録し、画面へは期限だけを返す。
 * GitLab に問い合わせるのは 1 日 1 回。
 *
 * GitLab（`gitlab.ts`）も同じ操作の名前で受ける。`commit` の答えは `{oid, parent}` で、`parent` は
 * ホストが実際に積んだ先。GitHub では `expectedHeadOid` で読んだ先頭に決まり、GitLab では事後に確かめる。
 * 「始める」のために、読む操作 `issues` と、ボードからだけ受ける `createBranch` も受ける。`createBranch` は
 * 登録したリポジトリの統合先の今の先頭から、issue から決める形の名前でブランチを作り、予約の名前・統合先の名前は断る。
 */
import { expiryNotice, parseManual, type Notice, type TokenMeta } from "./expiry.js";
import * as github from "./github.js";
import * as gitlab from "./gitlab.js";
import { DEFAULT_PLACES, PROTECTED, writeRefusal } from "./guard.js";
import type { Host } from "./hosts.js";
import { repoKey, type RepoConfig } from "./settings.js";

export type HostOp =
  | "repoInfo"
  | "branchHead"
  | "recentRefs"
  | "pathObjects"
  | "tree"
  | "blobs"
  | "viewer"
  | "approvalCommit"
  | "pullApprovals"
  | "commitParents"
  | "reviewCopy"
  | "compareFiles"
  | "issues"
  | "branchNames"
  | "createBranch"
  | "commit";

export type Request =
  | { readonly kind: "token.set"; readonly host: string; readonly token: string; readonly expires?: string }
  | { readonly kind: "token.clear"; readonly host: string }
  | { readonly kind: "token.status"; readonly host: string }
  | { readonly kind: "host"; readonly host: string; readonly op: HostOp; readonly args: readonly unknown[] };

export type Response =
  | { readonly ok: true; readonly value: unknown; readonly counter?: github.Counter }
  | { readonly ok: false; readonly error: string; readonly status?: number };

export interface Sender {
  readonly id?: string;
  readonly url?: string;
}

/** 送り手が拡張の自分のページか。`options` を真にすると設定画面だけ */
export function trustedSender(sender: Sender, extensionId: string, base: string, options = false): boolean {
  if (sender.id !== extensionId || typeof sender.url !== "string") {
    return false;
  }
  let url: URL;
  try {
    url = new URL(sender.url);
  } catch {
    return false;
  }
  const origin = new URL(base).origin;
  if (url.origin !== origin) {
    return false;
  }
  return !options || url.pathname === "/options.html";
}

function fromBoard(sender: Sender): boolean {
  try {
    return new URL(sender.url ?? "").pathname === "/board.html";
  } catch {
    return false;
  }
}

export interface Deps {
  readonly hosts: readonly Host[];
  /** 同梱の ccnavi の互換の版（「始める」の前に統合先の CCNAVI_COMPAT と比べる。無ければ比べずに断る） */
  readonly compat?: number;
  readonly extensionId: string;
  readonly base: string;
  readonly fetch: github.Fetch;
  getToken(host: string): Promise<string>;
  setToken(host: string, token: string): Promise<void>;
  clearToken(host: string): Promise<void>;
  /** PAT の期限の記録。PAT そのものとは別の鍵に置く */
  getMeta(host: string): Promise<TokenMeta>;
  /** 設定画面で登録したリポジトリ（書く頼みはここにあるものだけ受ける） */
  getRepos(): Promise<RepoConfig[]>;
  /** 待つ（レート制限の Retry-After）。無ければ本物の時計 */
  readonly sleep?: (ms: number) => Promise<void>;
  setMeta(host: string, meta: TokenMeta): Promise<void>;
  /** 今の時刻（期限の比べ） */
  now(): Date;
}

/** `token.status` の答え。PAT そのものは入れない */
export interface TokenStatus {
  readonly set: boolean;
  readonly notice: Notice | null;
}

const TOKEN = /^[A-Za-z0-9_\-.]{8,255}$/;
/** 書く先にしない名前（`ccnavi-push-approved.sh` の一覧と同じ）。大文字小文字をそろえて比べる */
/** 1 コミットで書くファイルの上限と、1 ファイルの大きさの上限（base64 の字数） */
const MAX_FILES = 200;
const MAX_CONTENTS = 4 * 1024 * 1024;
/** コミットの見出しと本文の字数の上限。見出しは件数にまとめ、全件は本文に書く（write.ts） */
export const MAX_HEADLINE = 200;
const MAX_BODY = 64 * 1024;
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;

function hostOf(deps: Deps, id: unknown): Host {
  const host = deps.hosts.find((h) => h.id === id);
  if (!host) {
    throw new github.HostError(`ビルドで組み込んだ通信先に無いホスト: ${String(id)}`);
  }
  return host;
}

/** 1 つの頼みに答える。例外は投げず、`ok: false` で返す */
export async function dispatch(message: unknown, sender: Sender, deps: Deps): Promise<Response> {
  try {
    const msg = message as Request;
    if (!msg || typeof msg !== "object" || typeof msg.kind !== "string") {
      return { ok: false, error: "要求の形が正しくない" };
    }
    if (!trustedSender(sender, deps.extensionId, deps.base, msg.kind === "token.set" || msg.kind === "token.clear")) {
      return { ok: false, error: "この送り手からの要求は受け付けない" };
    }
    if (msg.kind === "host" && (msg.op === "commit" || msg.op === "createBranch") && !fromBoard(sender)) {
      return { ok: false, error: "書く要求はボードからだけ受け付ける" };
    }
    const host = hostOf(deps, msg.host);
    switch (msg.kind) {
      case "token.set": {
        if (typeof msg.token !== "string" || !TOKEN.test(msg.token)) {
          return { ok: false, error: "PAT の形が正しくない（英数字と _ - . で 8〜255 字）" };
        }
        const manual = msg.expires === undefined || msg.expires === "" ? "" : parseManual(msg.expires);
        if (msg.expires !== undefined && msg.expires !== "" && !manual) {
          return { ok: false, error: "期限は YYYY-MM-DD の形の日付で入れてください" };
        }
        await deps.setToken(host.id, msg.token);
        // 差し替えたら、前のトークンの期限は捨てる（ホストの応答で読み直す）
        await deps.setMeta(host.id, manual ? { manual } : {});
        return { ok: true, value: null };
      }
      case "token.clear":
        await deps.clearToken(host.id);
        await deps.setMeta(host.id, {});
        return { ok: true, value: null };
      case "token.status": {
        const set = (await deps.getToken(host.id)) !== "";
        const status: TokenStatus = { set, notice: set ? expiryNotice(host.id, await deps.getMeta(host.id), deps.now()) : null };
        return { ok: true, value: status };
      }
      case "host":
        return await hostCall(host, msg.op, msg.args, deps);
      default:
        return { ok: false, error: "未対応の種類の要求" };
    }
  } catch (err) {
    const e = err as { message?: string; status?: number };
    return { ok: false, error: e.message ?? String(err), status: e.status };
  }
}

async function hostCall(host: Host, op: unknown, args: unknown, deps: Deps): Promise<Response> {
  if (!Array.isArray(args)) {
    return { ok: false, error: "args が配列でない" };
  }
  const token = await deps.getToken(host.id);
  if (!token) {
    return { ok: false, error: `${host.id} の PAT が登録されていない。設定画面で登録してください`, status: 401 };
  }
  const counter: github.Counter = { rest: 0, graphql: 0 };
  const seen = { expiration: "" };
  const client: github.Client = { host, token, fetch: deps.fetch, counter, seen, sleep: deps.sleep };
  let gitlabOk = false;
  try {
    const res = await hostOp(client, op, args, counter, deps);
    gitlabOk = res.ok && host.kind === "gitlab";
    return res;
  } finally {
    // 応答から期限を読めたら記録する（ホストの値が正）
    const iso = seen.expiration ? github.parseExpiration(seen.expiration) : "";
    if (iso) {
      const meta = await deps.getMeta(host.id);
      if (meta.host !== iso) await deps.setMeta(host.id, { ...meta, host: iso });
    }
    // GitLab の期限は、受けた頼みがホストに届いたときだけ聞く（断った頼みでは外へ出ない）
    if (gitlabOk) await gitlabExpiry(client, deps);
  }
}

const DAY_MS = 24 * 3600 * 1000;

/** GitLab の PAT の期限を 1 日 1 回聞いて記録する（読めなければ登録のときの日付のまま） */
async function gitlabExpiry(client: github.Client, deps: Deps): Promise<void> {
  const meta = await deps.getMeta(client.host.id);
  const last = meta.checked ? Date.parse(meta.checked) : NaN;
  if (!Number.isNaN(last) && deps.now().getTime() - last < DAY_MS) return;
  let iso = "";
  try {
    iso = await gitlab.tokenExpiry(client);
  } catch {
    iso = "";
  }
  await deps.setMeta(client.host.id, { ...meta, ...(iso ? { host: iso } : {}), checked: deps.now().toISOString() });
}

/** 書く頼みの断り（形が悪い・保護に当たった）。書く流れはこれを「先頭が動いた」と取り違えない */
export const REFUSED = 400;

function refuse(error: string): Response {
  return { ok: false, error, status: REFUSED };
}

interface Addition extends github.FileAddition {
  /** GitLab の Commits API の action（作るか書き換えるか）。GitHub は使わない */
  readonly op?: "create" | "update";
  /** GitLab: そのファイルを最後に変えたと書き手が知っているコミット（元に戻すコミットに使う） */
  readonly last?: string;
}

function commitArgs(args: unknown[]) {
  const [, , branch, expected, headline, body, additions, deletions] = args;
  const name = github.checkBranch(branch);
  if (!Array.isArray(additions) || !Array.isArray(deletions) || additions.length + deletions.length > MAX_FILES) {
    throw new Error(`書くファイルは ${MAX_FILES} 件までの配列`);
  }
  if (additions.length + deletions.length === 0) throw new Error("書くものが無い");
  const adds: Addition[] = additions.map((x: unknown) => {
    const e = x as { path?: unknown; contents?: unknown; op?: unknown; last?: unknown };
    if (typeof e.contents !== "string" || e.contents.length > MAX_CONTENTS || !BASE64.test(e.contents)) {
      throw new Error("contents が base64 でないか大きすぎる");
    }
    if (e.op !== undefined && e.op !== "create" && e.op !== "update") throw new Error("op は create か update");
    return { path: github.checkPath(e.path), contents: e.contents, ...(e.op ? { op: e.op } : {}), ...(e.last !== undefined ? { last: github.checkOid(e.last) } : {}) };
  });
  const dels = deletions.map((p: unknown) =>
    typeof p === "string" ? { path: github.checkPath(p) } : { path: github.checkPath((p as { path?: unknown })?.path), last: github.checkOid((p as { last?: unknown })?.last) },
  );
  if (typeof body !== "string" || body.length > MAX_BODY) throw new Error(`コミットの本文は ${MAX_BODY} 字までの文字列`);
  return { name, expected: github.checkOid(expected), headline: text(headline, "コミットの見出し", MAX_HEADLINE), body, adds, dels };
}

/** 設定画面で登録したリポジトリ（書く頼みと、レビュー済みの読み取りを受け付ける範囲） */
async function registeredRepo(deps: Deps, client: github.Client, o: string, r: string): Promise<RepoConfig | undefined> {
  return (await deps.getRepos()).find((x) => x.host === client.host.id && x.owner === o && x.repo === r);
}

async function registered(deps: Deps, client: github.Client, o: string, r: string): Promise<boolean> {
  return (await registeredRepo(deps, client, o, r)) !== undefined;
}

/** ホストの種類ごとの読み取り（書く頼みの保護が使う） */
function api(client: github.Client) {
  return client.host.kind === "gitlab" ? gitlab : github;
}

/**
 * 統合先の今の先頭（リモートに無ければ断る）。書く頼みでは先頭の値は使わず、統合先がリモートに
 * 在るかを確かめるためだけに呼ぶ
 */
async function integrationHead(client: github.Client, cfg: RepoConfig, integ: string): Promise<string> {
  const head = await api(client).branchHead(client, cfg.owner, cfg.repo, integ);
  if (head === null) throw new Error(`統合先 ${integ} がリモートに無い`);
  return head;
}

/** プロジェクトのリポジトリのワークスペース（設定画面で登録したもの）の統合先の先頭 */
async function workspaceOf(deps: Deps, cfg: RepoConfig): Promise<{ client: github.Client; cfg: RepoConfig; integ: string; head: string }> {
  const ws = (await deps.getRepos()).find((x) => repoKey(x) === cfg.workspace && !x.project);
  if (!ws) throw new Error(`ワークスペースのリポジトリ ${cfg.workspace || "（未設定）"} が設定画面に登録されていない`);
  const host = hostOf(deps, ws.host);
  const token = await deps.getToken(host.id);
  if (!token) throw new Error(`${host.id} の PAT が無い`);
  const client: github.Client = { host, token, fetch: deps.fetch, counter: { rest: 0, graphql: 0 }, sleep: deps.sleep };
  const a = api(client);
  const integ = ws.integration || (await a.repoInfo(client, ws.owner, ws.repo)).defaultBranch;
  const head = await a.branchHead(client, ws.owner, ws.repo, integ);
  if (head === null) throw new Error(`ワークスペースの統合先 ${integ} がリモートに無い`);
  return { client, cfg: ws, integ, head };
}

/** 統合先の先頭で読むファイルの本文（無ければ null） */
async function fileAt(client: github.Client, cfg: RepoConfig, head: string, path: string): Promise<string | null> {
  const a = api(client);
  const obj = (await a.pathObjects(client, cfg.owner, cfg.repo, head, [path]))[path];
  if (!obj) return null;
  if (obj.type !== "blob") throw new Error(`${path} を読めない（ファイルでない）`);
  const blob = (await a.blobs(client, cfg.owner, cfg.repo, [obj.oid]))[obj.oid];
  if (!blob || blob.binary || blob.text === null) throw new Error(`${path} を読めない`);
  return blob.text;
}

/**
 * 大文字小文字をそろえる（「始める」の重なりの検査）。作る名前は ASCII の英数字と記号、それに NFKC で変わらない
 * 日本語の字（ひらがな・カタカナ・長音記号・CJK 統合漢字・々）に限る（`startName`・Python の `ticket_ids._ID`）ので
 * Python の casefold と同じ答えになる。比べる相手（ホストの既にあるブランチの名前）は ASCII とは限らないので、
 * 互換分解（NFKC）してからそろえ、`ﬁ`・`ſ` のように casefold で ASCII に変わる字も重なりとして拾う（厳しくする向き）
 */
function fold(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}

/** 互換のマーカーのパス（`ccnavi_chrome.COMPAT_FILE`） */
const COMPAT_FILE = ".ccnavi/scripts/ccnavi-common.sh";

/**
 * 「始める」の前に service worker が統合先の今の先頭で確かめ直すもの（二重の確認）: 閉じた識別子（`done/` の名前を
 * 大文字小文字をそろえて）と互換の版（ワークスペースの統合先の CCNAVI_COMPAT と同梱の版）。空なら作ってよい
 */
async function startGuard(deps: Deps, client: github.Client, cfg: RepoConfig, integ: string, head: string, name: string): Promise<string> {
  const ws = cfg.project ? await workspaceOf(deps, cfg) : { client, cfg, integ, head };
  const text = await fileAt(ws.client, ws.cfg, ws.head, COMPAT_FILE);
  const theirs = text === null ? null : Number(/^CCNAVI_COMPAT=(\d+)\s*$/m.exec(text)?.[1] ?? NaN);
  if (deps.compat === undefined || theirs === null || theirs !== deps.compat) {
    return `統合先の互換の版（${theirs ?? "無い"}）と拡張の互換の版（${deps.compat ?? "不明"}）が違うので作らない`;
  }
  const a = api(client);
  const done = `${DEFAULT_PLACES.approved}/done`;
  const obj = (await a.pathObjects(client, cfg.owner, cfg.repo, head, [done]))[done];
  if (obj && obj.type === "tree") {
    const entries = client.host.kind === "gitlab" ? await gitlab.tree(client, cfg.owner, cfg.repo, obj.oid, head, done) : await github.tree(client, cfg.owner, cfg.repo, obj.oid);
    const folded = fold(name);
    const hit = entries.find((e) => !e.path.includes("/") && fold(e.path) === `${folded}.md`);
    if (hit) return `${name} は統合先 ${integ} の done/ で閉じている（閉じた識別子は使い直さない）`;
  }
  return "";
}

/** 識別子に使える日本語の字（Python の `ticket_ids.JA_CHARS` と同じ文字クラス） */
const JA = "\\u3005\\u3041-\\u3096\\u30a1-\\u30fa\\u30fc\\u4e00-\\u9fff";
/** issue から作る親の識別子の形（`<先頭の語>-<番号>-<slug>`。Python の `ticket_ids._FORM` と同じ） */
const ISSUE_BRANCH = new RegExp(`^(?<prefix>[a-z][a-z0-9]*)-[1-9][0-9]*-(?<slug>[A-Za-z0-9${JA}][A-Za-z0-9._\\-${JA}]*)$`, "u");
/** 先頭の語に使えない名前（統合先や保護されたブランチ。Python の `settings._RESERVED_PREFIXES`） */
const RESERVED_PREFIXES = new Set(["main", "master", "develop", "release"]);
/** 識別子の長さの上限（Python の `ticket_ids.MAX_ID_LENGTH`） */
const MAX_ID_LENGTH = 64;

/**
 * 「始める」で作るブランチの名前の形（issue から決める形）。プロジェクトなら slug の頭に `<名前>-`。
 * 名前は Python が issue のタイトルから作るが、タイトルは誰でも書けるので、ここでも字と形を確かめ直す（二重の確認）
 */
function startName(name: unknown, cfg: RepoConfig): string {
  const branch = github.checkBranch(name);
  const m = ISSUE_BRANCH.exec(branch);
  const prefix = m?.groups?.prefix ?? "";
  const slug = m?.groups?.slug ?? "";
  const ok =
    m !== null &&
    branch === branch.normalize("NFC") &&
    [...branch].length <= MAX_ID_LENGTH &&
    !RESERVED_PREFIXES.has(prefix) &&
    !/-[0-9]{2}$/.test(branch) &&
    (cfg.project ? slug.startsWith(`${cfg.project}-`) : true);
  if (!ok) {
    throw new Error(
      `${branch} は、issue から作る親のブランチの名前の形（${cfg.project ? `<先頭の語>-<番号>-${cfg.project}-<slug>` : "<先頭の語>-<番号>-<slug>"}）になっていない`,
    );
  }
  return branch;
}

function text(value: unknown, what: string, max: number): string {
  if (typeof value !== "string" || value === "" || value.length > max || /[\x00-\x1f\x7f]/.test(value)) {
    throw new github.HostError(`${what} が読めない`);
  }
  return value;
}

async function hostOp(client: github.Client, op: unknown, args: unknown[], counter: github.Counter, deps: Deps): Promise<Response> {
  const [owner, repo, a, b, c] = args as unknown[];
  const lab = client.host.kind === "gitlab";
  const o = lab ? gitlab.checkNamespace(owner) : github.checkName(owner, "owner");
  const r = github.checkName(repo, "repo");
  const x = api(client);
  // 読み取りも、設定画面で登録したリポジトリ（プロジェクトのワークスペースも登録したもの）だけ受ける
  const known = await registeredRepo(deps, client, o, r);
  if (!known) {
    const why = `${o}/${r} は設定画面に登録していないリポジトリなので${op === "commit" || op === "createBranch" ? "書かない" : "読まない"}`;
    return op === "commit" || op === "createBranch" ? refuse(why) : { ok: false, error: why };
  }
  let value: unknown;
  switch (op) {
    case "repoInfo":
      value = await x.repoInfo(client, o, r);
      break;
    case "branchHead":
      value = await x.branchHead(client, o, r, github.checkBranch(a));
      break;
    case "recentRefs": {
      if (typeof a !== "string" || Number.isNaN(Date.parse(a))) {
        return { ok: false, error: "since が日時でない" };
      }
      value = await x.recentRefs(client, o, r, new Date(a));
      break;
    }
    case "pathObjects": {
      if (!Array.isArray(b) || b.length > 20) {
        return { ok: false, error: "paths は 20 件までの配列" };
      }
      value = await x.pathObjects(client, o, r, github.checkOid(a), b.map(github.checkPath));
      break;
    }
    case "tree":
      // GitLab は tree の sha でなく、コミット（b）とパス（c）で引く
      value = lab
        ? await gitlab.tree(client, o, r, github.checkOid(a), github.checkOid(b), github.checkPath(c))
        : await github.tree(client, o, r, github.checkOid(a));
      break;
    case "blobs": {
      if (!Array.isArray(a)) {
        return { ok: false, error: "oids が配列でない" };
      }
      value = await x.blobs(client, o, r, a.map(github.checkOid));
      break;
    }
    case "viewer":
      value = await x.viewer(client);
      break;
    case "approvalCommit":
      value = await x.approvalCommit(client, o, r, github.checkOid(a), github.checkPath(b));
      break;
    case "pullApprovals":
      value = await x.pullApprovals(client, o, r, github.checkBranch(a));
      break;
    case "commit": {
      // 書く頼みの形が悪ければ 400 で断る（書く流れは「先頭が動いた」と取り違えずに原因を言う）
      let checked: ReturnType<typeof commitArgs>;
      try {
        checked = commitArgs(args);
      } catch (err) {
        return refuse((err as Error).message);
      }
      // 書く先の保護: 登録したリポジトリだけ。統合先の名前はボードの値を信頼せず自分で引き、
      // 置き場のパスは既定に固定する（ボードの値も統合先の `.claude/settings.json` も読まない）
      const cfg = await registeredRepo(deps, client, o, r);
      if (!cfg) return refuse(`${o}/${r} は設定画面に登録していないリポジトリなので書かない`);
      const integ = cfg.integration || (await x.repoInfo(client, o, r)).defaultBranch;
      try {
        await integrationHead(client, cfg, integ);
      } catch (err) {
        return refuse(`統合先 ${integ} を読めないので書かない（${(err as Error).message}）`);
      }
      const why = writeRefusal(checked.name, integ, [...checked.adds.map((e) => e.path), ...checked.dels.map((d) => d.path)], DEFAULT_PLACES);
      if (why) return refuse(why);
      if (lab) {
        if (checked.adds.some((e) => !e.op)) return refuse("GitLab へ書くときは作るか書き換えるか（op）が要る");
        const actions: gitlab.GitLabAction[] = [
          ...checked.adds.map((e) => ({ op: e.op as "create" | "update", path: e.path, contents: e.contents, ...(e.last ? { last: e.last } : {}) })),
          ...checked.dels.map((d) => ({ op: "delete" as const, path: d.path, ...(d.last ? { last: d.last } : {}) })),
        ];
        value = await gitlab.createCommit(client, o, r, checked.name, checked.expected, checked.headline, checked.body, actions);
      } else {
        const oid = await github.createCommit(
          client,
          o,
          r,
          checked.name,
          checked.expected,
          checked.headline,
          checked.body,
          checked.adds.map((e) => ({ path: e.path, contents: e.contents })),
          checked.dels.map((d) => d.path),
        );
        // expectedHeadOid で書いたので、親は読んだ先頭に決まる
        value = { oid, parent: checked.expected };
      }
      break;
    }
    case "commitParents":
      value = await x.commitParents(client, o, r, github.checkOid(a));
      break;
    case "reviewCopy":
      // レビュー済み: MR のスレッドとレビューを取得する。読むだけ。登録したリポジトリだけ
      if (!(await registered(deps, client, o, r))) return { ok: false, error: `${o}/${r} は設定画面に登録していないリポジトリなので読まない` };
      value = await x.reviewCopy(client, o, r, github.checkBranch(a));
      break;
    case "compareFiles":
      if (!(await registered(deps, client, o, r))) return { ok: false, error: `${o}/${r} は設定画面に登録していないリポジトリなので読まない` };
      value = await x.compareFiles(client, o, r, github.checkOid(a), github.checkOid(b));
      break;
    case "branchNames":
      value = await x.branchNames(client, o, r);
      break;
    case "issues":
      // 「始める」: 開いた issue の一覧。読むだけ。登録したリポジトリだけ
      if (!(await registered(deps, client, o, r))) return { ok: false, error: `${o}/${r} は設定画面に登録していないリポジトリなので読まない` };
      value = await x.issues(client, o, r);
      break;
    case "createBranch": {
      // 「始める」: 親のブランチを統合先の今の先頭から作る。PR/MR は作らない
      const cfg = await registeredRepo(deps, client, o, r);
      if (!cfg) return refuse(`${o}/${r} は設定画面に登録していないリポジトリなのでブランチを作らない`);
      let name: string;
      try {
        name = startName(a, cfg);
      } catch (err) {
        return refuse((err as Error).message);
      }
      const integ = cfg.integration || (await x.repoInfo(client, o, r)).defaultBranch;
      if (PROTECTED.test(name) || name.toLowerCase() === integ.toLowerCase()) {
        return refuse(`${name} は保護されたブランチか統合先の名前なので作らない`);
      }
      const head = await x.branchHead(client, o, r, integ);
      if (head === null) return refuse(`統合先 ${integ} がリモートに無い`);
      if (github.checkOid(b) !== head) return refuse(`統合先 ${integ} の先頭が、ボードで読んだときから動いている。ボードを更新してから始め直してください`);
      // 統合先の今の先頭で確かめ直す（Python の答えを信頼しない）: 大文字小文字をそろえた重なり・閉じた識別子・互換の版
      const folded = fold(name);
      const same = (await x.branchNames(client, o, r)).filter((n) => fold(n) === folded);
      if (same.length > 0) return refuse(`${name} は既にある（${same.join(", ")}）`);
      let guard: string;
      try {
        guard = await startGuard(deps, client, cfg, integ, head, name);
      } catch (err) {
        return refuse(`統合先 ${integ} を確かめられないので作らない（${(err as Error).message}）`);
      }
      if (guard) return refuse(guard);
      value = { name, head: await x.createBranch(client, o, r, name, head) };
      break;
    }
    default:
      return { ok: false, error: `未対応の操作:${String(op)}` };
  }
  return { ok: true, value, counter };
}
