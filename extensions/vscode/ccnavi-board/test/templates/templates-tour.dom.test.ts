/**
 * テンプレート管理画面の案内（`src/webview/tours/templates.yml`）を happy-dom で動かす。見本は使わず、今の画面を指す。
 * 案内の間は更新・切り替え・本文の依頼を拡張ホストへ送らない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openTemplates } from "../helpers/templates.js";
import { loadTour } from "../helpers/tour.js";
import type { DomPage } from "../helpers/dom.js";

const def = loadTour("templates");

async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

test("CB-D332 テンプレート管理画面に「?」があり、初回の依頼で基本の案内が始まる。閉じたら tourDone を返す", async () => {
  const dom = await openTemplates();
  try {
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, def.courses[0].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "tourDone"),
      [{ type: "tourDone" }],
    );
  } finally {
    await dom.close();
  }
});

test("CB-D333 全案内の全段で、指す先が画面に 1 つあり、吹き出しが中ほどへ逃げない。案内の間は操作を送らない", async () => {
  for (const course of def.courses) {
    const dom = await openTemplates();
    try {
      await startCourse(dom, course.id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 段目（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.equal(dom.all(`[data-tour="${step.target}"]`).length, 1, `${at}: 指す先 ${step.target} が画面に 1 つではない`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先が無い）`);
        dom.click(dom.one('[data-action="tour-next"]'));
      }
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の段で閉じない`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D334 案内の間に更新・テンプレートの開閉・本文の依頼を押しても、拡張ホストへ送らない", async () => {
  const dom = await openTemplates();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="intro"]'));
    await dom.settle();
    dom.click(dom.one('button[data-action="refresh"]'));
    dom.click(dom.one('li.template[data-name="feature"][data-layer="common"] .row-head'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready"),
      [],
    );
    assert.equal(dom.all('li.template[data-name="feature"][data-layer="common"].open').length, 0);
  } finally {
    await dom.close();
  }
});
