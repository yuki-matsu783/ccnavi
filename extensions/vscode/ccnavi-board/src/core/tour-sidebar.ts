/**
 * 左パネルの案内（VS Code のウォークスルー）の決まり。VS Code の API に触れないので単体で試せる。
 *
 * 左パネルは標準のツリー表示で吹き出しを出せない。案内の本文は `package.json` の `contributes.walkthroughs` と
 * `media/tour/` の Markdown に置き、版が変わった最初の起動で開く（`src/tour.ts`）。
 * ウォークスルーの段は、左パネルを開く → プロジェクトを clone してチケットで進捗を管理する → ルール → リスク → フェーズ → テンプレートの順に並ぶ。
 * 左パネルを開く段のあとの各段が、それぞれ画面の基本の案内（`webview/tours/<画面>.yml` の先頭の案内）を動かす。
 */

/** `package.json` の `contributes.walkthroughs[].id` */
export const SIDEBAR_WALKTHROUGH = "ccnaviBoard.sidebar";

/** 案内を動かせる画面。チケット制御（CCNAVI_TICKET_CONTROL）が有効なときだけ開けるものに印を付ける */
export const TOUR_SCREENS: Readonly<Record<string, { readonly needsTickets: boolean }>> = {
  projects: { needsTickets: false },
  board: { needsTickets: true },
  rules: { needsTickets: false },
  risk: { needsTickets: true },
  phases: { needsTickets: true },
  templates: { needsTickets: true },
};

/**
 * ウォークスルーの段のボタンが動かす画面の並び（コマンド → 順に動かす画面）。
 * 「プロジェクトを clone してチケットで進捗を管理する」の段は、プロジェクト管理 → チケット管理の 2 画面を続けて動かす
 */
export const WALKTHROUGH_TOURS: Readonly<Record<string, readonly string[]>> = {
  "ccnaviBoard.tourBasics": ["projects", "board"],
  "ccnaviBoard.tourRules": ["rules"],
  "ccnaviBoard.tourRisk": ["risk"],
  "ccnaviBoard.tourPhases": ["phases"],
  "ccnaviBoard.tourTemplates": ["templates"],
};

/** チケット制御が無効なら、開けない画面を外す */
export function playableScreens(screens: readonly string[], ticketsEnabled: boolean): readonly string[] {
  return screens.filter((screen) => ticketsEnabled || TOUR_SCREENS[screen]?.needsTickets !== true);
}
