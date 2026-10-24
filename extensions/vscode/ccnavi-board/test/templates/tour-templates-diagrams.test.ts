/**
 * テンプレート管理の案内の時系列の図（`tours/templates.yml` の `diagrams:`）と、図を引く 2 本の案内の検査。
 * 図の中の値（名前・説明・パス・計画の項）は、同じ YAML の見本（`sample:`）と食い違わないようにする。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { INTRO_ID } from "../../src/core/tour-def.js";
import { parseTemplatesSample } from "../../src/core/tour-templates.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("templates");
const sample = parseTemplatesSample(def.sample);

const NEW_COURSES = ["how-drafts-flow", "how-templates-used"];
const DIAGRAMS = ["template-draft", "template-import", "template-propose", "template-propose-feedback"];

/** 見本の層の置き場（`sample.path`） */
const PLACE = (def.sample as { path: string }).path;

/** 図の中の値をすべて、改行でつないだ 1 本の文字列にする */
function valuesOf(name: string): string {
  return def.diagrams[name].steps.map((step) => step.value).join("\n");
}

function courseOf(id: string) {
  const course = def.courses.find((c) => c.id === id);
  assert.ok(course !== undefined, `${id} の案内がない`);
  return course;
}

function featureOf() {
  const feature = sample.page.data.layers[0].templates.find((t) => t.name === "feature");
  assert.ok(feature !== undefined);
  return feature;
}

/** 見本の feature の計画を、提案に書かれる形（`1 design / 2 implement（待つ 1）`）にする。待つ項は 1 始まりの番号で言う */
function planText(): string {
  return featureOf()
    .plan.map((item, index) => `${index + 1} ${item.type}${item.after.length > 0 ? `（待つ ${index}）` : ""}`)
    .join(" / ");
}

test("CB-T1200 テンプレート管理の案内に、使い方ごとの案内が 2 本、基本の案内の次に並ぶ。既存の案内の ID・題は変わらない", () => {
  assert.deepEqual(
    def.courses.map((course) => course.id),
    [INTRO_ID, ...NEW_COURSES, "read-sum", "read-drafts"],
  );
  assert.equal(courseOf(INTRO_ID).title, "基本の案内");
  assert.equal(courseOf("read-sum").title, "統合結果を確認するとき");
  assert.equal(courseOf("read-drafts").title, "下書きを確認するとき");
  for (const id of NEW_COURSES) {
    const course = courseOf(id);
    assert.ok(course.title.endsWith("たいとき"), `${id}: 題は「〜たいとき」で終える（${course.title}）`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${id}: 手順は 4〜9（${course.steps.length}）`);
    for (const [index, step] of course.steps.entries()) {
      assert.notEqual(step.target, "", `${id} の ${index + 1} 番目: 指す先は画面の印にする`);
    }
  }
});

test("CB-T1201 図は 4 つで、登場者は 2〜6 人。2 本の案内の手順だけが引き、矢印には値の例が添えられている", () => {
  assert.deepEqual(Object.keys(def.diagrams).sort(), [...DIAGRAMS].sort());
  for (const name of DIAGRAMS) {
    const diagram = def.diagrams[name];
    assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 6, `${name}: 登場者の数`);
    assert.ok(diagram.steps.filter((step) => step.value !== "").length >= 2, `${name}: 値を添えた矢印が少ない`);
  }
  for (const course of def.courses.filter((c) => !NEW_COURSES.includes(c.id))) {
    assert.ok(course.steps.every((step) => step.diagram === ""), `${course.id}: 既存の案内は図を引かない`);
  }
  const used = new Set<string>();
  for (const id of NEW_COURSES) {
    for (const step of courseOf(id).steps) {
      if (step.diagram !== "") {
        used.add(step.diagram);
        assert.ok(step.focus.length > 0, `${id} の「${step.title}」: 光らせる矢印を決める`);
      }
    }
  }
  assert.deepEqual([...used].sort(), [...DIAGRAMS].sort(), "2 本の案内が使わない図がある");
});

test("CB-T1202 図の中の値は、見本の下書き・テンプレート・置き場と一致する（名前・説明・パス・計画の項・使う場面）", () => {
  const login = sample.page.data.drafts.find((d) => d.name === "login");
  assert.ok(login !== undefined);
  const feature = featureOf();
  const target = `${PLACE}${login.name}.yml`;

  const draft = valuesOf("template-draft");
  assert.ok(draft.includes(`ccnavi --templates --draft ${login.name}`), "下書きを作る命令に見本の下書きの名前");
  assert.ok(draft.includes(`wip/handoff/${target}`), "下書きの置き場は、見本の層の置き場と同じ相対パス");
  assert.ok(draft.includes(`description: ${login.description}`), "直したあとの説明は、見本の下書きの説明");

  const imported = valuesOf("template-import");
  assert.ok(imported.includes(`ccnavi user handoff ${target} --yes --expect`), "取り込みの命令に、見本の置き場と下書きの名前");
  assert.ok(imported.includes(`取り込めた: ${target}`));

  const propose = valuesOf("template-propose");
  assert.ok(propose.includes(`${feature.name}  [self]  ${feature.description}`), "一覧の答えに、見本のテンプレートの名前・説明");
  assert.ok(propose.includes(`いつ: ${feature.when}`), "一覧の答えに、見本の使う場面");
  assert.ok(propose.includes(`--template ${feature.name}`));
  assert.ok(propose.includes(`${PLACE}${feature.name}.yml`), "引く先は、見本の層の置き場の feature.yml");
  assert.ok(propose.includes(`plan（${feature.plan.length} 項）: ${planText()}`), "提案の計画は見本の plan と同じ並び");
  assert.ok(propose.includes(`フィードバック計画（${feature.feedback.length} 項）`), "見本の feedback の項数");
  assert.ok(!/ccnavi ticket new [^\n]*--with-feedback/.test(propose), "フィードバックなしの図の命令に --with-feedback を付けない");

  const withFeedback = valuesOf("template-propose-feedback");
  assert.ok(/ccnavi ticket new [^\n]*--template feature --with-feedback/.test(withFeedback));
  const planned = feature.plan.length;
  assert.ok(withFeedback.includes(`feedback（${feature.feedback.length} 項）: ${planned + 1} ${feature.feedback[0].type}`), "フィードバック計画の番号は全体計画の続き");
  assert.ok(withFeedback.includes(`plan（${planned} 項）: ${planText()}`));
});

test("CB-T1203 新しい案内の手順の scene は見本にある名前だけを指し、図を引く手順の focus は図の矢印にある", () => {
  const templates = new Set(sample.page.data.layers[0].templates.map((t) => t.name));
  const drafts = new Set(sample.page.data.drafts.map((d) => d.name));
  const sums = new Set(sample.page.data.sums.map((s) => s.name));
  for (const id of NEW_COURSES) {
    for (const [index, step] of courseOf(id).steps.entries()) {
      const at = `${id} の ${index + 1} 番目`;
      if (step.scene.open !== undefined) {
        assert.ok(templates.has(String(step.scene.open)), `${at}: open`);
      }
      if (step.scene.draft !== undefined) {
        assert.ok(drafts.has(String(step.scene.draft)), `${at}: draft`);
      }
      if (step.scene.sum !== undefined) {
        assert.ok(sums.has(String(step.scene.sum)), `${at}: sum`);
      }
      if (step.diagram !== "") {
        const ids = def.diagrams[step.diagram].steps.map((s) => s.id);
        for (const focus of step.focus) {
          assert.ok(ids.includes(focus), `${at}: focus ${focus}`);
        }
      }
    }
  }
});
