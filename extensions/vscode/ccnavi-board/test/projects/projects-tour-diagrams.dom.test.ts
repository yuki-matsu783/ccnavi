/**
 * プロジェクト管理画面の案内のうち、時系列の図を引く 2 本（`how-clone`・`how-card`）を happy-dom で動かす。
 * 見方は `test/rules/rules.dom.test.ts` の CB-D742 と同じ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openProjects, row } from "../helpers/projects.js";
import { loadTour } from "../helpers/tour.js";
import type { DomPage } from "../helpers/dom.js";

const def = loadTour("projects");

async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

test("CB-D780 図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする。値は等幅で出て、案内の間は操作を送らない", async () => {
  for (const id of ["how-clone", "how-card"]) {
    const course = def.courses.find((c) => c.id === id)!;
    const dom = await openProjects([row(), row({ name: "app", rel: "projects/app" })], { ignored: false });
    try {
      await startCourse(dom, id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${id} の ${index + 1} 番目（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        const diagram = dom.one(".tour-bubble .tour-diagram");
        const spec = def.diagrams[step.diagram];
        assert.equal(diagram.getAttribute("data-diagram"), step.diagram, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
        const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
        assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((entryId) => step.focus.includes(entryId)), `${at}: 光る矢印`);
        assert.ok(lit.length > 0 || step.focus.length === 0, at);
        assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, step.focus.length === 0 ? 0 : spec.steps.length - lit.length, at);
        assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, spec.steps.filter((entry) => entry.value !== "").length, at);
        dom.click(dom.one('[data-action="tour-next"]'));
      }
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, `${id}: 最後の手順で閉じない`);
      assert.deepEqual(dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone"), [], `${id}: 案内の間に操作を送った`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D781 clone の例の図は、ターミナルへ送る 1 行と、止まる例の一言を、定義の値のまま出す", async () => {
  const course = def.courses.find((c) => c.id === "how-clone")!;
  const dom = await openProjects([row()], { ignored: false });
  try {
    await startCourse(dom, "how-clone");
    const indexOf = (focus: string) => course.steps.findIndex((step) => step.focus.includes(focus));
    for (let at = 0; at < indexOf("send"); at += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    const send = dom.one('.tour-bubble .tour-diagram-step[data-step="send"]');
    assert.ok((send.textContent ?? "").includes("git clone -- 'https://example.com/team/web-app.git' '/ws/projects/web-app'"));
    for (let at = indexOf("send"); at < indexOf("cred-stop"); at += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    const stop = dom.one('.tour-bubble .tour-diagram-step[data-step="cred-stop"]');
    assert.ok((stop.textContent ?? "").includes("URL に資格情報（user:token@）が入っています。"));
    assert.equal(stop.className.includes("focus"), true);
  } finally {
    await dom.close();
  }
});
