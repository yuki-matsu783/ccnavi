/**
 * リスク管理画面の案内の時系列の図（`tours/risk.yml` の `diagrams:`）を happy-dom で動かす。
 * 形は `test/rules/rules.dom.test.ts` の CB-D742 と同じ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { DomPage } from "../helpers/dom.js";
import { openRisk } from "../helpers/risk.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("risk");

async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

async function checkCourseDiagrams(id: string): Promise<void> {
  const course = def.courses.find((c) => c.id === id)!;
  const dom = await openRisk();
  try {
    await startCourse(dom, id);
    let drawn = 0;
    for (const [index, step] of course.steps.entries()) {
      await dom.settle();
      const at = `${index + 1} 番目`;
      if (step.diagram === "") {
        assert.equal(dom.all(".tour-bubble .tour-diagram").length, 0, `${at}: 図のない手順に図が出ている`);
      } else {
        drawn += 1;
        const spec = def.diagrams[step.diagram];
        const diagram = dom.one(".tour-bubble .tour-diagram");
        assert.equal(diagram.getAttribute("data-diagram"), step.diagram, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
        const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
        assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((entryId) => step.focus.includes(entryId)), `${at}の光る矢印`);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, step.focus.length === 0 ? 0 : spec.steps.length - lit.length, at);
        assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, spec.steps.filter((entry) => entry.value !== "").length, at);
      }
      if (index < course.steps.length - 1) {
        dom.click(dom.one('[data-action="tour-next"]'));
      }
    }
    assert.ok(drawn >= 3, "図を引く手順が少ない");
  } finally {
    await dom.close();
  }
}

test("CB-D750 「点数とレベル」の案内の図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする。値は等幅で出る", async () => {
  await checkCourseDiagrams("how-score");
});

test("CB-D751 「保存してから効くまで」の案内の図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする", async () => {
  await checkCourseDiagrams("how-save-effect");
});

test("CB-D752 図の矢印の値は、見本の配点の数字を等幅で出す（HIGH の例は 55 点と、境目の 40 / 70）", async () => {
  const course = def.courses.find((c) => c.id === "how-score")!;
  const dom = await openRisk();
  try {
    await startCourse(dom, "how-score");
    const at = course.steps.findIndex((step) => step.title === "合計を境目の点と比べる");
    assert.ok(at > 0);
    for (let i = 0; i < at; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    const values = dom.all(".tour-bubble code.tour-diagram-value").map((el) => el.textContent ?? "");
    assert.ok(values.some((text) => text.includes("25 + 10 + 20 = 55") && text.includes("55 ≥ 40 かつ 55 < 70 → HIGH")));
    assert.ok(values.some((text) => text.includes("levels: {medium: 20, high: 40, critical: 70}")));
  } finally {
    await dom.close();
  }
});

test("CB-D810 入門の案内 first-look の図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする", async () => {
  await checkCourseDiagrams("first-look");
});
