/**
 * 図の下の注意（`src/webview/phases/text.ts` の `graphNotices`）と、吹き出しの置き場所
 * （`src/core/tour-place.ts`）。どちらも DOM に触れないので単体で試す。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { graphOf } from "../../src/core/phases-graph.js";
import type { PhaseForm, PhasesForm } from "../../src/core/phases-view.js";
import { placeBubble } from "../../src/core/tour-place.js";
import { graphNotices } from "../../src/webview/phases/text.js";

function phase(id: string, overrides: Partial<PhaseForm> = {}): PhaseForm {
  return { origin: null, id, title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], overlap: [], requires: [], after: [], agent: "", when: "", ...overrides };
}

function notices(f: PhasesForm, layer = false): readonly string[] {
  return graphNotices(graphOf(f), f, layer);
}

test("CB-T212 注意は当てはまるときだけ。sequential の after は、線にならない（ほかの層を指す）ものでも言う", () => {
  assert.deepEqual(notices({ order: "dag", phases: [phase("a"), phase("b", { after: ["a"] })] }), []);
  // 行き先がこのファイルに無い after だけでも、sequential では効かないと言う
  const seq = notices({ order: "sequential", phases: [phase("a", { after: ["外の層の種類"] })] }, true);
  assert.ok(seq.some((line) => /sequential なので、after は判定に効きません/.test(line)));
  assert.ok(seq.some((line) => /ほかの層の種類を指しているならそのままで構いません/.test(line)));
});

test("CB-T213 層の画面で dag を選んでいたら、ほかの層が sequential なら効かないと言う。共通層では言わない", () => {
  const f: PhasesForm = { order: "dag", phases: [phase("a"), phase("b", { after: ["a"] })] };
  assert.ok(notices(f, true).some((line) => /ほかの層のどれかが sequential なら、判定は sequential で待ちます/.test(line)));
  assert.ok(!notices(f, false).some((line) => /ほかの層/.test(line)));
});

test("CB-T214 吹き出しは画面の外に出ない。下に収まらなければ上、どちらにも収まらなければ画面の下端に寄せる", () => {
  const view = { width: 800, height: 600 };
  const bubble = { width: 340, height: 200 };
  // 下に収まる
  assert.deepEqual(placeBubble({ top: 50, left: 20, width: 100, height: 30 }, bubble, view), { top: 90, left: 20 });
  // 下に収まらず、上に収まる
  assert.equal(placeBubble({ top: 450, left: 20, width: 100, height: 30 }, bubble, view).top, 240);
  // 画面より大きな一覧を指す。前は下に出して画面の外へ消え、「次へ」が押せなかった
  const tall = placeBubble({ top: 40, left: 20, width: 700, height: 900 }, bubble, view);
  assert.ok(tall.top >= 8 && tall.top + bubble.height <= view.height - 8, `画面の外に出た: ${tall.top}`);
  // 右端の要素でも、吹き出しの右端は画面に収まる
  assert.ok(placeBubble({ top: 50, left: 780, width: 20, height: 20 }, bubble, view).left + bubble.width <= view.width - 8);
});
