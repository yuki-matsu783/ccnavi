/**
 * 図の線の経路（`src/core/phases-route.ts`）。**線が点を横切らないこと**を、格子に並べた点の
 * すべての組で確かめる。前はベジェ曲線で、別の行で 2 列以上離れた組の線が間の点を突き抜けていた。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { COLUMN, NODE_WIDTH, ROW } from "../../src/core/phases-graph.js";
import { crosses, pathOf, pointsOf, routeOf, type Point, type Rect } from "../../src/core/phases-route.js";

/** cols × rows の格子に点を置く。高さは題の折り返しで変わるので、見込みの幅を持たせて試す */
function grid(cols: number, rows: number, height: number): Map<string, Rect> {
  const out = new Map<string, Rect>();
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < cols; c += 1) {
      out.set(`n${r}-${c}`, { x: c * COLUMN, y: r * ROW, width: NODE_WIDTH, height });
    }
  }
  return out;
}

function segments(points: readonly Point[]): [Point, Point][] {
  return points.slice(1).map((point, index) => [points[index], point]);
}

test("CB-T209 格子のどの 2 点を結ぶ線も、どの点も横切らない（関係ごとにずらしても）", () => {
  for (const height of [60, 72, 88]) {
    const rects = grid(4, 3, height);
    const at = new Map(Array.from(rects, ([id, rect]) => [id, { x: rect.x, y: rect.y }]));
    const ids = Array.from(rects.keys());
    for (const a of ids) {
      for (const b of ids) {
        if (a === b) {
          continue;
        }
        const route = routeOf(a, b, at);
        for (const shift of [-7, 0, 7]) {
          const points = pointsOf(route, rects.get(route.from) as Rect, rects.get(route.to) as Rect, shift);
          for (const [p, q] of segments(points)) {
            assert.ok(p.x === q.x || p.y === q.y, `${a}→${b}: 斜めの線分がある`);
            for (const [id, rect] of rects) {
              assert.ok(!crosses(p, q, rect), `${a}→${b}（高さ ${height}、ずらし ${shift}）の線が ${id} を横切る: ${JSON.stringify(points)}`);
            }
          }
        }
      }
    }
  }
});

test("CB-T210 線は出る点の辺から出て、入る点の辺で終わる。ずらした 2 本は同じ経路にならない", () => {
  const rects = grid(4, 2, 72);
  const at = new Map(Array.from(rects, ([id, rect]) => [id, { x: rect.x, y: rect.y }]));
  // 左の点から右の点へ（右辺 → 左辺）。逆に渡しても、出るのは左の点
  const route = routeOf("n1-3", "n0-0", at);
  assert.equal(route.from, "n0-0");
  assert.equal(route.to, "n1-3");
  const points = pointsOf(route, rects.get("n0-0") as Rect, rects.get("n1-3") as Rect, 0);
  assert.equal(points[0].x, NODE_WIDTH, "右辺から出ていない");
  assert.equal(points[points.length - 1].x, 3 * COLUMN, "左辺に入っていない");
  const other = pointsOf(route, rects.get("n0-0") as Rect, rects.get("n1-3") as Rect, 7);
  assert.notEqual(pathOf(points), pathOf(other));
  // 同じ行の隣どうしは直線 1 本
  const next = routeOf("n0-0", "n0-1", at);
  assert.equal(pointsOf(next, rects.get("n0-0") as Rect, rects.get("n0-1") as Rect, 0).length, 2);
});
