/**
 * リスク管理画面の入門の案内（`tours/risk.yml` の `first-look` と、その図 `risk-first` / `risk-first-tune`）の形と、
 * 図の中の値が見本の配点と食い違わないことを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID } from "../../src/core/tour-def.js";
import { parseRiskSample } from "../../src/core/tour-risk.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("risk");
const FIRST_DIAGRAMS = ["risk-first", "risk-first-tune"];

test("CB-T1230 入門の案内 first-look は基本の案内の次にあり、題は「たいとき」で終わり、4〜5 手順で、最後に次の案内へ促す", () => {
  assert.deepEqual(def.courses.slice(0, 2).map((course) => course.id), [INTRO_ID, "first-look"]);
  const course = def.courses[1];
  assert.match(course.title, /たいとき$/);
  assert.ok(course.steps.length >= 4 && course.steps.length <= 5, `${course.steps.length} 手順`);
  assert.ok(course.steps.every((step) => step.diagram !== "" || step.target !== ""), "図も指す先も無い手順がある");
  assert.ok(course.steps[course.steps.length - 1].body.includes("次の案内では、どの値で点数とレベルが決まるかを例で見られます"));
});

test("CB-T1231 入門の図は 2 本で、登場者は 3 人以下、矢印は 5 本以下。手順が引く図と光らせる矢印は実在する", () => {
  for (const name of FIRST_DIAGRAMS) {
    const diagram = def.diagrams[name];
    assert.ok(diagram, `${name} がない`);
    assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 3, `${name}: 登場者が ${diagram.actors.length} 人`);
    assert.ok(diagram.steps.length >= 3 && diagram.steps.length <= 5, `${name}: 矢印が ${diagram.steps.length} 本`);
    assert.ok(diagram.steps.some((step) => step.value !== ""), `${name}: 値の例がない`);
  }
  const course = def.courses.find((c) => c.id === "first-look")!;
  assert.deepEqual([...new Set(course.steps.map((step) => step.diagram))].sort(), [...FIRST_DIAGRAMS].sort());
  for (const step of course.steps) {
    const ids = def.diagrams[step.diagram].steps.map((entry) => entry.id);
    assert.ok(step.focus.length > 0 && step.focus.every((id) => ids.includes(id)), `${step.title}: focus が図にない`);
  }
});

test("CB-T1232 入門の図の値は見本の配点と一致する（big-diff 25、no-tests 20、HIGH 40、直す前の no-tests 20）", () => {
  const form = parseRiskSample(def.sample).page.model.form;
  const points = (id: string): number => Number(form.factors.find((factor) => factor.id === id)!.points);
  const lineLimit = Number(form.factors.find((factor) => factor.id === "big-diff")!.value);
  const high = Number(form.levels.high);
  const valueOf = (name: string, id: string): string => def.diagrams[name].steps.find((step) => step.id === id)!.value;

  const total = points("big-diff") + points("no-tests");
  assert.ok(total >= high && total < Number(form.levels.critical), "例の合計は HIGH");
  assert.ok(330 > lineLimit);
  assert.equal(valueOf("risk-first", "measure"), `src/ の下を 330 行変えた（${lineLimit} 行を超える） → +${points("big-diff")}\nテストが足りない → +${points("no-tests")}`);
  assert.equal(valueOf("risk-first", "level"), `${points("big-diff")} + ${points("no-tests")} = ${total}\n${total} ≥ ${high} → HIGH`);
  assert.equal(valueOf("risk-first", "review"), `このレビューのリスク: ${total} (HIGH)`);
  assert.ok(valueOf("risk-first-tune", "edit").includes(`の点: ${points("no-tests")} → `));
});
