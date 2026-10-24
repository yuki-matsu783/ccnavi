/**
 * プロジェクト管理画面の案内（`src/webview/tours/projects.yml`）の、時系列の図と、図を引く 2 本の案内の定義を確かめる。
 * 画面に出る様子は `projects-tour-diagrams.dom.test.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { loadTour } from "../helpers/tour.js";

const def = loadTour("projects");
const DIAGRAM_COURSES = ["how-clone", "how-card"];

test("CB-T1210 プロジェクト管理の案内に、図を引く 2 本の案内が基本の案内の次に並ぶ。題は「〜たいとき」で終わり、手順は 4〜9 個で、図と光らせる矢印を持つ", () => {
  assert.deepEqual(def.courses.map((course) => course.id).slice(0, 4), ["intro", "first-look", ...DIAGRAM_COURSES]);
  // 既存の案内の ID と題は変えない
  assert.equal(def.courses[0].title, "基本の案内");
  assert.equal(def.courses.find((course) => course.id === "add-project")?.title, "プロジェクトを追加したいとき");
  for (const id of DIAGRAM_COURSES) {
    const course = def.courses.find((c) => c.id === id)!;
    assert.ok(course.title.endsWith("たいとき"), `${id}: 題は「〜たいとき」で終える`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${id}: 手順は ${course.steps.length} 個`);
    for (const step of course.steps) {
      assert.notEqual(step.diagram, "", `${id}「${step.title}」: 図を引かない手順がある`);
      assert.equal(step.image, "", `${id}「${step.title}」: 画像は使わない`);
      assert.deepEqual(step.scene, {}, `${id}「${step.title}」: 見本の様子は使わない（今の画面を指す）`);
    }
  }
});

test("CB-T1211 図の登場者は 2〜6 人で、値は例のものだけを使う（example.com。実在のドメインや本物のトークンを入れない）。値を変えると結果が変わる例は図を分けて持つ", () => {
  const names = Object.keys(def.diagrams);
  for (const name of ["project-clone", "project-clone-stop", "project-card", "project-lint", "project-layers", "project-stray"]) {
    assert.ok(names.includes(name), `図 ${name} がない`);
  }
  const hosts = new Set<string>();
  for (const [name, diagram] of Object.entries(def.diagrams)) {
    assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 6, `${name}: 登場者が ${diagram.actors.length} 人`);
    for (const step of diagram.steps) {
      for (const match of `${step.text}\n${step.value}`.matchAll(/(?:https?:\/\/|@)([a-z0-9.-]+\.[a-z]{2,})/gi)) {
        hosts.add(match[1].toLowerCase());
      }
    }
  }
  assert.deepEqual([...hosts], ["example.com"], "例のホストは example.com だけ");
  // 資格情報の例は、本物に見えない固定の語にする
  const stop = def.diagrams["project-clone-stop"];
  assert.equal(stop.steps.find((step) => step.id === "cred")?.value, "https://alice:TOKEN@example.com/team/web-app.git");
  // 受け入れる URL と止める URL は別の図（clone が進む例と、止まる例を 1 本に混ぜない）
  assert.ok(!def.diagrams["project-clone"].steps.some((step) => step.value.includes("TOKEN")));
});

test("CB-T1212 図の値が例であることを、各案内の最初の手順の本文に書く", () => {
  for (const id of DIAGRAM_COURSES) {
    const course = def.courses.find((c) => c.id === id)!;
    assert.match(course.steps[0].body, /例/, `${id}: 最初の手順に「例」がない`);
    assert.match(course.steps[0].body, /実在|すべて例/, `${id}: 図の値が例だと書いていない`);
  }
});
