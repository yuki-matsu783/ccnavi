/**
 * フェーズ管理の案内の時系列の図（`tours/phases.yml` の `diagrams:`）を happy-dom で動かす。図の部品の振る舞いは
 * ルール管理の CB-D742 と同じで、ここでは足した案内 2 本の全手順で図が出ることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { readPhases } from "../../src/core/phases-doc.js";
import { openPhases } from "../helpers/phases.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("phases");
const COURSES = ["how-phase-works", "how-phase-ends"];

const REAL_TEXT = `version: 1
phases:
  real-a:
    kind: work
    title: 実物A
    review: mr
    scope: inherit
`;

test("CB-D760 図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする。値は等幅で出て、案内の間は実際の定義に触れない", async () => {
  for (const id of COURSES) {
    const course = def.courses.find((c) => c.id === id)!;
    const dom = await openPhases({ model: readPhases(REAL_TEXT).model, target: { kind: "self", name: "" }, targets: [{ kind: "self", name: "", label: "ワークスペース" }] });
    try {
      dom.click(dom.one('[data-action="tour"]'));
      await dom.settle();
      dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
      await dom.settle();
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${id} の ${index + 1} 番目`;
        const diagram = dom.one(".tour-bubble .tour-diagram");
        const spec = def.diagrams[step.diagram];
        assert.equal(diagram.getAttribute("data-diagram"), step.diagram, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
        const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
        assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((entryId) => step.focus.includes(entryId)), `${at}の光る矢印`);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, step.focus.length === 0 ? 0 : spec.steps.length - lit.length, at);
        assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, spec.steps.filter((entry) => entry.value !== "").length, at);
        // 値は見本の定義から取ったもの。実際の定義 real-a は図にも画面にも出ない
        assert.ok(!(diagram.textContent ?? "").includes("real-a"), at);
        assert.ok(!dom.all(".phase .sum-id").some((el) => el.textContent === "real-a"), `${at}: 実際の定義が出ている`);
        if (index < course.steps.length - 1) {
          dom.click(dom.one('[data-action="tour-next"]'));
        }
      }
    } finally {
      await dom.close();
    }
  }
});
