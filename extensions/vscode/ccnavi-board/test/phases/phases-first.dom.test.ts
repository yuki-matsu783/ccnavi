/**
 * フェーズ管理の入門の案内 `first-look` を happy-dom で動かし、全手順で図が出て、光る矢印が focus どおりになることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { readPhases } from "../../src/core/phases-doc.js";
import { openPhases } from "../helpers/phases.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("phases");

test("CB-D820 入門の案内は一覧の 2 番目に出て、全手順で phase-first の図を出し、focus の矢印だけを光らせる", async () => {
  const course = def.courses.find((c) => c.id === "first-look")!;
  const text = "version: 1\nphases:\n  real-a:\n    kind: work\n    title: 実物A\n    review: mr\n    scope: inherit\n";
  const dom = await openPhases({ model: readPhases(text).model, target: { kind: "self", name: "" }, targets: [{ kind: "self", name: "", label: "ワークスペース" }] });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    assert.equal(dom.all('[data-action="tour-course"]')[1].getAttribute("data-course"), "first-look");
    dom.click(dom.one('[data-action="tour-course"][data-course="first-look"]'));
    await dom.settle();
    const spec = def.diagrams["phase-first"];
    for (const [index, step] of course.steps.entries()) {
      await dom.settle();
      const at = `${index + 1} 番目`;
      assert.equal(dom.one(".tour-bubble .tour-diagram").getAttribute("data-diagram"), "phase-first", at);
      assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
      assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
      const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
      assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((id) => step.focus.includes(id)), `${at}の光る矢印`);
      assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, 1, at);
      assert.ok(!dom.all(".phase .sum-id").some((el) => el.textContent === "real-a"), `${at}: 実際の定義が出ている`);
      if (index < course.steps.length - 1) {
        dom.click(dom.one('[data-action="tour-next"]'));
      }
    }
  } finally {
    await dom.close();
  }
});
