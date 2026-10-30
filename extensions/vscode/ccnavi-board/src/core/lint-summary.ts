/**
 * 保存後の通知に出す、検証（`--lint`）の要約行。実行ファイルの答え（`error N 件、warn N 件、info N 件`）の
 * 英語の深刻度を日本語に言い換える。リスク・ルール・フェーズの 3 画面で同じ言い方にそろえる。
 */
export function lintSummary(report: string): string {
  const tail = report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  return tail.replace(/\berror\b/g, "エラー").replace(/\bwarn\b/g, "警告").replace(/\binfo\b/g, "情報");
}
