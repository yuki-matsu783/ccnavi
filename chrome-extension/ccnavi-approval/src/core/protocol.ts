/**
 * 画面と service worker のあいだの約束（ADR-0093 の 5.5 の 4）。
 *
 * PAT を読むのは service worker だけ。画面は PAT を受け取らず、ホストの API は名前で限った
 * 操作を頼む。PAT を書く・消すのは設定画面からだけ受ける。送り手は拡張の自分のページに限り、
 * 他の拡張・ウェブページからの呼び出し（`onMessageExternal`）は受けない。
 *
 * 段階 3 から、書く操作 `commit`（`createCommitOnBranch`）を受ける。受けるのはボードからだけで、
 * 設定画面で登録したリポジトリだけに書く。書く先が予約の名前（`main`・`master`・`develop`・`release*`）か
 * 統合先の名前（ボードの値を信じず自分で引く）なら断り、書くパスは置き場（統合先の `.claude/settings.json`
 * から自分で引く）の下に限る（8.5・レビューの決定 D。Python も同じ検査をする。ここは二重の守り）。PAT の期限（D25）は応答ヘッダから読んで控え、
 * 画面へは期限だけを返す（PAT そのものは返さない）。
 */
import { expiryNotice, parseManual, type Notice, type TokenMeta } from "./expiry.js";
import * as github from "./github.js";
import { placesFromSettings, writeRefusal, type Places } from "./guard.js";
import type { Host } from "./hosts.js";
import type { RepoConfig } from "./settings.js";

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
  readonly extensionId: string;
  readonly base: string;
  readonly fetch: github.Fetch;
  getToken(host: string): Promise<string>;
  setToken(host: string, token: string): Promise<void>;
  clearToken(host: string): Promise<void>;
  /** PAT の期限の控え（D25）。PAT そのものとは別の鍵に置く */
  getMeta(host: string): Promise<TokenMeta>;
  /** 設定画面で登録したリポジトリ（書く頼みはここにあるものだけ受ける。決定 D） */
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
/** 書く先にしない名前（8.5。`ccnavi-push-approved.sh` の一覧と同じ）。大文字小文字を畳んで比べる */
/** 1 コミットで書くファイルの上限と、1 ファイルの大きさの上限（base64 の字数） */
const MAX_FILES = 200;
const MAX_CONTENTS = 4 * 1024 * 1024;
/** コミットの見出しと本文の字数の上限（見出しは件数に畳む。全件は本文。write.ts） */
export const MAX_HEADLINE = 200;
const MAX_BODY = 64 * 1024;
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;

function hostOf(deps: Deps, id: unknown): Host {
  const host = deps.hosts.find((h) => h.id === id);
  if (!host) {
    throw new github.HostError(`焼き込んだ通信先に無いホスト: ${String(id)}`);
  }
  return host;
}

/** 1 つの頼みに答える。例外は投げず、`ok: false` で返す */
export async function dispatch(message: unknown, sender: Sender, deps: Deps): Promise<Response> {
  try {
    const msg = message as Request;
    if (!msg || typeof msg !== "object" || typeof msg.kind !== "string") {
      return { ok: false, error: "頼みの形が違う" };
    }
    if (!trustedSender(sender, deps.extensionId, deps.base, msg.kind === "token.set" || msg.kind === "token.clear")) {
      return { ok: false, error: "送り手を受けない" };
    }
    if (msg.kind === "host" && msg.op === "commit" && !fromBoard(sender)) {
      return { ok: false, error: "書く頼みはボードからだけ受ける" };
    }
    const host = hostOf(deps, msg.host);
    switch (msg.kind) {
      case "token.set": {
        if (typeof msg.token !== "string" || !TOKEN.test(msg.token)) {
          return { ok: false, error: "PAT の形が違う（英数字と _ - . の 8〜255 字）" };
        }
        const manual = msg.expires === undefined || msg.expires === "" ? "" : parseManual(msg.expires);
        if (msg.expires !== undefined && msg.expires !== "" && !manual) {
          return { ok: false, error: "期限は YYYY-MM-DD の日付" };
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
        return { ok: false, error: "知らない頼み" };
    }
  } catch (err) {
    const e = err as { message?: string; status?: number };
    return { ok: false, error: e.message ?? String(err), status: e.status };
  }
}

async function hostCall(host: Host, op: unknown, args: unknown, deps: Deps): Promise<Response> {
  if (host.kind !== "github") {
    return { ok: false, error: `${host.id} は GitLab。GitLab の読み取りは段階 5 で入れる` };
  }
  if (!Array.isArray(args)) {
    return { ok: false, error: "args が並びでない" };
  }
  const token = await deps.getToken(host.id);
  if (!token) {
    return { ok: false, error: `${host.id} の PAT が無い。設定画面で登録する`, status: 401 };
  }
  const counter: github.Counter = { rest: 0, graphql: 0 };
  const seen = { expiration: "" };
  const client: github.Client = { host, token, fetch: deps.fetch, counter, seen, sleep: deps.sleep };
  try {
    return await hostOp(client, op, args, counter, deps);
  } finally {
    // 応答から期限を読めたら控える（D25。ホストの値が正）
    const iso = seen.expiration ? github.parseExpiration(seen.expiration) : "";
    if (iso) {
      const meta = await deps.getMeta(host.id);
      if (meta.host !== iso) await deps.setMeta(host.id, { ...meta, host: iso });
    }
  }
}

/** 書く頼みの断り（形が悪い・守りに当たった）。書く流れはこれを「先頭が動いた」と取り違えない */
export const REFUSED = 400;

function refuse(error: string): Response {
  return { ok: false, error, status: REFUSED };
}

function commitArgs(args: unknown[]) {
  const [, , branch, expected, headline, body, additions, deletions] = args;
  const name = github.checkBranch(branch);
  if (!Array.isArray(additions) || !Array.isArray(deletions) || additions.length + deletions.length > MAX_FILES) {
    throw new Error(`書くファイルは ${MAX_FILES} 件までの並び`);
  }
  if (additions.length + deletions.length === 0) throw new Error("書くものが無い");
  const adds = additions.map((x: unknown) => {
    const e = x as { path?: unknown; contents?: unknown };
    if (typeof e.contents !== "string" || e.contents.length > MAX_CONTENTS || !BASE64.test(e.contents)) {
      throw new Error("contents が base64 でないか大きすぎる");
    }
    return { path: github.checkPath(e.path), contents: e.contents };
  });
  const dels = deletions.map((p: unknown) => github.checkPath(p));
  if (typeof body !== "string" || body.length > MAX_BODY) throw new Error(`コミットの本文は ${MAX_BODY} 字までの文字列`);
  return { name, expected: github.checkOid(expected), headline: text(headline, "コミットの見出し", MAX_HEADLINE), body, adds, dels };
}

/** 統合先の先頭の `.claude/settings.json` から置き場の綴りを読む */
async function placesAt(client: github.Client, o: string, r: string, integ: string): Promise<Places> {
  const head = await github.branchHead(client, o, r, integ);
  if (head === null) throw new Error(`統合先 ${integ} がリモートに無い`);
  const objs = await github.pathObjects(client, o, r, head, [".claude/settings.json"]);
  const obj = objs[".claude/settings.json"];
  if (!obj) return placesFromSettings(null);
  const blob = (await github.blobs(client, o, r, [obj.oid]))[obj.oid];
  if (!blob || blob.binary || blob.text === null) throw new Error(".claude/settings.json を読めない");
  return placesFromSettings(blob.text);
}

function text(value: unknown, what: string, max: number): string {
  if (typeof value !== "string" || value === "" || value.length > max || /[\x00-\x1f\x7f]/.test(value)) {
    throw new github.HostError(`${what} が読めない`);
  }
  return value;
}

async function hostOp(client: github.Client, op: unknown, args: unknown[], counter: github.Counter, deps: Deps): Promise<Response> {
  const [owner, repo, a, b] = args as unknown[];
  const o = github.checkName(owner, "owner");
  const r = github.checkName(repo, "repo");
  let value: unknown;
  switch (op) {
    case "repoInfo":
      value = await github.repoInfo(client, o, r);
      break;
    case "branchHead":
      value = await github.branchHead(client, o, r, github.checkBranch(a));
      break;
    case "recentRefs": {
      if (typeof a !== "string" || Number.isNaN(Date.parse(a))) {
        return { ok: false, error: "since が日時でない" };
      }
      value = await github.recentRefs(client, o, r, new Date(a));
      break;
    }
    case "pathObjects": {
      if (!Array.isArray(b) || b.length > 20) {
        return { ok: false, error: "paths は 20 件までの並び" };
      }
      value = await github.pathObjects(client, o, r, github.checkOid(a), b.map(github.checkPath));
      break;
    }
    case "tree":
      value = await github.tree(client, o, r, github.checkOid(a));
      break;
    case "blobs": {
      if (!Array.isArray(a)) {
        return { ok: false, error: "oids が並びでない" };
      }
      value = await github.blobs(client, o, r, a.map(github.checkOid));
      break;
    }
    case "viewer":
      value = await github.viewer(client);
      break;
    case "approvalCommit":
      value = await github.approvalCommit(client, o, r, github.checkOid(a), github.checkPath(b));
      break;
    case "pullApprovals":
      value = await github.pullApprovals(client, o, r, github.checkBranch(a));
      break;
    case "commit": {
      // 書く頼みの形が悪ければ 400 で断る（書く流れは「先頭が動いた」と取り違えずに原因を言う）
      let checked: { name: string; expected: string; headline: string; body: string; adds: github.FileAddition[]; dels: string[] };
      try {
        checked = commitArgs(args);
      } catch (err) {
        return refuse((err as Error).message);
      }
      // 書く先の守り（決定 D）: 登録したリポジトリだけ。統合先の名前と置き場の綴りはボードの値を信じず、自分で引く
      const cfg = (await deps.getRepos()).find((x) => x.host === client.host.id && x.owner === o && x.repo === r);
      if (!cfg) return refuse(`${o}/${r} は設定画面に登録していないリポジトリなので書かない`);
      const integ = cfg.integration || (await github.repoInfo(client, o, r)).defaultBranch;
      let places: Places;
      try {
        places = await placesAt(client, o, r, integ);
      } catch (err) {
        return refuse(`統合先 ${integ} の置き場の綴りを読めないので書かない（${(err as Error).message}）`);
      }
      const why = writeRefusal(checked.name, integ, [...checked.adds.map((a) => a.path), ...checked.dels], places);
      if (why) return refuse(why);
      value = await github.createCommit(client, o, r, checked.name, checked.expected, checked.headline, checked.body, checked.adds, checked.dels);
      break;
    }
    case "commitParents":
      value = await github.commitParents(client, o, r, github.checkOid(a));
      break;
    default:
      return { ok: false, error: `知らない操作: ${String(op)}` };
  }
  return { ok: true, value, counter };
}
