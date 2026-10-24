/**
 * 設定画面で足すホスト（`chrome.storage.local` の `customHosts`）。ビルドに埋め込んだ一覧（`hosts.json`）と
 * 合わせて、通信先の一覧になる。
 *
 * 保存するのは種類と、入れたオリジンだけ（`{ kind, url }`）。API の欄は読むたびに、一覧と同じ規則
 * （`endpoint`）で組み直す。だから保存された値を手で書き換えても、https でないホストは通らない。
 * 足すときの許可（`chrome.permissions.request`）は設定画面の役目で、ここには持たない。
 */
import type { StorageArea } from "./settings.js";
import { isGheHost } from "./hostclass.js";
import { endpoint, type Host, type HostKind } from "./hosts.js";

export const CUSTOM_KEY = "customHosts";

/** 足すホストの id の形（`ホスト名[_ポート][_http]`。組み込みの一覧の ID より `_` の分だけ広い） */
const CUSTOM_ID = /^[a-z0-9][a-z0-9._-]*$/;

/**
 * h の許可の型のうち、`others`（h を除いた通信先：組み込みと、足したもの）が使っていないもの。外してよい許可。
 * 使っているものは外さない（同じホスト名の別の port や、組み込みの host_permissions と同じ許可を
 * 一緒に外してしまうため）。GHE.com は画面と API の origin が違うので、許可は複数になる
 */
export function releasablePatterns(h: Host, others: readonly Host[]): string[] {
  const used = new Set(others.flatMap((o) => originPatterns(o)));
  return originPatterns(h).filter((p) => !used.has(p));
}

/** 保存する 1 行。URL はオリジン（パスなし） */
export interface CustomRow {
  readonly kind: HostKind;
  readonly url: string;
}

/** 種類と URL から通信先を組む。読めなければ理由を投げる */
export function customHost(kind: unknown, value: unknown): Host {
  if (kind !== "github" && kind !== "gitlab") {
    throw new Error("種類は GitHub か GitLab を選んでください");
  }
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error("ホストの URL を入れてください（例 https://gitlab.example.com）");
  }
  const url = endpoint(value.trim(), "ホストの URL");
  if (url.pathname !== "/" && url.pathname !== "") {
    throw new Error("ホストの URL はオリジンのみ指定してください（パスは含められません。例 https://gitlab.example.com）");
  }
  if (url.hostname.startsWith("[") || url.hostname.includes(":")) {
    throw new Error("IPv6 のアドレスは登録できません（ホスト名か 127.0.0.1 で指定してください）");
  }
  // 末尾のドットと `_` は、同じサーバーに別の id を付けさせないために拒否する
  if (url.hostname.endsWith(".") || url.hostname.includes("_")) {
    throw new Error("ホスト名は、末尾にドットを付けず、_ を使わない形で指定してください");
  }
  const origin = url.origin;
  // id は `ホスト名[_ポート][_http]`。ホスト名に `_` が無いので、読み返すと一通りに決まり、
  // https と http・ポートの違いで別の id になる（PAT のキー `token:<id>` が別の送り先と重ならない）
  const id = url.hostname + (url.port ? `_${url.port}` : "") + (url.protocol === "http:" ? "_http" : "");
  if (!CUSTOM_ID.test(id)) {
    throw new Error(`ホストの名前に使えない形です：${id}`);
  }
  // 種類はユーザが明示したものを保つ（自動判定より優先）。GitHub として登録した *.ghe.com だけは、
  // 画面と API の origin が違う（https://api.<ホスト>）。それ以外の GitHub は Enterprise Server（/api/v3）
  if (kind === "github" && isGheHost(url.hostname)) {
    if (url.port) {
      throw new Error(`GHE.com のホスト ${url.hostname} にはポートを付けられません（API の URL を組めません）`);
    }
    const ghe = `https://api.${url.hostname}`;
    return { id, kind, api: ghe, graphql: `${ghe}/graphql`, web: origin };
  }
  const api = kind === "gitlab" ? `${origin}/api/v4` : `${origin}/api/v3`;
  const graphql = kind === "gitlab" ? api : `${origin}/api/graphql`;
  return { id, kind, api, graphql, web: origin };
}

/** 保存された 1 行の id。読めない行は空（どの id とも一致しない） */
export function rowId(raw: unknown): string {
  const row = raw as { kind?: unknown; url?: unknown } | null;
  try {
    return customHost(row?.kind, row?.url).id;
  } catch {
    return "";
  }
}

/** 保存された配列から、読めた行だけを取る（読めない行は破棄する） */
export function readCustomRows(value: unknown): CustomRow[] {
  if (!Array.isArray(value)) return [];
  const out: CustomRow[] = [];
  for (const raw of value) {
    const row = raw as { kind?: unknown; url?: unknown } | null;
    try {
      const h = customHost(row?.kind, row?.url);
      out.push({ kind: h.kind, url: h.web });
    } catch {
      // 読めない行は省く
    }
  }
  return out;
}

/** 行から通信先を組む（読めない行は外す。`builtIn` と同じ名前の行も外す） */
export function readCustomHosts(value: unknown, builtIn: readonly Host[]): Host[] {
  const out: Host[] = [];
  for (const row of readCustomRows(value)) {
    const h = customHost(row.kind, row.url);
    if (builtIn.some((b) => b.id === h.id) || out.some((o) => o.id === h.id)) continue;
    out.push(h);
  }
  return out;
}

/** ビルドの一覧と設定のホストを合わせた通信先の一覧（ビルドのものが先） */
export async function loadHosts(area: StorageArea, builtIn: readonly Host[]): Promise<Host[]> {
  const got = await area.get(CUSTOM_KEY);
  return [...builtIn, ...readCustomHosts(got[CUSTOM_KEY], builtIn)];
}

/**
 * ホストの許可の型（`chrome.permissions` の origins に渡す）。画面と、API と GraphQL の origin のすべて。
 * GHE.com は画面（`https://<名前>.ghe.com`）と API（`https://api.<名前>.ghe.com`）の origin が違う。
 * Chrome の一致パターンは port を書けないため、port を外す（`http://localhost/*` は port を問わず一致する）。
 */
export function originPatterns(h: Host): string[] {
  const out: string[] = [];
  for (const value of [h.web, h.api, h.graphql]) {
    const u = new URL(value);
    const pattern = `${u.protocol}//${u.hostname}/*`;
    if (!out.includes(pattern)) out.push(pattern);
  }
  return out;
}

/** 保存する行（ホストの web のオリジン） */
export function rowOf(h: Host): CustomRow {
  return { kind: h.kind, url: h.web };
}
