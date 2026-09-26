/**
 * フロー編集画面（React）の編集の道具を happy-dom で動かす。元に戻す・やり直す、写す・貼る・複製、
 * ミニマップ、保存前の差分の一覧、未保存のまま閉じた編集を戻して開くこと、を見る。
 *
 * 線を引く途中の断り（`isValidConnection`）はドラッグが要るので、ここでは見ない（規則は `flow-edit-ops.test.ts`
 * の `canConnect`、図の上は README の手動確認）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement, HTMLInputElement, HTMLTextAreaElement } from "happy-dom" with { "resolution-mode": "import" };
import { addNode, renameNode, templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { lockedReason } from "../../src/core/flow-view.js";
import type { DomPage } from "../helpers/dom.js";
import { openFlow, savedDoc } from "../helpers/flow.js";

const CTRL = { ctrlKey: true } as const;
const CTRL_SHIFT = { ctrlKey: true, shiftKey: true } as const;

/** 鍵を放す（keyup）。放さないと React Flow は押したままと読み、Shift での選び足しが効かない */
function release(dom: DomPage, name: string): void {
  const Keyboard = (dom.window as unknown as { KeyboardEvent: new (type: string, init: unknown) => unknown }).KeyboardEvent;
  (dom.document as unknown as { dispatchEvent: (event: unknown) => boolean }).dispatchEvent(new Keyboard("keyup", { key: name, bubbles: true }));
}

function button(dom: DomPage, action: string): HTMLButtonElement {
  return dom.one<HTMLButtonElement>(`[data-action="${action}"]`);
}

function dirty(dom: DomPage): boolean {
  return !dom.one("#dirty").classList.contains("hidden");
}

function nodeIds(dom: DomPage): (string | null)[] {
  return dom.all(".react-flow__node").map((node) => node.getAttribute("data-id"));
}

/** 開始 → プロンプト → 終了 */
function three(): FlowDoc {
  let doc = templateFlow("i0001-01", "調査");
  doc = addNode(doc, "prompt", { x: 260, y: 300 }).doc;
  return { ...doc, connections: [
    { id: "c1", from: "start", to: "prompt-1", fromPort: "output", toPort: "input" },
    { id: "c2", from: "prompt-1", to: "end", fromPort: "output", toPort: "input" },
  ] };
}

test("CB-D123 元に戻す・やり直すはボタンと Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y で効く。戻して読み込んだ中身に戻れば未保存が消える", async () => {
  const dom = await openFlow();
  try {
    assert.ok(button(dom, "undo").disabled);
    assert.ok(button(dom, "redo").disabled);
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 4);
    assert.ok(dirty(dom));
    assert.ok(!button(dom, "undo").disabled);
    // ボタンで 1 つ戻す
    dom.click(button(dom, "undo"));
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1"]);
    assert.ok(!button(dom, "redo").disabled);
    // Ctrl+Z でもう 1 つ戻すと読み込んだ中身と同じ。未保存が消え、保存も押せない
    dom.key("z", undefined, CTRL);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end"]);
    assert.ok(!dirty(dom));
    assert.ok(button(dom, "save").disabled);
    assert.ok(button(dom, "undo").disabled);
    assert.deepEqual(dom.posted.filter((m) => m.type === "dirty").map((m) => m.dirty), [true, false]);
    // やり直す（Ctrl+Shift+Z と Ctrl+Y）
    dom.key("Z", undefined, CTRL_SHIFT);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1"]);
    dom.key("y", undefined, CTRL);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1", "skill-1"]);
    assert.ok(dirty(dom));
    assert.ok(button(dom, "redo").disabled);
    // 欄の中の Ctrl+Z は欄に任せる（図は戻さない）
    dom.click(dom.one('.react-flow__node[data-id="skill-1"]'));
    await dom.settle();
    dom.key("z", dom.one<HTMLInputElement>("#inspector input.f-name"), CTRL);
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 4);
  } finally {
    await dom.close();
  }
});

test("CB-D124 欄に続けて打った字は元に戻す 1 件にまとまる。フォーカスが外れたら区切る", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    const area = (): HTMLTextAreaElement => dom.one<HTMLTextAreaElement>("#inspector textarea.f-prompt");
    for (const text of ["資", "資料", "資料を", "資料を読む"]) {
      dom.type(area(), text);
      await dom.settle();
    }
    assert.equal(area().value, "資料を読む");
    // フォーカスが外れた（区切り）あとに名前を打つ
    area().dispatchEvent(new dom.window.FocusEvent("focusout", { bubbles: true }));
    await dom.settle();
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name"), "読む");
    await dom.settle();
    // 1 回目の戻しで名前だけ戻り、2 回目で打った 4 字がまとめて戻る
    dom.click(button(dom, "undo"));
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>("#inspector input.f-name").value, "プロンプト");
    assert.equal(area().value, "資料を読む");
    dom.click(button(dom, "undo"));
    await dom.settle();
    assert.equal(area().value, "");
    assert.ok(button(dom, "undo").disabled);
    assert.ok(!dirty(dom));
  } finally {
    await dom.close();
  }
});

test("CB-D125 読むだけのときは元に戻す・やり直す・貼る・複製が効かない。中身が届くと履歴は空になる", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    await dom.send({ type: "lock", lock: { locked: true, reason: lockedReason("i0001-01") } });
    assert.ok(button(dom, "undo").disabled);
    dom.key("z", undefined, CTRL);
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 4, "錠の間は戻さない");
    await dom.send({ type: "lock", lock: { locked: false, reason: "" } });
    assert.ok(!button(dom, "undo").disabled);
    // 外で変わったと知らされたら、編集は残して履歴だけ空にする
    await dom.send({ type: "changed" });
    assert.ok(button(dom, "undo").disabled);
    assert.equal(dom.all(".react-flow__node").length, 4);
    assert.ok(dirty(dom));
    // 中身が届いたら、履歴は空で未保存も消える
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001-01", title: "調査", parent: "i0001", flowPath: "x.yml", flowRel: ".ccnavi/approved/flows/i0001-01.yml", exists: true, doc: three(), lock: { locked: false, reason: "" } } } });
    assert.ok(button(dom, "undo").disabled);
    assert.ok(button(dom, "redo").disabled);
    assert.ok(!dirty(dom));
  } finally {
    await dom.close();
  }
});

test("CB-D126 選んだノードを Ctrl+C で写して Ctrl+V で貼ると、線ごと新しい id で足され、貼ったものが選ばれる。Ctrl+D は複製。開始は写さない", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    assert.ok(button(dom, "copy-nodes").disabled);
    assert.ok(button(dom, "paste-nodes").disabled);
    // 開始だけでは写せない
    dom.click(dom.one('.react-flow__node[data-id="start"]'));
    await dom.settle();
    assert.ok(button(dom, "copy-nodes").disabled);
    assert.ok(button(dom, "duplicate-nodes").disabled);
    dom.key("c", undefined, CTRL);
    release(dom, "c");
    await dom.settle();
    assert.match(dom.one("#status").textContent ?? "", /開始は写さない/);
    // プロンプトと終了を選んで写す
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    dom.key("Shift");
    await dom.settle();
    dom.click(dom.one('.react-flow__node[data-id="end"]'));
    await dom.settle();
    dom.key("c", undefined, CTRL);
    await dom.settle();
    assert.match(dom.one("#status").textContent ?? "", /ノードを 2 個、線を 1 本写した/);
    assert.ok(!dirty(dom), "写しただけでは未保存にしない");
    dom.key("v", undefined, CTRL);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1", "end-1", "prompt-2"]);
    assert.equal(dom.all(".react-flow__edge").length, 3);
    assert.deepEqual(dom.all(".react-flow__node.selected").map((n) => n.getAttribute("data-id")).sort(), ["end-1", "prompt-2"]);
    assert.ok(dirty(dom));
    // 複製（ボタン）。いま選んでいる貼ったものが増える
    dom.click(button(dom, "duplicate-nodes"));
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 7);
    assert.equal(dom.all(".react-flow__edge").length, 4);
    // Ctrl+D も複製
    dom.key("d", undefined, CTRL);
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 9);
    // 貼るのも複製も 1 回で元に戻す 1 件
    dom.key("z", undefined, CTRL);
    await dom.settle();
    dom.key("z", undefined, CTRL);
    await dom.settle();
    dom.key("z", undefined, CTRL);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1"]);
    dom.click(button(dom, "save"));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0, "読み込んだ中身と同じなので保存は押せない");
  } finally {
    await dom.close();
  }
});

test("CB-D127 ミニマップは既定で出し、ボタンで隠す・出す。隠したことは画面の状態に覚える", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    assert.equal(dom.all(".react-flow__minimap").length, 1);
    const toggle = button(dom, "toggle-minimap");
    assert.equal(toggle.getAttribute("aria-pressed"), "true");
    dom.click(toggle);
    await dom.settle();
    assert.equal(dom.all(".react-flow__minimap").length, 0);
    assert.equal(button(dom, "toggle-minimap").getAttribute("aria-pressed"), "false");
    assert.deepEqual(dom.state(), { minimap: false });
    dom.click(button(dom, "toggle-minimap"));
    await dom.settle();
    assert.equal(dom.all(".react-flow__minimap").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D128 保存前の確かめが有効なら、読み込んだ時点からの差分を一覧で見せ、「保存する」で送る。「やめる」と Esc では送らない", async () => {
  const dom = await openFlow({ doc: three(), reviewSave: true });
  try {
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name"), "読む");
    await dom.settle();
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    dom.click(button(dom, "save"));
    await dom.settle();
    const review = dom.one("#save-review");
    assert.match(review.textContent ?? "", /足したノード（1）/);
    assert.match(dom.one('.review-list[data-kind="added-nodes"]').textContent ?? "", /スキル（skill-1）/);
    assert.match(dom.one('.review-list[data-kind="changed-nodes"]').textContent ?? "", /読む（prompt-1）.*: 名前/);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0, "確かめる前は送らない");
    // やめる
    dom.click(button(dom, "cancel-save"));
    await dom.settle();
    assert.equal(dom.all("#save-review").length, 0);
    // Esc でも閉じる
    dom.click(button(dom, "save"));
    await dom.settle();
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all("#save-review").length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0);
    // 保存する
    dom.click(button(dom, "save"));
    await dom.settle();
    dom.click(button(dom, "confirm-save"));
    await dom.settle();
    assert.equal(savedDoc(dom).nodes.length, 4);
    assert.equal(dom.all("#save-review").length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "reviewSave").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D129 「次から確かめずに保存する」を付けて保存すると設定を外すよう送り、以後は一覧を出さずに保存する", async () => {
  const dom = await openFlow({ doc: three(), reviewSave: true });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    dom.click(button(dom, "save"));
    await dom.settle();
    dom.click(dom.one<HTMLInputElement>("#save-review input.f-review-skip"));
    await dom.settle();
    dom.click(button(dom, "confirm-save"));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "reviewSave"), [{ type: "reviewSave", value: false }]);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 1);
    await dom.send({ type: "failed", message: "外で変更されている" });
    dom.click(button(dom, "save"));
    await dom.settle();
    assert.equal(dom.all("#save-review").length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 2);
  } finally {
    await dom.close();
  }
});

test("CB-D130 閉じる前の編集（draft）が渡れば、それを開いて未保存を立て、未保存の間は写しを拡張ホストに控えさせる", async () => {
  const draft = renameNode(three(), "prompt-1", "閉じる前の編集");
  const dom = await openFlow({ doc: three(), draft });
  try {
    assert.ok(dirty(dom));
    assert.match(dom.one('.react-flow__node[data-id="prompt-1"]').textContent ?? "", /閉じる前の編集/);
    assert.ok(!button(dom, "save").disabled);
    await new Promise((resolve) => setTimeout(resolve, 400));
    await dom.settle();
    const drafts = dom.posted.filter((m) => m.type === "draft");
    assert.ok(drafts.length >= 1);
    assert.deepEqual(drafts[drafts.length - 1].doc, draft);
    // 戻す先（読み込んだ中身）は draft ではなく doc。名前を元に戻せば未保存が消え、控えも消させる
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name"), "プロンプト");
    await dom.settle();
    assert.ok(!dirty(dom));
    assert.deepEqual(dom.posted.filter((m) => m.type === "draft").pop(), { type: "draft", doc: null });
  } finally {
    await dom.close();
  }
});
