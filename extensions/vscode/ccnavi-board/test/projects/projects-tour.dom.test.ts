/**
 * プロジェクト管理画面の案内（`src/webview/tours/projects.yml`）を happy-dom で動かす。見本は使わず、今の画面を指す。
 * 案内の間は clone・更新・.gitignore の操作を拡張ホストへ送らない。
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

test("CB-D321 プロジェクト管理画面に「?」があり、初回の案内の依頼で基本の案内が始まる。閉じたら tourDone を返す", async () => {
  const dom = await openProjects([row()]);
  try {
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, def.courses[0].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D322 全案内の全手順で、指す先が画面に 1 つあり、吹き出しが中ほどへ逃げない。案内の間は操作を送らない", async () => {
  for (const course of def.courses) {
    const dom = await openProjects([row(), row({ name: "app", rel: "projects/app" })], { ignored: false });
    try {
      await startCourse(dom, course.id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.equal(dom.all(`[data-tour="${step.target}"]`).length, 1, `${at}: 指す先 ${step.target} が画面に 1 つではない`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
        dom.click(dom.one('[data-action="tour-next"]'));
      }
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D323 案内の間に clone・更新・.gitignore を押しても、拡張ホストへ送らない", async () => {
  const dom = await openProjects([row()], { ignored: false });
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="intro"]'));
    await dom.settle();
    for (const action of ["refresh", "clone", "fix-ignore"]) {
      dom.click(dom.one(`[data-action="${action}"]`));
    }
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type !== "ready"), []);
  } finally {
    await dom.close();
  }
});
