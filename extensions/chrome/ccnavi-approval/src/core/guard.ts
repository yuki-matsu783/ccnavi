/**
 * 書き込みの保護。判定ではなく、書く先の形だけを見る。
 *
 * 判定は Python が出し、書く先（親のブランチ・置き場の下）も Python が決める。ここは service worker と
 * 書く流れ（`write.ts`）が、送る直前にもう一度見る二重の確認。画面で XSS が起きて service worker に
 * 書く頼みを直に送られても、保護されたブランチ・統合先・置き場の外・登録していないリポジトリには書かない。
 */

/** 書く先にしない名前（`ccnavi-push-approved.sh` の一覧と同じ）。大文字小文字をそろえて比べる */
export const PROTECTED = /^(?:main|master|develop|release|release[-/].*)$/i;

/** 置き場のパスの既定（`src/ccnavi/infra/settings.py` の DEFAULT_TICKETS・DEFAULT_APPROVED と同じ） */
export const DEFAULT_TICKETS = "wip/proposals";
export const DEFAULT_APPROVED = ".ccnavi/approved";

export interface Places {
  readonly tickets: string;
  readonly approved: string;
}

/**
 * 置き場のパス。既定に固定する（`ccnavi_chrome._placement` と同じ）。統合先の `.claude/settings.json` の
 * `env` は読まない。`env` で置き場を動かしたワークスペースでも断らず、既定の置き場を読み書きする
 */
export const DEFAULT_PLACES: Places = Object.freeze({ tickets: DEFAULT_TICKETS, approved: DEFAULT_APPROVED });

/** ルートから始まる形（`/`・`\`・`~`・ドライブ名）。`ccnavi_chrome._ROOTED` と同じ */
const ROOTED = /^(?:[/\\~]|[A-Za-z]:)/;

/**
 * ツリーの中に留まる相対パスか（`ccnavi_chrome._check_rel` と同じ形の検査）。ルートから始まるもの・バックスラッシュを
 * 含むもの・空や `.` や `..` の要素を持つものは断る。先頭の文字列だけを比べると `wip/proposals/../../x` が置き場の下に見えるため
 */
export function isPlainRel(path: string): boolean {
  if (path === "" || ROOTED.test(path) || path.includes("\\")) {
    return false;
  }
  return path.split("/").every((part) => part !== "" && part !== "." && part !== "..");
}

/** パスが置き場（提案・承認済み）の下か。置き場の外へ出る形のパスは下と見なさない */
export function underPlaces(path: string, places: Places): boolean {
  return isPlainRel(path) && [places.tickets, places.approved].some((p) => path.startsWith(`${p}/`));
}

/** 書く先の名前と書くパスを見る。書けなければ理由（空なら書ける） */
export function writeRefusal(branch: string, integration: string, paths: readonly string[], places: Places): string {
  if (PROTECTED.test(branch) || branch.toLowerCase() === integration.toLowerCase()) {
    return `${branch} は保護されたブランチまたは統合先の名前のため、反映できません`;
  }
  const outside = paths.filter((p) => !underPlaces(p, places));
  if (outside.length > 0) {
    return `反映先のパスが置き場（${places.tickets}/・${places.approved}/）の外にあります（${outside.join(", ")}）`;
  }
  return "";
}
