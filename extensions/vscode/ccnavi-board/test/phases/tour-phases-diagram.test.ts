/**
 * フェーズ管理の案内の時系列の図（`tours/phases.yml` の `diagrams:`）の検査。図の矢印に添えた値は、見本の定義（`sample:`）と
 * 食い違わないこと、計画の番号が図どうしで揃うこと、図を引く案内の形を見る。図の形そのものの検査は `test/shared/tour-def.test.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parse } from "yaml";

import type { DiagramDef } from "../../src/core/tour-def.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("phases");
const raw = def.sample as { phases: { phases: Record<string, { kind: string; review: string; scope: unknown; deliverables?: string[] }> } };
const sample = raw.phases.phases;

/** この担当で足した図と、それを引く案内 */
const DIAGRAMS = ["phase-save", "phase-fill", "phase-end-none", "phase-end-mr", "phase-review-up", "phase-order"];
const COURSES = ["how-phase-works", "how-phase-ends"];

function value(name: string, id: string): string {
  const step = def.diagrams[name].steps.find((entry) => entry.id === id);
  assert.ok(step !== undefined, `${name} に矢印 ${id} がない`);
  return step.value;
}

interface PlanItem {
  readonly type: string;
  readonly after: number[];
}

/** 図に書いた計画（`plan:` の値）を、ステップの型と先行の番号にする。名前だけのステップは先行なし */
function planOf(text: string): PlanItem[] {
  const list = (parse(text) as { plan: Array<string | { type: string; after?: number[] }> }).plan;
  return list.map((item) => (typeof item === "string" ? { type: item, after: [] } : { type: item.type, after: item.after ?? [] }));
}

/** `after` を推移的に辿った、ステップ（1 始まり）の先行の番号 */
function waits(items: readonly PlanItem[], number: number): number[] {
  const seen = new Set<number>();
  const walk = (n: number): void => {
    for (const before of items[n - 1].after) {
      if (!seen.has(before)) {
        seen.add(before);
        walk(before);
      }
    }
  };
  walk(number);
  return [...seen].sort((a, b) => a - b);
}

test("CB-T1190 図の中の値は、見本の定義（research・design など）と一致する。直した定義・コピー・review の例のどれも", () => {
  const research = sample.research;
  // 保存の図: 直す定義は見本の research と同じ値
  assert.deepEqual(parse(value("phase-save", "edit")), { review: research.review, scope: research.scope, deliverables: research.deliverables });
  assert.match(value("phase-save", "write"), /\.ccnavi\/config\/project\/phases\.yml/);
  assert.match(value("phase-save", "save"), /feedback/);
  assert.equal(sample["design-feedback"].kind, "feedback", "保存の図の例（feedback に none）が成り立つ定義");

  // コピーの図: コピーする定義は、見本の review と scope のまま
  const copied = (parse(value("phase-fill", "write")) as { phases: Record<string, { review: string; scope: unknown }> }).phases;
  const plan = planOf(value("phase-fill", "plan"));
  assert.deepEqual(Object.keys(copied).sort(), [...new Set(plan.map((item) => item.type))].sort(), "コピーは計画のステップが使う定義だけ");
  for (const [id, entry] of Object.entries(copied)) {
    assert.deepEqual(entry, { review: sample[id].review, scope: sample[id].scope }, `${id} のコピー`);
  }
  assert.ok(!Object.keys(copied).includes("design-feedback"), "使わない定義はコピーしない");
  assert.match(value("phase-fill", "write"), /design-feedback/);

  // review の図: 定義が none の research に、計画のステップの mr を足す
  assert.equal(research.review, "none");
  assert.deepEqual(parse(value("phase-review-up", "def")), { review: research.review });
  assert.deepEqual(parse(value("phase-review-up", "plan")), [{ type: "research", review: "mr" }]);
  assert.match(value("phase-review-up", "pick"), /none/);
  assert.match(value("phase-review-up", "risk"), /HIGH/);

  // 終わりの図: none の例は research、mr の例は design。成果物は定義の値
  assert.equal(research.review, "none");
  assert.equal(sample.design.review, "mr");
  assert.ok(research.deliverables !== undefined && value("phase-end-none", "deliverables").includes(research.deliverables[0]));
  assert.match(value("phase-end-none", "kind"), new RegExp(`review: ${research.review}`));
  assert.match(value("phase-end-mr", "ended"), new RegExp(`review: ${sample.design.review}`));
});

test("CB-T1191 計画の番号と先行は、図どうしで揃う。ステップは work の定義、after は小さい番号だけ、最後のステップに全部が先行する", () => {
  const plan = planOf(value("phase-fill", "plan"));
  for (const [index, item] of plan.entries()) {
    assert.equal(sample[item.type]?.kind, "work", `${index + 1} 番目のステップ ${item.type} は work の定義`);
    assert.ok(item.after.every((n) => Number.isInteger(n) && n >= 1 && n < index + 1), `${index + 1} 番目の after は自分より小さい番号だけ`);
  }
  assert.deepEqual(
    waits(plan, plan.length),
    plan.slice(0, -1).map((_, index) => index + 1),
    "最後のステップ（終端）に、前の全部が推移的に先行する",
  );

  // 順序の図は、同じ計画を「番号: ステップ」の形で書く
  const ordered = parse(value("phase-order", "after")) as Record<string, string | { type: string; after?: number[] }>;
  assert.deepEqual(
    Object.values(ordered).map((item) => (typeof item === "string" ? { type: item, after: [] } : { type: item.type, after: item.after ?? [] })),
    plan,
    "順序の図の計画が、コピーの図の計画と違う",
  );
  assert.deepEqual(Object.keys(ordered), plan.map((_, index) => String(index + 1)));
  // 先行の行（「N: 定義 — 先行 a, b」）は、計画から計算した先行と同じ
  const lines = value("phase-order", "waits").split("\n");
  assert.ok(lines.length >= 2);
  for (const line of lines) {
    const match = /^(\d+): (\S+) — 先行 (.+)$/.exec(line);
    assert.ok(match !== null, `先行の行の形: ${line}`);
    const number = Number(match[1]);
    assert.equal(plan[number - 1].type, match[2]);
    assert.deepEqual(match[3].split(", ").map(Number), waits(plan, number), `${number} 番目の先行`);
  }
  // 終わりの図が「N つ目のフェーズ（定義）」と呼ぶ番号と、レビューの依頼の --phase は、計画と同じ
  const numbered = (name: string, id: string): [number, string] => {
    const match = /(一|二|三|四|五|六|七|八|九)つ目のフェーズ（(\S+?)）/.exec(def.diagrams[name].steps.find((step) => step.id === id)!.text);
    assert.ok(match !== null, `${name}/${id} の本文に「N つ目のフェーズ（定義）」がない`);
    return ["一二三四五六七八九".indexOf(match[1]) + 1, match[2]];
  };
  const [noneNumber, noneType] = numbered("phase-end-none", "finish");
  const [mrNumber, mrType] = numbered("phase-end-mr", "finish");
  assert.equal(plan[noneNumber - 1].type, noneType);
  assert.equal(plan[mrNumber - 1].type, mrType);
  assert.match(value("phase-end-none", "ended"), new RegExp(`phase: ${noneNumber} `));
  assert.match(value("phase-end-mr", "ended"), new RegExp(`phase: ${mrNumber} `));
  assert.match(value("phase-end-mr", "request"), new RegExp(`--phase ${mrNumber}\\b`));
  assert.match(value("phase-end-mr", "confirm"), new RegExp(`--phase ${mrNumber}\\b`));
  assert.match(value("phase-order", "gate"), new RegExp(`${"一二三四五六七八九"[plan.length - 2]}つ目`));
});

test("CB-T1192 図は登場者 2〜6 人で、値つきの矢印を持つ。足した案内 2 本は「〜たいとき」の 4〜9 手順で、全手順が図を引き、既存の案内の ID とタイトルを変えない", () => {
  for (const name of DIAGRAMS) {
    const diagram: DiagramDef = def.diagrams[name];
    assert.ok(diagram !== undefined, `${name} がない`);
    assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 6, `${name}: 登場者 ${diagram.actors.length} 人`);
    assert.ok(diagram.steps.some((step) => step.value !== ""), `${name}: 値つきの矢印がない`);
    assert.ok(diagram.steps.some((step) => step.from !== step.to) , `${name}: 登場者どうしの矢印がない`);
  }
  assert.deepEqual(Object.keys(def.diagrams).sort(), [...DIAGRAMS].sort(), "引かれない図、または無い図");

  const ids = def.courses.map((course) => course.id);
  assert.deepEqual(ids.slice(0, 1), ["intro"], "基本の案内が先頭");
  assert.deepEqual(ids.slice(1, 3), COURSES, "足した 2 本は基本の案内の次");
  assert.deepEqual(ids.slice(3), ["add-phase", "guide-agent", "feedback", "project", "save"], "既存の案内の ID と順");
  const titles = def.courses.slice(3).map((course) => course.title);
  assert.deepEqual(titles, [
    "新しい工程の定義を追加して、計画に利用できるようにしたいとき",
    "受入テスト作成などの使いどころを、エージェントに案内したいとき",
    "フィードバック対応の定義を使い分けたいとき",
    "プロジェクトごとの定義に切り替えたいとき",
    "定義を保存して、書式を検証したいとき",
  ]);

  const used = new Set<string>();
  for (const course of def.courses.slice(1, 3)) {
    assert.match(course.title, /とき$/, `${course.id}: タイトルは「〜たいとき」`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 手順`);
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順`;
      assert.ok(step.diagram !== "", `${at}: 図を引いていない`);
      assert.ok(step.focus.length > 0, `${at}: focus がない`);
      assert.equal(step.image, "", at);
      used.add(step.diagram);
    }
  }
  assert.deepEqual([...used].sort(), [...DIAGRAMS].sort(), "足した案内が引かない図がある");
});
