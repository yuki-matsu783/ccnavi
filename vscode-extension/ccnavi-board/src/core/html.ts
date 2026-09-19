/**
 * HTML を文字列で組み立てる画面（ルール設定・リスク管理・フェーズ管理・プロジェクト管理）が使う逃がし。
 *
 * ボードの画面は React で、文字列を組み立てないのでこれを使わない（React が同じことをする）。
 * 残り 4 画面を React に移し終えたら、このファイルごと要らなくなる。
 */

/** HTML の特殊文字を実体参照にする。& を最初に変換して二重変換を避ける */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
