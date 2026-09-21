/**
 * フェーズ管理画面の図。種類の並び（`PhasesForm`）から、点と線と置き場所を組む純関数。
 *
 * **線に向きは無い。** `requires` は「計画にこの種類を置くなら一緒に置くべき種類」で、
 * 実行ファイル（`approval.py`）が見るのは `plan:` に入っているかどうかだけ。前後は見ない。
 * `overlap` は定義からして対称。順序を持つのは親チケットの `plan:` の並びのほうで、
 * このファイルには順序の情報が無い（ccnavi.md の種類の表）。矢印を描くと、無い制約を描くことになる。
 *
 * **判定はしない（ADR-0035）。** 循環も、到達不能も、孤立も、ここは見つけない。
 * 行き先がこのファイルに無い参照は**黙って線にしないだけ**で、なぜ無いのかは言わない。
 * 綴り違いなのか他の層の種類なのかを決めるのは実行ファイルで、`phasetypes.py` の
 * `reference_problems` が合成した集合で確かめ、無ければ error を出す。画面がその手前で
 * 別の答えを出すと、2 か所が違うことを言う。
 *
 * **このファイルの中しか見えない。** 層をまたぐ参照（プロジェクトの種類が共通層の種類を挙げる）は、
 * 画面には解けない。受け取るのがその層 1 本だけだから。解こうとすると層の合成を拡張が作り直すことになる。
 *
 * **置き場所は id だけで決まる。線は見ない。** 保存のたびに `model` が丸ごと届き直す（ADR-0062）ので、
 * 関係を 1 本直すたびに絵が組み替わると、この画面が唯一やらせる作業（関係を直しながら確かめる）と
 * 正面からぶつかる。**繋がっている種類を近くに寄せることはしない**。寄せると、線を 1 本足しただけで
 * 触っていない点まで動く。近くに置きたいときは人がドラッグで動かし、そのぶんは画面が覚える（`state.ts`）。
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

/** 点の間隔。CSS の `.react-flow__node-phase` の大きさと合わせる */
const COLUMN = 210;
const ROW = 120;
/** 1 行に並べる数 */
const WRAP = 4;

/** 前後の空白を落とした id。画面の他の場所（重なりの検査）と同じ読み方 */
function idOf(phase: { readonly id: string }): string {
  return phase.id.trim();
}

/**
 * 線の名前。**繋げた 1 本の文字列にしない。**
 *
 * id はハイフンを含められる（`phasetypes.py` の `_ID` は `[A-Za-z0-9._-]`）ので、
 * `関係:a--b` の形にすると `x` と `y--z` の組と、`x--y` と `z` の組が同じ文字列になり、
 * 重複除去で**片方が黙って消える**。区切りを跨げない形（JSON の配列）にする。
 * これは React Flow に渡す線の名前でもあるので、一意でないと描くほうでも 1 本になる。
 */
function edgeId(relation: Relation, a: string, b: string): string {
  return JSON.stringify([relation, a, b]);
}

/**
 * 線を組む。行き先がこのファイルに無いものは落とす。同じ組は 1 本にする
 * （`a` が `b` を、`b` が `a` を挙げていても 1 本）。
 *
 * 辿るのは**先に出てきた種類だけ**（`kept`）。同じ id が 2 つあるとき、点は先のほうを出すので、
 * 後ろの重複から線を作ると、出ている点の欄に無い関係が描かれることになる。
 */
function edgesOf(kept: readonly PhasesForm["phases"][number][], known: ReadonlySet<string>): GraphEdge[] {
  const seen = new Map<string, GraphEdge>();
  for (const phase of kept) {
    const from = idOf(phase);
    for (const relation of ["requires", "overlap"] as const) {
      for (const raw of phase[relation]) {
        const to = raw.trim();
        // 自分自身を挙げている種類は、実行ファイルが --lint で警告する。ここは線にしないだけ
        if (!known.has(to) || to === from) {
          continue;
        }
        const [a, b] = from < to ? [from, to] : [to, from];
        const id = edgeId(relation, a, b);
        if (!seen.has(id)) {
          seen.set(id, { id, a, b, relation });
        }
      }
    }
  }
  return Array.from(seen.values()).sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0));
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
  const kept = ids.map((id) => first.get(id) as PhasesForm["phases"][number]);
  const edges = edgesOf(kept, new Set(ids));

  // 置き場所は id の順の格子。線は見ない（頭のコメント）
  const nodes = ids.map((id, index) => {
    const phase = first.get(id) as PhasesForm["phases"][number];
    return {
      id,
      title: phase.title.trim(),
      kind: phase.kind,
      review: phase.review,
      x: (index % WRAP) * COLUMN,
      y: Math.floor(index / WRAP) * ROW,
    };
  });
  return { nodes, edges, unnamed };
}

// ---- 人がドラッグで動かした位置（画面の控え。`phases.yml` には書かない）

/** 点の置き場所の控え。鍵は種類の id */
export type Spots = Record<string, { readonly x: number; readonly y: number }>;

/**
 * ドラッグで動かした先を控えに入れる。px は丸める（控えを読みやすく保つ）。
 *
 * ここ（`core/`）に置いてあるのは、`state.ts` が `acquireVsCodeApi` を読み、node のテストから
 * import できないため。単体で試せる形にしておく（CB-T191）。
 *
 * ドラッグそのものは **jsdom** で通す（CB-D80）。happy-dom では d3-drag の待ちが
 * 終わらずテストが固まる（`test/helpers/jsdom.ts` の頭）。
 */
export function withSpot(spots: Spots, id: string, x: number, y: number): Spots {
  return { ...spots, [id]: { x: Math.round(x), y: Math.round(y) } };
}

/** 図に出ている種類の控えだけを残す。変わらなければ元のものをそのまま返す（描き直しを起こさない） */
export function keepSpots(spots: Spots, ids: readonly string[]): Spots {
  const next: Spots = {};
  for (const id of ids) {
    if (spots[id] !== undefined) {
      next[id] = spots[id];
    }
  }
  return Object.keys(next).length === Object.keys(spots).length ? spots : next;
}
