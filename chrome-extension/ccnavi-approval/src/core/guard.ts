/**
 * 書き込みの保護。判定ではなく、書く先の形だけを見る。
 *
 * 判定は Python が出し、書く先（親のブランチ・置き場の下）も Python が決める。ここは service worker と
 * 書く流れ（`write.ts`）が、送る直前にもう一度見る二重の確認。画面で XSS が起きて service worker に
 * 書く頼みを直に送られても、保護されたブランチ・統合先・置き場の外・登録していないリポジトリには書かない。
 */

/** 書く先にしない名前（`ccnavi-push-approved.sh` の一覧と同じ）。大文字小文字をそろえて比べる */
export const PROTECTED = /^(?:main|master|develop|release|release[-/].*)$/i;

/** 置き場のパスの既定（`ccnavi/infra/settings.py` の DEFAULT_TICKETS・DEFAULT_APPROVED と同じ） */
export const DEFAULT_TICKETS = "wip/proposals";
export const DEFAULT_APPROVED = ".ccnavi/approved";

export interface Places {
  readonly tickets: string;
  readonly approved: string;
}

/**
 * 置き場のパス。既定に固定する（`ccnavi_chrome._placement` と同じ）。統合先の `.claude/settings.json` の
 * `env` は読まない。置き場を既定から動かしたワークスペースは Chrome の対象外
 */
export const DEFAULT_PLACES: Places = Object.freeze({ tickets: DEFAULT_TICKETS, approved: DEFAULT_APPROVED });

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
