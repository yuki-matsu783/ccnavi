/**
 * フロー編集画面の、エージェントの下書きを取り込むところと、エージェントへの依頼のボタン。
 * 拡張ホストの役（下書きを読んで実行ファイルに確かめさせる・依頼の文を組む）は、送るメッセージで真似る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };
import { addNode, patchData, templateFlow, type FlowDoc } from "../../src/core/flow-doc.js";
import { lockedReason } from "../../src/core/flow-view.js";
import { openFlow, savedDoc } from "../helpers/flow.js";

const HASH = "0123456789abcdef".repeat(4);
const OFFER = { draftPath: ".claude/worktrees/i0001/wip/proposals/flows/i0001-01.yml" };

/** 雛形にプロンプトを 1 つ足した下書き */
function drafted(): FlowDoc {
  const added = addNode(templateFlow("i0001-01", "調査"), "prompt", { x: 200, y: 300 });
  return patchData(added.doc, added.id, { prompt: "既存の振る舞いを読む\nそのあと要点をまとめる" });
}

test("CB-D139 提案ありを開くと下書きを頼み、文の前後まで見せた差分から取り込むと編集中に入る。保存に取り込んだハッシュを添える", async () => {
  const dom = await openFlow({ offer: OFFER });
  try {
    assert.match(dom.one("#offer").textContent ?? "", /提案あり/);
    assert.match(dom.one("#offer").textContent ?? "", /wip\/proposals\/flows\/i0001-01\.yml/);
    dom.click(dom.one('[data-action="open-proposal"]'));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "openProposal").length, 1);
    assert.match(dom.one("#proposal-review").textContent ?? "", /確かめています/);
    assert.equal(dom.all('[data-action="import-proposal"]').length, 0, "確かめ終わるまで取り込めない");
    const draft = drafted();
    await dom.send({ type: "proposal", proposal: { doc: draft, hash: HASH, draftPath: OFFER.draftPath } });
    const review = dom.one("#proposal-review");
    // 変わった欄の名前だけでなく、文そのもの（改行も）を見せる
    assert.match(review.textContent ?? "", /足すノード/);
    assert.match(review.textContent ?? "", /中身\.prompt/);
    const after = dom.all(".proposal-after pre").map((e) => e.textContent ?? "");
    assert.ok(after.includes("既存の振る舞いを読む\nそのあと要点をまとめる"), after.join(" / "));
    assert.equal(dom.all("#proposal-dirty").length, 0);
    assert.equal(dom.one('[data-action="import-proposal"]').textContent, "取り込む");
    dom.click(dom.one('[data-action="import-proposal"]'));
    await dom.settle();
    // 取り込んだだけで書かない。未保存になり、図に足したノードが出る
    assert.equal(dom.all("#proposal-review").length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "save").length, 0);
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.all(".react-flow__node").length, 3);
    // 元に戻せる
    assert.ok(!dom.one<HTMLButtonElement>('[data-action="undo"]').disabled);
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedDoc(dom), draft);
    const save = dom.posted.filter((m) => m.type === "save").pop();
    assert.equal(save?.imported, HASH);
    // 保存が通って中身が届いたら、取り込んだ記録は消える（次の保存に添えない）
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001-01", title: "調査", parent: "i0001", flowPath: "x.yml", flowRel: ".ccnavi/approved/flows/i0001-01.yml", exists: true, doc: draft, lock: { locked: false, reason: "" } } } });
    assert.equal(dom.all("#offer").length, 0, "下書きが消えれば提案ありも消える");
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "save").pop()?.imported, undefined);
  } finally {
    await dom.close();
  }
});

test("CB-D140 未保存の変更があれば捨てることを言ってから取り込む。着手中は取り込めない。取り込めない下書きは理由だけ", async () => {
  const dom = await openFlow({ offer: OFFER });
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="open-proposal"]'));
    await dom.settle();
    await dom.send({ type: "proposal", proposal: { doc: drafted(), hash: HASH, draftPath: OFFER.draftPath } });
    assert.match(dom.one("#proposal-dirty").textContent ?? "", /未保存の変更があります/);
    assert.equal(dom.one('[data-action="import-proposal"]').textContent, "未保存の変更を捨てて取り込む");
    // 着手された（錠が掛かった）。取り込みも止まる
    await dom.send({ type: "lock", lock: { locked: true, reason: lockedReason("i0001-01") } });
    assert.ok(dom.one<HTMLButtonElement>('[data-action="import-proposal"]').disabled);
    assert.match(dom.one(".proposal-locked").textContent ?? "", /DENY_TICKET_FLOW_LOCKED/);
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all("#proposal-review").length, 0);
    // 実行ファイルが error を言った下書きは、理由だけで取り込むボタンを出さない
    await dom.send({ type: "lock", lock: { locked: false, reason: "" } });
    dom.click(dom.one('[data-action="open-proposal"]'));
    await dom.settle();
    await dom.send({ type: "proposal", error: "下書きを取り込めません（x.yml）: ノードの id が重なっている" });
    assert.match(dom.one("#proposal-error").textContent ?? "", /id が重なっている/);
    assert.equal(dom.all('[data-action="import-proposal"]').length, 0);
    // 下書きが動いた（消えた）と拡張ホストが知らせれば、帯が消える
    await dom.send({ type: "offer" });
    assert.equal(dom.all("#offer").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D141 依頼のボタンは言葉が届いたときだけ出し、錠が掛かれば隠す。押すと文を頼み、コピー / 新しいセッションで開く で渡す", async () => {
  const none = await openFlow();
  try {
    assert.equal(none.all('[data-action="request-flow"]').length, 0);
  } finally {
    await none.close();
  }
  const dom = await openFlow({ request: "エージェントにフローの作成を頼む" });
  try {
    const button = dom.one('[data-action="request-flow"]');
    assert.equal(button.textContent, "エージェントにフローの作成を頼む");
    dom.click(button);
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "request").length, 1);
    await dom.send({ type: "requestText", prompt: "[ccnavi] ユーザが子チケット i0001-01 のフローの作成を頼んだ" });
    assert.match(dom.one("#request-text pre").textContent ?? "", /フローの作成を頼んだ/);
    dom.click(dom.one('[data-action="request-copy"]'));
    dom.click(dom.one('[data-action="request-open"]'));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "requestCopy").length, 1);
    assert.equal(dom.posted.filter((m) => m.type === "requestOpen").length, 1);
    dom.click(dom.one('[data-action="request-close"]'));
    await dom.settle();
    assert.equal(dom.all("#request-text").length, 0);
    // 下書きが届いた（頼み直す）
    await dom.send({ type: "offer", request: "エージェントにフローを頼み直す", offer: OFFER });
    assert.equal(dom.one('[data-action="request-flow"]').textContent, "エージェントにフローを頼み直す");
    assert.equal(dom.all("#offer").length, 1);
    // 着手された。錠が掛かれば出さない
    await dom.send({ type: "lock", lock: { locked: true, reason: lockedReason("i0001-01") } });
    assert.equal(dom.all('[data-action="request-flow"]').length, 0);
    // 拡張ホストが言葉を外した（着手の前でない）
    await dom.send({ type: "lock", lock: { locked: false, reason: "" } });
    await dom.send({ type: "offer" });
    assert.equal(dom.all('[data-action="request-flow"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D142 取り込んだあとに元に戻して別の編集をした・取り込んだ中身を直したときは、保存に取り込みのハッシュを添えない", async () => {
  const dom = await openFlow({ offer: OFFER });
  try {
    const importDraft = async (): Promise<void> => {
      dom.click(dom.one('[data-action="open-proposal"]'));
      await dom.settle();
      await dom.send({ type: "proposal", proposal: { doc: drafted(), hash: HASH, draftPath: OFFER.draftPath } });
      dom.click(dom.one('[data-action="import-proposal"]'));
      await dom.settle();
    };
    // 取り込み → 元に戻す → 別の編集 → 保存
    await importDraft();
    dom.click(dom.one('[data-action="undo"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="add-node"][data-type="skill"]'));
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "save").pop()?.imported, undefined);
    await dom.send({ type: "failed", message: "やめた" });
    // 取り込み → 直す → 保存
    await importDraft();
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "save").pop()?.imported, undefined);
  } finally {
    await dom.close();
  }
});

test("CB-D143 並び順だけ違う下書きはそう言って取り込め、値の種類の違い（\"1.0.0\" と 1）は種類の表示で見える", async () => {
  const dom = await openFlow({ offer: OFFER });
  try {
    dom.click(dom.one('[data-action="open-proposal"]'));
    await dom.settle();
    const base = templateFlow("i0001-01", "調査");
    const reordered = { ...base, nodes: [...base.nodes].reverse() } as FlowDoc;
    await dom.send({ type: "proposal", proposal: { doc: reordered, hash: HASH, draftPath: OFFER.draftPath } });
    assert.equal(dom.all("#proposal-problem").length, 0);
    assert.match(dom.one("#proposal-order-only").textContent ?? "", /並び順だけ/);
    assert.ok(!dom.one<HTMLButtonElement>('[data-action="import-proposal"]').disabled);
    // 型の違いは種類の表示で見える
    dom.click(dom.one('[data-action="cancel-proposal"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="open-proposal"]'));
    await dom.settle();
    const typed = { ...base, version: 1 } as unknown as FlowDoc;
    await dom.send({ type: "proposal", proposal: { doc: typed, hash: HASH, draftPath: OFFER.draftPath } });
    const kinds = dom.all(".proposal-value-kind").map((e) => e.textContent);
    assert.ok(kinds.includes("数"), kinds.join(","));
  } finally {
    await dom.close();
  }
});
