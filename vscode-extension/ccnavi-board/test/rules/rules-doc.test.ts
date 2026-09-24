import { test } from "node:test";
import assert from "node:assert/strict";
import { asSections, readRules } from "../../src/core/rules-doc.js";
import type { RuleForm } from "../../src/core/rules-view.js";

const TEXT = `# 先頭の説明。消えてはいけない。
#
# glob と regex は必ず引用符で囲む。
version: 1

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

test("CB-T41 タイプごとに id / match / glob か regex / message を読む", () => {
  const { model } = readRules(TEXT);
  assert.equal(model.version, 1);
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

test("CB-T194 PyYAML が別の型に読む語は引用符で囲む（id: on は True、id: no は空の id に読まれる）", () => {
  const doc = readRules(TEXT);
  const s = doc.model.sections;
  const fresh: RuleForm = { ...s.deny[1], origin: null, id: "no", message: "新しい" };
  const out = doc.apply({ deny: [{ ...s.deny[0], id: "on" }, fresh], ask: s.ask, allow: s.allow });
  assert.match(out, /  - id: "on"\n/);
  assert.match(out, /  - id: "no"\n/);
  assert.match(out, /  - id: inspection\n/);
  assert.deepEqual(forms(out).deny.map((r) => r.id), ["on", "no"]);
});

test("CB-T44 タイプを移すとコメントごと動き、glob と regex は片方だけ残る", () => {
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
    additionalContextFile: "",
    additionalContextOnceFile: "",
    every: "",
  };
  const out = doc.apply({ deny: [s.deny[0], fresh], ask: s.ask, allow: [] });
  assert.match(out, /- id: no-rm\n    match: Bash\n    glob: '\*rm -rf\*'\n    message: >-\n      消さない。退避する。/);
  assert.doesNotMatch(out, /secrets/);
  assert.match(out, /allow: \[\]/);
  const again = forms(out);
  assert.deepEqual(again.deny.map((r) => r.id), ["git-push", "no-rm"]);
  assert.deepEqual(again.allow, []);
});

test("CB-T46 壊れたタイプは苦情にして、他のタイプは出す", () => {
  const { model } = readRules("version: 1\ndeny: nope\nallow:\n  - id: a\n    match: Read\n    glob: '*'\n    message: m\n");
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
  const text = `version: 1
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
test("CB-T54 additionalContextFile は 1 行の値で、対応する文の直後に置く", () => {
  const text = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push は人が行う
    additionalContext: ラッパを通す
    additionalContextFile: docs/push.md
allow:
  - id: src
    match: Write
    glob: "*/src/*"
    additionalContextOnce: 決まり
`;
  const doc = readRules(text);
  const s = doc.model.sections;
  assert.equal(s.deny[0].additionalContextFile, "docs/push.md");
  assert.equal(s.deny[0].additionalContextOnceFile, "");
  assert.equal(s.allow[0].additionalContextFile, "");
  assert.equal(doc.apply(s), text);
  // once のファイルは once の文の直後。文が無ければ末尾。空のままなら足さない
  const out = doc.apply({
    deny: [{ ...s.deny[0], additionalContextOnceFile: "docs/once.md" }],
    ask: [],
    allow: [{ ...s.allow[0], additionalContextOnceFile: "docs/testing.md" }],
  });
  assert.match(out, /additionalContextFile: docs\/push.md\n    additionalContextOnceFile: docs\/once.md\n/);
  assert.match(out, /additionalContextOnce: 決まり\n    additionalContextOnceFile: docs\/testing.md\n/);
  const again = readRules(out).model.sections;
  assert.equal(again.deny[0].additionalContextOnceFile, "docs/once.md");
  assert.equal(again.allow[0].additionalContextOnceFile, "docs/testing.md");
  // 空にすれば欄は残るが値は空
  const cleared = doc.apply({ deny: [{ ...s.deny[0], additionalContextFile: "" }], ask: [], allow: s.allow });
  assert.equal(readRules(cleared).model.sections.deny[0].additionalContextFile, "");
});

test("CB-T134 every（何回に 1 度渡すか）は書かれたまま読む。触らなければ書き戻しでも変わらない", () => {
  const text = `version: 1
deny: []
ask: []
allow:
  - id: nudge
    match: Write|Edit
    glob: "*/src/*"
    every: 5
    additionalContextOnce: 決まりと突き合わせる
  - id: broken
    match: Write
    glob: "*/lib/*"
    every: x
    additionalContext: 刻めていない
  - id: plain
    match: Read
    glob: "*"
`;
  const doc = readRules(text);
  const s = doc.model.sections;
  assert.deepEqual(
    s.allow.map((r) => r.every),
    ["5", "x", ""],
  );
  // 読めない値（every: x）は画面が黙って直さない。直すと --lint の苦情だけが宙に浮く。
  assert.equal(doc.apply(s), text);
  // 画面から来た並びも同じ。every を持たない古い画面の形は空として受け取る。
  const posted = asSections(JSON.parse(JSON.stringify({ ...s, allow: s.allow })));
  assert.notEqual(posted, undefined);
  assert.deepEqual(
    posted === undefined ? [] : posted.allow.map((r) => r.every),
    ["5", "x", ""],
  );
  assert.equal(doc.apply(posted ?? s), text);
});

test("CB-T135 every は数に読めれば数で書き、読めなければ打ったまま、空なら欄ごと消す", () => {
  const text = `version: 1
deny: []
ask: []
allow:
  - id: nudge
    match: Write|Edit
    glob: "*/src/*"
    every: 5
    additionalContextOnce: 決まりと突き合わせる
  - id: plain
    match: Read
    glob: "*"
`;
  const doc = readRules(text);
  const s = doc.model.sections;
  // 刻みを直す・無かったところに足す。足す先は match の直後
  const out = doc.apply({
    deny: [],
    ask: [],
    allow: [{ ...s.allow[0], every: "3" }, { ...s.allow[1], every: "10" }],
  });
  assert.match(out, /glob: "\*\/src\/\*"\n    every: 3\n/);
  assert.match(out, /- id: plain\n    match: Read\n    every: 10\n    glob: "\*"\n/);
  assert.deepEqual(
    readRules(out).model.sections.allow.map((r) => r.every),
    ["3", "10"],
  );
  // 空にすれば欄ごと消える。値を空にして残すと「毎回渡す」に読めない欄が残る
  const cleared = doc.apply({ deny: [], ask: [], allow: [{ ...s.allow[0], every: "" }, s.allow[1]] });
  assert.doesNotMatch(cleared, /every/);
  assert.equal(readRules(cleared).model.sections.allow[0].every, "");
  // 読めない値は打ったまま書く（止めるのは保存前の --lint であって画面ではない）
  const broken = doc.apply({ deny: [], ask: [], allow: [{ ...s.allow[0], every: "x" }, s.allow[1]] });
  assert.match(broken, /every: x\n/);
  assert.equal(readRules(broken).model.sections.allow[0].every, "x");
});
