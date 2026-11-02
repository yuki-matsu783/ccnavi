/**
 * 設定ファイル（rules.yml・phases.yml・risks.yml）を読む3つの文書（`rules-doc.ts`・`phases-doc.ts`・`risk-doc.ts`）の
 * 共通の読み取り。ここが拾うのは、実行ファイルも読めない「読み込みを止める」問題だけ。
 * versionの欠落のように保存で直せるものは、各文書の`problems`（画面の帯）に残す。
 */
import { isMap, type Document } from "yaml";

/**
 * 読み込みを止める問題。画面はフォームを出さず、エラー画面（［エディタで開く］［更新］）を出す。
 * - YAMLの構文エラー（行番号付き。`yaml`の`e.message`は英語のまま、日本語の前置きと行番号を付ける）
 * - 最上位がマップ（キーと値の組の集まり）でない
 */
export function blockingProblems(doc: Document): string[] {
  const found: string[] = [];
  for (const e of doc.errors) {
    const line = e.linePos?.[0]?.line;
    const detail = e.message.split("\n")[0].replace(/ at line \d+, column \d+:?$/, "");
    found.push(`YAMLの構文エラー${line === undefined ? "" : `（${line}行目）`}: ${detail}`);
  }
  if (found.length === 0 && doc.contents !== null && !isMap(doc.contents)) {
    found.push("最上位がマップ（キーと値の組の集まり）ではありません");
  }
  return found;
}
