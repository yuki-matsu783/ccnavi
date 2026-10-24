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
  comparePaths,
  exceptBlocker,
  toWhenBlocker,
  treeOfPattern,
  unwrapNot,
  wrapGroup,
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
              - regex: '^{root}[\\\\/]wip[\\\\/]handoff(?:[\\\\/]|$)'
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
  not(group("any", [leaf("regex", "^{root}[\\\\/]wip[\\\\/]handoff(?:[\\\\/]|$)"), leaf("glob", "{root}/.claude/worktrees/**")])),
]);

function withWhen(sections: Sections, when: CondNode): Sections {
  return { ...sections, deny: [{ ...sections.deny[0], when }, ...sections.deny.slice(1)] };
}

test("CB-T333 when を組み合わせ条件として読み、畳んだ行の要約にする。組み合わせ条件を持たないルールには欄がありません", () => {
  const { model } = readRules(WHEN_TEXT);
  assert.deepEqual(model.problems, []);
  assert.deepEqual(model.sections.deny[0].when, EXPECTED);
  assert.equal(model.sections.allow[0].when, undefined);
  assert.deepEqual(condProblems(EXPECTED), []);
  assert.equal(condSummary(EXPECTED), "all(regex ^{root}[\\\\/], not(any(2)))");
  assert.equal(condSummary(leaf("glob", "")), "glob （空）");
});

test("CB-T334 組み合わせ条件を変えなければ、when はコメントも書き方もそのまま戻る（画面を往復しても）", () => {
  const doc = readRules(WHEN_TEXT);
  assert.equal(doc.apply(doc.model.sections), WHEN_TEXT);
  const sent = asSections(JSON.parse(JSON.stringify(doc.model.sections)));
  assert.ok(sent);
  assert.deepEqual(sent.deny[0].when, EXPECTED);
  assert.equal(doc.apply(sent), WHEN_TEXT);
  // 他の欄だけ変えても、組み合わせ条件には触らない
  const edited = doc.apply({ ...doc.model.sections, deny: [{ ...doc.model.sections.deny[0], id: "root-guard" }] });
  assert.equal(edited, WHEN_TEXT.replace("id: workspace-root", "id: root-guard"));
});

test("CB-T335 組み合わせ条件を直すと when を書き直し、変えていないノードのコメントと他のキーは残る", () => {
  const doc = readRules(WHEN_TEXT);
  const s = doc.model.sections;
  // 個別条件の文字を変える: その個別条件だけが変わり、引用符は元のまま
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
  // 足した個別条件は単引用符。message の折り返しも元のまま
  const added = doc.apply(withWhen(s, addChild(EXPECTED, [], leaf("glob", "*.md"))));
  assert.ok(added.includes("        - glob: '*.md'\n"), added);
  assert.ok(added.includes("    message: >-\n      main では編集しない。\n      ワークツリーで直す。\n"), added);
  assert.ok(added.startsWith("version: 1\ndeny:\n  # ルート直下は止め、置き場だけ除く\n"), added);
  assert.deepEqual(readRules(added).model.sections.deny[0].when, addChild(EXPECTED, [], leaf("glob", "*.md")));
  // not で包んだ・外した組み合わせ条件も読み直せる。PyYAML と同じ形（YAML として読めること）も見る
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
  assert.match(model.problems[0], /when と glob \/ regex を両方指定しています/);
  const tree = model.sections.deny[0].when;
  assert.ok(tree);
  const messages = condProblems(tree).map((p) => `${p.path.join(".")} ${p.message}`);
  assert.equal(messages.length, 5, messages.join("\n"));
  assert.match(messages[0], /^0 ノードのキーはちょうど 1 つです（2 個あります）/);
  assert.match(messages[1], /^1 any の中が空です/);
  assert.match(messages[2], /^2 知らないキー nope です/);
  assert.match(messages[3], /^3 not の中が空です/);
  assert.match(messages[4], /^4 ノードはマップ/);
  // 読めなかったノードは、組み合わせ条件の他を直しても読んだ値のまま書き戻る
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

test("CB-T337 glob / regex と except のルールを組み合わせ条件に変換する。except は組み合わせ条件の中へ移り、ファイルから消える", () => {
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
        - glob: "*git push*"
`,
  );
  const again = readRules(out).model;
  assert.deepEqual(again.problems, []);
  assert.deepEqual(again.sections.deny[0].when, tree);
  assert.equal(again.sections.deny[0].excepts, undefined);
  // dropExcept が無ければ except は消さない（組み合わせ条件と併用できる）
  const kept = doc.apply({ ...doc.model.sections, deny: [{ ...rule, when: group("all", [leaf("regex", "^{root}[\\\\/]")]) }, plain] });
  assert.ok(kept.includes("    except:\n      - regex: '^{root}[\\\\/]wip'"), kept);
});

test("CB-T338 組み合わせ条件の操作（子の追加・削除・種別の変更・not で包む／外す）と、左から右へ伸びる置き方", () => {
  // not の中を消すと空になり、子を足すと埋まる。中が空の not は外せない
  const emptied = removeAt(not(leaf("glob", "a")), [0]);
  assert.deepEqual(emptied, not(null));
  assert.deepEqual(unwrapNot(emptied, []), not(null));
  assert.deepEqual(addChild(emptied, [], leaf("regex", "b")), not(leaf("regex", "b")));
  // 最上位のノードは消せない。個別条件に子は足せない。all ⇄ any と glob ⇄ regex 以外の変更は効かない
  assert.deepEqual(removeAt(EXPECTED, []), EXPECTED);
  assert.deepEqual(addChild(leaf("glob", "a"), [], leaf("glob", "b")), leaf("glob", "a"));
  assert.deepEqual(changeOp(leaf("glob", "a"), [], "all"), leaf("glob", "a"));
  // 置き方: 最上位のノードが左、段ごとに右へ。演算子は子の真ん中の高さ。中が空の not には穴の点
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

test("CB-T339 画面が送り返した組み合わせ条件は形を確認して受け取り、形の違う組み合わせ条件を持つルールは受け取らない", () => {
  assert.deepEqual(asCond(JSON.parse(JSON.stringify(EXPECTED))), EXPECTED);
  assert.deepEqual(asCond({ op: "not", child: null }), not(null));
  assert.equal(asCond({ op: "all", children: [{ op: "glob" }] }), undefined);
  assert.equal(asCond({ op: "xor", children: [] }), undefined);
  const { model } = readRules(WHEN_TEXT);
  const raw = JSON.parse(JSON.stringify(model.sections)) as { deny: Record<string, unknown>[] };
  raw.deny[0].when = { op: "all", children: "x" };
  assert.equal(asSections(raw), undefined);
});

test("CB-T340 個別条件の値を PyYAML が文字列以外に読む語へ差し替えると、引用符のない個別条件でも引用符で囲む", () => {
  const text = `version: 1
deny:
  - id: plain-leaf
    match: Bash
    when:
      any:
        - glob: foo # 行末
        - regex: bar
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.ok(tree);
  const out = doc.apply(withWhen(doc.model.sections, setPattern(setPattern(tree, [0], "on"), [1], "baz")));
  assert.ok(out.includes("        - glob: 'on' # 行末\n"), out);
  // 別の型に読まれない語は元の書き方（引用符なし）のまま
  assert.ok(out.includes("        - regex: baz\n"), out);
  assert.deepEqual(readRules(out).model.sections.deny[0].when, group("any", [leaf("glob", "on"), leaf("regex", "baz")]));
  // 読めない（引用符の無い yes）個別条件を置き換えても、PyYAML が真偽値に読む形では書かない
  const bad = readRules(text.replace("glob: foo", "glob: yes"));
  const badTree = bad.model.sections.deny[0].when;
  assert.ok(badTree);
  const fixed = bad.apply(withWhen(bad.model.sections, setPattern(replaceLeaf(badTree), [0], "yes")));
  assert.ok(fixed.includes("- glob: 'yes'"), fixed);
});

/** 先頭の子（読めない個別条件）を空の glob に置き換える（画面の「glob に置き換える」と同じ） */
function replaceLeaf(tree: CondNode): CondNode {
  return tree.op === "any" ? group("any", [leaf("glob"), ...tree.children.slice(1)]) : tree;
}

test("CB-T341 引用符のない個別条件の値が YAML 1.1 で文字列以外になる語なら、読み込めないノードとして誤りに挙げる（except も）", () => {
  const text = `version: 1
deny:
  - id: ambiguous
    match: Bash
    when:
      any:
        - glob: yes
        - regex: '0755'
        - glob: "on"
    except:
      - glob: off
    message: m
`;
  const rule = readRules(text).model.sections.deny[0];
  assert.ok(rule.when);
  const problems = condProblems(rule.when);
  assert.equal(problems.length, 1, JSON.stringify(problems));
  assert.deepEqual(problems[0].path, [0]);
  assert.match(problems[0].message, /glob の値 yes は引用符で囲んでください/);
  assert.equal(rule.exceptNodes?.[0].op, "invalid");
  // 読めないノードは読んだ値のまま書き戻す
  const doc = readRules(text);
  assert.equal(doc.apply(doc.model.sections), text);
});

test("CB-T342 「組み合わせ条件に変換」で、元のパターンと except の各要素のコメント（前置き・行末）が対応する個別条件に残る", () => {
  const text = `version: 1
deny:
  - id: commented
    match: Write
    # 本体の理由
    regex: '^{root}[\\\\/]' # 本体の行末
    # 除くものの説明
    except:
      # 1 つ目の理由
      - regex: '^{root}[\\\\/]wip' # 1 つ目の行末
      # 2 つ目の理由
      - glob: '{root}/.claude/**'
    message: m
`;
  const doc = readRules(text);
  const rule = doc.model.sections.deny[0];
  const converted: RuleForm = { ...rule, pattern: "", when: treeOfPattern(rule.kind, rule.pattern, rule.exceptNodes), excepts: undefined, exceptNodes: undefined, dropExcept: true };
  const out = doc.apply({ ...doc.model.sections, deny: [converted] });
  assert.equal(
    out,
    `version: 1
deny:
  - id: commented
    match: Write
    when:
      all:
        # 本体の理由
        - regex: '^{root}[\\\\/]' # 本体の行末
        # 除くものの説明
        - not:
            any:
              # 1 つ目の理由
              - regex: '^{root}[\\\\/]wip' # 1 つ目の行末
              # 2 つ目の理由
              - glob: '{root}/.claude/**'
    message: m
`,
  );
  assert.deepEqual(readRules(out).model.sections.deny[0].when, converted.when);
  // 変換したあとに個別条件を直しても、コメントはその個別条件に付いたまま
  const edited = doc.apply({ ...doc.model.sections, deny: [{ ...converted, when: setPattern(converted.when ?? leaf("glob"), [0], "^{root}") }] });
  assert.ok(edited.includes("        # 本体の理由\n        - regex: '^{root}' # 本体の行末\n"), edited);
});

test("CB-T343 except がリストでない・要素が glob / regex 1 つでない・空なら「組み合わせ条件に変換」できない理由を返す", () => {
  const rulesOf = (except: string) =>
    readRules(`version: 1
deny:
  - id: x
    match: Write
    glob: '*'
${except}
    message: m
`).model.sections.deny[0];
  assert.equal(exceptBlocker(rulesOf("").exceptNodes), undefined);
  assert.equal(exceptBlocker(rulesOf("    except:\n      - glob: '*.md'\n      - regex: 'x'").exceptNodes), undefined);
  assert.match(exceptBlocker(rulesOf("    except: '*.md'").exceptNodes) ?? "", /except の 1 件目が読み込めません（except の値はリストです）/);
  assert.match(exceptBlocker(rulesOf("    except:\n      - glob: '*.md'\n        regex: x").exceptNodes) ?? "", /1 件目が読み込めません/);
  assert.match(exceptBlocker(rulesOf("    except:\n      - glob: '*.md'\n      - any: [{glob: a}]").exceptNodes) ?? "", /2 件目が glob \/ regex 1 つのマップではありません/);
  assert.match(exceptBlocker(rulesOf("    except:\n      - '*.md'").exceptNodes) ?? "", /1 件目が読み込めません/);
  assert.match(exceptBlocker(rulesOf("    except: []").exceptNodes) ?? "", /空のリスト/);
  assert.match(exceptBlocker(rulesOf("    except:\n      - glob: ''").exceptNodes) ?? "", /glob が空/);
});

test("CB-T344 子が 10 個以上でも、図の点は組み合わせ条件の順（番号の数の順）に並ぶ", () => {
  const wide = group("any", Array.from({ length: 12 }, (_, i) => leaf("glob", String(i))));
  assert.deepEqual(
    layoutCond(wide).places.map((p) => p.key),
    ["r", ...Array.from({ length: 12 }, (_, i) => `r.${i}`)],
  );
  assert.ok(comparePaths([2], [10]) < 0);
  assert.ok(comparePaths([1], [1, 0]) < 0);
  assert.equal(comparePaths([1, 2], [1, 2]), 0);
});

test("CB-T345 個別条件や not の最上位のノードを all / any で包んで組み合わせへ広げられ、包んだノードのコメントは残る", () => {
  const text = `version: 1
deny:
  - id: leaf-root
    match: Write
    when:
      # 個別条件の理由
      glob: '*.pem' # 行末
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.deepEqual(tree, leaf("glob", "*.pem"));
  assert.ok(tree);
  const wrapped = wrapGroup(tree, [], "any");
  assert.deepEqual(wrapped, group("any", [leaf("glob", "*.pem")]));
  assert.deepEqual(wrapGroup(not(leaf("glob", "a")), [0], "all"), not(group("all", [leaf("glob", "a")])));
  const widened = addChild(wrapped, [], leaf("glob", "*.key"));
  const out = doc.apply(withWhen(doc.model.sections, widened));
  assert.ok(out.includes("# 個別条件の理由"), out);
  assert.ok(out.includes("glob: '*.pem' # 行末"), out);
  assert.deepEqual(readRules(out).model.sections.deny[0].when, widened);
  // not を外しても、中身のコメントは残る
  const negated = readRules(text.replace("      # 個別条件の理由\n      glob: '*.pem' # 行末", "      not:\n        # 個別条件の理由\n        glob: '*.pem' # 行末"));
  const unwrapped = negated.apply(withWhen(negated.model.sections, leaf("glob", "*.pem")));
  assert.ok(unwrapped.includes("glob: '*.pem' # 行末"), unwrapped);
});

test("CB-T346 子を削除してから別の子を直しても、削除した子のコメントは直した子に付かない", () => {
  const text = `version: 1
deny:
  - id: three
    match: Write
    when:
      any:
        # A の理由
        - glob: '*.pem'
        # B の理由
        - glob: '*/secrets/*'
        # C の理由
        - glob: '*.key'
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.ok(tree);
  // B を消して、C の文字を直す
  const next = setPattern(removeAt(tree, [1]), [1], "*.keys");
  const out = doc.apply(withWhen(doc.model.sections, next));
  assert.ok(!out.includes("B の理由"), out);
  assert.ok(out.includes("        # C の理由\n        - glob: '*.keys'\n"), out);
  assert.ok(out.includes("        # A の理由\n        - glob: '*.pem'\n"), out);
  // 消した子と別の種類の子を足したときも、消した子のノードを使い回さない
  const swapped = addChild(removeAt(tree, [1]), [], group("all", [leaf("glob", "x")]));
  const out2 = doc.apply(withWhen(doc.model.sections, removeAt(swapped, [1])));
  assert.ok(!out2.includes("B の理由"), out2);
  assert.ok(!out2.includes("C の理由"), out2);
  // 直しただけ（数が同じ）なら、位置のとおりに適用する
  const renamed = doc.apply(withWhen(doc.model.sections, setPattern(tree, [1], "*/vault/*")));
  assert.ok(renamed.includes("        # B の理由\n        - glob: '*/vault/*'\n"), renamed);
});

test("CB-T347 子を削除して似ていない文字へ別の子を直しても、削除した子のコメントは他の子へ移らない", () => {
  const text = `version: 1
deny:
  - id: three
    match: Write
    when:
      any:
        - glob: 'A' # A
        - glob: 'B' # B
        - glob: 'C' # C
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.ok(tree);
  // A を消して、B を zzz に直す
  const out = doc.apply(withWhen(doc.model.sections, setPattern(removeAt(tree, [0]), [0], "zzz")));
  assert.ok(!out.includes("# A"), out);
  assert.ok(out.includes("glob: 'C' # C"), out);
  // A を消して、C を直す（消されていない子は元の相対順で対応する）
  const out2 = doc.apply(withWhen(doc.model.sections, setPattern(removeAt(tree, [0]), [1], "zzz")));
  assert.ok(!out2.includes("# A"), out2);
  assert.ok(out2.includes("glob: 'B' # B"), out2);
  assert.ok(out2.includes("glob: 'zzz' # C"), out2);
  // A を消して末尾に足しても、足した子に A のコメントは付かない
  const out3 = doc.apply(withWhen(doc.model.sections, addChild(removeAt(tree, [0]), [], leaf("glob", "D"))));
  assert.ok(!out3.includes("# A"), out3);
});

test("CB-T348 先頭の子を削除すると、その子の前置きコメントも消える（新しい先頭に残らない）", () => {
  const text = `version: 1
deny:
  - id: three
    match: Write
    when:
      any:
        # A の理由
        - glob: 'A'
        - glob: 'B'
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.ok(tree);
  const out = doc.apply(withWhen(doc.model.sections, removeAt(tree, [0])));
  assert.ok(!out.includes("A の理由"), out);
  assert.deepEqual(readRules(out).model.sections.deny[0].when, group("any", [leaf("glob", "B")]));
  // 先頭を残して 2 番目を消したときは、先頭の前置きコメントは残る
  const kept = doc.apply(withWhen(doc.model.sections, removeAt(tree, [1])));
  assert.ok(kept.includes("# A の理由\n        - glob: 'A'"), kept);
});

test("CB-T349 最上位でないノードを not / all / any で包む・not を解除とき、そのノードと中の子のコメントは残る", () => {
  const text = `version: 1
deny:
  - id: nested
    match: Write
    when:
      all:
        - glob: 'A' # A
        # B の理由
        - glob: 'B' # B
        # G の理由
        - any:
            - glob: 'x' # x
            - glob: 'y' # y
        # N の理由
        - not:
            glob: 'n' # n
    message: m
`;
  const doc = readRules(text);
  const tree = doc.model.sections.deny[0].when;
  assert.ok(tree);
  const check = (next: CondNode, keep: readonly string[]): void => {
    const out = doc.apply(withWhen(doc.model.sections, next));
    for (const c of keep) {
      assert.ok(out.includes(c), `${c}\n${out}`);
    }
    assert.deepEqual(readRules(out).model.sections.deny[0].when, next);
  };
  const all = ["# A", "# B", "B の理由", "G の理由", "# x", "# y", "N の理由", "# n"];
  check(wrapNot(tree, [1]), all);
  check(wrapGroup(tree, [1], "any"), all);
  check(wrapGroup(tree, [2], "all"), all);
  check(wrapGroup(tree, [2], "any"), all);
  check(wrapNot(tree, [2]), all);
  check(unwrapNot(tree, [3]), all);
  check(wrapGroup(tree, [3], "any"), all);
  // 最上位のノードの all / any を包んでも残る
  check(wrapGroup(tree, [], "any"), all);
  check(wrapGroup(tree, [], "all"), all);
  check(wrapNot(tree, []), all);
});

test("CB-T351 glob と regex を両方書いたルールは読み込みの誤りに挙げ、「組み合わせ条件に変換」させない（regex を黙って落とさない）", () => {
  const doc = readRules(`version: 1
deny:
  - id: both
    match: Write
    glob: '*.md'
    regex: 'x'
    message: m
  - id: one
    match: Write
    glob: '*.md'
    message: m
`);
  assert.ok(doc.model.problems.some((p) => p.includes("both") && p.includes("glob と regex を両方")), doc.model.problems.join("\n"));
  const [both, one] = doc.model.sections.deny;
  assert.equal(both.bothPatterns, true);
  assert.equal(one.bothPatterns, undefined);
  assert.match(toWhenBlocker(both) ?? "", /glob と regex を両方指定しています/);
  assert.equal(toWhenBlocker(one), undefined);
  // 画面を往復しても印は残る
  const back = asSections(JSON.parse(JSON.stringify(doc.model.sections)));
  assert.equal(back?.deny[0].bothPatterns, true);
});

test("CB-T352 値が空の glob と regex の組は、実行ファイルと同じく併記の誤りにしない", () => {
  const doc = readRules(`version: 1
deny:
  - id: empty-glob
    match: Write
    glob: ''
    regex: 'x'
    message: m
`);
  assert.ok(!doc.model.problems.some((p) => p.includes("glob と regex を両方")), doc.model.problems.join("\n"));
  assert.equal(doc.model.sections.deny[0].bothPatterns, undefined);
});
