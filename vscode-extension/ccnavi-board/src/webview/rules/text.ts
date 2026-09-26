/**
 * ルール設定画面に出す言葉。行の要約、絞り込みが当てる文字列、件数の出し方。
 *
 * 判定はしない（当たる・当たらないは実行ファイルの `--test` が言う）。ここが作るのは
 * 「このルールは何を止めるか」を畳んだままでも読める形に縮めた文だけ。
 */
import type { RuleForm, Section } from "../../core/rules-view.js";

/** 1 行に縮める。空白を畳んで、長ければ後ろを落とす */
function excerpt(text: string, max: number): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length > max ? `${one.slice(0, max)}…` : one;
}

/** 要約の左の id。未設定なら薄く言う */
export function summaryId(rule: RuleForm): string {
  return rule.id === "" ? "（id 未設定）" : rule.id;
}

/** 要約のツール。空は「全ツール」＝ どのツールにも当たる */
export function summaryMatch(rule: RuleForm): string {
  return rule.match === "" ? "（全ツール）" : rule.match;
}

/**
 * 要約に添える文。deny は拒否の文面、ask と allow は渡す文（message はどこにも届かないため）。
 */
export function summaryNote(section: Section, rule: RuleForm): string {
  const shown = section === "deny" ? rule.message : rule.additionalContext || rule.additionalContextOnce;
  return shown === "" ? "" : excerpt(shown, 60);
}

/**
 * 刻み（`every`）とコンテキストの 4 欄のどれかがあるか。刻みだけを持つルールでも
 * 「コンテキストの追加」が開いて出るように、刻みも数える。
 */
export function hasContext(rule: RuleForm): boolean {
  return (
    rule.additionalContext !== "" ||
    rule.additionalContextOnce !== "" ||
    rule.additionalContextFile !== "" ||
    rule.additionalContextOnceFile !== "" ||
    rule.every !== ""
  );
}

/** 「コンテキストの追加」の見出しに続ける言葉 */
export function contextSummary(rule: RuleForm): string {
  return hasContext(rule)
    ? "（設定あり）"
    : "（未設定）。ヒットしたときにモデルへ渡すプロンプトやファイルと、何回に 1 度渡すか（every）";
}

/** ask と allow に message が残っているときに出す断り。lint が error にする */
export function staleMessage(section: Section): string {
  const where = section === "ask" ? "人の確認ダイアログにしか出ない" : "どこにも届かない";
  return `${section} の message は${where}ので、lint が error にします。モデルに渡すプロンプトは additionalContext に移してください: `;
}

/**
 * 絞り込みが当てる文字列。当てるのは**書いてある値そのもの**（id・ツール・パターン・文面・
 * 渡す文）で、要約に出ない全文にも当たる。
 */
export function findText(rule: RuleForm): string {
  return `${rule.id} ${rule.match} ${rule.pattern} ${rule.message} ${rule.additionalContext} ${rule.additionalContextOnce}`.toLowerCase();
}

/**
 * タイプごとの件数。絞り込んでいないときは全体の数だけ。絞り込み中は「一致 / 全体」で、
 * 一致しないのに開いたままで見えている行があればその数も言う。
 */
export function countText(query: string, shown: number, total: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}
