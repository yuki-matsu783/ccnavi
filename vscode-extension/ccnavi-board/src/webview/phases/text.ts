/**
 * フェーズ管理画面に出す言葉。要約に出す範囲の文、絞り込みが当てる文字列、id の重なりの文面。
 *
 * 種類の意味は判定しない（ADR-0035）。ここが作るのは並べて読めるようにした文だけ。
 */
import type { PhaseForm } from "../../core/phases-view.js";

/** 関係と案内（overlap / requires / agent / when）に何か入っているか */
export function hasRelations(phase: PhaseForm): boolean {
  return phase.overlap.length > 0 || phase.requires.length > 0 || phase.agent !== "" || phase.when !== "";
}

/** 「関係と案内」の見出しに添える一言 */
export function relationsNote(phase: PhaseForm): string {
  return hasRelations(phase) ? "（設定あり）" : "（未設定）— 並行できる種類・一緒に要る種類・エージェント・置く目安";
}

/** 要約に出す範囲。inherit ならその綴り、glob が無ければ未設定と言う */
export function scopeText(phase: PhaseForm): string {
  if (phase.inherit) {
    return "inherit";
  }
  return phase.scope.length === 0 ? "（scope 未設定）" : phase.scope.join(", ");
}

/** 範囲のツールチップ */
export function scopeTitle(phase: PhaseForm): string {
  return phase.inherit ? "親の範囲そのまま" : phase.scope.join(", ");
}

/** 並びの欄は 1 つの欄に "," 区切りで出し、打つたびに並びへ戻す */
export function splitList(text: string): readonly string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** 絞り込みが当てる文字列。id・題・範囲・成果物・置く目安に当たる */
export function findText(phase: PhaseForm): string {
  return `${phase.id} ${phase.title} ${phase.scope.join(" ")} ${phase.deliverables.join(" ")} ${phase.when}`.toLowerCase();
}

/** 種類の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}

/** id が重なっているときに下部へ出す文 */
export function duplicateNote(ids: ReadonlySet<string>): string {
  const names = Array.from(ids).map((id) => (id === "" ? "空" : id));
  return `id が重なっている（${names.join(", ")}）。1 つにするまで保存できない`;
}

/** 種類が 1 つも無いときに一覧へ出す文。ファイルの有無と、触れるかで変わる */
export function emptyNote(exists: boolean, editable: boolean): string {
  if (exists) {
    return "種類が無い。種類が 1 つも無いファイルは実行ファイルが読めないので、保存する前に足す";
  }
  return editable
    ? "ファイルが無い（無い層は空で、共通層の種類だけが使われる）。種類を足して保存すると、ファイルが作られる"
    : "ファイルが無い。上の「雛形でファイルを作る」で作ってから直す";
}
