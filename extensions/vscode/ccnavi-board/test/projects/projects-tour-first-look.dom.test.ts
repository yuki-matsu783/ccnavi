/**
 * プロジェクト管理画面の入門の案内（`first-look`）を happy-dom で動かす。見方は `projects-tour-diagrams.dom.test.ts` の CB-D780 と同じ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openProjects, row } from "../helpers/projects.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("projects");

test("CB-D840 入門の案内は、図を引く手順で focus の矢印だけを光らせ、案内の間は操作を送らない", async () => {
  const course = def.courses.find((c) => c.id === "first-look")!;
  const dom = await openProjects([row()], { ignored: false });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="first-look"]'));
    await dom.settle();
    for (const [index, step] of course.steps.entries()) {
      await dom.settle();
      const at = `${index + 1} 番目（${step.title}）`;
      assert.equal(dom.one("#tour-title").textContent, step.title, at);
      if (step.diagram === "") {
        assert.equal(dom.all(".tour-bubble .tour-diagram").length, 0, at);
      } else {
        const spec = def.diagrams[step.diagram];
        assert.equal(dom.one(".tour-bubble .tour-diagram").getAttribute("data-diagram"), step.diagram, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
        const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
        assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((id) => step.focus.includes(id)), at);
      }
      dom.click(dom.one('[data-action="tour-next"]'));
    }
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((m) => m.type !== "ready" && m.type !== "tourDone"), []);
  } finally {
    await dom.close();
  }
});
