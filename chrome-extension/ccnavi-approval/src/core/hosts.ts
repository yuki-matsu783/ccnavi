/**
 * 通信先（ホストの API）の一覧と、そこから組む manifest（ADR-0093 の D24・5.5 の 5）。
 *
 * 通信先は配布するときにビルドへ焼き込む。設定画面でホストを足す道は持たない。
 * `host_permissions` と CSP の `connect-src` は、この一覧の API のオリジンだけにする。
 * CSP に足すのは Pyodide に要る `'wasm-unsafe-eval'` だけ（5.5 の 3）。
 */

export type HostKind = "github" | "gitlab";

export interface Host {
  /** 設定と PAT の鍵。`github.com` など */
  readonly id: string;
  readonly kind: HostKind;
  /** REST の根。`https://api.github.com`、GHES なら `https://<ホスト>/api/v3` */
  readonly api: string;
  /** GitHub の GraphQL。GitLab は使わない（REST だけ。段階 5） */
  readonly graphql: string;
  /** 人が開く画面の根。PAT の作成画面へのリンクに使う */
  readonly web: string;
}

const ID = /^[a-z0-9][a-z0-9.-]*$/;
const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** URL を読み、https（試験用の loopback だけ http）でパスまでのものに限る */
function endpoint(value: unknown, what: string): URL {
  if (typeof value !== "string") {
    throw new Error(`${what} が文字列でない`);
  }
  const url = new URL(value);
  const loopback = url.protocol === "http:" && LOOPBACK.has(url.hostname);
  if (url.protocol !== "https:" && !loopback) {
    throw new Error(`${what} は https に限る（試験用の 127.0.0.1 だけ http を許す）: ${value}`);
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(`${what} に資格情報・クエリ・# を書かない: ${value}`);
  }
  return url;
}

/** 一覧の JSON を読む。形が違えばビルドを止める */
export function parseHosts(text: string): Host[] {
  const raw: unknown = JSON.parse(text);
  const list = (raw as { hosts?: unknown })?.hosts;
  if (!Array.isArray(list) || list.length === 0) {
    throw new Error("hosts が空");
  }
  const seen = new Set<string>();
  return list.map((entry: Record<string, unknown>) => {
    const id = entry.id;
    if (typeof id !== "string" || !ID.test(id) || seen.has(id)) {
      throw new Error(`id が読めないか重なっている: ${String(id)}`);
    }
    seen.add(id);
    const kind = entry.kind;
    if (kind !== "github" && kind !== "gitlab") {
      throw new Error(`${id}: kind は github か gitlab`);
    }
    const api = endpoint(entry.api, `${id}.api`);
    const web = endpoint(entry.web, `${id}.web`);
    const graphql = kind === "github" ? endpoint(entry.graphql, `${id}.graphql`) : api;
    const strip = (u: URL) => u.href.replace(/\/+$/, "");
    return { id, kind, api: strip(api), graphql: strip(graphql), web: strip(web) };
  });
}

/** 通信先のオリジン（重なりを除き、並びを保つ） */
export function origins(hosts: readonly Host[]): string[] {
  const out: string[] = [];
  for (const h of hosts) {
    for (const u of [h.api, h.graphql]) {
      const o = new URL(u).origin;
      if (!out.includes(o)) out.push(o);
    }
  }
  return out;
}

/**
 * 画像・フォームの送り先・base を塞ぐ（レビューの 14）。足すのは Pyodide に要る 'wasm-unsafe-eval' だけ。
 * `default-src 'self'` は入れない: 悪意のある本文の style 属性を DOMPurify が落とす前の解析で、Chromium が
 * インラインの style の違反を毎回報告する（止まるのは同じで、表示の守りは DOMPurify が持つ）
 */
export const CSP_BASE = "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; img-src 'self'; form-action 'none'; base-uri 'none'";

/** 拡張のページの CSP。`connect-src` は通信先だけ */
export function csp(hosts: readonly Host[]): string {
  return `${CSP_BASE}; connect-src 'self' ${origins(hosts).join(" ")}`;
}

/** manifest.json の中身 */
export function manifest(hosts: readonly Host[], version: string): Record<string, unknown> {
  return {
    manifest_version: 3,
    name: "ccnavi 承認ボード",
    description: "リモートのブランチの承認待ちを見せ、承認・取り下げ・レビュー済みを親のブランチへ書き、issue から親のブランチを始める（ADR-0093 段階 5。GitHub と GitLab）",
    version,
    minimum_chrome_version: "116",
    permissions: ["storage", "alarms"],
    host_permissions: origins(hosts).map((o) => `${o}/*`),
    background: { service_worker: "background.js", type: "module" },
    action: { default_title: "ccnavi 承認ボードを開く" },
    options_page: "options.html",
    content_security_policy: { extension_pages: csp(hosts) },
  };
}
