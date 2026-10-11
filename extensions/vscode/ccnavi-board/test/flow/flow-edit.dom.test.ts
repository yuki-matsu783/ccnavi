/**
 * フロー編集画面（React）の編集の道具を happy-dom で動かす。元に戻す・やり直す、コピー・貼り付け・複製、
 * ミニマップ、保存前の差分の一覧、未保存のまま閉じた編集を戻して開くこと、実行ファイルの答え（渡る手順・warn・候補）を見る。
 *
 * 線を引く途中の断り（`isValidConnection`）はドラッグが要るので、ここでは見ない（規則は `flow-edit-ops.test.ts`
 * の `canConnect`、図の上は README の手動確認）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement, HTMLInputElement, HTMLTextAreaElement } from "happy-dom" with { "resolution-mode": "import" };
import { addNode, removeNode, renameNode, templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { lockedReason, type FlowChecks } from "../../src/core/flow-view.js";
import type { DomPage } from "../helpers/dom.js";
import { openFlow, savedDoc } from "../helpers/flow.js";

const CTRL = { ctrlKey: true } as const;
const CTRL_SHIFT = { ctrlKey: true, shiftKey: true } as const;

/** キーを離す（keyup）。離さないと React Flow は押したままと読み、Shift での選び足しができない */
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
  let doc = templateFlow("i0001-01-01", "調査");
  doc = addNode(doc, "prompt", { x: 260, y: 300 }).doc;
  return { ...doc, connections: [
    { id: "c1", from: "start", to: "prompt-1", fromPort: "output", toPort: "input" },
    { id: "c2", from: "prompt-1", to: "end", fromPort: "output", toPort: "input" },
  ] };
}

test("CB-D137 元に戻す・やり直すはボタンと Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y で効く。戻して読み込んだ中身に戻れば未保存が消える", async () => {
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

test("CB-D138 欄に続けて打った字は元に戻す 1 件にまとまる。フォーカスが外れたら区切る", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    // 時計を止める（実時間に頼ると、遅い機械で打ち込みの間が 1 秒を超えてまとまりが切れる）
    (dom.window as unknown as { ccnaviClock: () => number }).ccnaviClock = () => 1000;
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
    await dom.send({ type: "lock", lock: { locked: true, reason: lockedReason("i0001-01-01") } });
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
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001-01-01", title: "調査", parent: "i0001", flowPath: "x.yml", flowRel: ".ccnavi/approved/flows/i0001-01-01.yml", exists: true, doc: three(), lock: { locked: false, reason: "" } } } });
    assert.ok(button(dom, "undo").disabled);
    assert.ok(button(dom, "redo").disabled);
    assert.ok(!dirty(dom));
  } finally {
    await dom.close();
  }
});

test("CB-D126 選んだノードを Ctrl+C でコピーして Ctrl+V で貼ると、線ごと新しい id で足され、貼ったものが選ばれる。Ctrl+D は複製。開始はコピーしない", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    assert.ok(button(dom, "copy-nodes").disabled);
    assert.ok(button(dom, "paste-nodes").disabled);
    // 開始だけではコピーできない
    dom.click(dom.one('.react-flow__node[data-id="start"]'));
    await dom.settle();
    assert.ok(button(dom, "copy-nodes").disabled);
    assert.ok(button(dom, "duplicate-nodes").disabled);
    dom.key("c", undefined, CTRL);
    release(dom, "c");
    await dom.settle();
    assert.match(dom.one("#status").textContent ?? "", /開始はコピーしません/);
    // プロンプトと終了を選んでコピーする
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    dom.key("Shift");
    await dom.settle();
    dom.click(dom.one('.react-flow__node[data-id="end"]'));
    await dom.settle();
    dom.key("c", undefined, CTRL);
    await dom.settle();
    assert.match(dom.one("#status").textContent ?? "", /ノードを 2 個、線を 1 本コピーしました/);
    assert.ok(!dirty(dom), "コピーしただけでは未保存にしない");
    dom.key("v", undefined, CTRL);
    await dom.settle();
    assert.deepEqual(nodeIds(dom), ["start", "end", "prompt-1", "end-1", "prompt-2"]);
    assert.equal(dom.all(".react-flow__edge").length, 3);
    assert.deepEqual(dom.all(".react-flow__node.selected").map((n) => n.getAttribute("data-id")).sort(), ["end-1", "prompt-2"]);
    assert.ok(dirty(dom));
    // 複製（ボタン）。いま選んでいる貼り付けたものが増える
    dom.click(button(dom, "duplicate-nodes"));
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 7);
    assert.equal(dom.all(".react-flow__edge").length, 4);
    // Ctrl+D も複製
    dom.key("d", undefined, CTRL);
    await dom.settle();
    assert.equal(dom.all(".react-flow__node").length, 9);
    // 貼り付けも複製も 1 回で元に戻す 1 件
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

test("CB-D130 閉じる前の編集（draft）が渡れば、それを開いて未保存を立て、未保存の間はコピーを拡張ホストに覚えさせる", async () => {
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
    // 戻す先（読み込んだ中身）は draft ではなく doc。名前を元に戻せば未保存が消え、覚えたコピーも消させる
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

/** 確かめ直しを頼むまでの間（`App.tsx` の CHECK_MS）より少し長く待つ */
async function waitCheck(dom: DomPage): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 900));
  await dom.settle();
}

function checksAsked(dom: DomPage): { seq: number; doc: FlowDoc }[] {
  return dom.posted.filter((m) => m.type === "check").map((m) => ({ seq: m.seq as number, doc: m.doc as FlowDoc }));
}

const SHOWN = ".claude/worktrees/i0001/.ccnavi/approved/flows/i0001-01-01.yml";

test("CB-D131 実行ファイルの warn は画面の注意と並べて出し、開始が無いことは実行ファイルの答えに寄せて 1 度だけ言う。渡る手順はプレビューに出す", async () => {
  const checks: FlowChecks = {
    warns: [`${SHOWN}: start が無い（どこから始めるかが決まらない）`],
    rendered: ["1. [prompt] プロンプト", "2. [end] 終了"],
  };
  const doc = removeNode(three(), "start");
  const dom = await openFlow({ doc, checks });
  try {
    const items = dom.all("#flow-notices li");
    assert.deepEqual(items.filter((li) => li.getAttribute("data-source") === "exe").map((li) => li.textContent), checks.warns);
    assert.ok(!items.some((li) => /開始（start）のノードがありません/.test(li.textContent ?? "")), "画面の注意と二重に出さない");
    assert.equal(dom.one("#flow-preview pre.flow-rendered").textContent, "1. [prompt] プロンプト\n2. [end] 終了");
    assert.equal(dom.all("#flow-preview-checking").length, 0);
    // 開いたままでは確かめ直さない（答えが指す中身のまま）
    await waitCheck(dom);
    assert.deepEqual(checksAsked(dom), []);
    // 直すと、止まってから確かめ直しを頼む。その間は前の答えを出したまま、そう言う
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    assert.equal(dom.all("#flow-preview-checking").length, 1);
    await waitCheck(dom);
    const asked = checksAsked(dom);
    assert.equal(asked.length, 1);
    assert.equal(asked[0].doc.nodes.length, 3);
    // 古い番号の答えは捨てる
    await dom.send({ type: "checked", seq: asked[0].seq - 1, checks: { warns: ["古い"], rendered: [] } });
    assert.equal(dom.all("#flow-preview-checking").length, 1);
    assert.doesNotMatch(dom.one("#flow-notices").textContent ?? "", /古い/);
    // 答えが届く。並べられなければその理由を言う
    await dom.send({ type: "checked", seq: asked[0].seq, checks: { warns: [`${SHOWN}: start が無い（どこから始めるかが決まらない）`, `${SHOWN}: start から届かないノードがある: スキル`], rendered: null } });
    assert.equal(dom.all("#flow-preview-checking").length, 0);
    assert.equal(dom.all('#flow-notices li[data-source="exe"]').length, 2);
    assert.equal(dom.all("#flow-preview pre.flow-rendered").length, 0);
    assert.match(dom.one("#flow-preview").textContent ?? "", /並べられませんでした/);
    // 確かめられなければ理由を出し、前の答えは残す
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    await waitCheck(dom);
    const again = checksAsked(dom);
    await dom.send({ type: "checked", seq: again[again.length - 1].seq, error: "実行ファイル（--lint --flow）が、フローを読めないと返しました: …" });
    assert.match(dom.one("#flow-preview-error").textContent ?? "", /読めないと返しました/);
    assert.equal(dom.all('#flow-notices li[data-source="exe"]').length, 2);
  } finally {
    await dom.close();
  }
});

test("CB-D132 答えの無いフロー（まだ無いファイル）は開いてすぐ確かめを頼む。古い実行ファイル（rendered が無い）ならそう言う", async () => {
  const dom = await openFlow({ exists: false });
  try {
    assert.match(dom.one("#flow-preview").textContent ?? "", /まだ実行ファイルで確かめていません/);
    await waitCheck(dom);
    const asked = checksAsked(dom);
    assert.equal(asked.length, 1);
    await dom.send({ type: "checked", seq: asked[0].seq, checks: { warns: [] } });
    assert.match(dom.one("#flow-preview").textContent ?? "", /実行ファイルが古いため、担当に渡る手順を表示できません/);
    // 答えが届いても、開始が 2 つあることは画面が言う（実行ファイルは言わない）
    dom.click(dom.one('[data-action="add-node"][data-type="start"]'));
    await dom.settle();
    assert.match(dom.one("#flow-notices").textContent ?? "", /開始（start）のノードが 2 つあります/);
  } finally {
    await dom.close();
  }
});

test("CB-D133 サブエージェントの種類とスキルの名前は候補から選べ、組み込み・プロジェクト・候補に無い、を添える。候補が無ければふつうの欄", async () => {
  let doc = three();
  doc = addNode(doc, "subAgent", { x: 500, y: 300 }).doc;
  doc = addNode(doc, "skill", { x: 700, y: 300 }).doc;
  const checks: FlowChecks = {
    warns: [],
    rendered: [],
    candidates: { agents: [{ name: "Plan", source: "builtin" }, { name: "reviewer", source: "project" }], skills: [{ name: "commit", source: "project" }] },
  };
  const dom = await openFlow({ doc, checks });
  try {
    dom.click(dom.one('.react-flow__node[data-id="subAgent-1"]'));
    await dom.settle();
    const field = (): HTMLInputElement => dom.one<HTMLInputElement>("#inspector input.f-builtInType");
    assert.equal(field().getAttribute("list"), "flow-agent-candidates");
    assert.deepEqual(
      dom.all("#flow-agent-candidates option").map((o) => [o.getAttribute("value"), o.getAttribute("label")]),
      [
        ["Plan", "組み込み"],
        ["reviewer", "プロジェクト（.claude/ の下）"],
      ],
    );
    assert.equal(dom.all("#inspector .candidate-source").length, 0, "空なら何も添えない");
    dom.type(field(), "reviewer");
    await dom.settle();
    assert.equal(dom.one("#inspector .candidate-source").getAttribute("data-source"), "project");
    // 自由入力も残す。候補に無ければそう言う（止めはしない）
    dom.type(field(), "my-agent");
    await dom.settle();
    assert.match(dom.one("#inspector .candidate-source.missing").textContent ?? "", /候補にありません/);
    assert.equal(field().value, "my-agent");
    // スキル
    dom.click(dom.one('.react-flow__node[data-id="skill-1"]'));
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>("#inspector input.f-name[list]").getAttribute("list"), "flow-skill-candidates");
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name[list]"), "commit");
    await dom.settle();
    assert.equal(dom.one("#inspector .candidate-source").textContent, "プロジェクト（.claude/ の下）");
  } finally {
    await dom.close();
  }
  const bare = await openFlow({ doc });
  try {
    bare.click(bare.one('.react-flow__node[data-id="subAgent-1"]'));
    await bare.settle();
    assert.equal(bare.one<HTMLInputElement>("#inspector input.f-builtInType").getAttribute("list"), null);
  } finally {
    await bare.close();
  }
});

test("CB-D134 確かめを頼んで答えを待つ間に直したら、届いた古い答えは使わず確かめ直している（のまま）と言う", async () => {
  const dom = await openFlow({ doc: three(), checks: { warns: [], rendered: ["1. 最初"] } });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    await waitCheck(dom);
    const first = checksAsked(dom);
    assert.equal(first.length, 1);
    // 答えを待つ間に、もう 1 つ直す
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    await dom.send({ type: "checked", seq: first[0].seq, checks: { warns: ["古いコピーの答え"], rendered: ["1. 古い"] } });
    assert.equal(dom.all("#flow-preview-checking").length, 1, "古い答えで確かめ終わったことにしない");
    assert.equal(dom.one("#flow-preview pre.flow-rendered").textContent, "1. 最初");
    assert.doesNotMatch(dom.one("body").textContent ?? "", /古いコピーの答え/);
    // 今の中身に対する答えは使う
    await waitCheck(dom);
    const second = checksAsked(dom);
    assert.equal(second.length, 2);
    assert.equal(second[1].doc.nodes.length, 5);
    await dom.send({ type: "checked", seq: second[1].seq, checks: { warns: [], rendered: ["1. 新しい"] } });
    assert.equal(dom.all("#flow-preview-checking").length, 0);
    assert.equal(dom.one("#flow-preview pre.flow-rendered").textContent, "1. 新しい");
  } finally {
    await dom.close();
  }
});

test("CB-D135 外で変わった知らせは最初の 1 回だけ履歴を空にし、送り直し（タブを表に戻した）では空にし直さない", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    await dom.send({ type: "changed" });
    assert.ok(button(dom, "undo").disabled);
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    assert.ok(!button(dom, "undo").disabled);
    await dom.send({ type: "changed" });
    assert.ok(!button(dom, "undo").disabled, "送り直しで、知らせの後に積んだ履歴を消さない");
  } finally {
    await dom.close();
  }
});

test("CB-D136 順序だけ変わって未保存のときも保存前の一覧を出し、順序だけ変わったと言う。時計を進めれば打ち込みは別の 1 件", async () => {
  const doc = three();
  const reordered: FlowDoc = { ...doc, nodes: [...doc.nodes].reverse() };
  const dom = await openFlow({ doc, draft: reordered, reviewSave: true });
  try {
    assert.ok(dirty(dom));
    dom.click(button(dom, "save"));
    await dom.settle();
    assert.match(dom.one("#review-order-only").textContent ?? "", /順序だけが変わりました/);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0);
    dom.click(button(dom, "cancel-save"));
    await dom.settle();
    // 打ち込みの間が 1 秒を超えれば別の 1 件
    let clock = 1000;
    (dom.window as unknown as { ccnaviClock: () => number }).ccnaviClock = () => clock;
    dom.click(dom.one('.react-flow__node[data-id="prompt-1"]'));
    await dom.settle();
    const area = (): HTMLTextAreaElement => dom.one<HTMLTextAreaElement>("#inspector textarea.f-prompt");
    dom.type(area(), "a");
    await dom.settle();
    clock += 1500;
    dom.type(area(), "ab");
    await dom.settle();
    dom.click(button(dom, "undo"));
    await dom.settle();
    assert.equal(area().value, "a");
  } finally {
    await dom.close();
  }
});

test("CB-D154 繰り返しを足すと出口が 2 つ並び、右の欄で名前・条件・上限を直せる。出口の増減と名前の編集はできず、上限は数のまま保存する", async () => {
  const dom = await openFlow({ doc: three() });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="loop"]'));
    await dom.settle();
    const node = dom.one('.react-flow__node[data-id="loop-1"]');
    assert.deepEqual(
      Array.from(node.querySelectorAll(".flow-port")).map((port) => [port.getAttribute("data-port"), port.textContent]),
      [["branch-0", "繰り返す"], ["branch-1", "抜ける"]],
    );
    assert.equal(node.querySelector(".flow-badge"), null);
    assert.equal(dom.one<HTMLInputElement>("#inspector input.f-maxIterations").value, "3");
    assert.equal(dom.one<HTMLInputElement>("#inspector input.f-maxIterations").getAttribute("min"), "1");
    assert.equal(dom.all('#inspector [data-action="add-branch"]').length, 0);
    assert.equal(dom.all('#inspector [data-action="remove-branch"]').length, 0);
    assert.ok(dom.all<HTMLInputElement>("#inspector input.f-branch-label").every((input) => input.disabled));
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-name"), "直す");
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-condition"), "テストが落ちる");
    dom.type(dom.one<HTMLInputElement>("#inspector input.f-maxIterations"), "5");
    await dom.settle();
    dom.click(button(dom, "save"));
    await dom.settle();
    const saved = savedDoc(dom).nodes.find((n) => n.id === "loop-1");
    assert.equal(saved?.name, "直す");
    assert.deepEqual(saved?.data, {
      label: "",
      condition: "テストが落ちる",
      maxIterations: 5,
      branches: [{ id: "body", label: "繰り返す" }, { id: "done", label: "抜ける" }],
    });
  } finally {
    await dom.close();
  }
});
