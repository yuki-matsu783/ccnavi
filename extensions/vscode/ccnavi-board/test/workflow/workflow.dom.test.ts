/**
 * 計画編集タブ（`src/webview/workflow/`）を happy-dom で動かす。
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
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };
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

test("CB-D166 開くと、計画ごとの図を線を引ける形で出す。提案のままなので未保存ではなく、保存と案に戻すは押せない", async () => {
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

test("CB-D167 線の × を押すと、元の番号での先行を送って答えを待つ。待つ間は線を変えられない。start と end の仮の線には × がありません", async () => {
  const dom = await openWorkflow();
  try {
    const edges = dom.all('button[data-action="plan-remove-edge"]').map((b) => b.getAttribute("data-edge"));
    assert.ok(edges.includes("after-1-3"));
    assert.ok(!edges.some((id) => id?.startsWith("start-") || id?.startsWith("end-")), String(edges));
    const sent = removeEdge(dom, "after-1-3");
    assert.deepEqual(sent.order, { plan: { "1": [], "2": [1], "3": [], "4": [2, 3] } });
    await dom.settle();
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 0, "答えを待つ間に線を変えられる");
    assert.match(dom.one("#status").textContent ?? "", /確認/);
    assert.equal(saveButton(dom).disabled, true);
  } finally {
    await dom.close();
  }
});

test("CB-D168 答えが届いたら、実行ファイルが振り直した番号で描き直し、未保存にして番号の変わるステップを出す。図の場所は動かさない", async () => {
  const dom = await openWorkflow();
  try {
    const placed = dom.one('.react-flow__node[data-id="n3"]').getAttribute("style");
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:実装とテスト", "3:設計", "4:docs"]);
    // 元の 3（実装とテスト）は 2 番になったが、置き場所は前の 3 番の場所のまま
    assert.equal(dom.one('.react-flow__node[data-id="n2"]').getAttribute("style"), placed);
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: true, checking: false });
    assert.match(dom.one("#workflow-renumbered").textContent ?? "", /設計.*2 → 3/);
    assert.equal(saveButton(dom).disabled, false);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="revert"]').disabled, false);
    assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 2);
    // 未保存のまま閉じたときに戻すため、線のコピーを拡張ホストに覚えさせる
    await new Promise((resolve) => setTimeout(resolve, 400));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "draft")?.order, { plan: { "1": [], "2": [1, 3], "3": [1], "4": [2] } });
  } finally {
    await dom.close();
  }
});

test("CB-D169 断られた線（循環など）は引く前の形に戻し、理由を出す。古い頼みへの答えは破棄する", async () => {
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

test("CB-D170 順序の検査に error がある答えは保存できない。理由（problems）は図の下に、終端に届かないステップの印は点に、実行ファイルの答えのまま出す", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-3-4");
    await dom.send({ type: "answer", seq, answer: planOrderSample("loose") });
    assert.equal(saveButton(dom).disabled, true);
    assert.match(dom.one('.plan-graph[data-part="plan"] .plan-graph-notes').textContent ?? "", /最後のステップ/);
    assert.match(saveButton(dom).getAttribute("title") ?? "", /順序の検査/);
    assert.equal(dom.one('.plan-node[data-number="3"]').getAttribute("data-loose"), "1");
  } finally {
    await dom.close();
  }
});

test("CB-D171 保存の前に、番号の変わるステップと付け替える子の提案（識別子と phase の前後）を見せ、保存すると開いたときのハッシュを添えて送る", async () => {
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

test("CB-D179 子の提案を付け替えられない理由・改版の錠・計画と食い違う子は、実行ファイルの文のまま一覧に出し、書けなければ保存できない", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    const reverse = planOrderSample("reverse");
    await dom.send({
      type: "answer",
      seq,
      answer: {
        ...reverse,
        writable: false,
        child_problems: ["i0001-02-01 の付け替え先 i0001-03-01 が承認済みチケットと重なる"],
        mismatched: [{ ticket: "i0001-09-01", path: "/ws/x.md", phase: 9, text: "i0001-09-01 の phase: 9 は親の計画（1〜6 番）にありません" }],
      },
    });
    const listed = dom.all("#workflow-problems li").map((li) => li.textContent ?? "");
    assert.ok(listed.some((text) => text.includes("承認済みチケットと重なる")), String(listed));
    assert.ok(listed.some((text) => text.includes("phase: 9")), String(listed));
    assert.equal(saveButton(dom).disabled, true);
    assert.match(saveButton(dom).getAttribute("title") ?? "", /子の提案/);
  } finally {
    await dom.close();
  }
});

test("CB-D172 案に戻すと、往復せずに開いたときの答えに戻る。未保存が消える", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    const asked = dom.posted.filter((m) => m.type === "order").length;
    dom.click(dom.one('[data-action="revert"]'));
    await dom.settle();
    assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:docs"]);
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: false, checking: false });
    assert.equal(dom.posted.filter((m) => m.type === "order").length, asked);
  } finally {
    await dom.close();
  }
});

test("CB-D173 提案が外で変わったら差分を出し、取り込むか編集を続けるかを選ばせる。編集を続けても保存はできない", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq, answer: planOrderSample("reverse") });
    await dom.send({ type: "changed", answer: planOrderSample("loose", "b".repeat(64)) });
    const banner = dom.one("#changed");
    assert.match(banner.textContent ?? "", /外で変わりました/);
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

test("CB-D174 提案が承認待ちから無くなったら（承認・取り下げ）、読むだけにして理由を出す", async () => {
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

test("CB-D175 読み込めなければ理由と再読み込み、読み込み中は一言だけ。中身が届けば図を出す", async () => {
  const dom = await openWorkflowData({ kind: "error", error: "ccnavi --plan-order が失敗しました: 親の提案がありません" });
  try {
    assert.match(dom.one(".load-error").textContent ?? "", /親の提案がありません/);
    dom.click(dom.one('[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "reload"), { type: "reload", dirty: false });
    await dom.send({ type: "data", data: { kind: "loading", text: "i0001 の計画編集を読み込み中…" } });
    assert.match(dom.one("#ccnavi-loading").textContent ?? "", /読み込み中/);
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0001", answer: planOrderSample("open") } } });
    assert.equal(dom.all(".plan-graph").length, 2);
  } finally {
    await dom.close();
  }
});

test("CB-D176 未保存のまま閉じた画面から戻した線（draft）があれば、開いてすぐその線で答えを頼む", async () => {
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

test("CB-D177 改版で子が承認された番号は、入る線に × を出さない（変えられない）。点に印を付ける", async () => {
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

test("CB-D178 並べ直すと、今の線で置き場所を組み直す（番号は変えない）", async () => {
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

/** 吹き出しの案内を最後まで進め、手順のタイトルと、指す先が見つかったか（中ほどに出す文だけの吹き出しでないか）を順に返す */
async function walkTour(dom: DomPage): Promise<string[]> {
  const titles: string[] = [];
  for (let i = 0; i < 20 && dom.all(".tour").length > 0; i += 1) {
    const centered = dom.one(".tour-bubble").classList.contains("center");
    titles.push(`${dom.one("#tour-title").textContent ?? ""}${centered ? "（指す先なし）" : ""}`);
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
  }
  return titles;
}

test("CB-D182 拡張ホストが頼んだらタブの案内を出し、最後まで進めると閉じて tourDone を返す。ヘッダ右上の ? からもう一度出せる", async () => {
  const dom = await openWorkflow();
  try {
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    assert.equal(dom.all(".tour").length, 0, "頼まれるまでは出さない");
    await dom.send({ type: "tour" });
    await dom.settle();
    // 計画のあるタブでも、案内の間は見本を出す（今の計画には触れない）
    assert.equal(dom.all(".tour-sample").length, 1);
    assert.deepEqual(await walkTour(dom), ["図", "直す手段", "この順序に決める", "承認との関係", "案内"]);
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
    // 「?」は案内の一覧を開き、基本の案内を選ぶと最初の手順から始まる
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="intro"]'));
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "図");
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 2);
    // 案内は何も送らない（線を変える頼みも保存も）
    assert.equal(dom.posted.filter((message) => message.type === "order" || message.type === "save").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D183 図を出せない答え（計画が空）でも、案内の間は見本の計画（実行ファイルの答えの形）を描き、閉じたら実際の表示に戻る", async () => {
  const empty = { ...planOrderSample("open"), plans: [] };
  const dom = await openWorkflow({ answer: empty });
  try {
    assert.equal(dom.all(".plan-graph").length, 0);
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour-sample").length, 1, "見本だと分かる帯がありません");
    assert.deepEqual(
      dom.all(".plan-graph").map((graph) => [graph.getAttribute("data-part"), graph.getAttribute("data-ticket")]),
      [
        ["plan", "sample-a"],
        ["feedback", "sample-a"],
      ],
    );
    assert.equal(dom.one("#tour-title").textContent, "図");
    assert.ok(!dom.one(".tour-bubble").classList.contains("center"), "見本の図を指していない");
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all(".plan-graph, .tour-sample").length, 0, "閉じたのに見本が残った");
    assert.match(dom.one(".workflow-graphs").textContent ?? "", /計画（plan:）がありません/);
    assert.equal(dom.posted.filter((message) => message.type === "order" || message.type === "save" || message.type === "dirty").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D186 線を変えたら、答えを待つ間も拡張ホストに「確認している」を知らせる（承認を止める）。答えが届けば、答えの未保存だけになる", async () => {
  const dom = await openWorkflow();
  try {
    const { seq } = removeEdge(dom, "after-1-3");
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: false, checking: true });
    // タイトルの「●」と「未保存」は答えから決める（待つ間はまだ出さない）
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    // 断られた線は引く前のまま。未保存でも確かめ中でもなくなる
    await dom.send({ type: "answer", seq, answer: planOrderSample("cycle") });
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: false, checking: false });
    const again = removeEdge(dom, "after-1-3");
    await dom.send({ type: "answer", seq: again.seq, answer: planOrderSample("reverse") });
    await dom.settle();
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: true, checking: false });
  } finally {
    await dom.close();
  }
});

test("CB-D187 ステップを押すと右の欄に詳細を出す。欄は畳め、畳んだことは覚える", async () => {
  const dom = await openWorkflow();
  try {
    assert.ok(dom.one('.plan-graph[data-part="plan"] .plan-detail-empty').textContent?.includes("ステップを押すと"));
    dom.click(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n2"]'));
    await dom.settle();
    const detail = dom.one('.plan-graph[data-part="plan"] .plan-detail-list');
    assert.equal(detail.getAttribute("data-number"), "2");
    assert.ok(detail.textContent?.includes("設計"));
    assert.equal(dom.one('.plan-graph[data-part="plan"] .plan-node[data-picked="1"] .plan-node-title').textContent, "設計");
    dom.click(dom.one('.plan-graph[data-part="plan"] button[data-action="toggle-plan-detail"]'));
    await dom.settle();
    assert.equal(dom.one('.plan-graph[data-part="plan"] .plan-detail').getAttribute("data-folded"), "1");
    assert.equal(dom.one('.plan-graph[data-part="feedback"] .plan-detail').getAttribute("data-folded"), "1", "ほかの計画の欄も同じ状態");
    assert.ok(dom.all('.plan-graph[data-part="plan"] .plan-detail-list').length > 0, "畳んでも中身は残す");
    dom.click(dom.one('.plan-graph[data-part="plan"] .react-flow__node[data-id="n1"] .react-flow__handle'));
    await dom.settle();
    assert.equal(dom.one('.plan-graph[data-part="plan"] .plan-detail-list').getAttribute("data-number"), "2", "取っ手のクリックでは選び直さない");
  } finally {
    await dom.close();
  }
});

test("CB-D189 図の拡大ボタンで図だけを全画面にし、閉じる・Esc で戻す", async () => {
  const dom = await openWorkflow();
  try {
    const canvas = '.plan-graph[data-part="plan"] .plan-graph-canvas';
    assert.equal(dom.one(canvas).classList.contains("is-max"), false);
    dom.click(dom.one(`${canvas} button[data-action="toggle-maximize"]`));
    await dom.settle();
    assert.ok(dom.one(canvas).classList.contains("is-max"));
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.one(canvas).classList.contains("is-max"), false);
  } finally {
    await dom.close();
  }
});
