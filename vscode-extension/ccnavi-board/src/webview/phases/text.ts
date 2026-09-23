/**
 * フェーズ管理画面に出す言葉。要約に出す範囲の文、絞り込みが当てる文字列、id の重なりの文面。
 *
 * 種類の意味は判定しない（ADR-0035）。ここが作るのは並べて読めるようにした文だけ。
 */
import type { PhasesGraph } from "../../core/phases-graph.js";
import type { PhaseForm } from "../../core/phases-view.js";

/** ほかの種類との関係と補足（overlap / requires / after / agent / when）に何か入っているか */
export function hasRelations(phase: PhaseForm): boolean {
  return phase.overlap.length > 0 || phase.requires.length > 0 || phase.after.length > 0 || phase.agent !== "" || phase.when !== "";
}

/** 「ほかの種類との関係・補足」の見出しに添える一言 */
export function relationsNote(phase: PhaseForm): string {
  return hasRelations(phase) ? "（設定あり）" : "（未設定）— 並行・一緒に必要・先に済ませる種類、担当エージェント、使う場面";
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

/** 絞り込みが当てる文字列。id・題・範囲・成果物・使う場面に当たる */
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

/**
 * 図の下に出す一言。**この絵が何を描いていないか**を言う。
 *
 * 言うのは、線の読み方（向きを持つのは `after` だけ）、「人が見る」が種類の宣言であること、
 * このファイルに無い種類を指す参照は線にならないこと（他の層の `after` も描かない）、
 * 判定が使う待ち方は承認のときに決まること、id の無い種類は出ないこと。
 *
 * **読み方をここで言うのは、線にラベルを付けないから。** 同じ組が両方の関係を持つとラベルどうしが
 * 重なって片方が読めない（`Graph.tsx` の `edgesOf`）。
 *
 * **線が落ちた理由は言わない。** 綴り違いかもしれないし、他の層の種類かもしれない。
 * 決めるのは実行ファイルで、`phasetypes.py` の `reference_problems` が合成した集合で
 * 確かめ、無ければ error を出す。画面がその手前で「他の層だ」と言うと、保存したときに
 * 実行ファイルが逆のことを言う（ADR-0035）。ここは「線にならない」までしか言わない。
 */
export function graphNote(graph: PhasesGraph): string {
  const parts = [
    `${graph.nodes.length} 種類・${graph.edges.length} 本`,
    "実線は requires（一緒に置く）、破線は overlap（並行してよい）で、どちらも向きは無い",
    graph.order === "dag"
      ? "矢印は after（待たれる側 → 待つ側）。辺で繋がっていない種類は並行して進む。列は after の深さ"
      : "矢印は after。待ち方が sequential なので、after は判定に効かない（全体計画は一直線）",
    "「人が見る」は種類の宣言（review）。計画の延期や実績のリスクで実際に見る場所は変わる",
    "このファイルに無い種類を指す線は出ない（綴り違いか、他の層の種類か。どちらかは保存のときの検証が言う）。他の層の after は描かないので、その種類は根に見える",
    "判定が使う待ち方は、層を合わせたうえで親チケットの承認のときに決まる",
  ];
  if (graph.unnamed > 0) {
    parts.push(`id が空の種類は出ない（${graph.unnamed} 件）`);
  }
  return parts.join("。 ");
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
