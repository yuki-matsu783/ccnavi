/**
 * 入門の案内（first-look）を happy-dom で動かす。図を引く手順は図を出し、focus の矢印だけを光らせる。案内の間は拡張ホストへ何も送らない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openTemplates } from "../helpers/templates.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("templates");
const course = def.courses.find((c) => c.id === "first-look");

test("CB-D830 入門の案内は、図を引く手順で図を出して focus の矢印だけを光らせ、最後まで進めても何も送らず、閉じると見本が消える", async () => {
  assert.ok(course !== undefined);
  const dom = await openTemplates();
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
        assert.deepEqual(lit, spec.steps.map((s) => s.id).filter((id) => step.focus.includes(id)), `${at}: 光る矢印`);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, spec.steps.length - lit.length, at);
      }
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.equal(dom.all(".tour").length, 0, "最後の手順で閉じない");
    assert.equal(dom.all(".banner.tour-sample").length, 0, "見本の帯が残った");
    assert.deepEqual(
      dom.posted.filter((m) => m.type !== "ready" && m.type !== "preview" && m.type !== "tourDone"),
      [],
      "案内の間に操作を送った",
    );
  } finally {
    await dom.close();
  }
});
