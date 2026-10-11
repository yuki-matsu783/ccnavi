/**
 * 計画の図の中身。実行ファイルが返す計画 1 つぶん（`plans` の 1 件。`approvemodel.ts` の `ApprovePlan`）から、
 * 点・線・置き場所を組む純関数。描くのは共有の図の部品（`webview/shared/PlanGraph.tsx`）で、承認のオーバーレイは
 * 読むだけで、ワークフロー編集タブは線を引き直して使う。
 *
 * **判定はしない。** どの項が終端に当たらないか（`loose`）、すぐ始まるか（`ready`）、延期を誰が引き受けるか
 * （`review_at`）、順序の検査の理由（`problems`）は実行ファイルが決めて返し、ここは印を付けるだけ。
 * ここが組むのは、線と点だけで決まる形の扱いに限る。
 *
 * - **start と end の仮の点。** 先行の無い項へ start から、後続の無い項から end へ線を引く。どちらも図だけの
 *   もので保存しない（実行ファイルは知らない）。この 2 種類の線は引き直せない（先行を全部消せば start からの
 *   線が出る）。フィードバック計画では start を「全体計画のレビュー後」、end を「親を閉じる」と名付ける
 * - **置き場所。** start からの最長の段数で列に分け、列の中は番号順。線が循環していても（ワークフロー編集タブで
 *   引いている途中など）止まらず、循環に戻った線は段数に数えない（循環を言うのは引けない線の検査と実行ファイル）
 * - **引けない線（`canConnect`）。** 項から項へだけ引ける。自分へ・start と end に触れる線・固定した番号
 *   （子が承認された番号）へ入る線・循環になる線を断る。線と点だけで決まる形の検査で、計画の良し悪しの判定ではない
 *
 * `after` が計画に無い番号を指していても、何も言わずに線にしないだけ（なぜ無いのかを言うのは実行ファイル）。
 *
 * VS Code の API も DOM も node も読まない（画面のバンドルに入る）。
 */
import type { ApprovePlan } from "./approvemodel.js";

/** 点の種類。`item` は計画の項、`start` と `end` は図だけの仮の点 */
export type PlanNodeKind = "start" | "item" | "end";

export interface PlanGraphNode {
  /** `start`・`end`・`n<番号>` */
  readonly id: string;
  readonly kind: PlanNodeKind;
  /** 項の番号。start と end は null */
  readonly number: number | null;
  /** 項なら定義の題、start と end なら名前 */
  readonly label: string;
  /** 定義の名前。start と end は空 */
  readonly type: string;
  /** 見る場所（実行ファイルの値のまま）。start と end は空 */
  readonly review: string;
  readonly deferred: boolean;
  /** 延期したレビューを引き受ける番号（実行ファイルの値のまま） */
  readonly reviewAt: number | null;
  /** 固定した番号（子が承認された番号）。入る線を変えられない */
  readonly locked: boolean;
  /** 終端に当たらない項（実行ファイルの `loose` に入っている） */
  readonly loose: boolean;
  /** start からの最長の段数。start が 0 */
  readonly column: number;
  readonly x: number;
  readonly y: number;
}

/** 線の種類。`after` は項どうし（待たれる側 → 待つ側）、`start` と `end` は図だけの仮の線 */
export type PlanEdgeKind = "after" | "start" | "end";

export interface PlanGraphEdge {
  /** `after-<先行>-<番号>`・`start-<番号>`・`end-<番号>` */
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly kind: PlanEdgeKind;
  /** 終端に当たらない項から end への線 */
  readonly loose: boolean;
}

export interface PlanGraph {
  readonly part: string;
  readonly nodes: readonly PlanGraphNode[];
  readonly edges: readonly PlanGraphEdge[];
}

/** 列の間隔と行の間隔。CSS の `.react-flow__node-planItem` の大きさと合わせる */
export const COLUMN = 210;
export const ROW = 96;

/** start と end の名前。フィードバック計画は全体計画のレビューのあとに始まり、終われば親を閉じる */
const ENDS: Readonly<Record<"plan" | "feedback", { readonly start: string; readonly end: string }>> = {
  plan: { start: "開始", end: "終了" },
  feedback: { start: "全体計画のレビュー後", end: "親を閉じる" },
};

export function nodeId(number: number): string {
  return `n${number}`;
}

/** 計画 1 つぶんの図。点は start・項（番号順）・end の順、線は項ごとに start・after・end の順 */
export function planGraphOf(plan: ApprovePlan): PlanGraph {
  const numbers = plan.items.map((item) => item.number);
  const known = new Set(numbers);
  // 直接の先行。計画に無い番号と、同じ番号の重なりは落とす
  const before = new Map<number, number[]>();
  for (const n of numbers) {
    const listed = plan.after[String(n)] ?? [];
    before.set(n, [...new Set(listed.filter((m) => known.has(m) && m !== n))]);
  }
  const waited = new Set([...before.values()].flat());

  const depth = depthsOf(numbers, before);
  const last = Math.max(0, ...depth.values()) + 1;
  const columns = new Map<number, number[]>();
  for (const n of numbers) {
    const column = depth.get(n) ?? 1;
    columns.set(column, [...(columns.get(column) ?? []), n]);
  }
  const tallest = Math.max(1, ...[...columns.values()].map((ns) => ns.length));
  const yOf = (index: number, count: number): number => (index + (tallest - count) / 2) * ROW;

  const ends = ENDS[plan.part === "feedback" ? "feedback" : "plan"];
  const loose = new Set(plan.loose);
  const blank = { type: "", review: "", deferred: false, reviewAt: null, locked: false, loose: false } as const;
  const nodes: PlanGraphNode[] = [{ id: "start", kind: "start", number: null, label: ends.start, ...blank, column: 0, x: 0, y: yOf(0, 1) }];
  for (const item of plan.items) {
    const column = depth.get(item.number) ?? 1;
    const same = columns.get(column) ?? [item.number];
    nodes.push({
      id: nodeId(item.number),
      kind: "item",
      number: item.number,
      label: item.title === "" ? item.type : item.title,
      type: item.type,
      review: item.review,
      deferred: item.deferred,
      reviewAt: item.review_at,
      locked: item.locked,
      loose: loose.has(item.number),
      column,
      x: column * COLUMN,
      y: yOf(same.indexOf(item.number), same.length),
    });
  }
  nodes.push({ id: "end", kind: "end", number: null, label: ends.end, ...blank, column: last, x: last * COLUMN, y: yOf(0, 1) });

  const edges: PlanGraphEdge[] = [];
  for (const n of numbers) {
    const preds = before.get(n) ?? [];
    if (preds.length === 0) {
      edges.push({ id: `start-${n}`, source: "start", target: nodeId(n), kind: "start", loose: false });
    }
    for (const m of preds) {
      edges.push({ id: `after-${m}-${n}`, source: nodeId(m), target: nodeId(n), kind: "after", loose: false });
    }
  }
  for (const n of numbers) {
    if (!waited.has(n)) {
      edges.push({ id: `end-${n}`, source: nodeId(n), target: "end", kind: "end", loose: loose.has(n) });
    }
  }
  return { part: plan.part, nodes, edges };
}

/** start からの最長の段数（先行の無い項が 1）。循環に戻った線は数えない */
function depthsOf(numbers: readonly number[], before: ReadonlyMap<number, readonly number[]>): Map<number, number> {
  const depth = new Map<number, number>();
  const visiting = new Set<number>();
  const visit = (n: number): number => {
    const known = depth.get(n);
    if (known !== undefined) {
      return known;
    }
    if (visiting.has(n)) {
      return 0;
    }
    visiting.add(n);
    const preds = before.get(n) ?? [];
    const d = 1 + Math.max(0, ...preds.map(visit));
    visiting.delete(n);
    depth.set(n, d);
    return d;
  };
  numbers.forEach(visit);
  return depth;
}

/**
 * `source` から `target` へ線を引けるか（`target` が `source` を待つ）。引けるのは項から項へだけで、
 * 自分へ・start と end に触れる線（図が自動で引く）・固定した番号へ入る線・循環になる線（`target` から
 * `source` へ既に辿れる）を断る。React Flow の `isValidConnection` に渡し、引いている最中に断る
 */
export function canConnect(graph: PlanGraph, source: string, target: string): boolean {
  if (source === target) {
    return false;
  }
  const from = graph.nodes.find((node) => node.id === source);
  const to = graph.nodes.find((node) => node.id === target);
  if (from === undefined || to === undefined || from.kind !== "item" || to.kind !== "item" || to.locked) {
    return false;
  }
  return !reaches(graph, target, source);
}

/** `from` から線を辿って `to` に着くか（after の線だけ） */
function reaches(graph: PlanGraph, from: string, to: string): boolean {
  const next = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.kind === "after") {
      next.set(edge.source, [...(next.get(edge.source) ?? []), edge.target]);
    }
  }
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length > 0) {
    const now = stack.pop() as string;
    if (now === to) {
      return true;
    }
    if (seen.has(now)) {
      continue;
    }
    seen.add(now);
    stack.push(...(next.get(now) ?? []));
  }
  return false;
}
