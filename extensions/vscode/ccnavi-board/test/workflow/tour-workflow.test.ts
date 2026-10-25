/**
 * 計画編集画面の案内の見本（`tours/workflow.yml` の `sample:`）と、手順の `scene`・指す先の検査。
 *
 * 見本は実行ファイルの答え（`--plan-order <親> --json`）の形で書き、`parseWorkflowSample` が欠けた欄を埋めて
 * `parsePlanOrder` に通す。実行ファイルの答えの実例（`test/fixtures/plan-order.json`）と形がずれないこと、実行ファイルが
 * 守る関係（終端に届かないステップ・すぐ始まるステップ・番号の振り直し・子の付け替え・書けるか）が見本でも成り立つことを、ここで見る。
 * 見本の数は多いので、関係の検査は実例の答え 6 つにも同じものを適用して、検査自体が実行ファイルの決まりと合っているのを保つ。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";

import type { ApprovePlan } from "../../src/core/approvemodel.js";
import { parsePlanOrder, type PlanOrderAnswer } from "../../src/core/planordermodel.js";
import { completeAnswer, parseWorkflowSample, SAMPLE_SHA, type WorkflowSample } from "../../src/core/tour-workflow.js";
import { CHECKING_TEXT, isDirty, refusedText, renumbered, saveBlocker } from "../../src/core/workflow-view.js";
import { PLAN_ORDER_FIXTURE, planOrderSample, type PlanOrderSample } from "../helpers/plan-order.js";
import { loadTour } from "../helpers/tour.js";

type Json = Record<string, unknown>;

const tour = loadTour("workflow");
const sample: WorkflowSample = parseWorkflowSample(tour.sample);
const FIXTURE = JSON.parse(fs.readFileSync(PLAN_ORDER_FIXTURE, "utf8")) as Record<string, Json>;
const FIXTURE_NAMES = Object.keys(FIXTURE) as PlanOrderSample[];

/** 見本の答えを、`parsePlanOrder` に渡す手前の JSON にしたもの（欠けた欄を埋めた形） */
function completed(): Record<string, Json> {
  const known: Record<string, Json> = {};
  const raw = (tour.sample as { answers: Record<string, unknown>; ticket: string });
  for (const [name, value] of Object.entries(raw.answers)) {
    known[name] = completeAnswer(name, value, raw.ticket, known).json;
  }
  return known;
}

const keysOf = (value: unknown): string[] => Object.keys(value as Json).sort();

/** ステップに推移的に先行する番号 */
function waitsOf(plan: ApprovePlan): Map<number, Set<number>> {
  const direct = new Map(plan.items.map((item) => [item.number, plan.after[String(item.number)] ?? []]));
  const out = new Map<number, Set<number>>();
  const visit = (n: number): Set<number> => {
    const known = out.get(n);
    if (known !== undefined) {
      return known;
    }
    const all = new Set<number>();
    out.set(n, all);
    for (const m of direct.get(n) ?? []) {
      all.add(m);
      visit(m).forEach((k) => all.add(k));
    }
    return all;
  };
  plan.items.forEach((item) => visit(item.number));
  return out;
}

/** 実行ファイルの `workflow.loose` / `workflow.ready` と同じ決まり */
function expectedLoose(plan: ApprovePlan): number[] {
  const numbers = plan.items.map((item) => item.number);
  if (numbers.length < 2) {
    return [];
  }
  const last = Math.max(...numbers);
  const waited = waitsOf(plan).get(last) ?? new Set<number>();
  return numbers.filter((n) => n !== last && !waited.has(n));
}

function expectedReady(plan: ApprovePlan): number[] {
  const numbers = plan.items.map((item) => item.number);
  return numbers.filter((n) => (plan.after[String(n)] ?? []).length === 0);
}

/** 実行ファイルの番号の振り直し（`plan_order.renumber`）。元の番号 → 振り直した番号。固定した番号は動かさない */
function renumberOf(plan: ApprovePlan): Map<number, number> {
  const numbers = plan.items.map((item) => item.number).sort((a, b) => a - b);
  const fromOf = new Map(plan.items.map((item) => [item.number, item.from]));
  const preds = new Map(plan.items.map((item) => [item.from, (plan.after[String(item.number)] ?? []).map((m) => fromOf.get(m) as number)]));
  const fixed = new Set(plan.items.filter((item) => item.locked).map((item) => item.number));
  const placed = new Map<number, number>();
  for (const pos of numbers) {
    let pick: number | undefined;
    if (fixed.has(pos)) {
      pick = (preds.get(pos) ?? []).every((m) => placed.has(m)) ? pos : undefined;
    } else {
      pick = numbers.find((m) => !placed.has(m) && !fixed.has(m) && (preds.get(m) ?? []).every((p) => placed.has(p)));
    }
    assert.ok(pick !== undefined, `${pos} 番に置けるステップがない（振り直せない形）`);
    placed.set(pick, pos);
  }
  return placed;
}

function allAnswers(): Array<[string, PlanOrderAnswer]> {
  return [
    ...Object.entries(sample.answers).map(([name, value]) => [`見本 ${name}`, value.answer] as [string, PlanOrderAnswer]),
    ...FIXTURE_NAMES.map((name) => [`実例 ${name}`, planOrderSample(name)] as [string, PlanOrderAnswer]),
  ];
}

test("CB-T500 見本の答えは、実行ファイルの答えの実例（plan-order.json）と、欄のキーと型が同じ形で parsePlanOrder を通る", () => {
  const real = FIXTURE.open;
  const realPlan = (real.plans as Json[])[0];
  const realItem = (realPlan.items as Json[])[0];
  const typeOf = (value: unknown): string => (value === null ? "null" : Array.isArray(value) ? "array" : typeof value);
  for (const [name, json] of Object.entries(completed())) {
    assert.deepEqual(keysOf(json), keysOf(real), `${name}: 答えのキーが実例とずれた`);
    for (const key of Object.keys(real)) {
      // 実例の current は null（改版でない）。改版の見本だけ、承認済みの先行を持つ
      if (key !== "plans") {
        assert.equal(typeOf(json[key]), typeOf(real[key]), `${name}.${key}`);
      }
    }
    for (const plan of json.plans as Json[]) {
      assert.deepEqual(keysOf(plan), keysOf(realPlan), `${name}: 計画のキーが実例とずれた`);
      for (const item of plan.items as Json[]) {
        assert.deepEqual(keysOf(item), keysOf(realItem), `${name}: ステップのキーが実例とずれた`);
        for (const key of Object.keys(realItem)) {
          assert.equal(typeOf(item[key]), typeOf(realItem[key]), `${name}: ステップの ${key}`);
        }
      }
    }
    const parsed = parsePlanOrder(JSON.stringify(json));
    assert.ok(parsed.ok, `${name}: parsePlanOrder を通らない`);
    assert.equal(parsed.ok && parsed.value.source_sha, SAMPLE_SHA);
  }
  assert.ok(Object.keys(sample.answers).length >= 10, "見本の答えが少ない");
});

test("CB-T501 見本の open は、実例の open と同じ線・番号・見る場所の並びで、識別子・ハッシュ・タイトル・見る場所の言葉だけが見本のもの", () => {
  const real = planOrderSample("open");
  const mask = (plans: readonly ApprovePlan[]): unknown =>
    plans.map((plan) => ({ ...plan, ticket: "", source_sha: "", items: plan.items.map((item) => ({ ...item, title: "", review: "" })) }));
  assert.equal(sample.ticket, "sample-a");
  assert.deepEqual(mask(sample.answers.open.answer.plans), mask(real.plans), "見本が実例とずれた。plan-order.json を書き出し直したら workflow.yml の見本もコピーし直す");
  const { plans: _a, ticket: _b, source_sha: _c, ...restSample } = sample.answers.open.answer;
  const { plans: _d, ticket: _e, source_sha: _f, ...restReal } = real;
  assert.deepEqual(restSample, restReal);
  // 見る場所は、実例が持つ言葉（none / mr）だけ。タイトルは実行ファイルが定義のタイトルを入れるので、見本は実際の定義のタイトルを使う
  for (const plan of sample.answers.open.answer.plans) {
    for (const item of plan.items) {
      assert.ok(["none", "mr"].includes(item.review), `${item.number} の見る場所 ${item.review}`);
      assert.notEqual(item.title, "");
    }
  }
});

test("CB-T502 終端に届かないステップ・すぐ始まるステップ・書けるか・答え全体の寄せ集めは、実行ファイルの決まりどおり（実例の答えにも同じ検査を適用する）", () => {
  for (const [name, answer] of allAnswers()) {
    for (const plan of answer.plans) {
      assert.deepEqual([...plan.loose].sort(), expectedLoose(plan), `${name} ${plan.part}: loose`);
      assert.deepEqual([...plan.ready].sort(), expectedReady(plan), `${name} ${plan.part}: ready`);
    }
    // 断った線のある答え・error のある答えは、書けない
    const blocked = answer.refused.length > 0 || answer.problems.length > 0 || answer.revision_problems.length > 0 || answer.child_problems.length > 0;
    assert.equal(answer.writable, !blocked, `${name}: writable`);
    assert.deepEqual(answer.loose, answer.plans.flatMap((plan) => plan.loose), `${name}: 答え全体の loose`);
    assert.deepEqual(answer.problems, answer.plans.flatMap((plan) => plan.problems), `${name}: 答え全体の problems`);
    // 終端の error は、終端に届かないステップがあるときだけ
    for (const plan of answer.plans) {
      assert.equal(plan.problems.length > 0, plan.loose.length > 0, `${name} ${plan.part}: 終端の error と loose が合わない`);
    }
  }
});

test("CB-T503 見本の番号の振り直しは、実行ファイルの決まり（先行が並んだステップのうち元の番号が小さい順。固定した番号は動かさない）で再現できる", () => {
  for (const [name, answer] of allAnswers()) {
    for (const plan of answer.plans) {
      const placed = renumberOf(plan);
      for (const item of plan.items) {
        assert.equal(placed.get(item.from), item.number, `${name} ${plan.part}: 元の ${item.from} 番は ${item.number} 番になるはず`);
      }
    }
  }
});

test("CB-T504 子の提案の付け替えは、番号の変わるステップの phase と predecessors に合う（実行ファイルの retarget と同じ決まり）", () => {
  const pad = (n: number): string => String(n).padStart(2, "0");
  let checked = 0;
  for (const [name, answer] of allAnswers()) {
    const moved = new Map(answer.plans.flatMap((plan) => plan.items.map((item) => [item.from, item.number] as const)));
    const rename = new Map<string, string>();
    for (const child of answer.children) {
      if (child.phase !== null && child.new_phase !== child.phase) {
        assert.equal(child.new_phase, moved.get(child.phase), `${name}: ${child.ticket} の新しい phase`);
        assert.equal(child.new_ticket, child.ticket.replace(`-${pad(child.phase)}-`, `-${pad(child.new_phase as number)}-`), `${name}: ${child.ticket} の新しい識別子`);
        rename.set(child.ticket, child.new_ticket);
        checked += 1;
      }
    }
    for (const child of answer.children) {
      assert.deepEqual(child.new_predecessors, child.predecessors.map((p) => rename.get(p) ?? p), `${name}: ${child.ticket} の predecessors`);
    }
    // 番号の変わるステップを指す子が、1 本も付け替わらないことは無い（見本の答えで、付け替えの一覧を持つものの間で）
    if (answer.children.length > 0) {
      for (const [from, number] of moved) {
        if (from !== number) {
          assert.ok(answer.children.some((child) => child.phase === from), `${name}: 元の ${from} 番を指す子が付け替わらない`);
        }
      }
    }
  }
  assert.ok(checked >= 2, "付け替わる子の見本がありません");
});

test("CB-T505 見本の答えは、引く前の答え（base）と提案に書いてある先行が同じで、未保存かどうかが手順の筋と合う", () => {
  const dirty: Record<string, boolean> = {
    open: false,
    chain: false,
    "chain-join": true,
    "chain-add": true,
    "chain-cut": true,
    "chain-loose": true,
    "after-23": true,
    "after-23-cut": true,
    swap: true,
    "swapped-saved": false,
    "fb-swap": true,
    loose: true,
    "locked-open": false,
    "locked-cut": true,
    unpackable: false,
    "child-problems": true,
    outside: false,
  };
  assert.deepEqual(Object.keys(sample.answers).sort(), Object.keys(dirty).sort(), "見本の答えと、この表がずれた");
  for (const [name, value] of Object.entries(sample.answers)) {
    assert.equal(isDirty(value.answer), dirty[name], `${name}: 未保存`);
    const base = sample.answers[value.base].answer;
    assert.equal(sample.answers[value.base].base, value.base, `${name}: base の base は自分自身`);
    for (const plan of value.answer.plans) {
      const before = base.plans.find((p) => p.part === plan.part);
      assert.ok(before !== undefined, `${name}: base に ${plan.part} がありません`);
      assert.deepEqual(plan.proposed, before.proposed, `${name} ${plan.part}: 提案に書いてある先行が base と違う`);
      assert.deepEqual(plan.items.map((item) => item.from).sort(), before.items.map((item) => item.from).sort(), `${name} ${plan.part}: ステップの集まりが base と違う`);
    }
  }
  // 保存して読み直したあとは、未保存でなく番号が入れ替わったまま
  assert.deepEqual(renumbered(sample.answers["swapped-saved"].answer), []);
  assert.equal(renumbered(sample.answers.swap.answer).length, 2);
});

/** 画面が見本に出す `data-tour` の印の一覧（画面の描き方のコピー。DOM のテストが実際に付いていることを見る） */
function targetsOf(scene: Record<string, string | number | boolean>): Set<string> {
  const name = (value: unknown, fallback: string): string => (typeof value === "string" && value in sample.answers ? value : fallback);
  const answer = sample.answers[name(scene.answer, "open")].answer;
  const out = new Set(["graphs", "controls", "relayout", "revert", "reload", "save", "summary", "dirty", "tour", "status"]);
  if (renumbered(answer).length > 0) {
    out.add("renumbered");
  }
  if (answer.revision_problems.length + answer.child_problems.length + answer.mismatched.length > 0) {
    out.add("problems");
  }
  if (typeof scene.changed === "string" && scene.gone !== true) {
    out.add("changed");
    out.add("adopt");
    if (scene.diff !== false) {
      out.add("changed-diff");
    }
  }
  if (scene.gone === true) {
    out.add("gone");
  }
  if (scene.review === true) {
    ["review-numbers", "review-children", "review-note"].forEach((t) => out.add(t));
  }
  const editable = scene.checking !== true && scene.gone !== true && scene.review !== true;
  for (const plan of answer.plans) {
    out.add(`graph-${plan.part}`);
    out.add(`detail-${plan.part}`);
    out.add(`zoom-${plan.part}`);
    if (plan.ready.length > 0 || plan.loose.length > 0 || plan.problems.length > 0) {
      out.add(`notes-${plan.part}`);
    }
    const locked = new Set(plan.items.filter((item) => item.locked).map((item) => item.number));
    for (const item of plan.items) {
      out.add(`node-${item.number}`);
      for (const m of plan.after[String(item.number)] ?? []) {
        out.add(`edge-${m}-${item.number}`);
        if (editable && !locked.has(item.number)) {
          out.add(`cut-${m}-${item.number}`);
        }
      }
    }
  }
  return out;
}

test("CB-T506 案内の scene は、画面が読むキーと、見本にある答えの名前・ステップの番号だけを指す。綴り違いで既定の画面へ戻ってしまわない。指す先は、その手順の見本に出る", () => {
  const known = ["answer", "refused", "checking", "changed", "diff", "gone", "review", "pick", "tidy"];
  let steps = 0;
  for (const course of tour.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
      const scene = step.scene;
      for (const key of Object.keys(scene)) {
        assert.ok(known.includes(key), `${at}: scene のキー ${key} を画面は読まない`);
      }
      for (const key of ["answer", "refused", "changed"] as const) {
        assert.ok(scene[key] === undefined || (typeof scene[key] === "string" && String(scene[key]) in sample.answers), `${at}: ${key} の ${String(scene[key])} が見本にありません`);
      }
      for (const key of ["checking", "gone", "review", "tidy", "diff"] as const) {
        assert.ok(scene[key] === undefined || typeof scene[key] === "boolean", `${at}: ${key} は真偽`);
      }
      if (scene.refused !== undefined) {
        assert.ok(sample.answers[String(scene.refused)].answer.refused.length > 0, `${at}: refused の答えに断った線がありません`);
      }
      if (scene.pick !== undefined) {
        const shown = sample.answers[String(scene.answer ?? "open")].answer;
        assert.ok(typeof scene.pick === "number" && shown.plans.some((plan) => plan.items.some((item) => item.number === scene.pick)), `${at}: pick のステップが答えにありません`);
      }
      // 指す先は、その手順の見本に出る印だけ（出ないものを指すと、吹き出しが中ほどに出る）
      assert.ok(step.target !== "", `${at}: 指す先がありません`);
      assert.ok(targetsOf(scene).has(step.target), `${at}: 指す先 ${step.target} はこの手順の見本に出ない`);
      steps += 1;
    }
  }
  assert.ok(steps >= 40, `手順が少ない（${steps}）`);
});

test("CB-T507 案内の構成。基本の案内は今のタイトルと順序のまま。ユースケースは 5〜8 本で 1 本 4〜10 手順、キーとタイトルが重ならない", () => {
  assert.deepEqual(
    tour.courses[0].steps.map((step) => step.title),
    ["図", "直す手段", "この順序に決める", "承認との関係", "案内"],
  );
  assert.equal(tour.courses[0].id, "intro");
  const cases = tour.courses.slice(1);
  assert.ok(cases.length >= 5 && cases.length <= 8, `ユースケースの数 ${cases.length}`);
  assert.equal(new Set(tour.courses.map((c) => c.title)).size, tour.courses.length, "タイトルが重なっている");
  for (const course of cases) {
    assert.ok(course.steps.length >= 4 && course.steps.length <= 10, `${course.id}: ${course.steps.length} 手順`);
    assert.equal(new Set(course.steps.map((s) => s.title)).size, course.steps.length, `${course.id}: 手順のタイトルが重なっている`);
  }
  // 基本の案内の最初の 4 手順は、画面の見どころ（図・ボタンの並び・保存・承認）を指す
  assert.deepEqual(
    tour.courses[0].steps.map((step) => step.target),
    ["graphs", "controls", "save", "summary", "tour"],
  );
});

test("CB-T508 見本の書き間違いは読み込みで落ちる（知らないキー・先に書いていない base・計画がない答え・開いたときの答えがありません）", () => {
  const plan = { items: [{ number: 1, type: "research", title: "調査", review: "none" }], after: {}, ready: [1] };
  const ok = { ticket: "t", answers: { open: { plan } } };
  assert.ok(parseWorkflowSample(ok).answers.open.answer.writable);
  const bad: Array<[string, unknown, RegExp]> = [
    ["オブジェクトでない", [], /オブジェクトではない/],
    ["ticket がありません", { answers: { open: { plan } } }, /ticket/],
    ["知らないキー（根）", { ...ok, anwsers: {} }, /未知のキー anwsers/],
    ["知らないキー（答え）", { ticket: "t", answers: { open: { plan, writeable: true } } }, /未知のキー writeable/],
    ["知らないキー（計画）", { ticket: "t", answers: { open: { plan: { ...plan, redy: [1] } } } }, /未知のキー redy/],
    ["知らないキー（ステップ）", { ticket: "t", answers: { open: { plan: { ...plan, items: [{ number: 1, type: "a", titel: "x" }] } } } }, /未知のキー titel/],
    ["base が先にありません", { ticket: "t", answers: { open: { plan }, next: { base: "later", plan }, later: { plan } } }, /base「later」/],
    ["計画がありません", { ticket: "t", answers: { open: {} } }, /計画/],
    ["open がありません", { ticket: "t", answers: { other: { plan } } }, /open/],
    ["実行ファイルの形でない（ステップに番号がありません）", { ticket: "t", answers: { open: { plan: { ...plan, items: [{ type: "a" }] } } } }, /\.number は/],
  ];
  for (const [name, raw, pattern] of bad) {
    assert.throws(() => parseWorkflowSample(raw), pattern, name);
  }
  // base の計画を引き継ぐ。書かなかった計画はそのまま、書いた計画の proposed は base の値
  const two = { items: [{ number: 1, type: "research", title: "調査" }, { number: 2, type: "design", title: "設計" }], ready: [1] };
  const feedback = { items: [{ number: 3, type: "design-feedback", title: "設計フィードバック対応" }], ready: [3] };
  const inherited = parseWorkflowSample({
    ticket: "t",
    answers: { open: { plan: { ...two, after: {} }, feedback }, edited: { base: "open", plan: { ...two, after: { 2: [1] } } } },
  });
  const edited = inherited.answers.edited;
  assert.equal(edited.base, "open");
  assert.deepEqual(edited.answer.plans.map((p) => p.part), ["plan", "feedback"]);
  assert.deepEqual(edited.answer.plans[0].proposed, {});
  assert.deepEqual(edited.answer.plans[0].after, { "2": [1] });
  assert.deepEqual(edited.answer.plans[1], inherited.answers.open.answer.plans[1]);
});

test("CB-T509 保存できない理由の言葉は、案内の見本でも実際と同じ関数から出る。断られた線と確認中の一言", () => {
  assert.equal(CHECKING_TEXT, "線を変えた順序を ccnavi で確認しています…");
  const refused = sample.answers.unpackable.answer.refused;
  assert.equal(refusedText(refused), `この線は引けません。線は引く前のままです: ${refused[0].text}`);
  const blocked = (name: string, state = { pending: false, changed: false, gone: false }): string => saveBlocker(sample.answers[name].answer, state);
  assert.equal(blocked("open"), "順序を変えていません");
  assert.equal(blocked("swap"), "");
  assert.match(blocked("swap", { pending: true, changed: false, gone: false }), /確認しています/);
  assert.match(blocked("swap", { pending: false, changed: true, gone: false }), /外で変わった/);
  assert.match(blocked("swap", { pending: false, changed: false, gone: true }), /承認待ちにありません/);
  assert.match(blocked("loose"), /順序の検査/);
  assert.match(blocked("locked-cut"), /改版で通らない/);
  assert.match(blocked("child-problems"), /子の提案を付け替えられない/);
});
