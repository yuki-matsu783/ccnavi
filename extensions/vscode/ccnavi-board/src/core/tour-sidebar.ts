/**
 * 左パネルの案内（VS Code のウォークスルー）の決まり。VS Code の API に触れないので単体で試せる。
 *
 * 左パネルは標準のツリー表示で吹き出しを出せない。案内の本文は `package.json` の `contributes.walkthroughs` と
 * `media/tour/` の Markdown に置き、版が変わった最初の起動で開く（`src/tour.ts`）。
 */

/** `package.json` の `contributes.walkthroughs[].id` */
export const SIDEBAR_WALKTHROUGH = "ccnaviBoard.sidebar";

/**
 * 最初に動かす基本の案内の画面（`webview/tours/<画面>.yml` の先頭の案内）。順に動かす。
 * チケット管理はチケット制御（CCNAVI_TICKET_CONTROL）が有効なときだけ開けるので、無効なら外す
 */
export function basicTourScreens(ticketsEnabled: boolean): readonly string[] {
  return ticketsEnabled ? ["projects", "board"] : ["projects"];
}
