/**
 * ルール管理の案内の見本（`tours/rules.yml` の `sample:`）の検査。見本のルールは rules.yml と同じ書き方で、
 * 画面は `tour-rules.ts` の小さな読み取りで `RuleForm` にする。実際の読み取り（`readRules`）と同じ結果になることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parse, stringify } from "yaml";

import { BOARD_VERSION, parseBoardJson } from "../../src/core/model.js";
import { readRules } from "../../src/core/rules-doc.js";
import { condSummary, condProblems, toWhenBlocker, treeOfPattern } from "../../src/core/rules-when.js";
import { rulesSums } from "../../src/core/sums.js";
import { parseRulesSample } from "../../src/core/tour-rules.js";
import { loadTour } from "../helpers/tour.js";

const sample = parseRulesSample(loadTour("rules").sample);

test("CB-T404 ルールの見本は、実際の読み取り（readRules）と同じ RuleForm になる", () => {
  const raw = (loadTour("rules").sample as { rules: unknown }).rules;
  const real = readRules(stringify({ version: 1, ...(raw as object) })).model;
  assert.deepEqual(real.problems, []);
  assert.deepEqual(sample.sections, real.sections);
});

test("CB-T405 ルールの見本には、組み合わせ条件を持つ行があり、図で見せられる（誤りが無く、深さを持つ）", () => {
  const withWhen = [...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].filter((rule) => rule.when !== undefined);
  assert.ok(withWhen.length >= 1);
  for (const rule of withWhen) {
    assert.deepEqual(condProblems(rule.when!), [], rule.id);
  }
  const env = withWhen.find((rule) => rule.id === "protect-env")!;
  assert.equal(condSummary(env.when!), "all(regex (^|[\\\\/])\\.env(\\.|$), not(any(2)))");
});

test("CB-T406 判定・期待する判定・候補の見本は、実行ファイルの JSON の読み取りを通る", () => {
  assert.ok(Object.keys(sample.judges).length >= 2);
  assert.equal(sample.judges["env-read"].verdict, "deny");
  assert.equal(sample.judges["env-example"].verdict, "");
  assert.ok(sample.checked !== undefined && sample.checked.expectations.length >= 1);
  assert.ok(sample.suggested !== undefined && sample.suggested.candidates.length >= 1);
});

test("CB-T407 ルールの案内の scene は、見本にあるタブ・行の id・判定の名前だけを指す（綴り違いが既定へ黙って戻らない）", () => {
  const ids = new Set([...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].map((rule) => rule.id));
  for (const course of loadTour("rules").courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順`;
      const scene = step.scene;
      assert.ok(scene.tab === undefined || ["rules", "judge", "hooks"].includes(String(scene.tab)), `${at}: tab`);
      for (const id of String(scene.open ?? "").split(",").filter((item) => item.trim() !== "")) {
        assert.ok(ids.has(id.trim()), `${at}: open の ${id} が見本にありません`);
      }
      assert.ok(scene.judge === undefined || String(scene.judge) in sample.judges, `${at}: judge の ${String(scene.judge)} が見本にありません`);
      for (const section of String(scene.fold ?? "").split(",").filter((item) => item.trim() !== "")) {
        assert.ok(["deny", "ask", "allow"].includes(section.trim()), `${at}: fold の ${section} がタイプではない`);
      }
      assert.ok(scene.sum === undefined || sample.sums.some((sum) => sum.name === scene.sum), `${at}: sum の ${String(scene.sum)} が見本にありません`);
      assert.ok(scene.error === undefined || (scene.error === true && sample.saveError !== ""), `${at}: error は true で、見本に save_error が必要です`);
      for (const key of ["checked", "suggested", "more"]) {
        assert.ok(scene[key] === undefined || scene[key] === true, `${at}: ${key} は true だけ`);
      }
      assert.ok(scene.when === undefined || /^r(\.\d+)*$/.test(String(scene.when)), `${at}: when`);
      const known = ["tab", "open", "when", "more", "find", "tool", "subject", "judge", "checked", "suggested", "fold", "sum", "error"];
      for (const key of Object.keys(scene)) {
        assert.ok(known.includes(key), `${at}: scene のキー ${key} を画面は読まない`);
      }
    }
  }
});

const SECTION_NAMES = ["deny", "ask", "allow"];
const FIELDS = ["match", "type", "pattern", "except", "message", "context", "every", "text", "once", "file", "once-file", "to-when", "buttons"];

/** 案内が指せる印（`data-tour`）。画面に付けた印と、この表が同じであること（冒頭のコメントにも同じ表がある） */
function knownTarget(target: string, ids: ReadonlySet<string>): boolean {
  if (["", "tabs", "rules", "find", "order-hint", "hooks", "save", "tour", "target", "status", "when-graph", "when-inspector"].includes(target)) {
    return true;
  }
  if (["judge-form", "judge-result", "expectations", "expectations-counts", "expectations-result", "expectation-ng", "open-expectations", "suggest", "suggest-result", "suggest-candidate"].includes(target)) {
    return true;
  }
  if (["sum", "sum-pick", "sum-count"].includes(target)) {
    return true;
  }
  const typed = /^(section|fold|count|add|sum)-(.+)$/.exec(target);
  if (typed !== null && SECTION_NAMES.includes(typed[2])) {
    return true;
  }
  const rule = /^rule-(.+)$/.exec(target);
  if (rule === null) {
    return false;
  }
  return ids.has(rule[1]) || FIELDS.some((field) => rule[1].endsWith(`-${field}`) && ids.has(rule[1].slice(0, -field.length - 1)));
}

test("CB-T520 ルールの案内は、使い方ごとの案内が 4〜9 手順で、指す先が知っている印（見本の行 id と欄を含む）だけ", () => {
  const ids = new Set([...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].map((rule) => rule.id));
  const courses = loadTour("rules").courses;
  assert.ok(courses.length >= 9, "ユースケースが足りない");
  for (const course of courses.slice(1)) {
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 手順`);
    for (const [index, step] of course.steps.entries()) {
      assert.ok(knownTarget(step.target, ids), `${course.id} の ${index + 1} 番目の手順: 指す先 ${step.target} を画面は付けない`);
    }
  }
});

test("CB-T521 統合結果の見本は、実際のボードの JSON の読み取りと同じ RulesSum になり、共通の設定と別のレイヤーの由来を持つ", () => {
  const raw = (loadTour("rules").sample as { sums: unknown }).sums;
  const parsed = parseBoardJson(JSON.stringify({ version: BOARD_VERSION, sums: raw }));
  assert.ok(parsed.ok);
  assert.deepEqual(sample.sums, rulesSums(parsed.board));
  assert.ok(sample.sums.length >= 2);
  for (const sum of sample.sums) {
    const rules = [...sum.deny, ...sum.ask, ...sum.allow];
    assert.ok(rules.some((rule) => rule.source === "common"), sum.name);
    const own = rules.filter((rule) => rule.source !== "common");
    assert.ok(own.length >= 1, sum.name);
    // 共通以外の id にはレイヤー名が付く
    for (const rule of own) {
      assert.ok(rule.id.startsWith(`${rule.source}:`), `${sum.name}: ${rule.id}`);
    }
    assert.equal(sum.layers[0], "common");
  }
});

test("CB-T522 除外を持つ見本の行は、組み合わせ条件へそのまま移せ、移すと all(本体, not(any(除外))) になる", () => {
  const rule = sample.sections.deny.find((item) => item.id === "protect-secrets")!;
  assert.equal(rule.excepts?.length, 1);
  assert.equal(toWhenBlocker(rule), undefined);
  assert.equal(condSummary(treeOfPattern(rule.kind, rule.pattern, rule.exceptNodes ?? [])), "all(glob **/secrets/**, not(any(1)))");
});

test("CB-T523 一括確認の見本は、行の ok と件数・不一致の数が合い、食い違う行が 1 件ある", () => {
  const checked = sample.checked!;
  for (const section of SECTION_NAMES) {
    const rows = checked.expectations.filter((row) => row.expected === section);
    assert.deepEqual(checked.counts[section], { ok: rows.filter((row) => row.ok).length, total: rows.length }, section);
  }
  assert.equal(checked.mismatches, checked.expectations.filter((row) => !row.ok).length);
  assert.equal(checked.mismatches, 1);
});

test("CB-T524 判定の見本は、ヒットしたルールが見本の行（か候補の下書き）にあり、タイプが判定と同じ。一括確認の行も同じ", () => {
  // 候補の下書きをコピーしたあとの判定（suggest-rule の案内）もあるので、候補の id も数える
  const byId = new Map<string, string>([...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].map((rule) => [rule.id, rule.origin!.section]));
  for (const candidate of sample.suggested!.candidates) {
    byId.set(candidate.id, candidate.section);
  }
  const hits = [...Object.entries(sample.judges).map(([name, judge]) => ({ at: name, verdict: judge.verdict, rules: judge.rules })), ...sample.checked!.expectations.map((row) => ({ at: row.subject, verdict: row.verdict, rules: row.rules }))];
  for (const item of hits) {
    for (const rule of item.rules) {
      assert.equal(byId.get(rule.id), rule.section, `${item.at}: ${rule.id}`);
    }
    if (item.verdict !== "") {
      assert.equal(item.rules[0]?.section, item.verdict, item.at);
    } else {
      assert.equal(item.rules.length, 0, item.at);
    }
  }
});

test("CB-T525 候補の見本は、下書きの YAML が候補の id・タイプ・ツールと同じルールを持ち、期待する例の組を持つ", () => {
  const candidates = sample.suggested!.candidates;
  assert.ok(candidates.length >= 2);
  assert.ok(candidates.some((c) => c.kind === "rule") && candidates.some((c) => c.kind === "message"));
  for (const candidate of candidates) {
    const doc = parse(candidate.yaml) as { rules: Record<string, { id: string; match: string }[]>; expectations: Record<string, { tool: string; subject: string }[]> };
    assert.equal(doc.rules[candidate.section][0].id, candidate.id);
    assert.equal(doc.rules[candidate.section][0].match, candidate.tool);
    assert.deepEqual(doc.expectations[candidate.section].map((e) => ({ tool: e.tool, subject: e.subject })), candidate.expectations.map((e) => ({ tool: e.tool, subject: e.subject })));
  }
});

test("CB-T526 保存前の検証エラーの見本は、lint の報告の形（設定の検証（--lint）でエラーが見つかりました…、続けて「error: id: 内容」）で、見本の行の id を名指しする", () => {
  const lines = sample.saveError.split("\n");
  assert.match(lines[0], /^設定の検証（--lint）でエラーが見つかりました。内容を修正してから保存してください:$/);
  const ids = new Set([...sample.sections.deny, ...sample.sections.ask, ...sample.sections.allow].map((rule) => rule.id));
  assert.ok(lines.length >= 2);
  for (const line of lines.slice(1)) {
    const m = /^error: ([^:]+): /.exec(line);
    assert.ok(m !== null && ids.has(m[1]), line);
  }
});
