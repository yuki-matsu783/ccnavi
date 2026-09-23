/**
 * フェーズ管理画面の図の線の経路。2 つの点の置き場所と大きさから、**点を横切らない折れ線**を組む純関数。
 *
 * 点は格子（`phases-graph.ts` の `COLUMN` × `ROW`）に並ぶので、点と点の間には縦の隙間（列の間）と
 * 横の隙間（行の間）がある。線はその隙間だけを通す。
 *
 * - 隣の列にいる: 右辺から出て、列の間の隙間で縦に折れ、相手の左辺へ入る（同じ行なら直線）
 * - 列を 1 つ以上跨ぐ: 右辺から出て列の間で折れ、相手の行の上（または下）の隙間を横に走り、
 *   相手の手前の列の間で折れて左辺へ入る
 * - 同じ行で列を跨ぐ: 下辺から出て行の下の隙間を走り、相手の下辺へ入る
 * - 同じ列で隣の行: 下辺から相手の上辺へ直線
 * - 同じ列で行を跨ぐ: 右辺から出て列の右の隙間を縦に走り、相手の右辺へ入る
 *
 * 前はベジェ曲線で結んでいて、別の行で 2 列以上離れた組（このリポジトリの設定の acceptance と
 * staging）では、曲線が間の点を突き抜けていた。
 *
 * **同じ組の 2 本は `shift` でずらす。** 同じ 2 つの種類が複数の関係を持つことがあり（`requires` と
 * `overlap` など）、同じ経路だと破線が実線の下に隠れる。辺の上の端も、隙間を走る位置も、関係ごとにずらす。
 * ずらしは隙間の半分より小さく抑え、隣の点に食い込ませない。
 *
 * 人がドラッグで格子から外した点どうしは、隙間の見込みが外れて点に触れることがある。そこまでは追わない。
 */
import { COLUMN, NODE_WIDTH, ROW } from "./phases-graph.js";

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface Point {
  readonly x: number;
  readonly y: number;
}

/** 端の辺。l = 左、r = 右、t = 上、b = 下 */
export type Side = "l" | "r" | "t" | "b";

/** 線 1 本の引き方。どちらの点から出て、どの辺に留めるか */
export interface Route {
  readonly from: string;
  readonly to: string;
  readonly fromSide: Side;
  readonly toSide: Side;
}

/** 隙間の中で線を走らせる位置（点の縁からの距離）。列の隙間は `COLUMN - NODE_WIDTH` */
const LANE = (COLUMN - NODE_WIDTH) / 2;

/** 2 つの点の間の辺を決める。置き場所（左上の座標）だけを見る */
export function routeOf(a: string, b: string, at: ReadonlyMap<string, Point>): Route {
  const pa = at.get(a) ?? { x: 0, y: 0 };
  const pb = at.get(b) ?? { x: 0, y: 0 };
  const dx = pb.x - pa.x;
  const dy = pb.y - pa.y;
  if (Math.abs(dx) < NODE_WIDTH) {
    const [from, to] = dy >= 0 ? [a, b] : [b, a];
    return Math.abs(dy) > ROW * 1.5 ? { from, to, fromSide: "r", toSide: "r" } : { from, to, fromSide: "b", toSide: "t" };
  }
  const [from, to] = dx >= 0 ? [a, b] : [b, a];
  if (Math.abs(dy) < ROW / 2 && Math.abs(dx) > COLUMN * 1.5) {
    return { from, to, fromSide: "b", toSide: "b" };
  }
  return { from, to, fromSide: "r", toSide: "l" };
}

/** 続けて同じ点が来たら 1 つにし、一直線に並ぶ途中の点を落とす */
function tidy(points: readonly Point[]): Point[] {
  const out: Point[] = [];
  for (const point of points) {
    const last = out[out.length - 1];
    if (last !== undefined && last.x === point.x && last.y === point.y) {
      continue;
    }
    const before = out[out.length - 2];
    if (last !== undefined && before !== undefined && ((before.x === last.x && last.x === point.x) || (before.y === last.y && last.y === point.y))) {
      out[out.length - 1] = point;
      continue;
    }
    out.push(point);
  }
  return out;
}

/**
 * 折れ線の点。`from` と `to` は `route` の向きどおりの点の枠。`shift` は関係ごとのずらし（px）で、
 * 隙間の半分（`LANE`）より小さい値を渡す。
 */
export function pointsOf(route: Route, from: Rect, to: Rect, shift: number): Point[] {
  const midY = (rect: Rect): number => rect.y + rect.height / 2 + shift;
  const midX = (rect: Rect): number => rect.x + rect.width / 2 + shift;
  if (route.fromSide === "b" && route.toSide === "t") {
    const x = midX(from);
    return tidy([
      { x, y: from.y + from.height },
      { x, y: to.y },
    ]);
  }
  if (route.fromSide === "r" && route.toSide === "r") {
    const lane = Math.max(from.x + from.width, to.x + to.width) + LANE + shift / 2;
    const start = { x: from.x + from.width, y: midY(from) };
    const end = { x: to.x + to.width, y: midY(to) };
    return tidy([start, { x: lane, y: start.y }, { x: lane, y: end.y }, end]);
  }
  if (route.fromSide === "b" && route.toSide === "b") {
    const lane = Math.max(from.y + from.height, to.y + to.height) + LANE + shift / 2;
    const start = { x: midX(from), y: from.y + from.height };
    const end = { x: midX(to), y: to.y + to.height };
    return tidy([start, { x: start.x, y: lane }, { x: end.x, y: lane }, end]);
  }
  // 右辺 → 左辺
  const start = { x: from.x + from.width, y: midY(from) };
  const end = { x: to.x, y: midY(to) };
  if (to.x - start.x < NODE_WIDTH) {
    // 隣の列。間の隙間の中ほどで縦に折れる
    const x = (start.x + end.x) / 2 + shift / 2;
    return tidy([start, { x, y: start.y }, { x, y: end.y }, end]);
  }
  // 列を跨ぐ。相手の行の上か下の隙間を横に走る
  const out = start.x + LANE + shift / 2;
  const into = end.x - LANE + shift / 2;
  const below = to.y + to.height >= from.y + from.height && to.y > from.y + from.height / 2;
  const above = to.y + to.height < from.y + from.height / 2;
  let lane: number;
  if (below) {
    lane = to.y - LANE + shift / 2;
  } else if (above) {
    lane = to.y + to.height + LANE + shift / 2;
  } else {
    lane = Math.max(from.y + from.height, to.y + to.height) + LANE + shift / 2;
  }
  return tidy([start, { x: out, y: start.y }, { x: out, y: lane }, { x: into, y: lane }, { x: into, y: end.y }, end]);
}

/** 折れ線を SVG の path にする。角は `radius` で丸める（短い辺では小さくする） */
export function pathOf(points: readonly Point[], radius = 6): string {
  if (points.length === 0) {
    return "";
  }
  const parts = [`M ${points[0].x},${points[0].y}`];
  for (let i = 1; i < points.length; i += 1) {
    const here = points[i];
    const next = points[i + 1];
    if (next === undefined) {
      parts.push(`L ${here.x},${here.y}`);
      continue;
    }
    const prev = points[i - 1];
    const inLen = Math.hypot(here.x - prev.x, here.y - prev.y);
    const outLen = Math.hypot(next.x - here.x, next.y - here.y);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    const enter = { x: here.x - ((here.x - prev.x) / (inLen || 1)) * r, y: here.y - ((here.y - prev.y) / (inLen || 1)) * r };
    const leave = { x: here.x + ((next.x - here.x) / (outLen || 1)) * r, y: here.y + ((next.y - here.y) / (outLen || 1)) * r };
    parts.push(`L ${enter.x},${enter.y}`, `Q ${here.x},${here.y} ${leave.x},${leave.y}`);
  }
  return parts.join(" ");
}

/** 線分が枠の内側（縁から `inset` だけ内側）を通るか。テストが「点を横切らない」を確かめるのに使う */
export function crosses(a: Point, b: Point, rect: Rect, inset = 1): boolean {
  const left = rect.x + inset;
  const right = rect.x + rect.width - inset;
  const top = rect.y + inset;
  const bottom = rect.y + rect.height - inset;
  // 折れ線は縦か横の線分だけ
  if (a.y === b.y) {
    return a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right;
  }
  if (a.x === b.x) {
    return a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom;
  }
  // 斜めの線分は来ない想定。来たら端の点で見る
  return [a, b].some((p) => p.x > left && p.x < right && p.y > top && p.y < bottom);
}
