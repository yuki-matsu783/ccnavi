/**
 * テンプレート管理の案内の時系列の図を happy-dom で動かす。図を引く手順は、本文の下に図を出し、focus の矢印だけを光らせる。
 * 案内の間は、図のある手順でも拡張ホストへ何も送らない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openTemplates } from "../helpers/templates.js";
import { loadTour } from "../helpers/tour.js";
import type { DomPage } from "../helpers/dom.js";

const def = loadTour("templates");

const NEW_COURSES = ["how-drafts-flow", "how-templates-used"];

function courseOf(id: string) {
  const course = def.courses.find((c) => c.id === id);
  assert.ok(course !== undefined, `${id} の案内がない`);
  return course;
}

async function start(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

async function next(dom: DomPage): Promise<void> {
  dom.click(dom.one('[data-action="tour-next"]'));
  await dom.settle();
}

test("CB-D770 図のある手順は、本文の下に時系列の図を出し、focus の矢印だけを光らせてほかを薄くする。値は等幅で出る", async () => {
  for (const id of NEW_COURSES) {
    const course = courseOf(id);
    const dom = await openTemplates();
    try {
      await start(dom, id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${id} の ${index + 1} 番目（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        if (step.diagram === "") {
          assert.equal(dom.all(".tour-bubble .tour-diagram").length, 0, `${at}: 図を引かない手順に図が出た`);
        } else {
          const spec = def.diagrams[step.diagram];
          assert.equal(dom.one(".tour-bubble .tour-diagram").getAttribute("data-diagram"), step.diagram, at);
          assert.equal(dom.all(".tour-bubble .tour-diagram-step").length, spec.steps.length, at);
          assert.equal(dom.all(".tour-bubble .tour-diagram-actor").length, spec.actors.length, at);
          const lit = dom.all(".tour-bubble .tour-diagram-step.focus").map((el) => el.getAttribute("data-step"));
          assert.deepEqual(lit, spec.steps.map((entry) => entry.id).filter((entry) => step.focus.includes(entry)), `${at}: 光る矢印`);
          assert.equal(dom.all(".tour-bubble .tour-diagram-step.dim").length, spec.steps.length - lit.length, `${at}: 薄い矢印`);
          assert.equal(dom.all(".tour-bubble code.tour-diagram-value").length, spec.steps.filter((entry) => entry.value !== "").length, `${at}: 値`);
        }
        if (index < course.steps.length - 1) {
          await next(dom);
        }
      }
    } finally {
      await dom.close();
    }
  }
});

test("CB-D771 図の値は画面に等幅でそのまま出る（下書きを作る命令・取り込みの命令・提案の命令・フィードバック計画の番号）", async () => {
  const wanted: Array<[string, number, string]> = [
    ["how-drafts-flow", 1, "ccnavi --templates --draft login"],
    ["how-drafts-flow", 5, "ccnavi user handoff .ccnavi/config/project/templates/login.yml --yes --expect"],
    ["how-templates-used", 2, "ccnavi ticket new signup"],
    ["how-templates-used", 5, "feedback（1 項）: 3 fix"],
  ];
  for (const [id, index, text] of wanted) {
    const dom = await openTemplates();
    try {
      await start(dom, id);
      for (let i = 0; i < index; i += 1) {
        await next(dom);
      }
      const values = dom.all(".tour-bubble code.tour-diagram-value").map((el) => el.textContent ?? "");
      assert.ok(values.some((value) => value.includes(text)), `${id} の ${index + 1} 番目に「${text}」が出ていない`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D772 新しい案内を最後まで進めても、図のある手順で拡張ホストへ何も送らない。閉じたら見本が消える", async () => {
  for (const id of NEW_COURSES) {
    const course = courseOf(id);
    const dom = await openTemplates();
    try {
      const before = dom.posted.filter((message) => message.type === "preview").length;
      await start(dom, id);
      assert.equal(dom.all(".banner.tour-sample").length, 1, id);
      for (let i = 0; i < course.steps.length; i += 1) {
        await next(dom);
      }
      assert.equal(dom.all(".tour").length, 0, `${id}: 最後の手順で閉じない`);
      assert.equal(dom.all(".banner.tour-sample").length, 0, `${id}: 閉じても見本の帯が残った`);
      assert.deepEqual(
        dom.posted.filter((message) => message.type !== "ready" && message.type !== "preview" && message.type !== "tourDone"),
        [],
        `${id}: 案内の間に操作を送った`,
      );
      assert.equal(dom.posted.filter((message) => message.type === "preview").length, before, `${id}: 案内の間に図の中身を頼んだ`);
    } finally {
      await dom.close();
    }
  }
});
