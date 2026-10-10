/**
 * フェーズ管理画面に出す言葉。要約に出す範囲の文、絞り込みが当てる文字列、id の重なりの文面。
 *
 * 定義の意味は判定しない。ここが作るのは並べて読めるようにした文だけ。
 */
import type { PhasesGraph } from "../../core/phases-graph.js";
import type { PhaseForm, PhasesForm } from "../../core/phases-view.js";

/** ほかの定義との関係と補足（overlap / requires / after / agent / when）に何か入っているか */
export function hasRelations(phase: PhaseForm): boolean {
  return phase.overlap.length > 0 || phase.requires.length > 0 || phase.after.length > 0 || phase.agent !== "" || phase.when !== "";
}

/** 「ほかの定義との関係・補足」の見出しにつける一言 */
export function relationsNote(phase: PhaseForm): string {
  return hasRelations(phase) ? "（設定あり）" : "（未設定）。並行できる定義・一緒に必要な定義・先に済ませる定義・案内するエージェント・使う場面";
}

/** 要約に出す範囲。inherit ならその表記、glob が無ければ未設定と言う */
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

/** リストの欄は 1 つの欄に "," 区切りで出し、打つたびにリストへ戻す */
export function splitList(text: string): readonly string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** 絞り込みが当てる文字列。id・題・範囲・成果物・使う場面に当たる */
export function findText(phase: PhaseForm): string {
  return `${phase.id} ${phase.title} ${phase.scope.join(" ")} ${phase.deliverables.join(" ")} ${phase.when}`.toLowerCase();
}

/** 定義の数。絞り込んでいるときは「一致 / 全体（開いたまま N）」 */
export function countText(total: number, query: string, shown: number, kept: number): string {
  if (query === "") {
    return String(total);
  }
  return `${shown} / ${total}${kept > 0 ? `（開いたまま ${kept}）` : ""}`;
}

/** id が重なっているときに下部へ出す文 */
export function duplicateNote(ids: ReadonlySet<string>): string {
  const names = Array.from(ids).map((id) => (id === "" ? "空" : id));
  return `id が重なっています（${names.join(", ")}）。1 つにするまで保存できません`;
}

/**
 * 図の下に出す注意。当てはまるときだけ出す。 線の読み方は凡例（`Graph.tsx` の `Legend`）が持ち、
 * 細かい説明（「ユーザが見る」の意味、待ち方が決まる時点）はバッジのツールチップとヘルプに置く。
 * 毎回 6 文を並べていたときは、要る注意がほかの文に埋もれていた。
 *
 * 線が落ちた理由は言わない。 表記違いかもしれないし、ほかの設定の定義かもしれない。
 * 決めるのは実行ファイルで、`phasetypes.py` の `reference_problems` が合成した集合で
 * 確かめ、無ければ error を出す。画面がその手前で「ほかの設定の定義だ」と言うと、保存したときに
 * 実行ファイルが逆のことを言う。ここは「線にしていない」までしか言わない。
 */
export function graphNotices(graph: PhasesGraph, form: PhasesForm): readonly string[] {
  const out: string[] = [];
  // after を 1 つでも書いていれば言う（線にならない、ほかの設定を指す after も使われないのは同じ）
  const hasAfter = form.phases.some((phase) => phase.after.some((id) => id.trim() !== ""));
  if (form.order === "sequential" && hasAfter) {
    out.push("待ち方が sequential なので、after は判定に使われません。全体計画は plan: に並べた順に一つずつ進みます");
  }
  if (graph.dropped > 0) {
    out.push(`このファイルに無い定義を指す関係が ${graph.dropped} 件あり、線にしていません（入力ミスなら保存のときの検証で分かります）`);
  }
  if (graph.unnamed > 0) {
    out.push(`id が空の定義は図に出ません（${graph.unnamed} 件）`);
  }
  return out;
}

/** 定義が 1 つも無いときに一覧へ出す文。ファイルの有無で変わる */
export function emptyNote(exists: boolean): string {
  if (exists) {
    return "定義がありません。定義が 1 つも無いファイルは実行ファイルが読めないので、保存する前に足してください";
  }
  return "ファイルがありません（この設定に定義が無い、という正常な状態です）。定義を足して保存すると、ファイルが作られます";
}
