import { test } from "node:test";
import assert from "node:assert/strict";
import { asPhasesForm, readPhases } from "../../src/core/phases-doc.js";
import { SAMPLE_PHASES_TEXT } from "../helpers/phases.js";
import type { PhaseForm } from "../../src/core/phases-view.js";

/** このリポジトリの phases.yml と同じ形。コメントの置き場と flow のリストを持つ */
const SAMPLE = `# フェーズ定義。ユーザが持つ設定で、エージェントは書き換えない。
#
# id と title はどちらも一意。
version: 1

phases:
  # 分からないときだけ
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    when: 既存の振る舞いが分からないとき

  # 触る場所が多いとき
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*", "docs/*"]

  implement:
    title: 実装とテスト
    scope: ["src/*", "tests/*"]
    requires: [acceptance]
    overlap: [acceptance]

  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]

  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr
    scope: inherit
`;

function phase(id: string, over: Partial<PhaseForm> = {}): PhaseForm {
  return {
    origin: null,
    id,
    title: "",
    kind: "work",
    review: "mr",
    inherit: true,
    scope: [],
    deliverables: [],
    agent: "",
    when: "",
    ...over,
  };
}

test("CB-T86 phases.yml を定義ごとに読む。kind と review は無ければ既定、scope は inherit かリスト", () => {
  const doc = readPhases(SAMPLE);
  assert.equal(doc.model.version, 1);
  assert.deepEqual(doc.model.problems, []);
  // 順序の古い欄は読まず、読まない欄として名指しするだけ（苦情にはしない）
  assert.deepEqual(doc.model.unread, [
    { phase: "implement", key: "requires" },
    { phase: "implement", key: "overlap" },
  ]);
  const phases = doc.model.form.phases;
  assert.deepEqual(
    phases.map((p) => p.id),
    ["research", "design", "implement", "acceptance", "implement-feedback"],
  );
  const research = phases[0];
  assert.equal(research.origin, 0);
  assert.equal(research.title, "調査");
  assert.equal(research.kind, "work");
  assert.equal(research.review, "none");
  assert.equal(research.inherit, false);
  assert.deepEqual(research.scope, ["wip/research/*"]);
  assert.deepEqual(research.deliverables, ["wip/research/summary.md"]);
  assert.equal(research.when, "既存の振る舞いが分からないとき");
  // kind と review を書いていない定義は、実行ファイルの既定（work / mr）で出す
  const implement = phases[2];
  assert.equal(implement.kind, "work");
  assert.equal(implement.review, "mr");
  // 画面に渡す形に古い欄は載らない（画面は読まない欄を直せない）
  assert.ok(!("requires" in implement) && !("overlap" in implement) && !("after" in implement), JSON.stringify(implement));
  assert.ok(!("order" in doc.model.form), JSON.stringify(Object.keys(doc.model.form)));
  const feedback = phases[4];
  assert.equal(feedback.kind, "feedback");
  assert.equal(feedback.inherit, true);
  assert.deepEqual(feedback.scope, []);
});

test("CB-T87 変えていない内容で書き戻すと 1 文字も変わらない", () => {
  const doc = readPhases(SAMPLE);
  assert.equal(doc.apply(doc.model.form), SAMPLE);
  const sample = readPhases(SAMPLE_PHASES_TEXT);
  assert.deepEqual(sample.model.problems, []);
  assert.equal(sample.apply(sample.model.form), SAMPLE_PHASES_TEXT);
});

test("CB-T88 変えた欄だけが差分になり、コメントと引用符は残る", () => {
  const doc = readPhases(SAMPLE);
  const phases = doc.model.form.phases.map((p) =>
    p.id === "design" ? { ...p, review: "none" as const, scope: ["wip/design/*"], when: "設計が要るとき" } : p,
  );
  const out = doc.apply({ phases });
  const expected = SAMPLE.replace(
    '    review: mr\n    scope: ["wip/design/*", "docs/*"]\n',
    '    review: none\n    scope: ["wip/design/*"]\n    when: 設計が要るとき\n',
  );
  assert.notEqual(expected, SAMPLE);
  assert.equal(out, expected);
});

test("CB-T89 並べ替えと改名で、定義の前のコメントが一緒に動く", () => {
  const doc = readPhases(SAMPLE);
  const [research, design, ...rest] = doc.model.form.phases;
  const out = doc.apply({ phases: [design, { ...research, id: "survey" }, ...rest] });
  const ids = [...out.matchAll(/^  ([A-Za-z-]+):$/gm)].map((m) => m[1]);
  assert.deepEqual(ids, ["design", "survey", "implement", "acceptance", "implement-feedback"]);
  // 先頭の定義の前のコメント（読み込みでは対応表に付く）も、改名した定義に付いて動く
  assert.match(out, /\n  # 触る場所が多いとき\n  design:\n/);
  assert.match(out, /\n  # 分からないときだけ\n  survey:\n    kind: work\n    title: 調査\n/);
  assert.ok(!out.includes("  research:"));
});

test("CB-T90 足す・消す・空のリストは欄ごと消す・scope の inherit とリストを行き来する", () => {
  const doc = readPhases(SAMPLE);
  const phases = doc.model.form.phases
    .filter((p) => p.id !== "acceptance")
    .map((p) => {
      if (p.id === "implement") {
        // scope を inherit にすれば値として書く。読まない欄（requires・overlap）は手を付けずに残る
        return { ...p, inherit: true };
      }
      if (p.id === "implement-feedback") {
        // inherit からリストへ。glob は二重引用符で囲む
        return { ...p, inherit: false, scope: ["*.md", "docs/*"] };
      }
      return p;
    })
    .concat([phase("docs", { title: "文書", review: "mr", inherit: false, scope: ["README.md"], agent: "writer" })]);
  const out = doc.apply({ phases });
  assert.ok(!out.includes("  acceptance:"));
  assert.match(out, /\n  implement:\n    title: 実装とテスト\n    scope: inherit\n    requires: \[acceptance\]\n    overlap: \[acceptance\]\n/);
  assert.match(out, /\n  implement-feedback:\n    kind: feedback\n    title: 実装フィードバック対応\n    review: mr\n    scope: \["\*\.md", "docs\/\*"\]\n/);
  // 新しい定義は kind と review を必ず書き、欄は決まった順
  assert.match(out, /\n  docs:\n    kind: work\n    title: 文書\n    review: mr\n    scope: \["README\.md"\]\n    agent: writer\n$/);
  // 書いたものは読み直せる
  const again = readPhases(out);
  assert.deepEqual(again.model.problems, []);
  assert.deepEqual(
    again.model.form.phases.map((p) => p.id),
    ["research", "design", "implement", "implement-feedback", "docs"],
  );
});

test("CB-T91 id が重なれば書き戻さない（実行ファイルは後ろで黙って上書きするため）", () => {
  const doc = readPhases(SAMPLE);
  const phases = doc.model.form.phases.map((p) => (p.id === "design" ? { ...p, id: "research" } : p));
  assert.throws(() => doc.apply({ phases }), /id `research` が 2 つあります/);
});

test("CB-T92 version が無ければ苦情を出し、保存で先頭に足す。読めない値は苦情にして既定で出す", () => {
  const doc = readPhases(`phases:\n  a:\n    kind: strange\n    review: maybe\n    scope: everything\n    overlap: b\n`);
  assert.equal(doc.model.version, null);
  assert.ok(doc.model.problems.some((p) => p.startsWith("version がありません")));
  assert.ok(doc.model.problems.some((p) => p.includes("kind `strange`")));
  assert.ok(doc.model.problems.some((p) => p.includes("review `maybe`")));
  assert.ok(doc.model.problems.some((p) => p.includes("scope `everything`")));
  // 古い順序の欄は形が崩れていても読まないので苦情にせず、読まない欄として名指しするだけ
  assert.ok(!doc.model.problems.some((p) => p.includes("overlap")), doc.model.problems.join("\n"));
  assert.deepEqual(doc.model.unread, [{ phase: "a", key: "overlap" }]);
  const a = doc.model.form.phases[0];
  assert.equal(a.kind, "work");
  assert.equal(a.review, "mr");
  assert.equal(a.inherit, true);
  const out = doc.apply(doc.model.form);
  assert.match(out, /^version: 1\nphases:\n  a:\n    kind: work\n    review: mr\n    scope: inherit\n    overlap: b\n$/);
});

test("CB-T93 phases が無い、定義が無い、空のファイルは苦情になり、保存で対応表から始める", () => {
  assert.ok(readPhases("version: 1\n").model.problems.some((p) => p.startsWith("phases がありません")));
  assert.ok(readPhases("version: 1\nphases: {}\n").model.problems.some((p) => p.startsWith("定義が 1 つもありません")));
  const empty = readPhases("");
  assert.deepEqual(empty.model.form.phases, []);
  const out = empty.apply({ phases: [phase("implement", { title: "実装", inherit: false, scope: ["src/*"] })] });
  assert.equal(out, 'version: 1\nphases:\n  implement:\n    kind: work\n    title: 実装\n    review: mr\n    scope: ["src/*"]\n');
});

test("CB-T94 画面から来た内容は形だけ確かめる。リストに文字以外が混ざれば受け取らない。順序の欄は送られても受け取らない", () => {
  const ok = asPhasesForm({
    phases: [{ origin: 0, id: "a", title: 1, kind: "work", review: "mr", inherit: true, scope: ["x"], when: "w" }],
  });
  assert.ok(ok !== undefined);
  assert.equal(ok.phases[0].title, "1");
  assert.equal(ok.phases[0].when, "w");
  assert.deepEqual(ok.phases[0].deliverables, []);
  // 画面は順序の欄を送らない。前の形（order・after・overlap・requires）が混ざっていても、受け取る形には載せない
  const old = asPhasesForm({
    order: "dag",
    phases: [{ origin: 0, id: "a", kind: "work", review: "mr", inherit: true, after: ["b"], overlap: [1], requires: "c" }],
  });
  assert.ok(old !== undefined);
  assert.deepEqual(Object.keys(old), ["phases"]);
  assert.ok(!("after" in old.phases[0]) && !("overlap" in old.phases[0]) && !("requires" in old.phases[0]), JSON.stringify(old.phases[0]));
  assert.equal(asPhasesForm({ phases: [{ id: "a", kind: "work", review: "mr", scope: [1] }] }), undefined);
  assert.equal(asPhasesForm({ phases: [{ id: "a", kind: "work", review: "mr", deliverables: [1] }] }), undefined);
  assert.equal(asPhasesForm({ phases: [{ id: "a", kind: "other", review: "mr" }] }), undefined);
  assert.equal(asPhasesForm({ phases: [{ id: "a", kind: "work", review: "mr", origin: -1 }] }), undefined);
  assert.equal(asPhasesForm({ phases: "a" }), undefined);
  assert.equal(asPhasesForm(null), undefined);
});

test("CB-T101 scope を書いていない定義は、無関係な保存で scope: inherit が補われる（それ以外は 1 文字も変わらない）", () => {
  const text = "version: 1\nphases:\n  a:\n    kind: work\n    title: A\n  b:\n    kind: work\n    title: B\n    scope: inherit\n";
  const doc = readPhases(text);
  assert.equal(doc.apply(doc.model.form), "version: 1\nphases:\n  a:\n    kind: work\n    title: A\n    scope: inherit\n  b:\n    kind: work\n    title: B\n    scope: inherit\n");
});

test("CB-T102 先頭を動かしても空白だけの行は出ず、先頭を消せば見出しのコメントは対応表に残る", () => {
  const doc = readPhases(SAMPLE);
  const [research, design, ...rest] = doc.model.form.phases;
  const swapped = doc.apply({ phases: [design, research, ...rest] });
  assert.ok(!/\n {2,}\n/.test(swapped), "空白だけの行が無い");
  assert.match(swapped, /^# フェーズ定義。ユーザが持つ設定で、エージェントは書き換えない。\n#\n# id と title はどちらも一意。\nversion: 1\n\nphases:\n  # 触る場所が多いとき\n  design:\n    kind: work\n    title: 設計\n    review: mr\n    scope: \["wip\/design\/\*", "docs\/\*"\]\n\n  # 分からないときだけ\n  research:\n    kind: work\n/);
  const dropped = doc.apply({ phases: [design, ...rest] });
  assert.match(dropped, /\nphases:\n  # 分からないときだけ\n  # 触る場所が多いとき\n  design:\n/);
  assert.ok(!dropped.includes("  research:"));
});

test("CB-T103 同じ元ノードを 2 回送れば書き戻さない。yes / no の id は引用符で囲む。phases: {} はブロックに直す", () => {
  const doc = readPhases(SAMPLE);
  const [research] = doc.model.form.phases;
  assert.throws(() => doc.apply({ phases: [research, { ...research, id: "x" }] }), /2 回送られました/);

  const renamed = doc.apply({ phases: doc.model.form.phases.map((p) => (p.id === "design" ? { ...p, id: "yes", when: "on" } : p)) });
  assert.match(renamed, /\n  "yes":\n    kind: work\n    title: 設計\n    review: mr\n    scope: \["wip\/design\/\*", "docs\/\*"\]\n    when: "on"\n/);
  assert.deepEqual(readPhases(renamed).model.form.phases.map((p) => p.id), ["research", "yes", "implement", "acceptance", "implement-feedback"]);

  const flow = readPhases("version: 1\nphases: {}\n");
  assert.equal(
    flow.apply({ phases: [phase("a", { title: "A" })] }),
    "version: 1\nphases:\n  a:\n    kind: work\n    title: A\n    review: mr\n    scope: inherit\n",
  );
  assert.match(readPhases("- a\n").model.problems[0], /最上位がマップ（キーと値の組の集まり）ではありません/);
  assert.match(readPhases("version: 1\nphases:\n  broken:\n  ok:\n    kind: work\n").model.problems[0], /保存するとこの定義は消えます/);
});

/** 前の版の順序の欄を持つファイル。実行ファイルはもう読まない（`--lint` が warn で「読まない」と言う） */
const OLD_FIELDS = `version: 1
order: dag

phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: inherit
  design:
    kind: work
    title: 設計
    scope: inherit
    after: [research]
    when: 設計が要るとき
  implement:
    kind: work
    title: 実装
    scope: inherit
    requires: [design]
    overlap: [design]
    after: [design]
`;

test("CB-T198 古い順序の欄（ファイルの頭の order、定義の after・overlap・requires）は読まず、読まない欄として名指しし、保存しても手を付けずに残す", () => {
  const doc = readPhases(OLD_FIELDS);
  assert.deepEqual(doc.model.problems, []);
  assert.deepEqual(doc.model.unread, [
    { phase: null, key: "order" },
    { phase: "design", key: "after" },
    { phase: "implement", key: "requires" },
    { phase: "implement", key: "overlap" },
    { phase: "implement", key: "after" },
  ]);
  // 変えなければ 1 文字も変わらない
  assert.equal(doc.apply(doc.model.form), OLD_FIELDS);
  // ほかの欄を直しても、古い欄はそのまま残る（消すのはユーザがエディタで）
  const phases = doc.model.form.phases.map((p) => (p.id === "design" ? { ...p, when: "", title: "設計する" } : p));
  const out = doc.apply({ phases });
  assert.match(out, /^version: 1\norder: dag\n/);
  assert.match(out, /\n  design:\n    kind: work\n    title: 設計する\n    scope: inherit\n    after: \[research\]\n  implement:\n/);
  assert.match(out, /\n    requires: \[design\]\n    overlap: \[design\]\n    after: \[design\]\n$/);
});

test("CB-T199 知らない値の order も苦情にせず読まない欄として名指しし、定義を並べ替えても古い欄は元の定義に付いて動く", () => {
  const doc = readPhases(OLD_FIELDS.replace("order: dag\n", "order: [graph]\n"));
  assert.ok(!doc.model.problems.some((p) => p.includes("order")), doc.model.problems.join("\n"));
  assert.deepEqual(doc.model.unread[0], { phase: null, key: "order" });
  const [research, design, implement] = doc.model.form.phases;
  const out = doc.apply({ phases: [implement, research, design] });
  assert.equal(out.match(/^order:/gm)?.length, 1);
  assert.match(out, /^order: \[graph\]$/m);
  assert.match(out, /\nphases:\n  implement:\n    kind: work\n    title: 実装\n    scope: inherit\n    requires: \[design\]\n    overlap: \[design\]\n    after: \[design\]\n/);
  assert.match(out, /\n  design:\n    kind: work\n    title: 設計\n    scope: inherit\n    after: \[research\]\n    when: 設計が要るとき\n$/);
});
