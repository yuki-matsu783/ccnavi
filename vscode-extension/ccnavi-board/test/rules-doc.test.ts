import { test } from "node:test";
import assert from "node:assert/strict";
import { asSections, readRules, type RuleForm } from "../src/core/rules-doc.js";

const TEXT = `# 先頭の説明。消えてはいけない。
#
# glob と regex は必ず引用符で囲む。
version: 3

deny:
  # push は人が行う
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: >-
      push は人が行う。
      ラッパに依頼する。

  - id: secrets
    match: Read|Write
    regex: '/secrets/'
    message: 秘密の置き場

ask: []

allow:
  - id: inspection
    match: Bash
    regex: '^(ls|cat)\\b'
    message: 見るだけ
`;

function forms(text: string) {
  return readRules(text).model.sections;
}

test("CB-T41 区画ごとに id / match / glob か regex / message を読む", () => {
  const { model } = readRules(TEXT);
  assert.equal(model.version, 3);
  assert.deepEqual(model.problems, []);
  assert.deepEqual(
    model.sections.deny.map((r) => [r.id, r.match, r.kind, r.pattern, r.origin]),
    [
      ["git-push", "Bash", "glob", "*git push*", { section: "deny", index: 0 }],
      ["secrets", "Read|Write", "regex", "/secrets/", { section: "deny", index: 1 }],
    ],
  );
  assert.equal(model.sections.deny[0].message, "push は人が行う。 ラッパに依頼する。");
  assert.deepEqual(model.sections.ask, []);
  assert.equal(model.sections.allow[0].pattern, "^(ls|cat)\\b");
});

test("CB-T42 何も変えずに書き戻せばコメントも本文も同じ", () => {
  const doc = readRules(TEXT);
  assert.equal(doc.apply(doc.model.sections), TEXT);
});

test("CB-T43 欄を変えても、他のルールのコメントと折り返しは残る", () => {
  const doc = readRules(TEXT);
  const s = doc.model.sections;
  const edited = {
    deny: [s.deny[0], { ...s.deny[1], pattern: "/secrets/|/keys/" }],
    ask: s.ask,
    allow: [{ ...s.allow[0], message: "見るだけ。書かない" }],
  };
  const out = doc.apply(edited);
  assert.match(out, /^# 先頭の説明。消えてはいけない。/);
  assert.match(out, /  # push は人が行う\n  - id: git-push/);
  assert.match(out, /message: >-\n      push は人が行う。\n      ラッパに依頼する。/);
  assert.match(out, /regex: '\/secrets\/\|\/keys\/'/);
  assert.match(out, /message: 見るだけ。書かない/);
  // 読み直しても同じ形
  const again = forms(out);
  assert.equal(again.deny[1].pattern, "/secrets/|/keys/");
  assert.equal(again.allow[0].message, "見るだけ。書かない");
});

test("CB-T44 区画を移すとコメントごと動き、glob と regex は片方だけ残る", () => {
  const doc = readRules(TEXT);
  const s = doc.model.sections;
  const moved: RuleForm = { ...s.deny[0], kind: "regex", pattern: "\\bgit push\\b" };
  const out = doc.apply({ deny: [s.deny[1]], ask: [moved], allow: s.allow });
  const again = forms(out);
  assert.deepEqual(again.deny.map((r) => r.id), ["secrets"]);
  assert.deepEqual(again.ask.map((r) => [r.id, r.kind, r.pattern]), [["git-push", "regex", "\\bgit push\\b"]]);
  assert.match(out, /ask:\n  # push は人が行う\n  - id: git-push/);
  assert.doesNotMatch(out, /glob: "\*git push\*"/);
});

test("CB-T45 新しいルールは引用符付きの glob と折り返しの message で足す。削除は消える", () => {
  const doc = readRules(TEXT);
  const s = doc.model.sections;
  const fresh: RuleForm = {
    origin: null,
    id: "no-rm",
    match: "Bash",
    kind: "glob",
    pattern: "*rm -rf*",
    message: "消さない。退避する。",
    additionalContext: "",
    additionalContextOnce: "",
  };
  const out = doc.apply({ deny: [s.deny[0], fresh], ask: s.ask, allow: [] });
  assert.match(out, /- id: no-rm\n    match: Bash\n    glob: '\*rm -rf\*'\n    message: >-\n      消さない。退避する。/);
  assert.doesNotMatch(out, /secrets/);
  assert.match(out, /allow: \[\]/);
  const again = forms(out);
  assert.deepEqual(again.deny.map((r) => r.id), ["git-push", "no-rm"]);
  assert.deepEqual(again.allow, []);
});

test("CB-T46 壊れた区画は苦情にして、他の区画は出す", () => {
  const { model } = readRules("version: 3\ndeny: nope\nallow:\n  - id: a\n    match: Read\n    glob: '*'\n    message: m\n");
  assert.equal(model.problems.length, 1);
  assert.match(model.problems[0], /deny/);
  assert.equal(model.sections.allow.length, 1);
});

test("CB-T47 画面から来た並びは形を確かめてから受け取る", () => {
  const ok = asSections({
    deny: [{ origin: { section: "deny", index: 0 }, id: "a", match: "Bash", kind: "glob", pattern: "*", message: "m" }],
    ask: [],
    allow: [{ origin: null, id: "b", match: "Read", kind: "regex", pattern: ".", message: "" }],
  });
  assert.ok(ok);
  assert.equal(ok.deny[0].origin?.index, 0);
  assert.equal(ok.allow[0].origin, null);
  assert.equal(asSections({ deny: [], ask: [] }), undefined);
  assert.equal(asSections({ deny: [{ kind: "nope" }], ask: [], allow: [] }), undefined);
  assert.equal(asSections({ deny: [{ origin: { section: "x", index: 0 }, kind: "glob" }], ask: [], allow: [] }), undefined);
});

test("CB-T53 additionalContext を読み、書き、変えていなければ折り返しを残す", () => {
  const text = `version: 3
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push は人が行う
    additionalContext: >-
      ラッパを通せば
      親が送れる
allow:
  - id: src
    match: Write
    glob: "*/src/*"
`;
  const doc = readRules(text);
  const s = doc.model.sections;
  assert.equal(s.deny[0].additionalContext, "ラッパを通せば 親が送れる");
  assert.equal(s.allow[0].additionalContext, "");
  // 何も変えなければそのまま
  assert.equal(doc.apply(s), text);
  // 無かった欄を足す。空のままなら足さない
  const out = doc.apply({
    deny: s.deny,
    ask: [],
    allow: [{ ...s.allow[0], additionalContext: "src は自由に直してよい" }],
  });
  assert.match(out, /additionalContext: >-\n      ラッパを通せば\n      親が送れる/);
  assert.match(out, /glob: "\*\/src\/\*"\n    additionalContext: >-\n      src は自由に直してよい/);
  const again = readRules(out).model.sections;
  assert.equal(again.allow[0].additionalContext, "src は自由に直してよい");
  // 空にすれば欄は残るが値は空
  const cleared = doc.apply({ deny: [{ ...s.deny[0], additionalContext: "" }], ask: [], allow: s.allow });
  assert.equal(readRules(cleared).model.sections.deny[0].additionalContext, "");
  // once の文も同じ扱い。書けば折り返しで足し、読み直せば同じ
  assert.equal(s.deny[0].additionalContextOnce, "");
  const once = doc.apply({ deny: [{ ...s.deny[0], additionalContextOnce: "最初に 1 度だけ" }], ask: [], allow: s.allow });
  assert.match(once, /親が送れる\n    additionalContextOnce: >-\n      最初に 1 度だけ\n/);
  assert.equal(readRules(once).model.sections.deny[0].additionalContextOnce, "最初に 1 度だけ");
});
