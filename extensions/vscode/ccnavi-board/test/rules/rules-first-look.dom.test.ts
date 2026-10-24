/**
 * ルール管理の入門の案内（`first-look`）を happy-dom で動かす。見方は `rules.dom.test.ts` の CB-D742 と同じ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openRules } from "../helpers/rules.js";
import { loadTour } from "../helpers/tour.js";

test("CB-D800 入門の案内は、全手順で入門の図を出し、focus の矢印だけを光らせてほかを薄くする。値は 1 つだけ等幅で出る", async () => {
  const def = loadTour("rules");
  const course = def.courses.find((c) => c.id === "first-look")!;
  const dom = await openRules({}, undefined, { measure: true });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="first-look"]'));
    await dom.settle();
    for (const [index, step] of course.steps.entries()) {
      await dom.settle();
      const at = `${index + 1} 番目`;
      const spec = def.diagrams[step.diagram];
      assert.equal(dom.one(".tour-bubble .tour-diagram").getAttribute("data-diagram"), "rules-first", at);
      assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
      assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
      const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
      assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((id) => step.focus.includes(id)), `${at}の光る矢印`);
      assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, spec.steps.length - lit.length, at);
      assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, 1, at);
      if (index < course.steps.length - 1) {
        dom.click(dom.one('[data-action="tour-next"]'));
      }
    }
  } finally {
    await dom.close();
  }
});
