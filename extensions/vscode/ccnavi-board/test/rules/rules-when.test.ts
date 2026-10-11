import { test } from "node:test";
import assert from "node:assert/strict";
import { parseDocument } from "yaml";
import { asSections, readRules } from "../../src/core/rules-doc.js";
import type { RuleForm, Sections } from "../../src/core/rules-view.js";
import {
  addChild,
  asCond,
  changeOp,
  condProblems,
  condSummary,
  group,
  layoutCond,
  leaf,
  not,
  removeAt,
  setPattern,
  treeOfPattern,
  unwrapNot,
  wrapNot,
  COND_COLUMN,
  type CondNode,
} from "../../src/core/rules-when.js";

const WHEN_TEXT = `version: 1
deny:
  # ルート直下は止め、置き場だけ除く
  - id: workspace-root
    match: Write|Edit|NotebookEdit
    when:
      all:
        # 本体
        - regex: '^{root}[\\\\/]'
        - not:
            any:
              # 下書きの置き場
              - regex: '^{root}[\\\\/]wip[\\\\/]scratchpad(?:[\\\\/]|$)'
              # ワークツリー
              - glob: '{root}/.claude/worktrees/**'
    message: >-
      main では編集しない。
      ワークツリーで直す。
allow:
  - id: plain
    match: Read
    glob: "*"
`;

const EXPECTED: CondNode = group("all", [
  leaf("regex", "^{root}[\\\\/]"),
  not(group("any", [leaf("regex", "^{root}[\\\\/]wip[\\\\/]scratchpad(?:[\\\\/]|$)"), leaf("glob", "{root}/.claude/worktrees/**")])),
]);

function withWhen(sections: Sections, when: CondNode): Sections {
  return { ...sections, deny: [{ ...sections.deny[0], when }, ...sections.deny.slice(1)] };
}

test("CB-T333 when を条件の木として読み、畳んだ行の要約にする。木を持たないルールには欄が無い", () => {
  const { model } = readRules(WHEN_TEXT);
  assert.deepEqual(model.problems, []);
  assert.deepEqual(model.sections.deny[0].when, EXPECTED);
  assert.equal(model.sections.allow[0].when, undefined);
  assert.deepEqual(condProblems(EXPECTED), []);
  assert.equal(condSummary(EXPECTED), "all(regex ^{root}[\\\\/], not(any(2)))");
  assert.equal(condSummary(leaf("glob", "")), "glob （空）");
});

test("CB-T334 木を変えなければ、when はコメントも書き方もそのまま戻る（画面を往復しても）", () => {
  const doc = readRules(WHEN_TEXT);
  assert.equal(doc.apply(doc.model.sections), WHEN_TEXT);
  const sent = asSections(JSON.parse(JSON.stringify(doc.model.sections)));
  assert.ok(sent);
  assert.deepEqual(sent.deny[0].when, EXPECTED);
  assert.equal(doc.apply(sent), WHEN_TEXT);
  // 他の欄だけ変えても、木には触らない
  const edited = doc.apply({ ...doc.model.sections, deny: [{ ...doc.model.sections.deny[0], id: "root-guard" }] });
  assert.equal(edited, WHEN_TEXT.replace("id: workspace-root", "id: root-guard"));
});

test("CB-T335 木を直すと when を書き直し、変えていないノードのコメントと他のキーは残る", () => {
  const doc = readRules(WHEN_TEXT);
  const s = doc.model.sections;
  // 葉の文字を変える: その葉だけが変わり、引用符は元のまま
  const renamed = doc.apply(withWhen(s, setPattern(EXPECTED, [1, 0, 1], "{root}/.git/**")));
  assert.equal(renamed, WHEN_TEXT.replace("'{root}/.claude/worktrees/**'", "'{root}/.git/**'"));
  // all → any と glob → regex は、キーだけが替わる
  const switched = doc.apply(withWhen(s, changeOp(changeOp(EXPECTED, [], "any"), [1, 0, 1], "regex")));
  assert.equal(switched, WHEN_TEXT.replace("      all:", "      any:").replace("- glob: '{root}/.claude", "- regex: '{root}/.claude"));
  // 先頭の子を消しても、残った子のコメントは自分に付いたまま（別の子へ付け替わらない）
  const removed = doc.apply(withWhen(s, removeAt(EXPECTED, [1, 0, 0])));
  assert.ok(removed.includes("              # ワークツリー\n              - glob: '{root}/.claude/worktrees/**'"), removed);
  assert.ok(!removed.includes("scratchpad"), removed);
  assert.deepEqual(readRules(removed).model.sections.deny[0].when, removeAt(EXPECTED, [1, 0, 0]));
  // 足した葉は単引用符。message の折り返しも元のまま
  const added = doc.apply(withWhen(s, addChild(EXPECTED, [], leaf("glob", "*.md"))));
  assert.ok(added.includes("        - glob: '*.md'\n"), added);
  assert.ok(added.includes("    message: >-\n      main では編集しない。\n      ワークツリーで直す。\n"), added);
  assert.ok(added.startsWith("version: 1\ndeny:\n  # ルート直下は止め、置き場だけ除く\n"), added);
  assert.deepEqual(readRules(added).model.sections.deny[0].when, addChild(EXPECTED, [], leaf("glob", "*.md")));
  // not で包んだ・外した木も読み直せる。PyYAML と同じ形（YAML として読めること）も見る
  const wrapped = wrapNot(EXPECTED, [0]);
  const text = doc.apply(withWhen(s, wrapped));
  assert.deepEqual(parseDocument(text).errors, []);
  assert.deepEqual(readRules(text).model.sections.deny[0].when, wrapped);
  assert.deepEqual(unwrapNot(wrapped, [0]), EXPECTED);
});

test("CB-T336 when の書式の誤りを挙げる（キーの数・空のリスト・知らないキー・空の not・上限・本体との併記）", () => {
  const text = `version: 1
deny:
  - id: broken
    match: Bash
    glob: "*"
    when:
      all:
        - glob: 'a'
          regex: 'b'
        - any: []
        - nope: 'x'
        - not:
        - 'bare'
`;
  const { model } = readRules(text);
  assert.equal(model.problems.length, 1);
  assert.match(model.problems[0], /when と glob \/ regex を両方書いています/);
  const tree = model.sections.deny[0].when;
  assert.ok(tree);
  const messages = condProblems(tree).map((p) => `${p.path.join(".")} ${p.message}`);
  assert.equal(messages.length, 5, messages.join("\n"));
  assert.match(messages[0], /^0 ノードのキーはちょうど 1 つです（2 個あります）/);
  assert.match(messages[1], /^1 any の中が空です/);
  assert.match(messages[2], /^2 知らないキー nope です/);
  assert.match(messages[3], /^3 not の中が空です/);
  assert.match(messages[4], /^4 ノードはマップ/);
  // 読めなかったノードは、木の他を直しても読んだ値のまま書き戻る
  const doc = readRules(text);
  const fixed = doc.apply(withWhen(doc.model.sections, removeAt(tree, [1])));
  const again = readRules(fixed).model.sections.deny[0].when;
  assert.ok(again !== undefined && again.op === "all");
  assert.equal(again.children.length, 4);
  assert.equal(again.children[0].op, "invalid");
  assert.equal(again.children[3].op, "invalid");
  // 上限: 深さ 9 とノード数 65
  let deep: CondNode = leaf("glob", "x");
  for (let i = 0; i < 8; i += 1) {
    deep = not(deep);
  }
  assert.deepEqual(condProblems(deep).map((p) => p.message), ["深さが 9 あります（上限 8）"]);
  assert.deepEqual(condProblems((deep as { child: CondNode }).child), []);
  const wide = group("any", Array.from({ length: 64 }, (_, i) => leaf("glob", String(i))));
  assert.deepEqual(condProblems(wide).map((p) => p.message), ["ノードが 65 個あります（上限 64）"]);
  assert.deepEqual(condProblems(group("any", Array.from({ length: 63 }, () => leaf("glob", "x")))), []);
});

test("CB-T337 glob / regex と except のルールを条件の木に変換する。except は木の中へ移り、ファイルから消える", () => {
  const text = `version: 1
deny:
  # 理由のコメント
  - id: workspace-root
    match: Write|Edit
    regex: '^{root}[\\\\/]'
    except:
      - regex: '^{root}[\\\\/]wip'
      - glob: '{root}/.claude/worktrees/**'
    message: main では編集しない
  - id: plain
    match: Bash
    glob: "*git push*"
`;
  const doc = readRules(text);
  const [rule, plain] = doc.model.sections.deny;
  assert.deepEqual(rule.exceptNodes, [leaf("regex", "^{root}[\\\\/]wip"), leaf("glob", "{root}/.claude/worktrees/**")]);
  const tree = treeOfPattern(rule.kind, rule.pattern, rule.exceptNodes);
  assert.deepEqual(tree, group("all", [leaf("regex", "^{root}[\\\\/]"), not(group("any", rule.exceptNodes ?? []))]));
  assert.deepEqual(treeOfPattern("glob", "*git push*"), group("all", [leaf("glob", "*git push*")]));
  const converted: RuleForm = { ...rule, pattern: "", when: tree, excepts: undefined, exceptNodes: undefined, dropExcept: true };
  const plainTree: RuleForm = { ...plain, when: treeOfPattern(plain.kind, plain.pattern) };
  const out = doc.apply({ ...doc.model.sections, deny: [converted, plainTree] });
  assert.equal(
    out,
    `version: 1
deny:
  # 理由のコメント
  - id: workspace-root
    match: Write|Edit
    when:
      all:
        - regex: '^{root}[\\\\/]'
        - not:
            any:
              - regex: '^{root}[\\\\/]wip'
              - glob: '{root}/.claude/worktrees/**'
    message: main では編集しない
  - id: plain
    match: Bash
    when:
      all:
        - glob: '*git push*'
`,
  );
  const again = readRules(out).model;
  assert.deepEqual(again.problems, []);
  assert.deepEqual(again.sections.deny[0].when, tree);
  assert.equal(again.sections.deny[0].excepts, undefined);
  // dropExcept が無ければ except は消さない（木と併用できる）
  const kept = doc.apply({ ...doc.model.sections, deny: [{ ...rule, when: group("all", [leaf("regex", "^{root}[\\\\/]")]) }, plain] });
  assert.ok(kept.includes("    except:\n      - regex: '^{root}[\\\\/]wip'"), kept);
});

test("CB-T338 木の操作（子の追加・削除・種別の変更・not で包む／外す）と、左から右へ伸びる置き方", () => {
  // not の中を消すと空になり、子を足すと埋まる。中が空の not は外せない
  const emptied = removeAt(not(leaf("glob", "a")), [0]);
  assert.deepEqual(emptied, not(null));
  assert.deepEqual(unwrapNot(emptied, []), not(null));
  assert.deepEqual(addChild(emptied, [], leaf("regex", "b")), not(leaf("regex", "b")));
  // 根は消せない。葉に子は足せない。all ⇄ any と glob ⇄ regex 以外の変更は効かない
  assert.deepEqual(removeAt(EXPECTED, []), EXPECTED);
  assert.deepEqual(addChild(leaf("glob", "a"), [], leaf("glob", "b")), leaf("glob", "a"));
  assert.deepEqual(changeOp(leaf("glob", "a"), [], "all"), leaf("glob", "a"));
  // 置き方: 根が左、段ごとに右へ。まとめは子の真ん中の高さ。中が空の not には穴の点
  const layout = layoutCond(EXPECTED);
  const at = new Map(layout.places.map((p) => [p.key, p]));
  assert.deepEqual(
    layout.places.map((p) => [p.key, p.op]),
    [
      ["r", "all"],
      ["r.0", "regex"],
      ["r.1", "not"],
      ["r.1.0", "any"],
      ["r.1.0.0", "regex"],
      ["r.1.0.1", "glob"],
    ],
  );
  assert.equal(at.get("r.1.0.1")?.x, 3 * COND_COLUMN);
  assert.equal(at.get("r.1.0")?.y, ((at.get("r.1.0.0")?.y ?? 0) + (at.get("r.1.0.1")?.y ?? 0)) / 2);
  assert.equal(at.get("r")?.y, ((at.get("r.0")?.y ?? 0) + (at.get("r.1")?.y ?? 0)) / 2);
  assert.equal(layout.edges.length, 5);
  assert.deepEqual(
    layoutCond(not(null)).places.map((p) => [p.key, p.op]),
    [
      ["r", "not"],
      ["r.0", "empty"],
    ],
  );
});

test("CB-T339 画面が送り返した木は形を確かめて受け取り、形の違う木を持つルールは受け取らない", () => {
  assert.deepEqual(asCond(JSON.parse(JSON.stringify(EXPECTED))), EXPECTED);
  assert.deepEqual(asCond({ op: "not", child: null }), not(null));
  assert.equal(asCond({ op: "all", children: [{ op: "glob" }] }), undefined);
  assert.equal(asCond({ op: "xor", children: [] }), undefined);
  const { model } = readRules(WHEN_TEXT);
  const raw = JSON.parse(JSON.stringify(model.sections)) as { deny: Record<string, unknown>[] };
  raw.deny[0].when = { op: "all", children: "x" };
  assert.equal(asSections(raw), undefined);
});
