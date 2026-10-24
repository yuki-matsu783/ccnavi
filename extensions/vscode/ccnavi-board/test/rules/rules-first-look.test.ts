/**
 * ルール管理の入門の案内（`first-look`）と、その図（`rules-first`）の形。
 * 動かして見るのは `rules-first-look.dom.test.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseRulesSample } from "../../src/core/tour-rules.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("rules");

test("CB-T1220 入門の案内 first-look は基本の案内の次（使い方ごとの先頭）にあり、4〜5 手順で、題は「たいとき」で終わる。既存の案内は残る", () => {
  assert.equal(def.courses[0].id, "intro");
  const course = def.courses[1];
  assert.equal(course.id, "first-look");
  assert.ok(course.steps.length >= 4 && course.steps.length <= 5, `${course.steps.length} 手順`);
  assert.ok(course.title.endsWith("たいとき"), course.title);
  const last = course.steps[course.steps.length - 1];
  assert.ok(last.body.includes("次の案内では、値を変えると結果がどう変わるかを例で見られます"));
  assert.equal(def.courses[2].id, "how-it-works");
  assert.equal(def.courses[3].id, "how-save-try");
});

test("CB-T1221 入門の図 rules-first は、登場者が 3 人以下、矢印が 5 本以下で、値の例は 1 つだけ", () => {
  const diagram = def.diagrams["rules-first"];
  assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 3, `${diagram.actors.length} 人`);
  assert.ok(diagram.steps.length >= 3 && diagram.steps.length <= 5, `${diagram.steps.length} 本`);
  assert.equal(diagram.steps.filter((entry) => entry.value !== "").length, 1);
});

test("CB-T1222 入門の案内の全手順は rules-first の図を引き、光らせる矢印は図にあり、scene は見本にある行だけを指す", () => {
  const diagram = def.diagrams["rules-first"];
  const ids = new Set(diagram.steps.map((entry) => entry.id));
  const sample = parseRulesSample(def.sample);
  const rules = new Set([...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].map((rule) => rule.id));
  for (const [index, step] of def.courses[1].steps.entries()) {
    assert.equal(step.diagram, "rules-first", `${index + 1} 番目`);
    for (const id of step.focus) {
      assert.ok(ids.has(id), `${index + 1} 番目: 矢印 ${id} が図にありません`);
    }
    for (const id of String(step.scene.open ?? "").split(",").filter((item) => item.trim() !== "")) {
      assert.ok(rules.has(id.trim()), `${index + 1} 番目: ${id}`);
    }
  }
  for (const actor of diagram.actors) {
    assert.ok(diagram.steps.some((entry) => entry.from === actor || entry.to === actor), `${actor} が出てこない`);
  }
});
