/**
 * フェーズ管理の案内の見本（`tours/phases.yml` の `sample:`）の検査。見本の定義は phases.yml と同じ書き方で、
 * 画面は `tour-phases.ts` の小さな読み取りで `PhasesPage` にする。実際の読み取り（`readPhases`）と同じ結果になることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { stringify } from "yaml";

import { readPhases } from "../../src/core/phases-doc.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import { parsePhasesSample, parsePhasesTexts } from "../../src/core/tour-phases.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("phases");
const raw = def.sample as { phases: unknown; added: unknown; project: { name: string; phases: unknown }; lock: string; error: string };
const sample = parsePhasesSample(def.sample);

/** 画面が `data-tour` で持つ指す先の印 */
const TARGETS = [
  "target",
  "path",
  "dirty",
  "open-file",
  "changed",
  "save",
  "lock",
  "phases",
  "phase-count",
  "add",
  "help",
  "help-panel",
  "find",
  "status",
  "phase-row",
  "phase-id",
  "phase-title",
  "phase-kind",
  "phase-review",
  "phase-scope",
  "phase-deliverables",
  "phase-more",
  "phase-agent",
  "phase-when",
  "tour",
];

/** 開いた定義の中を指す印（定義が開いていないと測れない） */
const INSIDE = ["phase-id", "phase-title", "phase-kind", "phase-review", "phase-scope", "phase-deliverables", "phase-agent", "phase-when"];

test("CB-T460 フェーズの見本は、実際の読み取り（readPhases）と同じ PhasesForm になる。ワークスペースもプロジェクトも", () => {
  const real = readPhases(stringify(raw.phases)).model;
  assert.deepEqual(real.problems, []);
  assert.deepEqual(sample.page.model.form, real.form);
  assert.equal(sample.page.model.version, 1);
  assert.equal(sample.page.exists, true);
  const lib = readPhases(stringify(raw.project.phases)).model;
  assert.deepEqual(lib.problems, []);
  assert.deepEqual(sample.project.model.form, lib.form);
  // 設定の切り替えの欄は、ワークスペースとプロジェクトの 2 つ。開いている対象は欄に載っている
  assert.deepEqual(sample.page.targets?.map((t) => `${t.kind}:${t.name}`), ["self:", `project:${raw.project.name}`]);
  assert.deepEqual(sample.page.target, { kind: "self", name: "" });
  assert.deepEqual(sample.project.target, { kind: "project", name: raw.project.name });
  assert.equal(sample.page.lock.locked, false);
});

test("CB-T461 追加した定義の見本は、実際の読み取りと同じ形で、新しい定義として origin が null になる", () => {
  const real = readPhases(stringify({ version: 1, phases: raw.added })).model;
  assert.deepEqual(real.problems, []);
  assert.equal(real.form.phases.length, 1);
  assert.deepEqual(sample.added, { ...real.form.phases[0], origin: null });
  // 足した定義は、計画に使えるよう id・タイトル・範囲・成果物・補足まで書いてある
  assert.ok(sample.added.id !== "" && sample.added.title !== "" && sample.added.scope.length > 0 && sample.added.deliverables.length > 0);
  assert.ok(sample.added.agent !== "" && sample.added.when !== "");
  assert.ok(!sample.page.model.form.phases.some((phase) => phase.id === sample.added.id), "追加した定義が見本の一覧にもある");
});

test("CB-T462 フェーズの見本は、work と feedback、inherit と glob、補足の有無が揃い、id もタイトルも重ならない", () => {
  for (const page of [sample.page, sample.project]) {
    const phases = page.model.form.phases;
    assert.equal(new Set(phases.map((phase) => phase.id)).size, phases.length);
    assert.equal(new Set(phases.map((phase) => phase.title)).size, phases.length);
    assert.ok(phases.some((phase) => phase.kind === "work") && phases.some((phase) => phase.kind === "feedback"));
    assert.ok(phases.filter((phase) => phase.kind === "feedback").every((phase) => phase.review === "mr"), "feedback は mr");
    assert.ok(phases.some((phase) => phase.inherit) && phases.some((phase) => !phase.inherit));
  }
  const phases = sample.page.model.form.phases;
  assert.ok(phases.some((phase) => phase.agent !== "" && phase.when !== ""), "補足を両方書いた定義");
  assert.ok(phases.some((phase) => phase.agent === "" && phase.when !== ""), "使う場面だけの定義");
  assert.ok(phases.some((phase) => phase.deliverables.length > 0) && phases.some((phase) => phase.deliverables.length === 0));
  assert.ok(phases.some((phase) => phase.review === "none"), "review none の定義");
  assert.equal(phases.filter((phase) => phase.kind === "feedback").length >= 2, true, "feedback の使い分けを見せるには 2 つ要る");
  assert.deepEqual(
    phases.map((phase) => phase.origin),
    phases.map((_, index) => index),
  );
});

test("CB-T463 作業中の文と検証の文は、見本にあり、プロジェクトの文にはプロジェクト名が入る", () => {
  const texts = parsePhasesTexts(def.sample);
  assert.equal(texts.locks.self, raw.lock);
  assert.ok(texts.locks.self !== "" && texts.error !== "");
  assert.ok(texts.locks.lib.includes(raw.project.name));
  assert.ok(texts.error.includes("feedback") && texts.error.includes("review"));
});

test("CB-T464 フェーズの案内の scene は、見本にある設定・定義の id・追加し方だけを指す（綴り違いが既定へ黙って戻らない）", () => {
  const known = ["target", "open", "more", "find", "add", "dirty", "locked", "error", "changed", "help"];
  const flags = ["dirty", "locked", "error", "changed", "help"];
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順`;
      const scene = step.scene;
      for (const key of Object.keys(scene)) {
        assert.ok(known.includes(key), `${at}: scene の鍵 ${key} を画面は読まない`);
      }
      assert.ok(scene.target === undefined || scene.target === "lib", `${at}: target は lib だけ（既定はワークスペース）`);
      const ids = new Set((scene.target === "lib" ? sample.project : sample.page).model.form.phases.map((phase) => phase.id));
      for (const key of ["open", "more"]) {
        for (const id of String((scene as Record<string, unknown>)[key] ?? "").split(",").filter((item) => item.trim() !== "")) {
          assert.ok(ids.has(id.trim()), `${at}: ${key} の ${id} が見本にありません`);
        }
      }
      assert.ok(scene.add === undefined || scene.add === "blank" || scene.add === "filled", `${at}: add`);
      assert.ok(scene.find === undefined || typeof scene.find === "string");
      for (const flag of flags) {
        const value = (scene as Record<string, unknown>)[flag];
        assert.ok(value === undefined || value === true, `${at}: ${flag} は true だけ`);
      }
      // 足した行は、既存の定義を開く scene と一緒には使わない（指す先が 2 つになる）
      assert.ok(scene.add === undefined || scene.open === undefined, `${at}: add と open は同時に使わない`);
    }
  }
});

test("CB-T465 フェーズの案内は、基本の案内の題と順序を保ち、ユースケースは「〜したいとき」の 4〜9 手順で、指す先は画面にある印だけを使う", () => {
  assert.equal(def.courses[0].id, INTRO_ID);
  assert.deepEqual(
    def.courses[0].steps.map((step) => step.title),
    ["フェーズ定義", "補足", "保存", "ヘルプ", "案内"],
  );
  assert.equal(def.courses[0].steps[def.courses[0].steps.length - 1].target, "tour", "最後は「?」を指す");
  const useCases = def.courses.slice(1);
  assert.ok(useCases.length >= 3 && useCases.length <= 8, `ユースケースが ${useCases.length} 本`);
  for (const course of useCases) {
    assert.match(course.title, /とき$/, `${course.id}: 題は利用者の目的（〜したいとき）で切る`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 手順`);
  }
  for (const course of def.courses) {
    for (const step of course.steps) {
      assert.ok(TARGETS.includes(step.target), `${course.id}: 指す先 ${step.target} は画面にない印`);
    }
  }
});

test("CB-T466 定義の中を指す手順は開いた定義を持ち、補足の欄・ヘルプ・作業中・検証の文・外の変更・未保存を指す手順は、それが出る scene を持つ", () => {
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順（${step.target}）`;
      const scene = step.scene as Record<string, unknown>;
      if (INSIDE.includes(step.target)) {
        assert.ok(scene.open !== undefined || scene.add !== undefined, `${at}: 定義が開いていない`);
      }
      if (step.target === "phase-agent" || step.target === "phase-when") {
        assert.ok(scene.add !== undefined || String(scene.more ?? "").split(",").map((id) => id.trim()).includes(String(scene.open).split(",")[0].trim()), `${at}: 補足が開いていない`);
      }
      if (step.target === "phase-more") {
        assert.ok(scene.open !== undefined, `${at}: 補足の見出しは開いた定義の中`);
      }
      if (step.target === "help-panel") {
        assert.equal(scene.help, true, at);
      }
      if (step.target === "lock") {
        assert.equal(scene.locked, true, at);
      }
      if (step.target === "status") {
        assert.equal(scene.error, true, at);
      }
      if (step.target === "changed") {
        assert.equal(scene.changed, true, at);
      }
      if (step.target === "dirty") {
        assert.equal(scene.dirty, true, at);
      }
    }
  }
});

test("CB-T467 見本の形が壊れていれば、読み取りが名指しして落ちる", () => {
  const base = def.sample as Record<string, unknown>;
  assert.throws(() => parsePhasesSample({ ...base, added: { a: { kind: "work" }, b: { kind: "work" } } }), /sample\.added は定義 1 件/);
  assert.throws(() => parsePhasesSample({ ...base, phases: [] }), /sample\.phases がオブジェクトではない/);
  assert.throws(() => parsePhasesSample({ ...base, project: undefined }), /sample\.project/);
  // 区分と既定のレビューは、実際の読み取りと同じ既定（work / mr / inherit）に落ちる
  const one = parsePhasesSample({ ...base, phases: { version: 1, phases: { x: { kind: "nonsense" } } } });
  assert.deepEqual(one.page.model.form.phases[0], { origin: 0, id: "x", title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], agent: "", when: "" });
  assert.deepEqual(readPhases(stringify({ version: 1, phases: { x: { kind: "nonsense" } } })).model.form.phases[0], one.page.model.form.phases[0]);
});
