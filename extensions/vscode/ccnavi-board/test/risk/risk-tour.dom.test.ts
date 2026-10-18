/**
 * リスク管理画面の案内（`src/webview/tours/risk.yml`）を happy-dom で動かす。案内の間は見本を描き、実際の配点・編集中の内容・
 * 拡張ホストへの送信には触れない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { riskSums } from "../../src/core/sums.js";
import type { DomPage } from "../helpers/dom.js";
import { openRisk } from "../helpers/risk.js";
import { sumsBoard } from "../helpers/sums.js";
import { loadTour } from "../helpers/tour.js";
import type { HTMLButtonElement, HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

const def = loadTour("risk");

/** 実際の配点（`test/helpers/risk.ts`）にだけある項目の id。案内の間に出ていたら、実際の配点を描いている */
const REAL_ONLY = ["sh", "q"];
const REAL_IDS = ["big-diff", "ci", "sh", "q"];

/** 開いた「?」の一覧から案内を選んで始める */
async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
}

/** 画面に出ている項目の id（絞り込みで隠れたものも含む） */
function factorIds(dom: DomPage): string[] {
  return dom.all(".factor .sum-id").map((el) => el.textContent ?? "");
}

async function next(dom: DomPage): Promise<void> {
  dom.click(dom.one('[data-action="tour-next"]'));
  await dom.settle();
}

/** 案内を始めて、n 番目の手順（0 始まり）まで進める */
async function goto(dom: DomPage, id: string, index: number): Promise<void> {
  await startCourse(dom, id);
  for (let i = 0; i < index; i += 1) {
    await next(dom);
  }
}

/** 案内が拡張ホストへ送ったもの（`ready` は組み上がり、`tourDone` は案内を閉じた印なので除く）。空であること */
function extraPosts(dom: DomPage): unknown[] {
  return dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone");
}

const levelsOf = (dom: DomPage): string[] => dom.all<HTMLInputElement>("input.f-level").map((el) => el.value);

test("CB-D220 「?」は案内の一覧を開き、選んだ案内だけが始まる。基本の案内が先頭で、閉じたら tourDone を返す", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all<HTMLButtonElement>('[data-action="tour-course"]');
    assert.deepEqual(
      items.map((item) => item.dataset.course),
      def.courses.map((course) => course.id),
    );
    assert.equal(items[0].dataset.course, "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    dom.click(items[1]);
    await dom.settle();
    assert.equal(dom.all(".tour-menu").length, 0);
    assert.equal(dom.one("#tour-title").textContent, def.courses[1].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D221 全ユースケースの全手順で、指す先が画面にあり、吹き出しが中ほどへ逃げない。見本を描き、閉じたら実際の配点に戻る", async () => {
  const sums = riskSums(sumsBoard());
  for (const course of def.courses) {
    const dom = await openRisk({ sums });
    try {
      assert.deepEqual(factorIds(dom), REAL_IDS);
      await startCourse(dom, course.id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        if (step.target !== "") {
          assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面にありません`);
          assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
          // 同じ印が 2 か所にあると、先頭の 1 つを測って別の行を指してしまう
          assert.equal(dom.all(`[data-tour="${step.target}"]`).length, 1, `${at}: 指す先 ${step.target} が複数ある`);
        }
        const ids = factorIds(dom);
        for (const real of REAL_ONLY) {
          assert.ok(!ids.includes(real), `${at}: 実際の項目 ${real} が出ている`);
        }
        assert.ok(ids.includes(step.scene.missing === true ? "deletes" : "no-tests"), `${at}: 見本の項目が出ていない`);
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
        await next(dom);
      }
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
      assert.deepEqual(factorIds(dom), REAL_IDS, "閉じたあと実際の配点に戻らない");
      assert.equal(dom.all(".banner.tour-sample").length, 0);
      assert.equal(dom.all("#dirty:not(.hidden)").length, 0, "案内が未保存にした");
      assert.deepEqual(extraPosts(dom), [], "案内の間に余計な送信があった");
      assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
      assert.equal(dom.all("details#sum[open]").length, 0, "閉じたあと統合結果の枠が開いたまま");
    } finally {
      await dom.close();
    }
  }
});

test("CB-D222 案内の間は、画面の編集・ボタン・拡張ホストへの送信が実際には動かない。閉じたあとは実際の操作に戻る", async () => {
  const dom = await openRisk({ sums: riskSums(sumsBoard()) });
  try {
    await goto(dom, "add-factor", 1);
    assert.equal(dom.all(".factor").length, 5);
    // 見本の上の操作は何も起こさない（打つ・押す・足す・並べ替える・消す・保存する・読み直す・開く）
    dom.type(dom.one<HTMLInputElement>("input.f-level"), "999");
    dom.type(dom.one<HTMLInputElement>("#find"), "zzz");
    dom.click(dom.one('[data-action="add"]'));
    dom.click(dom.one(".factor .row-head"));
    dom.click(dom.one(".factor.open .buttons button[title='下へ']"));
    dom.click(dom.one(".factor.open .buttons button:last-child"));
    dom.click(dom.one('[data-action="save"]'));
    dom.click(dom.one('[data-action="open-risk"]'));
    dom.click(dom.one('button[data-action="reload"]'));
    await dom.send({ type: "changed" });
    await dom.settle();
    assert.equal(dom.all(".factor").length, 5);
    assert.deepEqual(factorIds(dom).slice(0, 2), ["big-diff", "many-files"]);
    assert.equal(dom.one<HTMLInputElement>("input.f-level").value, "20");
    assert.equal(dom.one<HTMLInputElement>("#find").value, "");
    assert.equal(dom.all(".factor.open").length, 1, "見本の開く行が操作で変わった");
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
    assert.ok(dom.one("#changed").classList.contains("hidden"), "案内の間に外の変更の帯が出た");
    assert.equal(dom.one("#status").textContent, "");
    assert.deepEqual(extraPosts(dom), []);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    // 閉じたあとは実際の画面。案内の間に届いた外の変更の帯は、ここで出る
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    assert.deepEqual(factorIds(dom), REAL_IDS);
    dom.click(dom.one('[data-action="add"]'));
    await dom.settle();
    assert.equal(dom.all(".factor").length, 5);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D223 手順の scene が画面の様子を替える（境目を空にする・開く項目・絞り込み）。scene は次の手順へ引き継がない", async () => {
  const sums = riskSums(sumsBoard());

  // 境目を空にして、組み込みの値を薄く出す
  let dom = await openRisk({ sums });
  try {
    await goto(dom, "levels", 3);
    assert.deepEqual(levelsOf(dom), ["20", "40", ""]);
    assert.equal(dom.all<HTMLInputElement>("input.f-level")[2].placeholder, "既定 70");
    await next(dom);
    assert.deepEqual(levelsOf(dom), ["20", "40", "70"], "手順をまたいで空が残った");
  } finally {
    await dom.close();
  }

  // 開く項目。factor-* の印は開いた行にだけ付き、加点条件で出る欄が変わる
  dom = await openRisk({ sums });
  try {
    await goto(dom, "kinds", 2);
    assert.equal(dom.all(".factor.open").length, 1);
    assert.equal(dom.one(".factor.open .sum-id").textContent, "big-diff");
    assert.equal(dom.all('.factor.open [data-tour="factor-include"] textarea').length, 1);
    assert.equal(dom.one<HTMLInputElement>('[data-tour="factor-include"] textarea').value, "src/**");
    await next(dom);
    assert.equal(dom.one<HTMLInputElement>('[data-tour="factor-exclude"] textarea').value, "src/generated/**");
    await next(dom);
    assert.equal(dom.one(".factor.open .sum-id").textContent, "ci");
    assert.equal(dom.all(".factor.open input.f-max").length, 1);
    assert.equal(dom.all('[data-tour="factor-include"]').length, 0, "glob に対象パスの欄はありません");
  } finally {
    await dom.close();
  }

  // 絞り込み
  dom = await openRisk({ sums });
  try {
    await goto(dom, "organize", 0);
    assert.equal(dom.one<HTMLInputElement>("#find").value, "github");
    assert.equal(dom.all(".factor:not(.hidden-by-find)").length, 1);
    assert.equal(dom.one("#factor-count").textContent, "1 / 5");
    await next(dom);
    assert.equal(dom.all(".factor.hidden-by-find").length, 0, "手順をまたいで絞り込みが残った");
    assert.equal(dom.one("#factor-count").textContent, "5");
  } finally {
    await dom.close();
  }
});

test("CB-D224 ファイルがない状態の案内は、組み込みの配点を読み取り専用で見せ、「作る」を押しても実際には送らない", async () => {
  const dom = await openRisk({ sums: riskSums(sumsBoard()) });
  try {
    assert.equal(dom.all(".banner.missing").length, 0);
    await goto(dom, "create-file", 0);
    assert.equal(dom.all(".banner.missing").length, 1);
    assert.deepEqual(factorIds(dom), ["big-diff", "many-files", "ci", "deletes"]);
    assert.ok(dom.all<HTMLInputElement>("input.f-level").every((el) => el.disabled));
    assert.ok(dom.one<HTMLButtonElement>('[data-action="add"]').disabled);
    assert.equal(dom.all("details#sum").length, 0, "組み込みの配点に統合結果はありません");
    dom.click(dom.one('[data-action="create"]'));
    await dom.settle();
    assert.deepEqual(extraPosts(dom), [], "見本の「作る」が実際に送られた");
    assert.equal(dom.all(".banner.missing").length, 1);
    await next(dom);
    await next(dom);
    await next(dom);
    assert.equal(dom.all(".banner.missing").length, 0, "最後の手順でファイルがある状態に戻らない");
    assert.ok(dom.all<HTMLInputElement>("input.f-level").every((el) => !el.disabled));
  } finally {
    await dom.close();
  }
});

test("CB-D225 統合結果の案内は、枠を開いて見本の統合結果を選ぶ。実際の統合結果は出ず、閉じたら枠も選びも実際のものに戻る", async () => {
  const dom = await openRisk({ target: { kind: "project", name: "lib" }, sums: riskSums(sumsBoard()) });
  try {
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "lib");
    assert.equal(dom.all("details#sum[open]").length, 0);
    await goto(dom, "sum", 0);
    assert.equal(dom.all("details#sum[open]").length, 1);
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "self");
    assert.ok(dom.one(".sum-table").textContent?.includes("migrations"));
    await next(dom);
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "lib");
    await next(dom);
    assert.match(dom.one("#sum-levels").textContent ?? "", /HIGH\s*30/);
    await next(dom);
    assert.ok(dom.one(".sum-table").textContent?.includes("lib:big-diff"));
    assert.ok(!dom.one(".sum-table").textContent?.includes("schema"), "実際の統合結果が出ている");
    await next(dom);
    assert.equal(dom.all("details#sum[open]").length, 0, "統合結果に関わらない手順で枠が開いたまま");
    await next(dom);
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "lib");
    assert.ok(dom.one(".sum-table").textContent?.includes("schema"), "閉じたあと実際の統合結果に戻らない");
    assert.deepEqual(extraPosts(dom), []);
  } finally {
    await dom.close();
  }
});

test("CB-D226 実際にファイルがない画面でも、案内の間は見本の配点を描き、閉じたら組み込みの配点の読み取り専用に戻る", async () => {
  const dom = await openRisk({ exists: false, builtin: true });
  try {
    assert.equal(dom.all(".banner.missing").length, 1);
    await goto(dom, "add-factor", 1);
    assert.equal(dom.all(".banner.missing").length, 0);
    assert.ok(dom.all<HTMLInputElement>("input.f-level").every((el) => !el.disabled));
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".banner.missing").length, 1);
    assert.ok(dom.all<HTMLInputElement>("input.f-level").every((el) => el.disabled));
    assert.deepEqual(extraPosts(dom), []);
  } finally {
    await dom.close();
  }
});
