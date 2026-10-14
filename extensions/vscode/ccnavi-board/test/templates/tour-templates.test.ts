/**
 * テンプレート管理の案内の見本（`tours/templates.yml` の `sample:`）の検査。見本は小さい書き方で、
 * 画面は `tour-templates.ts` の読み取りで `TemplatesPage` にする。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID } from "../../src/core/tour-def.js";
import { parseTemplatesSample } from "../../src/core/tour-templates.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("templates");
const sample = parseTemplatesSample(def.sample);

/** 画面が `data-tour` で持つ指す先の印 */
const TARGETS = ["summary", "target", "refresh", "list", "template", "drafts", "draft", "draft-import", "import-form", "import-preview", "sum", "sum-pick", "sum-count", "sum-table", "tour"];

test("CB-T572 テンプレートの見本は、self の層のテンプレート・下書き・統合結果・本文・プレビューに読める", () => {
  const data = sample.page.data;
  assert.equal(data.layers.length, 1);
  assert.equal(data.layers[0].name, "self");
  assert.deepEqual(
    data.layers[0].templates.map((t) => `${t.layer}/${t.name}/${t.effective}`),
    ["self/feature/true", "self/research/false"],
  );
  const research = data.layers[0].templates[1];
  assert.equal(research.dropped?.reason, "conflict");
  assert.equal(research.usable, false);
  assert.deepEqual(data.drafts.map((d) => `${d.name}/${d.importable}`), ["login/true", "broken/false"]);
  assert.deepEqual(data.sums.map((s) => s.name), ["self", "lib"]);
  assert.equal(data.errors, 1);
  assert.equal(data.warns, 1);
  assert.ok(sample.texts.feature.includes("name: feature"));
  assert.equal(sample.preview.draft?.name, "login");
  assert.equal(sample.preview.target, ".ccnavi/config/common/templates/login.yml");
  assert.equal(sample.page.lock.locked, false);
});

test("CB-T573 案内の定義は、基本の案内が先頭で、指す先は画面の印だけ。scene は見本にある名前を指す", () => {
  assert.equal(def.courses[0].id, INTRO_ID);
  const names = new Set(sample.page.data.layers[0].templates.map((t) => t.name));
  const drafts = new Set(sample.page.data.drafts.map((d) => d.name));
  const sums = new Set(sample.page.data.sums.map((s) => s.name));
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 段目`;
      assert.ok(step.target === "" || TARGETS.includes(step.target), `${at}: 知らない指す先 ${step.target}`);
      for (const name of String(step.scene.open ?? "").split(",").filter((n) => n !== "")) {
        assert.ok(names.has(name.trim()), `${at}: open ${name} が見本にありません`);
      }
      for (const name of String(step.scene.draft ?? "").split(",").filter((n) => n !== "")) {
        assert.ok(drafts.has(name.trim()), `${at}: draft ${name} が見本にありません`);
      }
      if (step.scene.import !== undefined) {
        assert.ok(drafts.has(String(step.scene.import)), `${at}: import ${step.scene.import} が見本にありません`);
      }
      if (step.scene.sum !== undefined) {
        assert.ok(sums.has(String(step.scene.sum)), `${at}: sum ${step.scene.sum} が見本にありません`);
      }
      if (step.scene.preview === true) {
        assert.ok(step.scene.import !== undefined, `${at}: preview には import が必要です`);
      }
    }
  }
});
