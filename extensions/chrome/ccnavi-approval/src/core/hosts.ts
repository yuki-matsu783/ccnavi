/**
 * 通信先（ホストの API）の一覧と、そこから組む manifest。
 *
 * 一覧の通信先は配布するときにビルドへ埋め込み、`host_permissions` に入れる。設定画面で足したホスト
 * （`custom-hosts.ts`）は、実行時に `optional_host_permissions` の許可を取って使う。拡張のページの CSP は
 * manifest に固定されて実行時には変えられないため、`connect-src` は https 全般と loopback の http を許す。
 * PAT を送る先はユーザが設定画面で入れたホストだけになる（一覧の外へ勝手に送ることはしない）。
 * CSP に足すのは Pyodide に要る `'wasm-unsafe-eval'` だけ。`'unsafe-eval'`・`'unsafe-inline'` は入れない。
 */

export type HostKind = "github" | "gitlab";

export interface Host {
  /** 設定と PAT のキー。`github.com` など */
  readonly id: string;
  readonly kind: HostKind;
  /** REST の根。`https://api.github.com`、GHES なら `https://<ホスト>/api/v3` */
  readonly api: string;
  /** GitHub の GraphQL。GitLab は使わない（REST だけ） */
  readonly graphql: string;
  /** ユーザが開く画面の根。PAT の作成画面へのリンクに使う */
  readonly web: string;
}

export const ID = /^[a-z0-9][a-z0-9.-]*$/;
const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

/** URL を読み、https（loopback だけ http）で、資格情報・クエリ・# の無いものに限る */
export function endpoint(value: unknown, what: string): URL {
  if (typeof value !== "string") {
    throw new Error(`${what} が文字列ではありません`);
  }
  const url = new URL(value);
  const loopback = url.protocol === "http:" && LOOPBACK.has(url.hostname);
  if (url.protocol !== "https:" && !loopback) {
    throw new Error(`${what} は https のみ指定できます（http は localhost と 127.0.0.1 だけです）：${value}`);
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(`${what} には資格情報・クエリ・# を含めないでください：${value}`);
  }
  return url;
}

/** 一覧の JSON を読む。形が違えばビルドを止める */
export function parseHosts(text: string): Host[] {
  const raw: unknown = JSON.parse(text);
  const list = (raw as { hosts?: unknown })?.hosts;
  if (!Array.isArray(list) || list.length === 0) {
    throw new Error("hosts が空です");
  }
  const seen = new Set<string>();
  return list.map((entry: Record<string, unknown>) => {
    const id = entry.id;
    if (typeof id !== "string" || !ID.test(id) || seen.has(id)) {
      throw new Error(`id が不正か重複しています：${String(id)}`);
    }
    seen.add(id);
    const kind = entry.kind;
    if (kind !== "github" && kind !== "gitlab") {
      throw new Error(`${id}：kind は github か gitlab を指定してください`);
    }
    const api = endpoint(entry.api, `${id}.api`);
    const web = endpoint(entry.web, `${id}.web`);
    const graphql = kind === "github" ? endpoint(entry.graphql, `${id}.graphql`) : api;
    const strip = (u: URL) => u.href.replace(/\/+$/, "");
    return { id, kind, api: strip(api), graphql: strip(graphql), web: strip(web) };
  });
}

/** 通信先のオリジン（重なりを除き、順序を保つ） */
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
 * 画像・フォームの送り先・base を制限する。足すのは Pyodide に要る 'wasm-unsafe-eval' だけ。
 * `default-src 'self'` は入れない: 悪意のある本文の style 属性を DOMPurify が取り除く前の解析で、Chromium が
 * インラインの style の違反を毎回報告する（止まるのは同じで、表示の保護は DOMPurify が受け持つ）
 */
export const CSP_BASE = "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; img-src 'self'; form-action 'none'; base-uri 'none'";

/**
 * 拡張のページの CSP の `connect-src`。設定画面で足すホストの https を通すため https 全般を許し、http は
 * loopback（試験と手元の GitLab。`localhost:8080` など）だけ。
 */
export const CONNECT_SRC = "'self' https: http://localhost:* http://127.0.0.1:*";

/** 拡張のページの CSP */
export function csp(): string {
  return `${CSP_BASE}; connect-src ${CONNECT_SRC}`;
}

/**
 * 設定画面で足すホストの許可（`optional_host_permissions`）。実際に許すのは登録のときに、そのオリジンだけ。
 * `http://localhost/*` は port を問わない（Chrome の一致規則）。
 */
export const OPTIONAL_HOST_PERMISSIONS = ["https://*/*", "http://localhost/*", "http://127.0.0.1/*"];

/** manifest.json の中身 */
export function manifest(hosts: readonly Host[], version: string): Record<string, unknown> {
  return {
    manifest_version: 3,
    name: "ccnavi 承認ボード",
    description: "リモートのブランチにある承認待ちを表示し、承認とレビュー済みを親ブランチへ反映します。Issue から親ブランチを作成することもできます（GitHub と GitLab）",
    version,
    minimum_chrome_version: "116",
    permissions: ["storage", "alarms"],
    host_permissions: origins(hosts).map((o) => `${o}/*`),
    optional_host_permissions: OPTIONAL_HOST_PERMISSIONS,
    background: { service_worker: "background.js", type: "module" },
    action: { default_title: "ccnavi 承認ボードを開く" },
    options_page: "options.html",
    content_security_policy: { extension_pages: csp() },
  };
}
