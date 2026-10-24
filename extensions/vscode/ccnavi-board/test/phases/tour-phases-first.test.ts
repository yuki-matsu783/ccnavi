/**
 * フェーズ管理の入門の案内（`tours/phases.yml` の `first-look` と図 `phase-first`）の検査。
 * 入門は、基本の案内の次に置く 4〜5 手順で、図は登場者 3 人以下・矢印 5 本以下・値の例は 1 つだけ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parse } from "yaml";

import { INTRO_ID } from "../../src/core/tour-def.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("phases");
const raw = def.sample as { phases: { phases: Record<string, { review: string; scope: unknown; deliverables?: string[] }> } };
const first = def.courses.find((course) => course.id === "first-look")!;
const diagram = def.diagrams["phase-first"];

test("CB-T1240 入門の案内 first-look は基本の案内の次にあり、4〜5 手順で、題は「たいとき」で終わる。最後は次の案内へ促す", () => {
  assert.ok(first !== undefined);
  assert.equal(def.courses[0].id, INTRO_ID);
  assert.equal(def.courses[1].id, "first-look");
  assert.ok(first.steps.length >= 4 && first.steps.length <= 5, `${first.steps.length} 手順`);
  assert.match(first.title, /たいとき$/);
  assert.ok(first.steps.every((step) => step.diagram === "phase-first" && step.focus.length > 0 && step.image === ""));
  assert.match(first.steps[first.steps.length - 1].body, /次の案内では、定義の値で動きがどう変わるかを例で見られます/);
});

test("CB-T1241 入門の図は、登場者 3 人以下・矢印 5 本以下で、値の例は 1 つだけ。focus は図の矢印を指し、全矢印がどこかの手順で光る", () => {
  assert.ok(diagram !== undefined);
  assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 3, `登場者 ${diagram.actors.length} 人`);
  assert.ok(diagram.steps.length >= 3 && diagram.steps.length <= 5, `矢印 ${diagram.steps.length} 本`);
  assert.equal(diagram.steps.filter((step) => step.value !== "").length, 1, "値の例は 1 つ");
  const ids = diagram.steps.map((step) => step.id);
  const lit = new Set<string>();
  for (const step of first.steps) {
    for (const id of step.focus) {
      assert.ok(ids.includes(id), `focus ${id} が図にない`);
      lit.add(id);
    }
  }
  assert.deepEqual([...lit].sort(), [...ids].sort());
  for (const step of diagram.steps) {
    assert.ok([step.from, step.to === "" ? step.from : step.to].every((name) => diagram.actors.includes(name)), `${step.id}: 登場者にない名前`);
  }
});

test("CB-T1242 入門の図の値の例は、見本の research の定義と一致する", () => {
  const sample = raw.phases.phases.research;
  const shown = diagram.steps.find((step) => step.value !== "")!;
  assert.deepEqual(parse(shown.value), { review: sample.review, scope: sample.scope, deliverables: sample.deliverables });
});
