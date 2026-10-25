/**
 * リスク管理の案内の見本（`tours/risk.yml` の `sample:`）の検査。見本の配点は risks.yml と同じ書き方で、
 * 画面は `tour-risk.ts` の小さな読み取りで `RiskPage` にする。実際の読み取り（`readRisk`）と同じ結果になることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { stringify } from "yaml";

import { BUILTIN_RISK_TEXT, readRisk } from "../../src/core/risk-doc.js";
import { KINDS, LEVEL_NAMES } from "../../src/core/risk-view.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import { parseRiskSample } from "../../src/core/tour-risk.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("risk");
const raw = def.sample as { risks: unknown; builtin: unknown; sums: Array<{ name: string; layers: string[]; levels: Record<string, number>; factors: Array<{ id: string; source: string }> }> };
const sample = parseRiskSample(def.sample);

/** 画面が `data-tour` で持つ指す先の印 */
const TARGETS = [
  "levels",
  "level-high",
  "factors",
  "factor-count",
  "find",
  "add",
  "factor-row",
  "factor-id",
  "factor-points",
  "factor-kind",
  "factor-value",
  "factor-include",
  "factor-exclude",
  "factor-message",
  "factor-buttons",
  "save",
  "missing",
  "create",
  "sum",
  "sum-pick",
  "sum-levels",
  "sum-table",
  "tour",
];

test("CB-T440 リスクの見本は、実際の読み取り（readRisk）と同じ RiskForm になる", () => {
  const real = readRisk(stringify({ version: 1, ...(raw.risks as object) })).model;
  assert.deepEqual(real.problems, []);
  assert.deepEqual(sample.page.model.form, real.form);
  assert.equal(sample.page.exists, true);
  assert.equal(sample.page.builtin, undefined);
  // 4 つの数える・触る加点条件と、script・judge が揃い、数える範囲（include / exclude）を持つ項目がある
  const kinds = new Set(real.form.factors.map((factor) => factor.kind));
  for (const kind of KINDS.filter((k) => k !== "deleted_over")) {
    assert.ok(kinds.has(kind), `見本に ${kind} の項目がありません`);
  }
  const scoped = real.form.factors.find((factor) => factor.include !== "" && factor.exclude !== "");
  assert.equal(scoped?.kind, "lines_over");
});

test("CB-T441 組み込みの配点の見本は、実際の組み込みの配点（BUILTIN_RISK_TEXT）と同じ RiskForm になる", () => {
  const real = readRisk(BUILTIN_RISK_TEXT).model;
  assert.deepEqual(sample.missing.model.form, real.form);
  assert.equal(sample.missing.exists, false);
  assert.equal(sample.missing.builtin, true);
  assert.equal(sample.missing.sums, undefined);
});

test("CB-T442 統合結果の見本は、共通の設定の項目に追加した設定の項目が続き、境目の点は小さいほう、同じ id の別の項目はレイヤー名が付く", () => {
  const sums = sample.page.sums ?? [];
  assert.deepEqual(sums.map((sum) => sum.name), ["self", "lib"]);
  const common = sample.page.model.form.factors.map((factor) => factor.id);
  for (const sum of raw.sums) {
    const layer = sum.layers[1];
    assert.deepEqual(sum.layers, ["common", sum.name]);
    assert.deepEqual(sum.factors.filter((f) => f.source === "common").map((f) => f.id), common, `${sum.name}: 共通の項目が見本の配点と違う`);
    const added = sum.factors.filter((f) => f.source !== "common");
    assert.ok(added.length >= 1 && added.every((f) => f.source === layer));
    assert.equal(new Set(sum.factors.map((f) => f.id)).size, sum.factors.length, `${sum.name}: id が重なっている`);
    // 後ろの設定が共通と同じ id を使うときは、`<レイヤー>:<id>` にする
    for (const f of added) {
      assert.ok(!common.includes(f.id), `${sum.name}: ${f.id} が共通の id と重なる（レイヤー名を付ける）`);
    }
    for (const name of LEVEL_NAMES) {
      assert.equal(typeof sum.levels[name], "number");
    }
  }
  const lib = sums.find((sum) => sum.name === "lib")!;
  assert.equal(lib.levels.high, 30);
  assert.ok(lib.levels.high! < Number(sample.page.model.form.levels.high), "小さいほうを採る");
  assert.ok(lib.factors.some((factor) => factor.id === "lib:big-diff"));
  assert.ok(lib.factors.every((factor) => typeof factor.value === "string"), "値は文字にして持つ");
});

test("CB-T443 リスクの案内の scene は、見本にある項目の id・境目の名前・統合結果の名前だけを指す（綴り違いが既定へ黙って戻らない）", () => {
  const ids = new Set(sample.page.model.form.factors.map((factor) => factor.id));
  const sums = new Set((sample.page.sums ?? []).map((sum) => sum.name));
  const known = ["open", "find", "blank", "missing", "sum"];
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順`;
      const scene = step.scene;
      for (const id of String(scene.open ?? "").split(",").filter((item) => item.trim() !== "")) {
        assert.ok(ids.has(id.trim()), `${at}: open の ${id} が見本にありません`);
      }
      assert.ok(scene.blank === undefined || (LEVEL_NAMES as readonly string[]).includes(String(scene.blank)), `${at}: blank`);
      assert.ok(scene.sum === undefined || sums.has(String(scene.sum)), `${at}: sum の ${String(scene.sum)} が見本にありません`);
      assert.ok(scene.missing === undefined || scene.missing === true, `${at}: missing`);
      assert.ok(scene.find === undefined || typeof scene.find === "string");
      for (const key of Object.keys(scene)) {
        assert.ok(known.includes(key), `${at}: scene のキー ${key} を画面は読まない`);
      }
    }
  }
});

test("CB-T444 リスクの案内は、基本の案内のタイトルと順序を保ち、ユースケースは「〜したいとき」の 4〜9 手順で、指す先は画面にある印だけを使う", () => {
  assert.equal(def.courses[0].id, INTRO_ID);
  assert.deepEqual(
    def.courses[0].steps.map((step) => step.title),
    ["リスクレベルの境目の点", "項目", "保存", "案内"],
  );
  assert.equal(def.courses[0].steps[def.courses[0].steps.length - 1].target, "tour", "最後は「?」を指す");
  const useCases = def.courses.slice(1);
  assert.ok(useCases.length >= 3 && useCases.length <= 8, `ユースケースが ${useCases.length} 本`);
  for (const course of useCases) {
    assert.match(course.title, /とき$/, `${course.id}: タイトルは利用者の目的（〜したいとき）で切る`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 手順`);
  }
  for (const course of def.courses) {
    for (const step of course.steps) {
      assert.ok(step.target === "" || TARGETS.includes(step.target), `${course.id}: 指す先 ${step.target} は画面にない印`);
    }
  }
});

test("CB-T445 項目の中を指す手順は、その項目の加点条件に出る欄を指し、ファイルがない状態の手順だけが帯と「作る」を指す", () => {
  const forms = new Map(sample.page.model.form.factors.map((factor) => [factor.id, factor]));
  const counting = ["lines_over", "files_over", "deleted_over"];
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順（${step.target}）`;
      if (step.target.startsWith("factor-") && step.target !== "factor-count") {
        const open = String(step.scene.open ?? "").split(",")[0].trim();
        assert.ok(forms.has(open), `${at}: 指す項目が開かれていない`);
        if (step.target === "factor-include" || step.target === "factor-exclude") {
          assert.ok(counting.includes(forms.get(open)!.kind), `${at}: 数える加点条件の項目だけに出る欄`);
        }
      }
      if (step.target === "missing" || step.target === "create") {
        assert.equal(step.scene.missing, true, at);
      }
      if (step.target.startsWith("sum")) {
        assert.ok(step.scene.sum !== undefined, `${at}: 統合結果を開く scene が必要です`);
      }
      if (step.scene.missing === true) {
        assert.ok(["missing", "levels", "create"].includes(step.target), `${at}: 組み込みの配点の状態で指せるのは帯・境目・作るボタン`);
      }
    }
  }
});
