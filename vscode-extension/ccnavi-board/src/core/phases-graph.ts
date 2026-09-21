/**
 * フェーズ管理画面の図。種類の並び（`PhasesForm`）から、点と線と置き場所を組む純関数。
 *
 * **線に向きは無い。** `requires` は「計画にこの種類を置くなら一緒に置くべき種類」で、
 * 実行ファイル（`approval.py`）が見るのは `plan:` に入っているかどうかだけ。前後は見ない。
 * `overlap` は定義からして対称。順序を持つのは親チケットの `plan:` の並びのほうで、
 * このファイルには順序の情報が無い（ccnavi.md の種類の表）。矢印を描くと、無い制約を描くことになる。
 *
 * **判定はしない（ADR-0035）。** 循環・到達不能・孤立を、ここは見つけない。実行ファイルも
 * 見ていない（`phasetypes.py` が見るのは自己参照だけ）。画面が言えば、実行ファイルが出さない
 * 答えを画面が出すことになる。ここが組むのは「ファイルに書いてあるものを並べ直した形」だけ。
 *
 * **このファイルの中しか見えない。** 層をまたぐ参照（プロジェクトの種類が共通層の種類を挙げる）は、
 * 画面には解けない。画面が受け取るのはその層 1 本だけだから。解こうとすると層の合成を
 * 拡張が作り直すことになる。ここは行き先がこのファイルに無い参照を**黙って線にしない**。
 * 「無い」とは言わない。言うのは画面の帯の一言（`text.ts` の `graphNote`）で、種類ごとには言わない。
 *
 * **置き場所は id から決まる。** 保存のたびに `model` が丸ごと届き直す（ADR-0062）ので、
 * 数え上げの順や前の絵に依らない置き方にしておかないと、1 つ直すたびに全体が組み替わる。
 * ここは「同じ id の集合なら同じ絵」になる。人が摘まんで動かしたぶんは画面が覚える（`state.ts`）。
 */
import type { PhaseKind, PhasesForm, Review } from "./phases-view.js";

/** 点 1 つ。`x` と `y` は図の座標で、`phases.yml` には書かない（人が持つ設定に座標は入れない） */
export interface GraphNode {
  readonly id: string;
  readonly title: string;
  readonly kind: PhaseKind;
  readonly review: Review;
  readonly x: number;
  readonly y: number;
}

/** 線の種類。`requires` は一緒に置く、`overlap` は並行してよい。どちらも向きは無い */
export type Relation = "requires" | "overlap";

/** 線 1 本。`a` と `b` は辞書順で、同じ組を 2 度描かない */
export interface GraphEdge {
  readonly id: string;
  readonly a: string;
  readonly b: string;
  readonly relation: Relation;
}

export interface PhasesGraph {
  readonly nodes: readonly GraphNode[];
  readonly edges: readonly GraphEdge[];
  /** 図に出せなかった種類の数（id が空で、指すことも指されることもできない） */
  readonly unnamed: number;
}

/** 点の間隔。CSS の `.phase-node` の大きさと合わせる */
const COLUMN = 210;
const ROW = 120;
/** 1 行に並べる数。繋がった組も、独りの種類も、これで折り返す */
const WRAP = 4;

/** 前後の空白を落とした id。画面の他の場所（重なりの検査）と同じ読み方 */
function idOf(phase: { readonly id: string }): string {
  return phase.id.trim();
}

/**
 * 線を組む。行き先がこのファイルに無いものは落とす（層をまたぐ参照かもしれないので、
 * 無いとは言わない）。同じ組は 1 本にする（`a` が `b` を、`b` が `a` を挙げていても 1 本）。
 */
function edgesOf(form: PhasesForm, known: ReadonlySet<string>): GraphEdge[] {
  const seen = new Map<string, GraphEdge>();
  for (const phase of form.phases) {
    const from = idOf(phase);
    if (!known.has(from)) {
      continue;
    }
    for (const relation of ["requires", "overlap"] as const) {
      for (const raw of phase[relation]) {
        const to = raw.trim();
        // 自分自身を挙げている種類は、実行ファイルが --lint で警告する。ここは線にしないだけ
        if (!known.has(to) || to === from) {
          continue;
        }
        const [a, b] = from < to ? [from, to] : [to, from];
        const id = `${relation}:${a}--${b}`;
        if (!seen.has(id)) {
          seen.set(id, { id, a, b, relation });
        }
      }
    }
  }
  return Array.from(seen.values()).sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
}

/**
 * 繋がっている組に分ける。線の種類は問わない（`requires` でも `overlap` でも、
 * 一緒に読むものは近くに置く）。組の中は id の順、組そのものは「大きい順・先頭の id の順」。
 * どちらも id だけで決まるので、同じ設定なら同じ並びになる。
 */
function groupsOf(ids: readonly string[], edges: readonly GraphEdge[]): string[][] {
  const near = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const edge of edges) {
    near.get(edge.a)?.push(edge.b);
    near.get(edge.b)?.push(edge.a);
  }
  const seen = new Set<string>();
  const groups: string[][] = [];
  for (const start of ids) {
    if (seen.has(start)) {
      continue;
    }
    const group: string[] = [];
    const stack = [start];
    while (stack.length > 0) {
      const id = stack.pop() as string;
      if (seen.has(id)) {
        continue;
      }
      seen.add(id);
      group.push(id);
      stack.push(...(near.get(id) ?? []));
    }
    groups.push(group.sort());
  }
  return groups.sort((x, y) => y.length - x.length || (x[0] < y[0] ? -1 : 1));
}

/**
 * 図を組む。id が空の種類は出さない（指すことも指されることもできないので、線を持てない）。
 * 同じ id が 2 つあるときは先に出てきたほうだけを出す（保存は画面が止めるので、直すまでの間の姿）。
 */
export function graphOf(form: PhasesForm): PhasesGraph {
  const first = new Map<string, PhasesForm["phases"][number]>();
  let unnamed = 0;
  for (const phase of form.phases) {
    const id = idOf(phase);
    if (id === "") {
      unnamed += 1;
      continue;
    }
    if (!first.has(id)) {
      first.set(id, phase);
    }
  }
  const ids = Array.from(first.keys()).sort();
  const edges = edgesOf(form, new Set(ids));
  const groups = groupsOf(ids, edges);

  // 繋がった組を先に、独りの種類はまとめて後ろへ。どちらも WRAP で折り返す
  const linked = groups.filter((group) => group.length > 1);
  const alone = groups.filter((group) => group.length === 1).map((group) => group[0]);
  const place = new Map<string, { x: number; y: number }>();
  let row = 0;
  for (const group of linked) {
    group.forEach((id, index) => {
      place.set(id, { x: (index % WRAP) * COLUMN, y: (row + Math.floor(index / WRAP)) * ROW });
    });
    row += Math.ceil(group.length / WRAP);
  }
  alone.forEach((id, index) => {
    place.set(id, { x: (index % WRAP) * COLUMN, y: (row + Math.floor(index / WRAP)) * ROW });
  });

  const nodes = ids.map((id) => {
    const phase = first.get(id) as PhasesForm["phases"][number];
    const at = place.get(id) ?? { x: 0, y: 0 };
    return { id, title: phase.title.trim(), kind: phase.kind, review: phase.review, x: at.x, y: at.y };
  });
  return { nodes, edges, unnamed };
}
