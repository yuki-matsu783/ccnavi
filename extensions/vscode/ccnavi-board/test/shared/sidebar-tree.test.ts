/** サイドパネルのツリーの中身（`core/sidebar-tree.ts`）。件数と分類は実行ファイルの`inbox`のまま並べる */
import { test } from "node:test";
import assert from "node:assert/strict";
import { sidebarTree, type SidebarNode } from "../../src/core/sidebar-tree.js";
import type { BoardJson, InboxJson } from "../../src/core/model.js";
import { fixture } from "../helpers/fixture.js";

const EMPTY_INBOX: InboxJson = { approval: [], review: [], notice: [], handoff: [], flow_draft: [] };

function withInbox(inbox: Partial<InboxJson>): BoardJson {
  return { ...fixture(), inbox: { ...EMPTY_INBOX, ...inbox } };
}

function find(nodes: readonly SidebarNode[], id: string): SidebarNode | undefined {
  for (const n of nodes) {
    if (n.id === id) {
      return n;
    }
    const inner = find(n.children, id);
    if (inner !== undefined) {
      return inner;
    }
  }
  return undefined;
}

test("CB-T1282 見出しはユーザ待ち・進行中の親・画面の順。種類は件数つきで、0件の種類は出さない。バッジは出した件数の合計", () => {
  const tree = sidebarTree({
    board: withInbox({
      approval: ["i0001-02-03"],
      review: [{ parent: "i0001", phase: 2 }],
      notice: ["i0001-02-02"],
      handoff: [
        { tree: "<root>", count: 2 },
        { tree: "<root>/.claude/worktrees/i0001", count: 1 },
      ],
    }),
    tickets: true,
  });
  assert.deepEqual(
    tree.nodes.map((n) => n.label),
    ["ユーザ待ち", "進行中の親", "画面"],
  );
  const waiting = tree.nodes[0];
  assert.deepEqual(
    waiting.children.map((n) => [n.label, n.description]),
    [
      ["承認待ち", "1"],
      ["レビュー待ち", "1"],
      ["注意", "1"],
      ["取り込み待ち", "3"],
      ["片付け", "MRの状況を確認"],
    ],
  );
  // 取り込み待ちはツリーごとの数の和。フローの下書きは0件なので出ない
  assert.equal(tree.badge, 6);
  assert.equal(waiting.description, "6");
  assert.equal(find(tree.nodes, "flow-draft"), undefined);
});

test("CB-T1283 行を押すと、該当の画面と場所を開く（承認・レビュー完了・カード・取り込み待ちのツリー・フロー編集・MRの状況を確認）", () => {
  const tree = sidebarTree({
    board: withInbox({
      approval: ["i0001-02-03"],
      review: [{ parent: "i0001", phase: 2 }],
      notice: ["i0001-02-02"],
      handoff: [{ tree: "<ROOT>/.claude/worktrees/i0001", count: 1 }],
      flow_draft: ["i0001-02-04"],
    }),
    tickets: true,
  });
  const approval = find(tree.nodes, "approval:i0001-02-03");
  assert.equal(approval?.label, "i0001-02-03 子 i0001-02-03");
  assert.equal(approval?.description, "子");
  assert.deepEqual(approval?.action, { kind: "board", target: { kind: "approve", ticket: "i0001-02-03" } });
  const review = find(tree.nodes, "review:i0001:2");
  assert.equal(review?.label, "i0001 フェーズ2（設計）");
  assert.deepEqual(review?.action, { kind: "board", target: { kind: "decide", parent: "i0001", phase: 2 } });
  assert.deepEqual(find(tree.nodes, "notice:i0001-02-02")?.action, { kind: "board", target: { kind: "select", ticket: "i0001-02-02" } });
  // ツリーはルートのパスで引く（大文字小文字は区別しない）。キーは取り込み待ち画面のもの
  const handoff = find(tree.nodes, "handoff:<ROOT>/.claude/worktrees/i0001");
  assert.equal(handoff?.label, "ワークツリーi0001");
  assert.equal(handoff?.description, "1件");
  assert.deepEqual(handoff?.action, { kind: "handoff", tree: "worktree:i0001" });
  assert.deepEqual(find(tree.nodes, "flow-draft:i0001-02-04")?.action, { kind: "flow", ticket: "i0001-02-04" });
  assert.deepEqual(find(tree.nodes, "retire")?.action, { kind: "board", target: { kind: "mergeCheck" } });
});

test("CB-T1284 進行中の親は、進行中の列の親を局面の文つきで並べ、押すとカードを選ぶ。画面には取り込み待ちを含む7つが並ぶ", () => {
  const tree = sidebarTree({ board: withInbox({}), tickets: true });
  const progress = find(tree.nodes, "progress");
  assert.deepEqual(
    progress?.children.map((n) => [n.label, n.description]),
    [["i0001 親", "作業中・フェーズ2 設計"]],
  );
  assert.deepEqual(progress?.children[0].action, { kind: "board", target: { kind: "select", ticket: "i0001" } });
  const screens = find(tree.nodes, "screens");
  assert.deepEqual(
    screens?.children.map((n) => n.label),
    ["チケット管理", "プロジェクト管理", "ルール管理", "リスク管理", "フェーズ管理", "テンプレート管理", "取り込み待ち"],
  );
  assert.deepEqual(screens?.children[6].action, { kind: "command", command: "ccnaviBoard.openHandoff" });
  // ユーザ待ちが無くても片付けの行は出す（数えない）。バッジは出さない
  assert.equal(tree.badge, 0);
  assert.deepEqual(
    find(tree.nodes, "waiting")?.children.map((n) => n.id),
    ["retire"],
  );
});

test("CB-T1285 チケット制御が無効なら、チケットに関わる行を出さない。取り込み待ちと、プロジェクト管理・ルール管理は残る", () => {
  const tree = sidebarTree({
    board: withInbox({
      approval: ["i0001-02-03"],
      review: [{ parent: "i0001", phase: 2 }],
      notice: ["i0001-02-02"],
      handoff: [{ tree: "<root>", count: 1 }],
      flow_draft: ["i0001-02-04"],
    }),
    tickets: false,
  });
  assert.deepEqual(
    tree.nodes.map((n) => n.label),
    ["ユーザ待ち", "画面"],
  );
  assert.deepEqual(
    tree.nodes[0].children.map((n) => n.label),
    ["取り込み待ち"],
  );
  assert.equal(tree.badge, 1);
  assert.deepEqual(
    find(tree.nodes, "screens")?.children.map((n) => n.label),
    ["プロジェクト管理", "ルール管理", "取り込み待ち"],
  );
  // 何も無ければ「ありません」の1行
  const none = sidebarTree({ board: withInbox({}), tickets: false });
  assert.deepEqual(
    none.nodes[0].children.map((n) => [n.label, n.action]),
    [["ありません", undefined]],
  );
});

test("CB-T1286 答えがまだ無い・読めないときは、ユーザ待ちの下にそう書き、バッジを出さない。画面の入口は出す", () => {
  const loading = sidebarTree({ tickets: true });
  assert.deepEqual(
    loading.nodes[0].children.map((n) => n.label),
    ["読み込み中…"],
  );
  assert.equal(loading.badge, 0);
  const failed = sidebarTree({ error: "ccnavi --explain --jsonが失敗しました: x", tickets: true });
  assert.equal(failed.nodes[0].children[0].label, "読み込めません");
  assert.equal(failed.nodes[0].children[0].tooltip, "ccnavi --explain --jsonが失敗しました: x");
  assert.equal(failed.nodes[1].label, "画面");
  assert.equal(failed.nodes[1].children.length, 7);
});
