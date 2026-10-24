/**
 * 計画編集画面の案内（`src/webview/tours/workflow.yml`）を happy-dom で動かす。
 *
 * 案内の間、画面は今の計画ではなく見本の答え（実行ファイルの答えの形。`core/tour-workflow.ts`）を描く。手順ごとの様子
 * （描く答え・開く確かめ・選ぶステップ）は `scene` が決め、指す先は `data-tour` の印。実際の答え・未保存の線・置き場所・確かめと、
 * 拡張ホストへの送信（線の頼み・保存・再読み込み・取り込む）には触れない。
 *
 * 図に点と線を描くので、大きさの偽物（`measure`）が要る（`test/helpers/dom.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };
import type { DomPage } from "../helpers/dom.js";
import type { TourCourseDef } from "../../src/core/tour-def.js";
import { planOrderSample } from "../helpers/plan-order.js";
import { loadTour } from "../helpers/tour.js";
import { lastPosted, openWorkflow } from "../helpers/workflow.js";

const def = loadTour("workflow");

function course(id: string): TourCourseDef {
  const found = def.courses.find((c) => c.id === id);
  assert.ok(found !== undefined, `案内 ${id} がありません`);
  return found;
}

/** 「?」から一覧を開き、案内を 1 本選んで始める */
async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

/** 案内の手順を順に進め、各手順で `check` を呼ぶ。最後の手順の「完了」で閉じる */
async function walk(dom: DomPage, id: string, check: (index: number, at: string) => void | Promise<void>): Promise<void> {
  for (const [index, step] of course(id).steps.entries()) {
    await dom.settle();
    const at = `${id} の ${index + 1} 番目の手順（${step.title}）`;
    assert.equal(dom.one("#tour-title").textContent, step.title, at);
    await check(index, at);
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
  }
}

/** 画面が拡張ホストへ送ってはいけないもの。案内の間は `tourDone` のほかは何も送らない */
function sentSince(dom: DomPage, from: number): string[] {
  return dom.posted.slice(from).map((message) => message.type);
}

/** 点の番号と題（その図の中） */
function numbers(dom: DomPage, part: string): string[] {
  return dom
    .all(`.plan-graph[data-part="${part}"] .plan-node`)
    .map((node) => `${node.getAttribute("data-number")}:${node.querySelector(".plan-node-title")?.textContent ?? ""}`);
}

/** 点の横位置（React Flow が置いた translate の x） */
function xOf(dom: DomPage, id: string): number {
  const style = dom.one(`.react-flow__node[data-id="${id}"]`).getAttribute("style") ?? "";
  const found = /translate\(\s*(-?[\d.]+)px/.exec(style);
  assert.ok(found !== null, `${id} の置き場所が読み込めない: ${style}`);
  return Number(found[1]);
}

/** 線の × を押して、`order` が送られたかを返す（実際の画面なら送る） */
function pressCut(dom: DomPage, edge: string): void {
  dom.click(dom.one(`button[data-action="plan-remove-edge"][data-edge="${edge}"]`));
}

test("CB-D280 「?」は案内の一覧を開き、基本の案内が先頭に並ぶ。選んだ案内だけが始まり、閉じると tourDone だけを返す", async () => {
  const dom = await openWorkflow();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all<HTMLButtonElement>('[data-action="tour-course"]');
    assert.deepEqual(
      items.map((item) => item.dataset.course),
      def.courses.map((c) => c.id),
    );
    assert.equal(items[0].dataset.course, "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    assert.ok(items.length >= 6, "ユースケースの案内が少ない");
    dom.click(items[2]);
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    assert.equal(dom.all(".tour").length, 1);
    assert.equal(dom.one("#tour-title").textContent, def.courses[2].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.map((message) => message.type),
      ["ready", "tourDone"],
    );
  } finally {
    await dom.close();
  }
});

test("CB-D281 全ユースケースの全手順で、指す先が画面にあり吹き出しが中ほどに出ない。見本を描き、実際の計画を出さない。最後の手順で閉じる", async () => {
  for (const c of def.courses) {
    // 実際の親は i0001。見本は sample-a なので、どちらを描いているかが見分けられる
    const dom = await openWorkflow();
    try {
      await startCourse(dom, c.id);
      const from = dom.posted.length;
      await walk(dom, c.id, (index, at) => {
        const step = c.steps[index];
        if (step.target !== "") {
          assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面にありません`);
          assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
          assert.equal(dom.all(".tour-spot").length, 1, `${at}: 指す先を囲う枠がありません`);
        }
        assert.equal(dom.all(".banner.tour-sample").length, 1, `${at}: 見本だと分かる帯がありません`);
        assert.ok(dom.one(".toolbar .workflow-ticket").textContent?.includes("sample-a"), `${at}: 見本の親を出していない`);
        assert.ok(!(dom.one("#root").textContent ?? "").includes("i0001"), `${at}: 実際の親の識別子が出ている`);
        assert.ok(dom.all(".plan-graph").length >= 1 && dom.all(".plan-graph").every((g) => g.getAttribute("data-ticket") === "sample-a"), `${at}: 見本の図でない`);
        assert.ok(dom.all(".plan-graph .react-flow__edge").length > 0, `${at}: 線が描かれていない`);
      });
      assert.equal(dom.all(".tour").length, 0, `${c.id}: 最後の手順で閉じない`);
      assert.equal(dom.all(".tour-sample, .workflow-review").length, 0, `${c.id}: 閉じたのに見本が残った`);
      assert.ok(dom.all(".plan-graph").every((g) => g.getAttribute("data-ticket") === "i0001"), `${c.id}: 閉じたあと実際の計画に戻らない`);
      assert.deepEqual(sentSince(dom, from), ["tourDone"], `${c.id}: 案内の間に拡張ホストへ余計な送信をした`);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D282 案内の間は、実際の答え・未保存の線・保存の確認しに触れない。閉じたら実際の表示に戻り、送信も増えない", async () => {
  const dom = await openWorkflow();
  try {
    // 実際の画面で線を 1 本消し、答え（番号の入れ替わり）を受けて未保存にする
    dom.click(dom.one('button[data-action="plan-remove-edge"][data-edge="after-1-3"]'));
    const sent = lastPosted(dom, "order");
    assert.ok(sent !== undefined);
    await dom.send({ type: "answer", seq: sent.seq, answer: planOrderSample("reverse") });
    await new Promise((resolve) => setTimeout(resolve, 400));
    await dom.settle();
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    const real = numbers(dom, "plan");
    assert.deepEqual(real, ["1:調査", "2:実装とテスト", "3:設計", "4:docs"]);
    const realRenumbered = dom.one("#workflow-renumbered").textContent;
    const from = dom.posted.length;

    await startCourse(dom, "renumber");
    await walk(dom, "renumber", (index, at) => {
      // 見本の案内は、実際の答えの未保存とは別に、見本の答えから決める（最初の手順は開いたとき＝未保存でない）
      const sampleDirty = !dom.one("#dirty").classList.contains("hidden");
      assert.equal(sampleDirty, index > 0 && index < 7, at);
    });
    assert.deepEqual(sentSince(dom, from), ["tourDone"]);

    // 閉じたあと、実際の答え・未保存・番号の変わるステップが、案内の前のまま
    assert.deepEqual(numbers(dom, "plan"), real);
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.one("#workflow-renumbered").textContent, realRenumbered);
    assert.equal(dom.all(".workflow-review").length, 0);
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, false);
    assert.deepEqual(lastPosted(dom, "dirty"), { type: "dirty", dirty: true, checking: false });
  } finally {
    await dom.close();
  }
});

test("CB-D283 案内の間は、線を引く・削除する・保存・再読み込み・案に戻す・並べ直す・取り込むを押しても、画面も拡張ホストも動かない", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "tools");
    const from = dom.posted.length;
    await dom.settle();
    const style = dom.one('.react-flow__node[data-id="n3"]').getAttribute("style");
    assert.ok(dom.one<HTMLButtonElement>('[data-action="save"]').disabled === false, "見本の答えは保存できる形のはず");
    for (const action of ["relayout", "revert", "reload", "save"]) {
      dom.click(dom.one(`[data-action="${action}"]`));
      await dom.settle();
    }
    const cuts = dom.all('button[data-action="plan-remove-edge"]');
    assert.ok(cuts.length > 0, "見本の図に × がない（押せる形で見せていない）");
    dom.click(cuts[0]);
    await dom.settle();
    assert.deepEqual(sentSince(dom, from), [], "案内の間に拡張ホストへ送った");
    assert.equal(dom.all(".workflow-review").length, 0, "保存の確認が開いた");
    assert.equal(dom.one('.react-flow__node[data-id="n3"]').getAttribute("style"), style, "並べ直しで点が動いた");
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.all(".workflow-graphs .react-flow__edge").length > 0, true);

    // 外で変わった帯の「取り込む」「編集を続ける」
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    await startCourse(dom, "external");
    await dom.settle();
    dom.click(dom.one('[data-action="adopt"]'));
    dom.click(dom.one('[data-action="keep-editing"]'));
    await dom.settle();
    assert.ok(dom.all("#changed-diff").length === 1, "編集を続けるで差分が閉じた");
    assert.deepEqual(sentSince(dom, from).filter((type) => type !== "tourDone"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D284 ノード・線・× を指す案内は、指す先のステップの番号と線を実際の図の要素に付け、枠で囲う。ステップを選ぶ手順は詳細の欄を開く", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "parallel");
    const seen: string[] = [];
    await walk(dom, "parallel", (index) => {
      const target = course("parallel").steps[index].target;
      seen.push(target);
      const el = dom.one(`[data-tour="${target}"]`);
      if (target.startsWith("node-")) {
        assert.ok(el.classList.contains("react-flow__node"), target);
        assert.equal(el.getAttribute("data-id"), `n${target.slice(5)}`);
      }
      if (target.startsWith("edge-")) {
        assert.ok(el.classList.contains("react-flow__edge"), target);
        assert.equal(el.getAttribute("data-id"), `after-${target.slice(5)}`);
      }
      if (target.startsWith("cut-")) {
        assert.equal(el.getAttribute("data-edge"), `after-${target.slice(4)}`);
      }
    });
    assert.ok(seen.some((t) => t.startsWith("node-")) && seen.some((t) => t.startsWith("edge-")) && seen.some((t) => t.startsWith("cut-")), seen.join());

    // ステップを選ぶ手順（renumber の 4 番目の手順）: 詳細の欄に、提案での番号と振り直した番号を並べる
    await startCourse(dom, "renumber");
    for (let i = 0; i < 3; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "詳細で元の番号を見る");
    const rows = Object.fromEntries(dom.all(".plan-graph[data-part=\"plan\"] .plan-detail-row").map((row) => [row.querySelector("dt")?.textContent, row.querySelector("dd")?.textContent]));
    assert.equal(rows["番号"], "2");
    assert.equal(rows["提案での番号"], "3");
    assert.equal(rows["題"], "実装とテスト");
    assert.equal(dom.all(".tour-bubble.center").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D285 手順ごとの答えで図を描き直す。線が増える・消える。点の置き場所は引く前のままで、並べ直した手順だけ組み直す", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "parallel");
    const edges = (): string[] => dom.all(".plan-graph[data-part=\"plan\"] .react-flow__edge").map((e) => e.getAttribute("data-id") ?? "");
    const kept: Record<string, string[]> = {};
    const xs: Record<number, [number, number]> = {};
    let notes = "";
    await walk(dom, "parallel", (index) => {
      notes = dom.all('[data-tour="notes-plan"]')[0]?.textContent ?? "";
      kept[index] = edges();
      xs[index] = [xOf(dom, "n2"), xOf(dom, "n3")];
    });
    assert.ok(!kept[0].includes("after-2-4") && kept[2].includes("after-2-4"), "合流の線が増えない");
    assert.ok(kept[3].includes("after-2-4") && !kept[3].includes("after-1-3"), "1 → 3 が先に出ている");
    assert.ok(kept[4].includes("after-1-3") && kept[4].includes("after-2-3"), "1 → 3 を追加した手順");
    assert.ok(!kept[5].includes("after-2-3") && kept[5].includes("after-1-3"), "2 → 3 を削除した手順");
    // 一直線の置き場所のまま（2 と 3 は別の列）。並べ直した手順（5）だけ、2 と 3 が同じ列に並ぶ
    assert.notEqual(xs[4][0], xs[4][1], "並べ直す前に 2 と 3 が並んだ（置き場所を動かした）");
    assert.equal(xs[5][0], xs[5][1], "並べ直した手順で 2 と 3 が並ばない");
    // 先に消した形（最後の手順）は、終端に届かないステップがあり、図の下に error が出る
    assert.match(notes, /最後のステップ/);
  } finally {
    await dom.close();
  }
});

test("CB-D286 保存の確認の手順は見本の確認を開き、焦点は案内の吹き出しに残す。番号と子の提案を並べ、閉じると確認も消える", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "renumber");
    const from = dom.posted.length;
    await walk(dom, "renumber", (index, at) => {
      const open = dom.all(".workflow-review").length === 1;
      assert.equal(open, index >= 4 && index <= 6, at);
      if (open) {
        assert.equal(dom.document.activeElement?.getAttribute("data-action"), "tour-next", `${at}: 焦点が確認しに移った`);
        assert.deepEqual(
          dom.all("#review-numbers li").map((li) => li.textContent),
          ["実装とテスト: 3 → 2", "設計: 2 → 3"],
          at,
        );
        assert.deepEqual(
          dom.all("#review-children li").map((li) => li.textContent),
          [
            "sample-a-02-01 → sample-a-03-01（phase: 2 → 3）",
            "sample-a-03-01 → sample-a-02-01（phase: 3 → 2）",
            "sample-a-04-01（predecessors: sample-a-02-01 → sample-a-03-01）",
          ],
          at,
        );
      }
    });
    assert.equal(dom.all(".workflow-review").length, 0);
    assert.deepEqual(sentSince(dom, from), ["tourDone"]);
  } finally {
    await dom.close();
  }
});

test("CB-D287 外で変わった提案の案内は、帯と差分（開いたときの答えとの違い）を出す。編集を続けるは差分を閉じ、承認待ちから無くなった手順は読むだけにする", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "external");
    await walk(dom, "external", (index, at) => {
      const banner = dom.all("#changed").length;
      if (index <= 3) {
        assert.equal(banner, 1, at);
        assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, `${at}: 外で変わったのに保存できる`);
        assert.match(dom.one("#save").getAttribute("title") ?? "", /外で変わった/, at);
        assert.match(dom.one('[data-action="adopt"]').textContent ?? "", /編集を破棄する/, at);
      }
      if (index <= 2) {
        // 開いたとき（open）と外で変わった提案（outside）の違いは 3 と 4 の先行
        assert.deepEqual(
          dom.all("#changed-diff tbody tr").map((row) => row.getAttribute("data-number")),
          ["3", "4"],
          at,
        );
      } else {
        assert.equal(dom.all("#changed-diff").length, 0, `${at}: 差分が残っている`);
      }
      if (index === 4) {
        assert.equal(banner, 0, at);
        assert.match(dom.one("#gone").textContent ?? "", /承認済みです/, at);
        assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, at);
        assert.equal(dom.one('.plan-graph[data-part="plan"]').getAttribute("data-editable"), "0", `${at}: 読むだけにしていない`);
      }
    });
  } finally {
    await dom.close();
  }
});

test("CB-D288 保存できない理由の案内は、断られた線の一言・終端に届かないステップ・子が承認された番号・改版で通らない先行・子の付け替えの問題を、実際と同じ読み取りで出す", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "blocked");
    const titleOf = (): string => dom.one("#save").getAttribute("title") ?? "";
    await walk(dom, "blocked", (index, at) => {
      switch (index) {
        case 0:
          assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, at);
          assert.match(titleOf(), /順序を変えていません/, at);
          break;
        case 2:
          // 子が承認された番号: 印が付き、入る線に × が無い
          assert.ok(dom.one('.plan-node[data-number="3"] .tag.locked').textContent?.includes("子が承認済み"), at);
          assert.equal(dom.all('button[data-action="plan-remove-edge"][data-edge="after-2-3"]').length, 0, at);
          assert.equal(dom.all('button[data-action="plan-remove-edge"][data-edge="after-1-2"]').length, 1, at);
          break;
        case 3:
          assert.match(dom.one("#status").textContent ?? "", /この線は引けません。線は引く前のままです: 子が承認された番号を動かさずに/, at);
          assert.ok(dom.one(".foot").classList.contains("error"), at);
          break;
        case 4:
          assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, at);
          assert.match(titleOf(), /順序の検査/, at);
          assert.equal(dom.one('.plan-node[data-number="3"]').getAttribute("data-loose"), "1", at);
          assert.match(dom.one('[data-tour="notes-plan"]').textContent ?? "", /最後のステップ/, at);
          break;
        case 5:
          assert.match(titleOf(), /改版で通らない/, at);
          assert.match(dom.one('#workflow-problems li[data-kind="revision"]').textContent ?? "", /先行を変えられない/, at);
          break;
        case 6:
          assert.match(titleOf(), /子の提案を付け替えられない/, at);
          assert.equal(dom.all('#workflow-problems li[data-kind="child"]').length, 1, at);
          assert.equal(dom.all('#workflow-problems li[data-kind="mismatched"]').length, 1, at);
          break;
        default:
          break;
      }
    });
  } finally {
    await dom.close();
  }
});

test("CB-D289 保存と承認の案内は、答えを待つ間の一言と、保存したあとの未保存でない画面を出す。フィードバック計画の案内は、その計画だけ番号が入れ替わる", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "approval");
    await walk(dom, "approval", (index, at) => {
      if (index === 1) {
        assert.match(dom.one("#status").textContent ?? "", /確認しています/, at);
        assert.equal(dom.all('.plan-graph[data-editable="1"]').length, 0, `${at}: 答えを待つ間に線を変えられる`);
        assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, at);
      }
      if (index === 4) {
        assert.ok(dom.one("#dirty").classList.contains("hidden"), `${at}: 保存したあとなのに未保存`);
        assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:実装とテスト", "3:設計", "4:文書"], at);
      }
    });
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="feedback"]'));
    await dom.settle();
    await walk(dom, "feedback", (index, at) => {
      if (index >= 4) {
        assert.deepEqual(numbers(dom, "feedback"), ["5:実装フィードバック対応", "6:設計フィードバック対応"], at);
        assert.deepEqual(numbers(dom, "plan"), ["1:調査", "2:設計", "3:実装とテスト", "4:文書"], `${at}: 全体計画の番号が動いた`);
        assert.match(dom.one("#workflow-renumbered").textContent ?? "", /実装フィードバック対応 6 → 5/, at);
      }
    });
  } finally {
    await dom.close();
  }
});

test("CB-D290 保存の確認や案内が開いている間に別の中身が届いても、案内は見本のまま続き、閉じたあとに新しい中身を描く", async () => {
  const dom = await openWorkflow();
  try {
    await startCourse(dom, "reorder");
    await dom.send({ type: "data", data: { kind: "page", page: { root: "/ws", ticket: "i0002", answer: { ...planOrderSample("open"), ticket: "i0002" } } } });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, course("reorder").steps[0].title);
    assert.ok(dom.one(".toolbar .workflow-ticket").textContent?.includes("sample-a"));
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.ok(dom.one(".toolbar .workflow-ticket").textContent?.includes("i0002"));
  } finally {
    await dom.close();
  }
});
