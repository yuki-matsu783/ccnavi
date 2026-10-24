/**
 * プロジェクト管理画面の入門の案内（`first-look`）と、その図（`project-first`）の定義を確かめる。
 * 画面に出る様子は `projects-tour-first-look.dom.test.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadTour } from "../helpers/tour.js";

const def = loadTour("projects");

test("CB-T1260 入門の案内 first-look は基本の案内の次にあり、手順は 4〜5 個で、題は「〜たいとき」で終わる。最後の手順は次の案内へ促す", () => {
  assert.deepEqual(def.courses.map((course) => course.id).slice(0, 2), ["intro", "first-look"]);
  const course = def.courses[1];
  assert.ok(course.title.endsWith("たいとき"));
  assert.ok(course.steps.length >= 4 && course.steps.length <= 5, `手順は ${course.steps.length} 個`);
  assert.match(course.steps[0].body, /すべて例/);
  assert.match(course.steps[course.steps.length - 1].body, /次の案内では、clone してから一覧に出るまでを例で見られます/);
  for (const step of course.steps) {
    assert.equal(step.image, "", `「${step.title}」: 画像は使わない`);
    assert.deepEqual(step.scene, {}, `「${step.title}」: 見本は使わない`);
  }
});

test("CB-T1261 入門の図は、登場者が 3 人以下、矢印が 5 本以下で、値のホストは example.com だけ。手順の focus は図の矢印にある", () => {
  const diagram = def.diagrams["project-first"];
  assert.ok(diagram, "図 project-first がない");
  assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 3, `登場者が ${diagram.actors.length} 人`);
  assert.ok(diagram.steps.length >= 3 && diagram.steps.length <= 5, `矢印が ${diagram.steps.length} 本`);
  const hosts = new Set<string>();
  for (const step of diagram.steps) {
    for (const match of `${step.text}\n${step.value}`.matchAll(/(?:https?:\/\/|@)([a-z0-9.-]+\.[a-z]{2,})/gi)) hosts.add(match[1].toLowerCase());
  }
  assert.deepEqual([...hosts], ["example.com"]);
  assert.equal(diagram.steps.filter((step) => step.value !== "").length, 1, "値の例は 1 つだけ");
  const ids = diagram.steps.map((step) => step.id);
  for (const step of def.courses[1].steps) {
    assert.ok(step.diagram === "project-first" || step.target === "tour", `「${step.title}」: 図か、画面の部品（?）を指す`);
    for (const id of step.focus) assert.ok(ids.includes(id), `focus ${id} が図にない`);
  }
});
