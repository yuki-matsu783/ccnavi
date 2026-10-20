/**
 * origin の URL からのホストの分類（GitHub か GitLab か）と接続先。自動判定の表。
 *
 * 規則は実行ファイルの `review_host.classify` と sh の `ccnavi_host_parse` と同じで、
 * `tests/fixtures/host-kinds.json` の同じ表で突き合わせる。分類に使うホストは、origin から取り出した
 * ホストを小文字にしてポートを除いたもの。照合は `.` の区切りで行う（`evilgithub.com` や `github.com.evil` は
 * GitHub にならない）。scheme の大文字小文字は区別しない。
 *
 * - GitHub: `github.com`、`ghe.com` とそのサブドメイン（`*.ghe.com`）。REST は github.com が
 *   `https://api.github.com`、GHE.com が `https://api.<ホスト>`。GraphQL はその根の `/graphql`。
 *   GHE.com の origin にポートが付いていれば API の URL を組めない（問題として返す）。
 *   GHE.com の API の URL の規則は、実物では未検証
 * - GitLab: 上のどれにも当たらないもの全部。接続先は origin の scheme・ホスト・ポートを保つ
 *
 * これは自動判定の表で、設定画面でユーザが明示した種類（`custom-hosts.ts`）は、これより優先する。
 * 登録していないホストへ通信したり PAT を送ったりする入口ではない（分類して接続先を言うだけ）。
 */
import type { HostKind } from "./hosts.js";

export const GHE_DOMAIN = "ghe.com";

export interface HostClass {
  readonly kind: HostKind | "";
  /** 分類に使ったホスト（小文字・ポート無し） */
  readonly host: string;
  readonly api: string;
  /** GitLab は REST だけなので空 */
  readonly graphql: string;
  readonly path: string;
  /** 接続先を組めない理由（空なら組めた） */
  readonly problem: string;
}

const NONE: HostClass = { kind: "", host: "", api: "", graphql: "", path: "", problem: "" };
const REMOTE = /^(?:https?:\/\/|ssh:\/\/git@|git@)(?:[^/]*@)?(\[[^\]/]+\]|[^/:@]+)(?::(\d+))?[/:]+(.+?)(?:\.git)?\/?$/;

/** 小文字のホスト名が `ghe.com` か、そのサブドメインか（`.` の区切りで照合する） */
export function isGheHost(host: string): boolean {
  return host === GHE_DOMAIN || (host.endsWith(`.${GHE_DOMAIN}`) && host.length > GHE_DOMAIN.length + 1);
}

/** ホスト名だけからの自動判定。GHE.com と github.com は GitHub、それ以外は GitLab */
export function autoKind(hostname: string): HostKind {
  const host = hostname.toLowerCase();
  return host === "github.com" || isGheHost(host) ? "github" : "gitlab";
}

export function classifyOrigin(origin: string): HostClass {
  let url = origin;
  const at = url.indexOf("://");
  if (at > 0) {
    const scheme = url.slice(0, at);
    if (!/[/@:]/.test(scheme)) url = `${scheme.toLowerCase()}${url.slice(at)}`;
  }
  const m = REMOTE.exec(url);
  if (m === null) return NONE;
  const raw = m[1];
  const port = m[2] ?? "";
  const host = raw.toLowerCase();
  const path = m[3];
  if (host === "github.com") {
    return { kind: "github", host, api: "https://api.github.com", graphql: "https://api.github.com/graphql", path, problem: "" };
  }
  if (isGheHost(host)) {
    if (port !== "") {
      return { kind: "github", host, api: "", graphql: "", path, problem: `GHE.com のホスト ${host} にポート :${port} が付いているので、API の URL（https://api.${host}）を組めない` };
    }
    const api = `https://api.${host}`;
    return { kind: "github", host, api, graphql: `${api}/graphql`, path, problem: "" };
  }
  const scheme = url.startsWith("http://") ? "http" : "https";
  return { kind: "gitlab", host, api: `${scheme}://${raw}${port === "" ? "" : `:${port}`}/api/v4`, graphql: "", path, problem: "" };
}
