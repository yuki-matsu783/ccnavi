/**
 * リスク管理の案内の時系列の図（`tours/risk.yml` の `diagrams:`）の検査。図の中の値（差分・点・合計・レベル・境目の点）が、
 * 見本の配点（`sample:`）をそのとおりに当てた結果と一致していること、新しい 2 本の案内の形を見る。
 * 点を数えるのは実行ファイルで、ここでは `risk.py` の `evaluate` と同じ数え方を小さく書き直して、図の数字と突き合わせる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { DiagramDef } from "../../src/core/tour-def.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import { parseRiskSample } from "../../src/core/tour-risk.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("risk");
const sample = parseRiskSample(def.sample);
const form = sample.page.model.form;

interface Change {
  readonly path: string;
  readonly added: number;
  readonly deleted: number;
}

interface Rule {
  readonly kind: string;
  readonly value: string;
  readonly points: number;
  readonly max?: number;
  readonly include: readonly string[];
  readonly exclude: readonly string[];
}

const lines = (text: string): string[] => text.split("\n").map((line) => line.trim()).filter((line) => line !== "");

/** `dir/**` と完全一致だけを読む（見本が使う glob はこの 2 つ）。大文字小文字は区別しない */
function globMatch(glob: string, path: string): boolean {
  const g = glob.toLowerCase();
  const p = path.toLowerCase();
  return g.endsWith("/**") ? p.startsWith(g.slice(0, -2)) : g === p;
}

/** 見本の配点（共通の設定）の項目 */
function commonRules(): Map<string, Rule> {
  return new Map(
    form.factors.map((factor) => [
      factor.id,
      {
        kind: factor.kind,
        value: factor.value,
        points: Number(factor.points),
        max: factor.max === "" ? undefined : Number(factor.max),
        include: lines(factor.include),
        exclude: lines(factor.exclude),
      },
    ]),
  );
}

/** 統合結果の追加した設定の項目（`lib:big-diff` など） */
function addedRules(sumName: string): Map<string, Rule> {
  const sum = (sample.page.sums ?? []).find((entry) => entry.name === sumName)!;
  return new Map(
    sum.factors
      .filter((factor) => factor.source !== "common")
      .map((factor) => [factor.id, { kind: factor.kind, value: factor.value, points: Number(factor.points), include: [], exclude: [] }]),
  );
}

/** 項目の加点。実行ファイルの `risk.evaluate` と同じ数え方（script と judge は答えを渡す） */
function score(rules: Map<string, Rule>, changes: readonly Change[], answers: Record<string, number>): Record<string, number> {
  const hits: Record<string, number> = {};
  for (const [id, rule] of rules) {
    const scoped = changes.filter((change) => {
      const included = rule.include.length === 0 || rule.include.some((glob) => globMatch(glob, change.path));
      return included && !rule.exclude.some((glob) => globMatch(glob, change.path));
    });
    let points = 0;
    if (rule.kind === "lines_over") {
      points = scoped.reduce((sum, change) => sum + change.added + change.deleted, 0) > Number(rule.value) ? rule.points : 0;
    } else if (rule.kind === "files_over") {
      points = scoped.length > Number(rule.value) ? rule.points : 0;
    } else if (rule.kind === "glob") {
      const count = changes.filter((change) => globMatch(rule.value, change.path)).length;
      points = Math.min(rule.points * count, rule.max ?? Number.POSITIVE_INFINITY);
    } else {
      points = answers[id] ?? 0;
    }
    if (points > 0) {
      hits[id] = points;
    }
  }
  return hits;
}

function levelOf(points: number, levels: { medium: number; high: number; critical: number }): string {
  return points >= levels.critical ? "CRITICAL" : points >= levels.high ? "HIGH" : points >= levels.medium ? "MEDIUM" : "LOW";
}

const SAMPLE_LEVELS = { medium: Number(form.levels.medium), high: Number(form.levels.high), critical: Number(form.levels.critical) };

function valueOf(name: string, id: string): string {
  const step = def.diagrams[name].steps.find((entry) => entry.id === id);
  assert.ok(step !== undefined, `${name} に矢印 ${id} がありません`);
  return step.value;
}

function textOf(name: string, id: string): string {
  return def.diagrams[name].steps.find((entry) => entry.id === id)!.text;
}

function levelsText(levels: { medium: number; high: number; critical: number }): string {
  return `levels: {medium: ${levels.medium}, high: ${levels.high}, critical: ${levels.critical}}`;
}

test("CB-T1180 リスクの図の中の値（差分・加点・合計・レベル・境目）は、見本の配点を当てた結果と一致する", () => {
  const rules = commonRules();

  // 4 ファイルの子: big-diff（src/** から src/generated/** を外す）と ci が当たり、judge が yes で HIGH
  const big: Change[] = [
    { path: "src/auth/login.py", added: 190, deleted: 60 },
    { path: "src/auth/session.py", added: 70, deleted: 10 },
    { path: "src/generated/api.py", added: 500, deleted: 0 },
    { path: ".github/workflows/ci.yml", added: 6, deleted: 2 },
  ];
  const hits = score(rules, big, { "no-tests": 20 });
  assert.deepEqual(hits, { "big-diff": 25, ci: 10, "no-tests": 20 });
  const total = Object.values(hits).reduce((sum, points) => sum + points, 0);
  assert.equal(levelOf(total, SAMPLE_LEVELS), "HIGH");
  const diff = valueOf("risk-score-high", "diff");
  for (const change of big) {
    assert.ok(diff.includes(`${change.path}`) && diff.includes(`+${change.added} -${change.deleted}`), `diff に ${change.path} の行数がありません`);
  }
  const counted = big.filter((change) => change.path.startsWith("src/") && !change.path.startsWith("src/generated/")).map((change) => change.added + change.deleted);
  assert.equal(valueOf("risk-score-high", "c-lines"), `${counted.join(" + ")} = ${counted.reduce((a, b) => a + b, 0)} 行 > ${rules.get("big-diff")!.value}\n→ +${hits["big-diff"]}`);
  assert.ok(valueOf("risk-score-high", "c-glob").endsWith(`→ +${hits.ci}`));
  assert.ok(big.length <= Number(rules.get("many-files")!.value), "many-files は当たらない例");
  assert.ok(valueOf("risk-score-high", "c-skip").includes(`${big.length} ファイル ≤ ${rules.get("many-files")!.value}`));
  assert.equal(valueOf("risk-score-high", "sum"), `${Object.values(hits).join(" + ")} = ${total}\n${total} ≥ ${SAMPLE_LEVELS.high} かつ ${total} < ${SAMPLE_LEVELS.critical} → HIGH`);
  assert.ok(valueOf("risk-score-high", "write").includes(`points: ${total}, level: HIGH`));
  assert.ok(valueOf("risk-score-high", "judge").startsWith("no-tests:"));
  assert.equal(valueOf("risk-score-high", "load"), levelsText(SAMPLE_LEVELS));
  assert.equal(valueOf("risk-high-review", "record"), `phases/親/子.risk.json  points: ${total}, level: HIGH`);
  assert.ok(valueOf("risk-high-review", "request").includes(`${total} (HIGH)`));

  // 小さな変更: どの項目にも当たらず 0 点で LOW
  const small: Change[] = [{ path: "src/auth/login.py", added: 9, deleted: 3 }];
  assert.deepEqual(score(rules, small, { "no-tests": 0 }), {});
  assert.equal(levelOf(0, SAMPLE_LEVELS), "LOW");
  assert.ok(valueOf("risk-score-low", "diff").includes("+9 -3"));
  assert.ok(valueOf("risk-score-low", "c-all").includes("12 行 ≤ 300"));
  assert.ok(valueOf("risk-score-low", "sum").includes("0 < 20 → LOW"));
  assert.equal(valueOf("risk-score-low", "load"), levelsText(SAMPLE_LEVELS));

  // 境目は「以上」
  for (const [id, points] of [["p19", 19], ["p20", 20], ["p39", 39], ["p40", 40], ["p70", 70]] as const) {
    assert.ok(textOf("risk-level", id).includes(`${points} 点`), id);
    assert.ok(valueOf("risk-level", id).includes(`→ ${levelOf(points, SAMPLE_LEVELS)}`), `${points} 点は ${levelOf(points, SAMPLE_LEVELS)}`);
  }
  assert.equal(valueOf("risk-level", "read"), levelsText(SAMPLE_LEVELS));

  // HIGH を 50 に上げると、同じ 40 点が HIGH から MEDIUM に変わる
  const raised = { ...SAMPLE_LEVELS, high: 50 };
  assert.equal(levelOf(40, SAMPLE_LEVELS), "HIGH");
  assert.equal(levelOf(40, raised), "MEDIUM");
  assert.equal(valueOf("risk-level-edit", "before"), levelsText(SAMPLE_LEVELS));
  assert.equal(valueOf("risk-level-edit", "after"), levelsText(raised));
  assert.ok(valueOf("risk-level-edit", "before-sum").endsWith("→ HIGH"));
  assert.ok(valueOf("risk-level-edit", "after-sum").includes("→ MEDIUM"));
  assert.equal(valueOf("risk-save", "edit"), levelsText(raised));
});

test("CB-T1181 共通の設定に lib の設定を足した図は、統合結果の見本（境目は小さいほう、項目は連結）の数え方と一致する", () => {
  const lib = (sample.page.sums ?? []).find((entry) => entry.name === "lib")!;
  const levels = { medium: lib.levels.medium!, high: lib.levels.high!, critical: lib.levels.critical! };
  const common = commonRules();
  const added = addedRules("lib");
  const merged = new Map([...common, ...added]);
  assert.equal(merged.size, lib.factors.length);

  const changes: Change[] = [{ path: "src/api/v1.py", added: 100, deleted: 20 }];
  const hits = score(merged, changes, { "no-tests": 0 });
  assert.deepEqual(hits, { "lib:big-diff": 15, "public-api": 20 });
  const total = Object.values(hits).reduce((sum, points) => sum + points, 0);
  assert.equal(levelOf(total, levels), "HIGH");
  assert.ok(total < Number(form.levels.high), "共通の HIGH なら届かない合計の例（小さいほうの境目が効く）");

  assert.equal(valueOf("risk-sum-lib", "common"), `${levelsText(SAMPLE_LEVELS)}\n項目: ${[...common.keys()].join(", ")}`);
  assert.equal(valueOf("risk-sum-lib", "layer"), `${levelsText(levels)}\n項目: ${[...added.keys()].join(", ")}`);
  assert.equal(valueOf("risk-sum-lib", "merge"), `medium ${levels.medium} / high ${levels.high} / critical ${levels.critical}\n項目 ${common.size} + ${added.size} = ${merged.size} 件`);
  assert.equal(levels.high, Math.min(SAMPLE_LEVELS.high, levels.high));
  assert.equal(valueOf("risk-sum-lib", "diff"), "src/api/v1.py  +100 -20");
  assert.equal(
    valueOf("risk-sum-lib", "score"),
    [
      `big-diff（共通）: 120 行 ≤ ${common.get("big-diff")!.value} → 0`,
      `lib:big-diff: 120 行 > ${added.get("lib:big-diff")!.value} → +${hits["lib:big-diff"]}`,
      `public-api: src/api/v1.py の 1 ファイル → +${hits["public-api"]}`,
      "no-tests: no → 0",
    ].join("\n"),
  );
  assert.equal(valueOf("risk-sum-lib", "level"), `${hits["lib:big-diff"]} + ${hits["public-api"]} = ${total}\n${total} ≥ ${levels.high} → HIGH`);
});

/** 図の中の `levels: {…}` は、見本の配点・統合結果の境目、または案内が「直した例」として示す HIGH 50 だけ */
test("CB-T1182 図に出る levels の値は、見本の配点・統合結果の境目か、直した例（HIGH 50）だけ。直した例は保存の図に限る", () => {
  const allowed = new Set([levelsText(SAMPLE_LEVELS)]);
  for (const sum of sample.page.sums ?? []) {
    allowed.add(levelsText({ medium: sum.levels.medium!, high: sum.levels.high!, critical: sum.levels.critical! }));
  }
  const edited = levelsText({ ...SAMPLE_LEVELS, high: 50 });
  const editedOnly = new Set(["risk-level-edit", "risk-save"]);
  for (const [name, diagram] of Object.entries(def.diagrams) as Array<[string, DiagramDef]>) {
    for (const step of diagram.steps) {
      for (const match of step.value.match(/levels: \{[^}]*\}/g) ?? []) {
        if (match === edited) {
          assert.ok(editedOnly.has(name), `${name}/${step.id}: 直した例の境目が保存の図の外にある`);
        } else {
          assert.ok(allowed.has(match), `${name}/${step.id}: ${match} は見本の境目にない`);
        }
      }
    }
  }
});

test("CB-T1183 リスクの新しい 2 本の案内は、基本の案内の次に並び、題は「〜たいとき」で 4〜9 手順。図は 2〜6 人で、手順ごとに図を引く", () => {
  assert.deepEqual(
    def.courses.slice(0, 4).map((course) => course.id),
    [INTRO_ID, "first-look", "how-score", "how-save-effect"],
  );
  const added = def.courses.slice(2, 4);
  assert.deepEqual(added.map((course) => course.title), [
    "点数とレベルが、どの値で決まるのかを例で知りたいとき",
    "保存してから効くまでを知りたいとき",
  ]);
  for (const course of added) {
    assert.match(course.title, /たいとき$/);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 手順`);
    for (const step of course.steps) {
      assert.ok(step.diagram === "" || step.diagram in def.diagrams);
    }
    assert.ok(course.steps.filter((step) => step.diagram !== "").length >= 3, `${course.id}: 図を引く手順が少ない`);
  }
  for (const [name, diagram] of Object.entries(def.diagrams) as Array<[string, DiagramDef]>) {
    assert.ok(diagram.actors.length >= 2 && diagram.actors.length <= 6, `${name}: 登場者が ${diagram.actors.length} 人`);
    assert.ok(diagram.steps.some((step) => step.value !== ""), `${name}: 値の例がない`);
  }
  // 既存の案内の ID・題は変えない
  assert.deepEqual(
    def.courses.slice(4).map((course) => course.id),
    ["levels", "add-factor", "kinds", "organize", "sum", "create-file"],
  );
});
