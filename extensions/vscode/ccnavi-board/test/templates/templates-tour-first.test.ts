/**
 * テンプレート管理の入門の案内（`first-look`）と、その図（`template-first`）の検査。
 * 入門は、基本の案内の次に置き、やさしい全体像を見せる。具体的な案内（値の例つき）はその後ろに続く。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID } from "../../src/core/tour-def.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("templates");
const FIRST = "first-look";
const FIRST_DIAGRAM = "template-first";

function first() {
  const course = def.courses.find((c) => c.id === FIRST);
  assert.ok(course !== undefined, "first-look の案内がない");
  return course;
}

test("CB-T1250 入門の案内 first-look は基本の案内の直後にあり、題は「たいとき」で終わり、手順は 4〜5", () => {
  assert.equal(def.courses[0].id, INTRO_ID);
  assert.equal(def.courses[1].id, FIRST);
  const course = first();
  assert.ok(course.title.endsWith("たいとき"), course.title);
  assert.ok(course.steps.length >= 4 && course.steps.length <= 5, `手順は 4〜5（${course.steps.length}）`);
});

test("CB-T1251 入門の図は登場者が 3 人以下、矢印が 5 本以下で、値の例は 1 つだけ", () => {
  const diagram = def.diagrams[FIRST_DIAGRAM];
  assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 3, `登場者 ${diagram.actors.length}`);
  assert.ok(diagram.steps.length >= 3 && diagram.steps.length <= 5, `矢印 ${diagram.steps.length}`);
  assert.equal(diagram.steps.filter((step) => step.value !== "").length, 1, "値の例は 1 つ");
  for (const step of diagram.steps) {
    for (const name of [step.from, step.to]) {
      assert.ok(name === "" || diagram.actors.includes(name), `${step.id}: 知らない登場者 ${name}`);
    }
  }
});

test("CB-T1252 入門の手順は図の矢印を指し、すべての矢印がどれかの手順で光り、最後の手順で次の案内へ促す", () => {
  const course = first();
  const ids = def.diagrams[FIRST_DIAGRAM].steps.map((s) => s.id);
  const used = new Set<string>();
  for (const [index, step] of course.steps.entries()) {
    assert.notEqual(step.target, "", `${index + 1} 番目: 指す先は画面の印にする`);
    if (step.diagram !== "") {
      assert.equal(step.diagram, FIRST_DIAGRAM);
      assert.ok(step.focus.length > 0, `${index + 1} 番目: focus`);
      for (const focus of step.focus) {
        assert.ok(ids.includes(focus), `${index + 1} 番目: focus ${focus}`);
        used.add(focus);
      }
    }
  }
  assert.deepEqual([...used].sort(), [...ids].sort(), "光らない矢印がある");
  assert.ok(course.steps[course.steps.length - 1].body.includes("次の案内では、下書きから取り込み、次の提案で使うまでを例で見られます"));
});

test("CB-T1253 入門が伝える 4 点（使い回せる・下書きを溜める・この画面は読む場所・テンプレートから始める）が本文か図にある", () => {
  const course = first();
  const text = [...course.steps.map((s) => s.body), ...def.diagrams[FIRST_DIAGRAM].steps.map((s) => s.text)].join("\n");
  for (const word of ["使い回せ", "下書き", "読む場所", "取り込み待ち", "テンプレートを指して"]) {
    assert.ok(text.includes(word), `「${word}」がない`);
  }
});
