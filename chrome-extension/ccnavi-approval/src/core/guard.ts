/**
 * 書き込みの守り。判定ではなく、書く先の形だけを見る。
 *
 * 判定は Python が出し、書く先（親のブランチ・置き場の下）も Python が決める。ここは service worker と
 * 書く流れ（`write.ts`）が、送る直前にもう一度見る二重の守り。画面で XSS が起きて service worker に
 * 書く頼みを直に送られても、保護されたブランチ・統合先・置き場の外・登録していないリポジトリには書かない。
 */

/** 書く先にしない名前（`ccnavi-push-approved.sh` の一覧と同じ）。大文字小文字をそろえて比べる */
export const PROTECTED = /^(?:main|master|develop|release|release[-/].*)$/i;

/** 置き場の綴りの既定（`ccnavi/infra/settings.py` の DEFAULT_TICKETS・DEFAULT_APPROVED と同じ） */
export const DEFAULT_TICKETS = "wip/proposals";
export const DEFAULT_APPROVED = ".ccnavi/approved";
const TICKETS_ENV = "CCNAVI_TICKETS_PROPOSAL";
const APPROVED_ENV = "CCNAVI_TICKETS_APPROVED";

export interface Places {
  readonly tickets: string;
  readonly approved: string;
}

function place(value: unknown, fallback: string, what: string): string {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string" || /^(?:[/\\~]|[A-Za-z]:)/.test(value)) {
    throw new Error(`${what}の置き場の綴りを読めない（リポジトリの外を指すか、文字列でない）`);
  }
  const clean = value.replace(/^\/+|\/+$/g, "");
  if (clean === "" || clean.split("/").some((p) => p === "" || p === "." || p === "..") || clean.includes("\\")) {
    throw new Error(`${what}の置き場の綴りを読めない: ${value}`);
  }
  return clean;
}

/** 統合先の `.claude/settings.json` の `env` から置き場の綴りを読む（無ければ既定） */
export function placesFromSettings(text: string | null): Places {
  let env: Record<string, unknown> = {};
  if (text !== null) {
    const data = JSON.parse(text) as { env?: unknown };
    if (data && typeof data === "object" && data.env && typeof data.env === "object") env = data.env as Record<string, unknown>;
  }
  return { tickets: place(env[TICKETS_ENV], DEFAULT_TICKETS, "提案"), approved: place(env[APPROVED_ENV], DEFAULT_APPROVED, "承認済み") };
}

/** パスが置き場（提案・承認済み）の下か */
export function underPlaces(path: string, places: Places): boolean {
  return [places.tickets, places.approved].some((p) => path.startsWith(`${p}/`));
}

/** 書く先の名前と書くパスを見る。書けなければ理由（空なら書ける） */
export function writeRefusal(branch: string, integration: string, paths: readonly string[], places: Places): string {
  if (PROTECTED.test(branch) || branch.toLowerCase() === integration.toLowerCase()) {
    return `${branch} は保護されたブランチか統合先の名前なので書かない`;
  }
  const outside = paths.filter((p) => !underPlaces(p, places));
  if (outside.length > 0) {
    return `書くパスが置き場（${places.tickets}/・${places.approved}/）の外にある（${outside.join(", ")}）`;
  }
  return "";
}
