/**
 * テンプレートの編集の手元のコピー（`src/core/template-edit.ts`）を確かめる。見本は `tests/fixtures/templates-check-json.json`
 * の `data`（実行ファイルが読み返した中身）。編集はコピーを作り直すだけなので、期待値は手で書いた中身から決める。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parse } from "yaml";

import type { FlowDoc } from "../../src/core/flow-doc.js";
import {
  addItem,
  blankTemplate,
  canMove,
  connectItems,
  disconnectItems,
  fromData,
  locate,
  moveItem,
  removeFlow,
  removeItem,
  renumber,
  serializeTemplate,
  sameData,
  untouched,
  setFlow,
  setItem,
  setMeta,
  toData,
  type EditTemplate,
} from "../../src/core/template-edit.js";
import { parseTemplateCheck, shownLayerOf, templateNameTaken, validTemplateName } from "../../src/core/templatesmodel.js";
import { templatesCheckFixtureText } from "../helpers/templates.js";

const FLOW = (id: string): FlowDoc => ({ id, name: id, nodes: [{ id: "s", type: "start", name: "開始" }], connections: [] });

/** 4 ステップの全体計画（2 に 1 が、3 に 1 が、4 に 2 と 3 が先行する）と、2 番と 4 番のフロー、フィードバック計画 2 ステップ */
function sample(): EditTemplate {
  return {
    name: "t",
    description: "説明",
    when: "",
    plan: [
      { type: "design", review: "", after: [] },
      { type: "implement", review: "mr", after: [1] },
      { type: "docs", review: "", after: [1] },
      { type: "acceptance", review: "", after: [2, 3] },
    ],
    feedback: [
      { type: "implement-feedback", review: "", after: [] },
      { type: "docs-feedback", review: "", after: [1] },
    ],
    flows: { "2": FLOW("f2"), "4": FLOW("f4") },
    extra: { note: "知らない欄" },
  };
}

function fixtureData(): unknown {
  const parsed = parseTemplateCheck(templatesCheckFixtureText());
  assert.ok(parsed.ok, parsed.ok ? "" : parsed.error);
  return parsed.value.data;
}

test("CB-T1139 実行ファイルが読み返した中身を編集のコピーにして、書き出すと同じ中身に戻る（往復）。知らない欄も残る", () => {
  const data = fixtureData();
  const read = fromData(data);
  assert.ok(read !== undefined);
  assert.equal(read.name, "feature");
  assert.equal(read.plan.length, 2);
  assert.deepEqual(read.plan[1], { type: "implement", review: "", after: [1] });
  assert.equal(read.feedback.length, 1);
  assert.deepEqual(Object.keys(read.flows), ["2"]);
  assert.ok(sameData(toData(read), data));
  const extra = fromData({ ...(data as object), note: "x" });
  assert.deepEqual(toData(extra as EditTemplate).note, "x");
});

test("CB-T1140 書き出した YAML を読み直すと書こうとした中身と同じになる。書き方（ステップの短い形・フィードバック計画とフローの省略）は実行ファイルと同じ", () => {
  const t = sample();
  const read = parse(serializeTemplate(t)) as Record<string, unknown>;
  assert.ok(sameData(toData(t), read));
  const bare = toData({ ...t, plan: [{ type: "design", review: "", after: [] }], feedback: [], flows: {}, extra: {}, when: "" });
  assert.deepEqual(bare, { name: "t", description: "説明", plan: ["design"] });
  assert.deepEqual(Object.keys(toData(t)), ["name", "description", "plan", "feedback", "flows", "note"]);
});

test("CB-T1141 中身の見比べはキーの順を問わず、値の違いは見逃さない", () => {
  assert.ok(sameData({ a: 1, b: { c: [1, 2], d: "x" } }, { b: { d: "x", c: [1, 2] }, a: 1 }));
  assert.ok(!sameData({ a: 1 }, { a: 2 }));
  assert.ok(!sameData({ a: [1, 2] }, { a: [2, 1] }));
  assert.ok(!sameData({ a: 1 }, null));
});

test("CB-T1142 ステップを消すと、後ろのステップの添字・先行・全体計画のフローのキーを振り直す。最後の 1 ステップは消せない", () => {
  const next = removeItem(sample(), "plan", 2);
  assert.deepEqual(
    next.plan.map((i) => [i.type, [...i.after]]),
    [
      ["design", []],
      ["docs", [1]],
      ["acceptance", [1, 2]],
    ],
  );
  // 途中のステップを消しても、後ろのステップは前のステップが先行のまま残る（並行して始まるステップに変わらない）
  const chain: EditTemplate = { ...sample(), plan: [{ type: "a", review: "", after: [] }, { type: "b", review: "", after: [1] }, { type: "c", review: "", after: [2] }], flows: {} };
  assert.deepEqual(removeItem(chain, "plan", 2).plan.map((i) => [i.type, [...i.after]]), [["a", []], ["c", [1]]]);
  // 2 番のフローはステップと一緒に消え、4 番のフローは 3 番になる（4 番は 2 が先行だったので、2 の先行の 1 を引き継ぐ。先行は 1 と 3）
  assert.deepEqual(Object.keys(next.flows), ["3"]);
  assert.equal((next.flows["3"] as FlowDoc).id, "f4");
  assert.equal(removeItem({ ...sample(), plan: [sample().plan[0]], flows: {} }, "plan", 1).plan.length, 1);
  // フィードバック計画は空にできる
  assert.equal(removeItem(removeItem(sample(), "feedback", 2), "feedback", 1).feedback.length, 0);
});

test("CB-T1143 ステップは先行か後続を越えない動きだけ前後に動かせる。動かすとフローのキーと先行が合わせて入れ替わる", () => {
  const t = sample();
  // 2 と 3 は互いに先行しないので入れ替えられる。1 は 2 の先行なので 2 の前へは動かせない。4 は 2 と 3 の後続
  assert.ok(canMove(t, "plan", 3, -1));
  assert.ok(!canMove(t, "plan", 2, -1));
  assert.ok(!canMove(t, "plan", 4, -1));
  assert.ok(!canMove(t, "plan", 1, -1));
  assert.ok(!canMove(t, "plan", 4, 1));
  const moved = moveItem(t, "plan", 3, -1);
  assert.deepEqual(moved.plan.map((i) => i.type), ["design", "docs", "implement", "acceptance"]);
  assert.deepEqual(moved.plan[3].after, [2, 3]);
  assert.deepEqual(moved.plan[2].after, [1]);
  // 2 番のフロー（implement）は 3 番へ付いて動く
  assert.equal((moved.flows["3"] as FlowDoc).id, "f2");
  assert.equal(moveItem(t, "plan", 2, -1), t);
});

test("CB-T1144 線を引くと、先行が自分より小さい添字になるようステップを並べ直す。引けない線（自分へ・計画をまたぐ・同じ線）は何もしない", () => {
  const t = sample();
  // 図の番号 3（docs）が 2（implement）の先行になる線。2 は 3 より前にあるので、docs を implement の前へ出す
  const reordered = connectItems(t, 3, 2);
  assert.deepEqual(reordered.plan.map((i) => i.type), ["design", "docs", "implement", "acceptance"]);
  assert.deepEqual(reordered.plan[2].after, [1, 2]);
  assert.deepEqual(reordered.plan[3].after, [2, 3]);
  // 並べ直しでフローのキーもステップに付いて動く（2 番の implement は 3 番へ）
  assert.equal((reordered.flows["3"] as FlowDoc).id, "f2");
  assert.equal((reordered.flows["4"] as FlowDoc).id, "f4");
  // 並べ直さずに済む線は、先行を足すだけ
  const plain = connectItems(t, 1, 4);
  assert.deepEqual(plain.plan[3].after, [1, 2, 3]);
  assert.equal(connectItems(t, 2, 2), t);
  assert.equal(connectItems(t, 1, 5), t);
  assert.equal(connectItems(t, 1, 2), t);
  // フィードバック計画は全体計画の続きの番号（5・6）
  assert.deepEqual(locate(t, 6), { part: "feedback", index: 2 });
  const noLine = disconnectItems(t, 5, 6);
  assert.deepEqual(noLine.feedback[1].after, []);
  assert.deepEqual(noLine.plan, t.plan);
  assert.equal(disconnectItems(t, 1, 3).plan[2].after.length, 0);
  assert.equal(disconnectItems(t, 4, 1), t);
});

test("CB-T1145 並べ直せない循環の線は引かない。先行の添字が崩れた計画は並べ直して満たす", () => {
  const t = sample();
  // 受入（4）が設計（1）の先行になると、4 には 2・3 を通って 1 が先行するので循環する
  assert.equal(connectItems(t, 4, 1), t);
  const broken: EditTemplate = { ...t, plan: [{ type: "a", review: "", after: [2] }, { type: "b", review: "", after: [] }], flows: {} };
  const fixed = renumber(broken, "plan");
  assert.deepEqual(fixed?.plan.map((i) => i.type), ["b", "a"]);
  assert.deepEqual(fixed?.plan[1].after, [1]);
  const cycle: EditTemplate = { ...t, plan: [{ type: "a", review: "", after: [2] }, { type: "b", review: "", after: [1] }], flows: {} };
  assert.equal(renumber(cycle, "plan"), undefined);
  assert.equal(renumber(t, "plan"), t);
});

test("CB-T1146 ステップの足し方・型とレビューの差し替え・説明の差し替え・フローの置き外し", () => {
  const t = sample();
  const added = addItem(t, "plan", "staging");
  assert.deepEqual(added.plan[4], { type: "staging", review: "", after: [4] });
  assert.deepEqual(addItem(blankTemplate("n", "implement"), "feedback", "x").feedback, [{ type: "x", review: "", after: [] }]);
  assert.deepEqual(setItem(t, "plan", 1, { type: "research", review: "defer" }).plan[0], { type: "research", review: "defer", after: [] });
  assert.equal(setItem(t, "plan", 9, { type: "x" }), t);
  assert.equal(setMeta(t, { description: "新" }).description, "新");
  assert.equal(setFlow(t, 9, FLOW("x")), t);
  assert.deepEqual(Object.keys(setFlow(t, 1, FLOW("f1")).flows), ["1", "2", "4"]);
  assert.deepEqual(Object.keys(removeFlow(t, 2).flows), ["4"]);
  assert.equal(removeFlow(t, 3), t);
  // 空白だけの違いは書き出しで取る（実行ファイルは読むときに前後の空白を取る）
  assert.equal(toData(setMeta(t, { description: "  説明  " })).description, "説明");
});

test("CB-T1147 実行ファイルの検査の答えを読む。図の中身・苦情・型の選択肢・読み返した中身が取れる。読めない答えは中身を持たない", () => {
  const parsed = parseTemplateCheck(templatesCheckFixtureText());
  assert.ok(parsed.ok);
  assert.equal(parsed.value.valid, true);
  assert.deepEqual(parsed.value.plans.map((p) => [p.part, p.items.length]), [["plan", 2], ["feedback", 1]]);
  assert.deepEqual(parsed.value.plans[0].after, { "2": [1] });
  assert.ok(parsed.value.types.some((t) => t.kind === "work") && parsed.value.types.some((t) => t.kind === "feedback"));
  const invalid = parseTemplateCheck(JSON.stringify({ version: 1, ok: true, layer: "self", name: "x", sha: "s", valid: false, problems: [{ severity: "error", detail: "最上位がマッピングではない" }], data: null, plans: [{ part: "plan" }], types: [] }));
  assert.ok(invalid.ok);
  assert.equal(invalid.value.data, null);
  assert.deepEqual(invalid.value.plans, []);
  assert.equal(invalid.value.problems[0].severity, "error");
  assert.deepEqual(parseTemplateCheck(JSON.stringify({ version: 1, ok: false, error: "レイヤー 'x' はありません" })), { ok: false, error: "レイヤー 'x' はありません" });
  const old = parseTemplateCheck(JSON.stringify({ version: 1, ok: true, valid: true, problems: [], plans: [], types: [] }));
  assert.ok(!old.ok && /data/.test(old.error));
  assert.ok(!parseTemplateCheck(JSON.stringify({ version: 2 })).ok);
});

test("CB-T1148 テンプレートの名前はファイル名になるので、実行ファイルと同じ形（英数字で始め、英数字と . _ - だけ、64 字まで）だけ通す。編集できる層は対象から決める", () => {
  for (const ok of ["feature", "a", "full-phases", "v1.2_x", "A".repeat(64)]) {
    assert.ok(validTemplateName(ok), ok);
  }
  for (const bad of ["", "-x", ".x", "_x", "a b", "a/b", "..", "a\\b", "日本語", "A".repeat(65), "a\n"]) {
    assert.ok(!validTemplateName(bad), JSON.stringify(bad));
  }
  assert.equal(shownLayerOf({ kind: "workspace", name: "" }), "common");
  assert.equal(shownLayerOf({ kind: "self", name: "" }), "self");
  assert.equal(shownLayerOf({ kind: "project", name: "lib" }), "lib");
});

test("CB-T1149 新しい名前は、共通の層とこの層の既存の名前と、大文字小文字を区別せず重なるかを見る。ほかの層の名前は見ない", () => {
  const layer = (name: string, names: string[]) => ({ name, path: "", exists: true, unreadable: "", writable: true, problems: [], templates: names.map((n) => ({ name: n })) }) as never;
  const layers = [layer("common", ["Review"]), layer("self", ["impl"]), layer("lib", ["libonly"])];
  assert.deepEqual(templateNameTaken(layers, "self", "review"), { layer: "common", name: "Review" });
  assert.deepEqual(templateNameTaken(layers, "self", "IMPL"), { layer: "self", name: "impl" });
  assert.equal(templateNameTaken(layers, "self", "libonly"), undefined);
  assert.equal(templateNameTaken(layers, "self", "fresh"), undefined);
});

test("CB-T1225 untouched は開いた直後の雛形と同じ内容のときだけ真になる", () => {
  const blank = blankTemplate("a", "implement");
  assert.equal(untouched(blankTemplate("a", "implement"), blank), true);
  assert.equal(untouched(setMeta(blank, { description: "x" }), blank), false);
});
