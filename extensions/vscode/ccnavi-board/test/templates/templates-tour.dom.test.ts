/**
 * テンプレート管理画面の案内（`src/webview/tours/templates.yml`）を happy-dom で動かす。案内の間は見本を描き、実際の中身には触れない。
 * 案内の間は更新・切り替え・本文の依頼を拡張ホストへ送らない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openTemplates } from "../helpers/templates.js";
import { loadTour } from "../helpers/tour.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

const def = loadTour("templates");

async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

async function next(dom: DomPage): Promise<void> {
  dom.click(dom.one('[data-action="tour-next"]'));
  await dom.settle();
}

/** 案内を始めて、n 段目（0 始まり）まで進める */
async function goto(dom: DomPage, id: string, index: number): Promise<void> {
  await startCourse(dom, id);
  for (let i = 0; i < index; i += 1) {
    await next(dom);
  }
}

const names = (dom: DomPage): (string | null)[] => dom.all("li.template").map((li) => li.getAttribute("data-name"));

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
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
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
    dom.click(dom.one('button[data-action="reload"][data-tour="refresh"]'));
    dom.click(dom.one('li.template[data-name="feature"][data-layer="self"] .row-head'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready"),
      [],
    );
    assert.equal(dom.all('li.template[data-name="feature"][data-layer="self"].open').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D351 案内の間は見本を描き、実際のテンプレートは出さない。閉じたら実際の一覧に戻り、余計な送信は無い", async () => {
  const dom = await openTemplates();
  try {
    assert.deepEqual(names(dom), ["ends", "feature", "local", "research"]);
    assert.equal(dom.all(".banner.tour-sample").length, 0);
    await startCourse(dom, "intro");
    assert.deepEqual(names(dom), ["feature", "research"]);
    assert.equal(dom.all(".banner.tour-sample").length, 1);
    assert.deepEqual(dom.all("li.draft").map((li) => li.getAttribute("data-name")), ["login", "broken"]);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(names(dom), ["ends", "feature", "local", "research"]);
    assert.equal(dom.all(".banner.tour-sample").length, 0);
    assert.equal(dom.all("li.draft").length, 3);
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone"),
      [],
    );
  } finally {
    await dom.close();
  }
});

test("CB-D352 段の scene が画面の様子を替える（テンプレートを開く・本文・足し算の枠）。scene は次の段へ引き継がない", async () => {
  const dom = await openTemplates();
  try {
    // intro の 4 段目（0 始まりで 3）: 見本の feature を開き、本文を出す
    await goto(dom, "intro", 3);
    assert.equal(dom.all("li.template.open").length, 1);
    assert.match(dom.one("pre.tpl-text").textContent ?? "", /name: feature/);
    assert.equal(dom.all("details#sum[open]").length, 0);
    // 5 段目: 足し算の枠を開く。開いたテンプレートは閉じる
    await next(dom);
    assert.equal(dom.all("details#sum[open]").length, 1);
    assert.equal(dom.all("li.template.open").length, 0, "段をまたいで開いたままになった");
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "self");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all("details#sum[open]").length, 0, "閉じたあと足し算の枠が開いたまま");
  } finally {
    await dom.close();
  }
});

test("CB-D353 足し算と取り込みの案内は、見本の足し算を選び、取り込みの欄と下見の見本を出す。送信はせず、閉じたら消える", async () => {
  const sum = await openTemplates();
  try {
    await goto(sum, "read-sum", 1);
    assert.equal(sum.one<HTMLSelectElement>("select#sum-select").value, "lib");
    assert.ok((sum.one(".sum-table").textContent ?? "").includes("lib"));
  } finally {
    await sum.close();
  }
  const dom = await openTemplates();
  try {
    await goto(dom, "import", 2);
    assert.equal(dom.all('[data-role="import-form"]').length, 1);
    assert.equal(dom.all('[data-role="import-preview"]').length, 0);
    await next(dom);
    assert.equal(dom.all('[data-role="import-preview"]').length, 1);
    assert.match(dom.one('[data-role="import-preview"]').textContent ?? "", /login\.yml/);
    // 見本の上の「下見」「取り込む」は何も送らない
    dom.click(dom.one('[data-action="preview-import"]'));
    dom.click(dom.one('[data-action="import-yes"]'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready"),
      [],
    );
    await next(dom);
    assert.equal(dom.all('[data-role="import-form"]').length, 0, "段をまたいで取り込みの欄が残った");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="import-preview"]').length, 0);
  } finally {
    await dom.close();
  }
});
