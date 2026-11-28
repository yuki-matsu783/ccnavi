/**
 * 保存後の通知に出す、検証（`--lint`）の要約行。実行ファイルの答え（`error N件、warn N件、info N件`）の最後の行を、
 * 深刻度の語（error・warn・info）を言い換えずにそのまま出す。リスク・ルール・フェーズの3画面で同じ言い方にそろえる。
 */
export function lintSummary(report: string): string {
  return report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
}
