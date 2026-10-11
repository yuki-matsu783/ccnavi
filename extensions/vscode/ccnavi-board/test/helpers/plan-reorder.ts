/**
 * 計画の番号の振り直しの見本の表（リポジトリの `tests/fixtures/plan-reorder.json`）を読む。
 * 表は実行ファイルのテスト（`tests/ticket/test_plan_order.py`）と共有していて、**ここでは書き換えない**。
 *
 * 拡張はまだ番号を振り直さない（振り直しの正は実行ファイル）。ここで使うのは、表の計画（振り直す前の
 * `plan` と、振り直したあとの `expect.plan`）を、実行ファイルが承認の preview に載せる `plans` の形に
 * 並べ直すことだけ。番号は計画の並びのまま（全体計画が 1 から、フィードバック計画はその続き）で、
 * `after` も書いてあるとおりに写す。図がその `plans` どおりに点と線を描くかを見るのに使う。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import type { ApprovePlan, ApprovePlanItem } from "../../src/core/approvemodel.js";

/** 表の計画の項。名前だけか、`type`・`review`・`after` を持つ辞書 */
export type RawItem = string | { readonly type: string; readonly review?: string; readonly after?: readonly number[] };

export interface ReorderCase {
  readonly name: string;
  readonly plan: readonly RawItem[];
  readonly feedback?: readonly RawItem[];
  readonly fixed?: readonly number[];
  readonly refused?: string;
  readonly expect?: { readonly plan: readonly RawItem[]; readonly feedback?: readonly RawItem[]; readonly from: readonly number[] };
}

/** このファイルは `out/test/helpers/` から走る。6 つ上がリポジトリのルート */
export const PLAN_REORDER = path.join(__dirname, "..", "..", "..", "..", "..", "..", "tests", "fixtures", "plan-reorder.json");

export function reorderCases(): readonly ReorderCase[] {
  const raw = JSON.parse(fs.readFileSync(PLAN_REORDER, "utf8")) as { cases: ReorderCase[] };
  return raw.cases;
}

function typeOf(item: RawItem): string {
  return typeof item === "string" ? item : item.type;
}

function afterOf(item: RawItem): readonly number[] {
  return typeof item === "string" ? [] : (item.after ?? []);
}

/** 表の計画を `plans` の形に並べる。`fixed` の番号は固定した番号（子が承認された番号）として印を付ける */
export function plansOf(plan: readonly RawItem[], feedback: readonly RawItem[] | undefined, fixed: readonly number[] = []): ApprovePlan[] {
  const out: ApprovePlan[] = [];
  let first = 1;
  for (const [part, items] of [["plan", plan], ["feedback", feedback]] as const) {
    if (items === undefined) {
      continue;
    }
    const entries: ApprovePlanItem[] = items.map((item, index) => ({
      number: first + index,
      from: first + index,
      type: typeOf(item),
      title: typeOf(item),
      review: "mr",
      deferred: typeof item !== "string" && item.review === "defer",
      review_at: null,
      locked: fixed.includes(first + index),
    }));
    const after: Record<string, number[]> = {};
    items.forEach((item, index) => {
      if (afterOf(item).length > 0) {
        after[String(first + index)] = [...afterOf(item)];
      }
    });
    out.push({
      ticket: "i0001",
      part,
      source_sha: "",
      items: entries,
      after,
      proposed: after,
      current: null,
      loose: [],
      ready: entries.filter((item) => after[String(item.number)] === undefined).map((item) => item.number),
      problems: [],
    });
    first += items.length;
  }
  return out;
}
