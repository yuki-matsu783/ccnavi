/**
 * フロー編集画面の案内。「?」の一覧・案内の間の見本・実際の編集に触れないこと・閉じたら戻ることを、happy-dom で見る。
 * 指す先（`data-tour`）が全手順で画面にあることは、ルール管理の案内（`rules.dom.test.ts` の CB-D193）と同じ形で確かめる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

import { connectionsOf, isGroup } from "../../src/core/flow-doc.js";
import { lockedReason } from "../../src/core/flow-view.js";
import { parseFlowSample, sceneOf } from "../../src/core/tour-flow.js";
import type { DomPage } from "../helpers/dom.js";
import { openFlow, openPage, page } from "../helpers/flow.js";
import { loadTour } from "../helpers/tour.js";

const tour = loadTour("flow");
const sample = parseFlowSample(tour.sample);

/** 「?」から案内を選んで始める */
async function start(dom: DomPage, course: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${course}"]`));
  await dom.settle();
}

/** 案内を最後まで進める。手順ごとに `each` を呼ぶ（手順の画面が整うのを待ってから） */
async function walk(dom: DomPage, each: (index: number) => void): Promise<void> {
  let index = 0;
  while (dom.all(".tour").length > 0) {
    await dom.settle();
    each(index);
    index += 1;
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
  }
}

function nodeIds(dom: DomPage): string[] {
  return dom.all(".react-flow__node").map((node) => node.getAttribute("data-id") ?? "");
}

test("CB-D260 「?」は案内の一覧を開き、選んだ案内だけが始まる。基本の案内が先頭で、閉じたら tourDone だけを返す", async () => {
  const dom = await openFlow();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all<HTMLButtonElement>('[data-action="tour-course"]');
    assert.deepEqual(
      items.map((item) => item.dataset.course),
      tour.courses.map((course) => course.id),
    );
    assert.equal(items[0].dataset.course, "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    const before = dom.posted.length;
    dom.click(items[1]);
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    assert.equal(dom.all(".tour").length, 1);
    assert.equal(dom.one("#tour-title").textContent, tour.courses[1].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.slice(before), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D261 基本の案内は、今のタイトルと順序のまま（部品箱・図・欄・保存・案内）で、拡張ホストが送る tour でも始まる", async () => {
  const dom = await openFlow();
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    const titles: string[] = [];
    await walk(dom, () => titles.push(dom.one("#tour-title").textContent ?? ""));
    assert.deepEqual(titles, ["部品箱", "図", "欄", "保存", "案内"]);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D262 全部の案内の全部の手順で、指す先が画面にあり（吹き出しが中ほどに出ない）、見本の図が描かれ、実際のフローは出ない。余計な送信もありません", async () => {
  for (const course of tour.courses) {
    const dom = await openFlow();
    try {
      const before = dom.posted.length;
      await start(dom, course.id);
      await walk(dom, (index) => {
        const step = course.steps[index];
        const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面にありません`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
        // 実際のフロー（bareFlow の start / end だけ）ではなく、手順の見本を描く
        const scene = sceneOf(sample, step.scene);
        const doc = scene.flow.doc;
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
        assert.equal(dom.one(".flow-ticket strong").textContent, sample.page.ticket, at);
        assert.deepEqual(nodeIds(dom).sort(), doc.nodes.map((node) => node.id).sort(), `${at}: 図のノードが見本（${scene.name}）と違う`);
        const drawn = connectionsOf(doc).filter((c) => doc.nodes.some((n) => n.id === c.from && !isGroup(n)) && doc.nodes.some((n) => n.id === c.to && !isGroup(n))).length;
        assert.equal(dom.all(".react-flow__edge").length, drawn, `${at}: 図の線が見本と違う`);
        for (const node of dom.all(".react-flow__node")) {
          assert.notEqual((node as unknown as { style: { visibility: string } }).style.visibility, "hidden", `${at}: 測れていない点がある`);
        }
      });
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
      // 閉じたら実際のフローへ戻る。送ったのは tourDone だけ（dirty・draft・check・save・request などを送らない）
      assert.deepEqual(nodeIds(dom).sort(), ["end", "start"], `${course.id}: 閉じたあと実際のフローに戻らない`);
      assert.equal(dom.all(".banner.tour-sample").length, 0);
      assert.equal(dom.one(".flow-ticket strong").textContent, "i0001-01-01");
      assert.equal(dom.all("#dirty:not(.hidden)").length, 0, "案内が未保存にした");
      assert.deepEqual(dom.posted.slice(before), [{ type: "tourDone" }], course.id);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D263 案内の間は、押す・キーで編集しても何も動かず、何も送らない。閉じたあとも実際のフローは変わらず未保存にもならない", async () => {
  const dom = await openFlow();
  try {
    const before = dom.posted.length;
    await start(dom, "build");
    // 覆いは実機では押せないが、ここでは直に押して、画面の側が守っていることを見る
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    dom.click(dom.one('[data-action="save"]'));
    dom.click(dom.one('[data-action="request-flow"]'));
    dom.click(dom.one('[data-action="open-flow"]'));
    dom.click(dom.one('[data-action="toggle-minimap"]'));
    dom.click(dom.one('[data-action="toggle-palette"]'));
    dom.key("d", undefined, { ctrlKey: true });
    dom.key("z", undefined, { ctrlKey: true });
    dom.key("v", undefined, { ctrlKey: true });
    await dom.settle();
    assert.deepEqual(dom.posted.slice(before), []);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.slice(before), [{ type: "tourDone" }]);
    assert.deepEqual(nodeIds(dom).sort(), ["end", "start"]);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
    assert.equal(dom.one('[data-action="toggle-minimap"]').getAttribute("aria-pressed"), "true", "ミニマップの表示が案内で変わった");
  } finally {
    await dom.close();
  }
});

test("CB-D264 実際に編集中の内容は、案内の間は出ず、閉じたあとに残っている（選んだノードも選んだまま）。案内の間に dirty などを送り直さない", async () => {
  const dom = await openFlow();
  try {
    dom.click(dom.one('[data-action="add-node"][data-type="prompt"]'));
    await dom.settle();
    assert.equal(dom.one("#inspector").getAttribute("data-selected"), "node");
    assert.deepEqual(nodeIds(dom).sort(), ["end", "prompt-1", "start"]);
    const dirtyPosts = dom.posted.filter((message) => message.type === "dirty").length;
    assert.equal(dirtyPosts, 1);
    const before = dom.posted.length;
    await start(dom, "intro");
    await dom.settle();
    // 案内の間は、編集中の内容でなく見本。未保存の印も見本の scene（基本の案内の 1 番目の手順は未保存でない）
    assert.ok(!nodeIds(dom).includes("prompt-1"));
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(nodeIds(dom).sort(), ["end", "prompt-1", "start"], "閉じたあと編集中の内容に戻らない");
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1, "閉じたあと未保存の印が戻らない");
    assert.equal(dom.one("#inspector").getAttribute("data-selected"), "node");
    assert.equal(dom.all(".react-flow__node.selected").length, 1, "閉じたあと選んでいたノードが選ばれていない");
    assert.deepEqual(
      dom.posted.slice(before).filter((message) => message.type !== "draft"),
      [{ type: "tourDone" }],
      "案内の間・直後に余計なものを送った",
    );
  } finally {
    await dom.close();
  }
});

test("CB-D265 scene が図の選びと欄を替える（複数選択・グループ・線）。グループ化の案内は、選んだ 2 つで「グループ化」が押せる", async () => {
  const dom = await openFlow();
  try {
    await start(dom, "bulk");
    const picked: string[] = [];
    const inspector: string[] = [];
    const group: boolean[] = [];
    await walk(dom, () => {
      picked.push(
        dom
          .all(".react-flow__node.selected")
          .map((node) => node.getAttribute("data-id") ?? "")
          .sort()
          .join(","),
      );
      inspector.push(`${dom.one("#inspector").getAttribute("data-selected")}:${dom.one("#inspector").getAttribute("data-type") ?? ""}`);
      group.push(!dom.one<HTMLButtonElement>('[data-action="group-nodes"]').disabled);
    });
    assert.equal(picked[0], "fix-small,redesign");
    assert.equal(group[0], true, "2 つ選んでも「グループ化」が押せない");
    assert.equal(inspector[2], "node:group", "枠を選んだ欄にならない");
    assert.equal(picked[2], "group-1");
    assert.equal(picked[4], "investigate");
    assert.equal(group[4], false, "1 つだけなら「グループ化」は押せない");
    // 見本のグループ化のあとは、枠の中にノードが入っている
    assert.ok(inspector.length === 8);
  } finally {
    await dom.close();
  }
  const edge = await openFlow();
  try {
    await start(edge, "build");
    const seen: string[] = [];
    await walk(edge, () => seen.push(edge.one("#inspector").getAttribute("data-selected") ?? ""));
    assert.deepEqual(seen, ["flow", "node", "flow", "flow", "edge", "flow"]);
  } finally {
    await edge.close();
  }
});

test("CB-D266 保存前の差分・下書きの差分・依頼の文・提案あり・着手中・外で変わった帯は、見本の様子で出て、閉じたら消える", async () => {
  const dom = await openFlow();
  try {
    await start(dom, "save-review");
    const seen: string[] = [];
    await walk(dom, () => {
      seen.push(
        [
          dom.all("#save-review").length > 0 ? "review" : "",
          dom.all("#lock").length > 0 ? "lock" : "",
          dom.all("#changed:not(.hidden)").length > 0 ? "changed" : "",
          dom.all("#dirty:not(.hidden)").length > 0 ? "dirty" : "",
        ]
          .filter((item) => item !== "")
          .join("+"),
      );
    });
    assert.deepEqual(seen, ["dirty", "dirty", "review+dirty", "review+dirty", "lock", "changed+dirty"]);
    assert.equal(dom.all("#save-review").length + dom.all("#lock").length, 0, "閉じても見本の帯・一覧が残る");

    await start(dom, "agent-draft");
    const draft: string[] = [];
    await walk(dom, () => {
      draft.push(
        [
          dom.all("#request-text").length > 0 ? "request-text" : "",
          dom.all("#offer").length > 0 ? "offer" : "",
          dom.all("#proposal-review").length > 0 ? "proposal" : "",
        ]
          .filter((item) => item !== "")
          .join("+"),
      );
    });
    assert.deepEqual(draft, ["", "request-text", "offer", "proposal", "proposal", ""]);
    assert.equal(dom.all("#offer, #proposal-review, #request-text").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D267 差分の一覧は見本どうしの差分で、変えた中身が並ぶ。下書きの差分は前と後の文を並べ、取り込むボタンが押せる状態で出る", async () => {
  const dom = await openFlow();
  try {
    await start(dom, "save-review");
    for (let i = 0; i < 2; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    // 3 番目の手順（変更の一覧）
    await dom.settle();
    assert.ok(dom.all('#save-review [data-kind="added-nodes"] li').length >= 1);
    assert.ok(dom.all('#save-review [data-kind="changed-nodes"] li').length >= 1);
    assert.ok(dom.all('#save-review [data-kind="removed-connections"] li').length >= 1);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();

    await start(dom, "agent-draft");
    for (let i = 0; i < 3; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    await dom.settle();
    assert.ok(dom.all("#proposal-review .proposal-change").length >= 2);
    assert.ok(dom.all("#proposal-review .proposal-after").length >= 1);
    assert.equal(dom.one<HTMLButtonElement>('[data-action="import-proposal"]').disabled, false);
    dom.click(dom.one('[data-action="import-proposal"]'));
    await dom.settle();
    const acts = ["save", "reviewSave", "request", "openProposal", "openFile", "reload", "requestCopy", "requestOpen"];
    assert.deepEqual(
      dom.posted.filter((message) => acts.includes(message.type)),
      [],
      "案内の見本の差分から保存や取り込みが動いた",
    );
  } finally {
    await dom.close();
  }
});

test("CB-D268 畳んだ部品箱・右の欄は、案内の間は開いて指す先を見せ、閉じたら畳み直る。覚えている状態も変わらない", async () => {
  const dom = await openPage({ kind: "page", page: page() }, { folded: { palette: true, side: true } });
  try {
    assert.equal(dom.one("#flow-palette").getAttribute("data-folded"), "1");
    await start(dom, "intro");
    assert.equal(dom.one("#flow-palette").getAttribute("data-folded"), "0", "案内の間も畳んだままで、指す先が見えない");
    assert.equal(dom.one(".flow-side").getAttribute("data-folded"), "0");
    dom.click(dom.one('[data-action="toggle-palette"]'));
    await dom.settle();
    assert.equal(dom.one("#flow-palette").getAttribute("data-folded"), "0", "案内の間に畳めてしまう");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.one("#flow-palette").getAttribute("data-folded"), "1", "閉じたあと畳み直らない");
    assert.equal(dom.one(".flow-side").getAttribute("data-folded"), "1");
    assert.deepEqual((dom.state() as { folded?: unknown }).folded, { palette: true, side: true });
  } finally {
    await dom.close();
  }
});

test("CB-D269 読み込み中に頼まれた案内はフローが出てから始め、別の対象へ切り替わって読み込み中になったら閉じて tourDone を返す", async () => {
  const dom = await openPage({ kind: "loading", text: "フローを読み込み中…" });
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "指す先がない読み込み中には出さない");
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "部品箱");
    await dom.send({ type: "data", data: { kind: "loading", text: "フローを読み込み中…" } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
    // 閉じたあとは、拡張ホストへ送れる（送らない状態が残らない）
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    dom.click(dom.one('[data-action="reload"]'));
    await dom.settle();
    assert.equal(dom.posted.filter((message) => message.type === "reload").length, 1, "案内を閉じたあとも送れない");
  } finally {
    await dom.close();
  }
});

test("CB-D270 実際のフローが着手中（錠）でも、案内は見本を動く状態で出し、閉じたら錠の帯が戻る。見本の「着手中」の手順では見本の錠の文を出す", async () => {
  const dom = await openFlow({ lock: { locked: true, reason: lockedReason("i0001-01-01") } });
  try {
    assert.equal(dom.all("#lock").length, 1);
    await start(dom, "save-review");
    const locks: string[] = [];
    await walk(dom, () => locks.push(dom.all("#lock").map((el) => el.textContent ?? "").join("")));
    assert.equal(locks[0], "", "案内の間に実際の錠が出ている");
    assert.ok(locks[4].includes(sample.page.ticket), "見本の錠の文が出ない");
    assert.equal(dom.all("#lock").length, 1);
    assert.ok(dom.one("#lock").textContent?.includes("i0001-01-01"), "閉じたあと実際の錠に戻らない");
  } finally {
    await dom.close();
  }
});
