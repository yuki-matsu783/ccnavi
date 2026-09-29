/**
 * 画面と service worker のあいだの約束（ADR-0093 の 5.5 の 4）。
 *
 * PAT を読むのは service worker だけ。画面は PAT を受け取らず、ホストの API は名前で限った
 * 操作を頼む。PAT を書く・消すのは設定画面からだけ受ける。送り手は拡張の自分のページに限り、
 * 他の拡張・ウェブページからの呼び出し（`onMessageExternal`）は受けない。
 */
import * as github from "./github.js";
import type { Host } from "./hosts.js";

export type HostOp = "repoInfo" | "branchHead" | "recentRefs" | "pathObjects" | "tree" | "blobs";

export type Request =
  | { readonly kind: "token.set"; readonly host: string; readonly token: string }
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

export interface Deps {
  readonly hosts: readonly Host[];
  readonly extensionId: string;
  readonly base: string;
  readonly fetch: github.Fetch;
  getToken(host: string): Promise<string>;
  setToken(host: string, token: string): Promise<void>;
  clearToken(host: string): Promise<void>;
}

const TOKEN = /^[A-Za-z0-9_\-.]{8,255}$/;

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
    const host = hostOf(deps, msg.host);
    switch (msg.kind) {
      case "token.set": {
        if (typeof msg.token !== "string" || !TOKEN.test(msg.token)) {
          return { ok: false, error: "PAT の形が違う（英数字と _ - . の 8〜255 字）" };
        }
        await deps.setToken(host.id, msg.token);
        return { ok: true, value: null };
      }
      case "token.clear":
        await deps.clearToken(host.id);
        return { ok: true, value: null };
      case "token.status":
        return { ok: true, value: { set: (await deps.getToken(host.id)) !== "" } };
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
  const client: github.Client = { host, token, fetch: deps.fetch, counter };
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
    default:
      return { ok: false, error: `知らない操作: ${String(op)}` };
  }
  return { ok: true, value, counter };
}
