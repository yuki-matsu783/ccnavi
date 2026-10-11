/**
 * `--plan-order <親> --json` の答えの実例（`test/fixtures/plan-order.json`）を読む。Python 側の
 * `tests/ticket/test_plan_order.py`（`PlanOrderFixtureTest`）が書き出したもので、ここでは書き換えない。
 *
 * 並んでいる答えは 6 つ。開いたとき（`open`。全体計画 1〜4 とフィードバック計画 5〜6、子の提案 2 本）、
 * 逆向きの線で 2 と 3 が入れ替わり子が付け替わるとき（`reverse`）、循環で断るとき（`cycle`）、
 * 終端の検査で書けないとき（`loose`）、書いたとき（`written`）、改版で 1 番に子が承認されているとき（`revision`）。
 * `source_sha` はどれも `<sha>` に置き換えてある。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { parsePlanOrder, type PlanOrderAnswer } from "../../src/core/planordermodel.js";

export const PLAN_ORDER_FIXTURE = path.join(__dirname, "..", "..", "..", "test", "fixtures", "plan-order.json");

export type PlanOrderSample = "open" | "reverse" | "cycle" | "loose" | "written" | "revision";

/** 実例の答えの、JSON の文字列（実行ファイルの標準出力と同じ形） */
export function planOrderText(name: PlanOrderSample): string {
  const all = JSON.parse(fs.readFileSync(PLAN_ORDER_FIXTURE, "utf8")) as Record<string, unknown>;
  return JSON.stringify(all[name]);
}

/** 実例の答えを読んだもの。`sha` を渡せば `source_sha` をその値にする（外で変わったときの見本） */
export function planOrderSample(name: PlanOrderSample, sha?: string): PlanOrderAnswer {
  const parsed = parsePlanOrder(planOrderText(name));
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  if (sha === undefined) {
    return parsed.value;
  }
  return { ...parsed.value, source_sha: sha, plans: parsed.value.plans.map((plan) => ({ ...plan, source_sha: sha })) };
}
