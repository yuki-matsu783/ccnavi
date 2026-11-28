/**
 * 検証（`--lint`）の深刻度（`error`・`warn`・`info`）を、画面に出す日本語に言い換える。
 * 保存後の通知の要約行と、テンプレート管理・プロジェクト管理・取り込み待ちの指摘の行で同じ言い方にそろえる。
 */
const SEVERITY_TEXT: Readonly<Record<string, string>> = { error: "エラー", warn: "警告", info: "情報" };

/** 深刻度1つの言葉。知らない値はそのまま返す */
export function severityText(severity: string): string {
  return SEVERITY_TEXT[severity] ?? severity;
}

/** 保存後の通知に出す要約行。実行ファイルの答え（`error N件、warn N件、info N件`）の最後の行を言い換える */
export function lintSummary(report: string): string {
  const tail = report.split("\n").filter((l) => l.trim() !== "").pop() ?? "";
  return tail.replace(/\b(error|warn|info)\b/g, (word) => severityText(word));
}
