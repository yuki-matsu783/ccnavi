import { test } from "node:test";
import assert from "node:assert/strict";
import { asRiskForm, BUILTIN_RISK_TEXT, readRisk, type FactorForm } from "../src/core/risk-doc.js";

const TEXT = `# 先頭の説明。消えてはいけない。
version: 1
levels:
  medium: 20
  high: 40
  critical: 70
factors:
  # 大きい差分は人が見る
  - id: big-diff
    points: 25
    lines_over: 300
    message: 行数が多い
  - id: ci
    points: 35
    glob: ".github/**"
    max: 35
    message: CI に触った
  - id: untested
    points: 30
    judge: テストの無い振る舞いの変更を含むか
  # 例: 使うときにコメントを外す
  # - id: complexity
  #   points: 30
  #   script: .claude/ccnavi/risk/complexity.sh
`;

test("CB-T72 閾値と項目を読む。当て方は 1 つで、値は欄の文字として持つ", () => {
  const { model } = readRisk(TEXT);
  assert.equal(model.version, 1);
  assert.deepEqual(model.problems, []);
  assert.deepEqual(model.form.levels, { medium: "20", high: "40", critical: "70" });
  assert.deepEqual(
    model.form.factors.map((f) => [f.origin, f.id, f.points, f.kind, f.value, f.max, f.message]),
    [
      [0, "big-diff", "25", "lines_over", "300", "", "行数が多い"],
      [1, "ci", "35", "glob", ".github/**", "35", "CI に触った"],
      [2, "untested", "30", "judge", "テストの無い振る舞いの変更を含むか", "", ""],
    ],
  );
});

test("CB-T73 何も変えずに書き戻せばコメントも本文も同じ", () => {
  const doc = readRisk(TEXT);
  assert.equal(doc.apply(doc.model.form), TEXT);
  const builtin = readRisk(BUILTIN_RISK_TEXT);
  assert.deepEqual(builtin.model.problems, []);
  assert.equal(builtin.apply(builtin.model.form), BUILTIN_RISK_TEXT);
});

test("CB-T74 組み込みの配点の本文は実行ファイルの組み込みと同じ値", () => {
  const { model } = readRisk(BUILTIN_RISK_TEXT);
  assert.deepEqual(model.form.levels, { medium: "20", high: "40", critical: "70" });
  assert.deepEqual(
    model.form.factors.map((f) => [f.id, f.points, f.kind, f.value, f.max]),
    [
      ["big-diff", "25", "lines_over", "300", ""],
      ["many-files", "15", "files_over", "10", ""],
      ["ci", "35", "glob", ".github/**", "35"],
      ["deletes", "20", "deleted_over", "3", ""],
    ],
  );
});

test("CB-T75 欄を変えても他の項目のコメントは残り、数は数として書く", () => {
  const doc = readRisk(TEXT);
  const f = doc.model.form;
  const out = doc.apply({
    levels: { ...f.levels, high: "45" },
    factors: [f.factors[0], { ...f.factors[1], points: "40", max: "" }, { ...f.factors[2], message: "テスト無し" }],
  });
  assert.match(out, /^# 先頭の説明。消えてはいけない。\n/);
  assert.match(out, /  high: 45\n/);
  assert.match(out, /  # 大きい差分は人が見る\n  - id: big-diff/);
  assert.match(out, /  - id: ci\n    points: 40\n    glob: "\.github\/\*\*"\n    message: CI に触った\n/);
  assert.doesNotMatch(out, /max:/);
  assert.match(out, /judge: テストの無い振る舞いの変更を含むか\n    message: テスト無し\n/);
  // 末尾の例のコメントも残る
  assert.match(out, /  # 例: 使うときにコメントを外す\n  # - id: complexity/);
  const again = readRisk(out).model.form;
  assert.equal(again.levels.high, "45");
  assert.equal(again.factors[1].points, "40");
  assert.equal(again.factors[2].message, "テスト無し");
});

test("CB-T76 当て方を変えると前の当て方の欄は消え、新しい項目は points の後ろに当て方を置く", () => {
  const doc = readRisk(TEXT);
  const f = doc.model.form;
  const changed: FactorForm = { ...f.factors[0], kind: "files_over", value: "10" };
  const fresh: FactorForm = { origin: null, id: "deletes", points: "20", kind: "deleted_over", value: "3", max: "", message: "消したファイルが多い" };
  const scripted: FactorForm = { origin: null, id: "complexity", points: "30", kind: "script", value: ".claude/ccnavi/risk/complexity.sh", max: "", message: "" };
  const out = doc.apply({ levels: f.levels, factors: [changed, fresh, scripted, f.factors[2]] });
  assert.match(out, /  - id: big-diff\n    points: 25\n    files_over: 10\n    message: 行数が多い\n/);
  assert.doesNotMatch(out, /lines_over/);
  assert.match(out, /  - id: deletes\n    points: 20\n    deleted_over: 3\n    message: 消したファイルが多い\n/);
  assert.match(out, /  - id: complexity\n    points: 30\n    script: \.claude\/ccnavi\/risk\/complexity\.sh\n/);
  assert.doesNotMatch(out, /id: ci/);
  const again = readRisk(out).model.form;
  assert.deepEqual(again.factors.map((x) => [x.id, x.kind, x.value]), [
    ["big-diff", "files_over", "10"],
    ["deletes", "deleted_over", "3"],
    ["complexity", "script", ".claude/ccnavi/risk/complexity.sh"],
    ["untested", "judge", "テストの無い振る舞いの変更を含むか"],
  ]);
});

test("CB-T77 新しい glob は引用符で囲み、空の閾値は書かない。整数でない文字はそのまま書く", () => {
  const doc = readRisk("version: 1\nfactors: []\n");
  const out = doc.apply({
    levels: { medium: "", high: "50", critical: "" },
    factors: [{ origin: null, id: "agents", points: "1O", kind: "glob", value: "*.agent.md", max: "20", message: "" }],
  });
  assert.equal(out, 'version: 1\nlevels:\n  high: 50\nfactors:\n  - id: agents\n    points: 1O\n    glob: "*.agent.md"\n    max: 20\n');
  // 読み直しても同じ形。points は lint が「整数ではない」と言う値のまま
  const again = readRisk(out).model.form;
  assert.deepEqual(again.levels, { medium: "", high: "50", critical: "" });
  assert.equal(again.factors[0].points, "1O");
  // 全部消せば並びは空
  assert.equal(doc.apply({ levels: { medium: "", high: "", critical: "" }, factors: [] }), "version: 1\nfactors: []\n");
});

test("CB-T78 version が無ければ苦情にして、保存で先頭に足す。壊れた形は苦情にして他は出す", () => {
  const missing = readRisk("levels:\n  high: 40\n");
  assert.equal(missing.model.version, null);
  assert.match(missing.model.problems[0], /version が無い/);
  assert.match(missing.apply(missing.model.form), /^version: 1\nlevels:\n  high: 40\n$/);

  const broken = readRisk("version: 1\nlevels: nope\nfactors:\n  - id: a\n    points: 1\n    lines_over: 1\n    glob: '*'\n  - not-a-map\n");
  assert.equal(broken.model.problems.length, 3);
  assert.match(broken.model.problems[0], /levels/);
  assert.match(broken.model.problems[1], /当て方が 2 個/);
  assert.match(broken.model.problems[2], /2 件目/);
  // 当て方が 2 つある項目は最初の 1 つで出し、保存すると他は消える
  assert.equal(broken.model.form.factors.length, 1);
  assert.equal(broken.model.form.factors[0].kind, "lines_over");
  const out = broken.apply(broken.model.form);
  assert.doesNotMatch(out, /glob/);
  assert.doesNotMatch(out, /not-a-map/);

  const wrong = readRisk("version: 2\n");
  assert.match(wrong.model.problems[0], /version 2/);
});

test("CB-T79 画面から来た内容は形を確かめてから受け取る", () => {
  const ok = asRiskForm({
    levels: { medium: 20, high: "40", critical: "" },
    factors: [
      { origin: 0, id: "a", points: 25, kind: "lines_over", value: 300, max: "", message: "m" },
      { origin: null, id: "b", points: "", kind: "judge", value: "問い" },
    ],
  });
  assert.ok(ok);
  assert.deepEqual(ok.levels, { medium: "20", high: "40", critical: "" });
  assert.deepEqual(ok.factors[0], { origin: 0, id: "a", points: "25", kind: "lines_over", value: "300", max: "", message: "m" });
  assert.deepEqual(ok.factors[1], { origin: null, id: "b", points: "", kind: "judge", value: "問い", max: "", message: "" });
  assert.equal(asRiskForm({ factors: [] }), undefined);
  assert.equal(asRiskForm({ levels: {}, factors: [{ kind: "nope" }] }), undefined);
  assert.equal(asRiskForm({ levels: {}, factors: [{ origin: -1, kind: "glob" }] }), undefined);
  assert.equal(asRiskForm({ levels: {}, factors: [{ origin: "0", kind: "glob" }] }), undefined);
});

test("CB-T97 対応表でない項目が前にあっても、後ろの項目は自分の元ノードに書き戻す", () => {
  const text = "version: 1\nfactors:\n  - ごみ\n  # a の理由\n  - id: a\n    points: 1\n    lines_over: 1\n  # b の理由\n  - id: b\n    points: 2\n    files_over: 2\n";
  const doc = readRisk(text);
  assert.deepEqual(doc.model.form.factors.map((f) => [f.origin, f.id]), [[1, "a"], [2, "b"]]);
  assert.match(doc.model.problems[0], /保存するとこの項目は消える/);
  // 何も変えずに保存: 読めない項目だけが消え、a と b はそれぞれのコメントごと残る
  const out = doc.apply(doc.model.form);
  assert.equal(out, "version: 1\nfactors:\n  # a の理由\n  - id: a\n    points: 1\n    lines_over: 1\n  # b の理由\n  - id: b\n    points: 2\n    files_over: 2\n");
});

test("CB-T98 先頭の項目を消しても並びの見出しのコメントは残り、先頭に来た項目は空行を連れてこない", () => {
  const text = "version: 1\nfactors:\n  # 見出し\n  - id: a\n    points: 1\n    lines_over: 1\n\n  # b の理由\n  - id: b\n    points: 2\n    files_over: 2\n";
  const doc = readRisk(text);
  const [a, b] = doc.model.form.factors;
  assert.equal(doc.apply({ levels: doc.model.form.levels, factors: [b] }), "version: 1\nfactors:\n  # 見出し\n  # b の理由\n  - id: b\n    points: 2\n    files_over: 2\n");
  // 入れ替えると、見出しは a に付いて動き、空行は 2 番目の位置に残る
  assert.equal(doc.apply({ levels: doc.model.form.levels, factors: [b, a] }), "version: 1\nfactors:\n  # b の理由\n  - id: b\n    points: 2\n    files_over: 2\n\n  # 見出し\n  - id: a\n    points: 1\n    lines_over: 1\n");
});

test("CB-T99 同じ元ノードを 2 回送れば書き戻さない。glob 以外の max は消す。最上位が対応表でなければ苦情", () => {
  const doc = readRisk(TEXT);
  const f = doc.model.form;
  assert.throws(() => doc.apply({ levels: f.levels, factors: [f.factors[0], { ...f.factors[0], id: "x" }] }), /2 回送られた/);
  const withMax = readRisk("version: 1\nfactors:\n  - id: a\n    points: 1\n    lines_over: 5\n    max: 3\n");
  assert.equal(withMax.apply(withMax.model.form), "version: 1\nfactors:\n  - id: a\n    points: 1\n    lines_over: 5\n");
  assert.match(readRisk("hello\n").model.problems[0], /最上位が対応表ではない/);
  assert.match(readRisk("- a\n").model.problems[0], /最上位が対応表ではない/);
});

test("CB-T100 PyYAML が別の型に読む語は引用符で囲み、それ以外は裸のまま", () => {
  const doc = readRisk(TEXT);
  const f = doc.model.form;
  const out = doc.apply({
    levels: f.levels,
    factors: [
      { ...f.factors[0], message: "yes" },
      { ...f.factors[2], value: "1:30" },
      { origin: null, id: "no", points: "1_000", kind: "script", value: "0755", max: "", message: "普通の文" },
    ],
  });
  assert.match(out, /    message: "yes"\n/);
  assert.match(out, /    judge: "1:30"\n/);
  assert.match(out, /  - id: "no"\n    points: "1_000"\n    script: "0755"\n    message: 普通の文\n/);
  // 読み直せば文字のまま
  const again = readRisk(out).model.form;
  assert.deepEqual(again.factors.map((x) => [x.id, x.points, x.value, x.message]), [
    ["big-diff", "25", "300", "yes"],
    ["untested", "30", "1:30", ""],
    ["no", "1_000", "0755", "普通の文"],
  ]);
});
