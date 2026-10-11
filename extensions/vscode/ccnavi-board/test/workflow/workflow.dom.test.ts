/**
 * ワークフロー編集タブ（`src/webview/workflow/`）を happy-dom で動かす。
 *
 * 画面は判定しない。線を変えるたびに、元の番号での直接の先行（`--order`）を拡張ホストに送り（`order`）、
 * 実行ファイルの答え（`answer`）が届いたらその振り直し・`problems`・`loose`・`writable` を図に出す。
 * 番号は画面で振り直さない。断られた線（`refused`）は、線を引く前の形に戻す。
 *
 * **大きさの偽物が要る**（`openWorkflow` の `measure`）。React Flow は点の大きさを測れないと線を 1 本も描かず、
 * テストは空の絵を見て通ってしまう。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { DomPage } from "../helpers/dom.js";
import { planOrderSample } from "../helpers/plan-order.js";
import { lastPosted, openWorkflow, openWorkflowData } from "../helpers/workflow.js";

/** その計画の図の中の、点の番号（React Flow の data-id が n<番号> のもの） */
function numbers(dom: DomPage, part: string): string[] {
  return dom
    .all(`.plan-graph[data-part="${part}"] .plan-node`)
    .map((node) => `${node.getAttribute("data-number")}:${node.querySelector(".plan-node-title")?.textContent ?? ""}`);
}

function saveButton(dom: DomPage): HTMLButtonElement {
  return dom.one<HTMLButtonElement>("#save");
}

/** 線の × を押して `order` を送らせ、その頼みの番号を返す */
function removeEdge(dom: DomPage, edge: string): { readonly seq: number; readonly order: unknown } {
  dom.click(dom.one(`button[data-action="plan-remove-edge"][data-edge="${edge}"]`));
  const sent = lastPosted(dom, "order");
  assert.ok(sent !== undefined, "order を送っていない");
  return { seq: sent.seq as number, order: sent.order };
}

test("CB-D161 開くと、計画ごとの図を線を引ける形で出す。提案のままなので未保存ではなく、保存と案に戻すは押せない", async () => {
  const dom = await openWorkflow();
  try {
    assert.deepEqual(
      dom.all(".plan-graph").map((graph) => [graph.getAttribute("data-part"), graph.getAttribute("data-editable")]),
      [
        ["plan", "1"],
        ["feedback", "1"],
      ],
    );
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:docs"]);
    assert.ok(dom.all(".plan-graph .react-flow__edge").length > 0, "線が描かれていない");
    assert.ok(dom.one(".toolbar").textContent?.includes("i0001"));
    assert.equal(saveButton(dom).disabled, true);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="revert"]').disabled, true);
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.deepEqual(dom.posted.map((m) => m.type), ["ready"]);
  } finally {
    await dom.close();
  }
});

test("CB-D162 線の × を押すと、元の番号での先行を送って答えを待つ。待つ間は線を変えられない。start と end の仮の線には × が無い", async () => {
  const dom = await openWorkflow();
  try {
    const edges = dom.all('button[data-action="plan-remove-edge"]').map((b) => b.getAttribute("data-edge"));
    assert.ok(edges.includes("after-1-3"));
    assert.ok(!edges.some((id) => id?.startsWith("start-") || id?.startsWith("end-")), String(edges));
    const sent = removeEdge(dom, "after-1-3");
    assert.deepEqual(sent.order, { plan: { "1": [], "2": [1], "3": [], "4": [2, 3] } });
    await dom.settle();
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 0, "答えを待つ間に線を変えられる");
    assert.match(dom.one("#status").textContent ?? "", /確かめ/);
    assert.equal(saveButton(dom).disabled, true);
  } finally {
    await dom.close();
  }
});

test("CB-D163 答えが届いたら、実行ファイルが振り直した番号で描き直し、未保存にして番号の変わる項を出す。図の場所は動かさない", async () => {
  const dom = await openWorkflow();
  try {
    const placed = dom.one('.react-flow__node[data-id="n3"]').getAttribute("style");
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:実装とテスト", "3:設計", "4:docs"]);
    // 元の 3（実装とテスト）は 2 番になったが、置き場所は前の 3 番の場所のまま
    assert.equal(dom.one('.react-flow__node[data-id="n2"]').getAttribute("style"), placed);
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: true });
    assert.match(dom.one("#workflow-renumbered").textContent ?? "", /設計.*2 → 3/);
    assert.equal(saveButton(dom).disabled, false);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="revert"]').disabled, false);
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 2);
    // 未保存のまま閉じたときに戻すため、線の写しを拡張ホストに覚えさせる
    await new Promise((resolve) => setTimeout(resolve, 400));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "draft")?.order, { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } });
  } finally {
    await dom.close();
  }
});

test("CB-D164 断られた線（循環など）は引く前の形に戻し、理由を出す。古い頼みへの答えは捨てる", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq: seq - 1, answer: planOrderSample("reverse") });
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:docs"], "古い頼みへの答えで描き直した");
    await dom.send({ type: "answer", seq, answer: planOrderSample("cycle") });
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:docs"]);
    assert.match(dom.one("#status").textContent ?? "", /循環/);
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 2, "断られたあとも線を変えられる");
    // 確かめられなかった（実行ファイルが落ちた）ときも、引く前の形のまま理由を出す
    const again = removeEdge(dom, "after-1-2");
    await dom.send({ type: "answer", seq: again.seq, error: "ccnavi --plan-order が失敗しました" });
    assert.match(dom.one("#status").textContent ?? "", /失敗/);
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 2);
  } finally {
    await dom.close();
  }
});

test("CB-D165 順序の検査に error がある答えは保存できない。理由（problems）と終端に当たらない項の印を実行ファイルの答えのまま出す", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-3-4");
    await dom.send({ type: "answer", seq, answer: planOrderSample("loose") });
    assert.equal(saveButton(dom).disabled, true);
    assert.match(dom.one("#workflow-problems").textContent ?? "", /最後の項/);
    assert.match(saveButton(dom).getAttribute("title") ?? "", /順序の検査/);
    assert.equal(dom.one('.plan-node[data-number="3"]').getAttribute("data-loose"), "1");
  } finally {
    await dom.close();
  }
});

test("CB-D166 保存の前に、番号の変わる項と付け替える子の提案（識別子と phase の前後）を見せ、保存すると開いたときのハッシュを添えて送る", async () => {
  const dom = await openWorkflow({ answer: planOrderSample("open", "a".repeat(64)) });
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse", "a".repeat(64)) });
    dom.click(saveButton(dom));
    await dom.settle();
    const review = dom.one(".workflow-review");
    assert.deepEqual(
      dom.all("#review-children li").map((li) => li.textContent),
      ["i0001-02-01 → i0001-03-01（phase: 2 → 3）", "i0001-03-01 → i0001-02-01（phase: 3 → 2）"],
    );
    assert.ok(dom.all("#review-numbers li").length === 2);
    assert.match(review.textContent ?? "", /コメント/);
    dom.click(dom.one('[data-action="cancel-save"]'));
    await dom.settle();
    assert.equal(dom.all(".workflow-review").length, 0);
    assert.equal(lastPosted(dom, "save"), undefined);
    dom.click(saveButton(dom));
    await dom.settle();
    dom.click(dom.one('[data-action="confirm-save"]'));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "save"), { type: "save", order: { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } }, expect: "a".repeat(64) });
    assert.equal(saveButton(dom).disabled, true, "保存中にもう 1 度押せる");
    // 書けなかった（拡張ホストが理由を返した）ら、押せる形に戻して理由を出す
    await dom.send({ type: "failed", message: "子の提案を付け替えられない" });
    assert.match(dom.one("#status").textContent ?? "", /付け替えられない/);
    assert.equal(saveButton(dom).disabled, false);
  } finally {
    await dom.close();
  }
});

test("CB-D167 案に戻すと、往復せずに開いたときの答えに戻る。未保存が消える", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    const asked = dom.posted.filter((m) => m.type === "order").length;
    dom.click(dom.one('[data-action="revert"]'));
    await dom.settle();
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:docs"]);
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: false });
    assert.equal(dom.posted.filter((m) => m.type === "order").length, asked);
  } finally {
    await dom.close();
  }
});

test("CB-D168 提案が外で変わったら差分を出し、取り込むか編集を続けるかを選ばせる。編集を続けても保存はできない", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    await dom.send({ type: "changed", answer: planOrderSample("loose", "b".repeat(64)) });
    const banner = dom.one("#changed");
    assert.match(banner.textContent ?? "", /外で変わった/);
    assert.deepEqual(
      dom.all("#changed-diff tr[data-number]").map((row) => row.getAttribute("data-number")),
      ["4"],
    );
    assert.equal(saveButton(dom).disabled, true);
    dom.click(dom.one('[data-action="keep-editing"]'));
    await dom.settle();
    assert.equal(dom.all("#changed-diff").length, 0);
    assert.match(dom.one("#changed").textContent ?? "", /保存できません/);
    assert.equal(saveButton(dom).disabled, true);
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 2, "編集は続けられる");
    dom.click(dom.one('[data-action="adopt"]'));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "adopt"), { type: "adopt" });
  } finally {
    await dom.close();
  }
});

test("CB-D169 提案が承認待ちから無くなったら（承認・取り下げ）、読むだけにして理由を出す", async () => {
  const dom = await openWorkflow();
  try {
    await dom.send({ type: "gone", reason: "承認済みです。順序を変えるには改版を出してください" });
    assert.match(dom.one("#gone").textContent ?? "", /改版/);
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 0);
    assert.equal(dom.all('button[data-action="plan-remove-edge"]').length, 0);
    assert.equal(saveButton(dom).disabled, true);
  } finally {
    await dom.close();
  }
});

test("CB-D170 読み込めなければ理由と再読込、読み込み中は一言だけ。中身が届けば図を出す", async () => {
  const dom = await openWorkflowData({ kind: "error", error: "ccnavi --plan-order が失敗しました: 親の提案が無い" });
  try {
    assert.match(dom.one(".load-error").textContent ?? "", /親の提案が無い/);
    dom.click(dom.one('[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "reload"), { type: "reload", dirty: false });
    await dom.send({ type: "data", data: { kind: "loading", text: "i0001 のワークフローを読み込み中…" } });
    assert.match(dom.one("#ccnavi-loading").textContent ?? "", /読み込み中/);
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001", answer: planOrderSample("open") } } });
    assert.equal(dom.all(".plan-graph").length, 2);
  } finally {
    await dom.close();
  }
});

test("CB-D171 未保存のまま閉じた画面から戻した線（draft）があれば、開いてすぐその線で答えを頼む", async () => {
  const draft = { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } };
  const dom = await openWorkflow({ draft });
  try {
    assert.deepEqual(lastPosted(dom, "order")?.order, draft);
    const seq = lastPosted(dom, "order")?.seq;
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
  } finally {
    await dom.close();
  }
});

test("CB-D172 改版で子が承認された番号は、入る線に × を出さない（変えられない）。点に印を付ける", async () => {
  const dom = await openWorkflow({ answer: planOrderSample("revision") });
  try {
    assert.equal(dom.one('.plan-node[data-number="1"]').getAttribute("data-locked"), "1");
    const edges = dom.all('button[data-action="plan-remove-edge"]').map((b) => b.getAttribute("data-edge"));
    assert.ok(edges.length > 0);
    assert.ok(!edges.some((id) => id?.endsWith("-1")), String(edges));
  } finally {
    await dom.close();
  }
});

test("CB-D173 並べ直すと、今の線で置き場所を組み直す（番号は変えない）", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    const before = dom.one('.react-flow__node[data-id="n3"]').getAttribute("style");
    dom.click(dom.one('[data-action="relayout"]'));
    await dom.settle();
    assert.notEqual(dom.one('.react-flow__node[data-id="n3"]').getAttribute("style"), before);
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:実装とテスト", "3:設計", "4:docs"]);
  } finally {
    await dom.close();
  }
});
