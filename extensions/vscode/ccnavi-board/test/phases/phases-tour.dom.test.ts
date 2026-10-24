/**
 * フェーズ管理画面の案内（`src/webview/tours/phases.yml`）を happy-dom で動かす。案内の間は見本を描き、実際の定義・編集中の内容・
 * 拡張ホストへの送信には触れない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { readPhases } from "../../src/core/phases-doc.js";
import type { DomPage } from "../helpers/dom.js";
import { openPage, openPhases, page } from "../helpers/phases.js";
import { loadTour } from "../helpers/tour.js";
import type { HTMLButtonElement, HTMLElement, HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

const def = loadTour("phases");

/** 実際の定義。見本の id と重ならない id だけで書き、案内の間に出ていたら実際の定義を描いていると分かるようにする */
const REAL_TEXT = `version: 1
phases:
  real-a:
    kind: work
    title: 実物A
    review: mr
    scope: inherit
    when: 実物だけの使う場面
  real-b:
    kind: feedback
    title: 実物B
    review: mr
    scope: inherit
`;
const REAL_IDS = ["real-a", "real-b"];
const SAMPLE_IDS = ["research", "design", "acceptance", "implement", "design-feedback", "implement-feedback"];
const LIB_IDS = ["quick", "api-change", "quick-feedback"];

async function openReal(): Promise<DomPage> {
  return openPhases({ model: readPhases(REAL_TEXT).model, target: { kind: "self", name: "" }, targets: [{ kind: "self", name: "", label: "ワークスペース" }, { kind: "project", name: "lib", label: "プロジェクト lib" }] });
}

/** 開いた「?」の一覧から案内を選んで始める */
async function startCourse(dom: DomPage, id: string): Promise<void> {
  dom.click(dom.one('[data-action="tour"]'));
  await dom.settle();
  dom.click(dom.one(`[data-action="tour-course"][data-course="${id}"]`));
  await dom.settle();
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

/** 画面に出ている定義の id（絞り込みで隠れたものも含む。足した空の行は空文字） */
function ids(dom: DomPage): string[] {
  return dom.all<HTMLElement>(".phase .sum-id").map((el) => (el.classList.contains("dim") ? "" : (el.textContent ?? "")));
}

/** 画面に出ている定義の行のキー */
function keys(dom: DomPage): string[] {
  return dom.all(".phase").map((el) => el.dataset.key ?? "");
}

/** 案内が拡張ホストへ送ったもの（`ready` は組み上がり、`tourDone` は案内を閉じた印なので除く）。空であること */
function extraPosts(dom: DomPage): unknown[] {
  return dom.posted.filter((message) => message.type !== "ready" && message.type !== "tourDone");
}

test("CB-D240 「?」は案内の一覧を開き、選んだ案内だけが始まる。基本の案内が先頭で、閉じたら tourDone を返す", async () => {
  const dom = await openReal();
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

test("CB-D241 全ユースケースの全手順で、指す先が画面に 1 つあり、吹き出しが中ほどに出ない。見本だけを描き、閉じたら実際の定義に戻る", async () => {
  for (const course of def.courses) {
    const dom = await openReal();
    try {
      assert.deepEqual(ids(dom), REAL_IDS);
      await startCourse(dom, course.id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 番目の手順（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.equal(dom.all(`[data-tour="${step.target}"]`).length, 1, `${at}: 指す先 ${step.target} が画面に 1 つではない`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先がありません）`);
        const shown = ids(dom);
        for (const real of REAL_IDS) {
          assert.ok(!shown.includes(real), `${at}: 実際の定義 ${real} が出ている`);
        }
        assert.ok(keys(dom).every((key) => !/^p\d+$/.test(key)), `${at}: 実際の行のキーが出ている`);
        assert.ok(shown.includes(step.scene.target === "lib" ? "quick" : "research"), `${at}: 見本の定義が出ていない`);
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
        await next(dom);
      }
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の手順で閉じない`);
      assert.deepEqual(ids(dom), REAL_IDS, "閉じたあと実際の定義に戻らない");
      assert.equal(dom.all(".banner.tour-sample").length, 0);
      assert.equal(dom.all("#dirty:not(.hidden)").length, 0, "案内が未保存にした");
      assert.equal(dom.all("#lock:not(.hidden)").length, 0, "案内の作業中の文が残った");
      assert.equal(dom.all("#changed:not(.hidden)").length, 0, "案内の外の変更の帯が残った");
      assert.equal(dom.all("#help").length, 0, "案内のヘルプの説明が残った");
      assert.equal(dom.one("#status").textContent, "", "案内の状態の文が残った");
      assert.deepEqual(extraPosts(dom), [], "案内の間に余計な送信があった");
      assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D242 案内の間は、画面の編集・ボタン・設定の切り替え・拡張ホストへの送信が実際には動かない。閉じたあとは実際の操作に戻る", async () => {
  const dom = await openReal();
  try {
    await goto(dom, "add-phase", 1);
    assert.equal(dom.all(".phase").length, 7);
    const added = '.phase[data-key="s-add"]';
    // 見本の上の操作は何も起こさない（打つ・押す・足す・並べ替える・消す・保存する・読み直す・開く・切り替える）
    dom.type(dom.one<HTMLInputElement>(`${added} input.f-id`), "zzz");
    dom.type(dom.one<HTMLInputElement>("#find"), "zzz");
    dom.change(dom.one<HTMLSelectElement>(`${added} select.f-kind`), "feedback");
    dom.click(dom.one('[data-action="add"]'));
    dom.click(dom.one(".phase .row-head"));
    dom.click(dom.one(`${added} .buttons button[title='上へ']`));
    dom.click(dom.one(`${added} .buttons button:last-child`));
    dom.click(dom.one('[data-action="save"]'));
    dom.click(dom.one('[data-action="open-phases"]'));
    dom.click(dom.one('button[data-action="reload"]'));
    dom.click(dom.one('[data-action="help"]'));
    dom.change(dom.one<HTMLSelectElement>("select#target"), "project:lib");
    await dom.send({ type: "changed" });
    await dom.settle();
    assert.equal(dom.all(".phase").length, 7);
    assert.equal(dom.one<HTMLInputElement>(`${added} input.f-id`).value, "");
    assert.equal(dom.one<HTMLSelectElement>(`${added} select.f-kind`).value, "work");
    assert.equal(dom.one<HTMLInputElement>("#find").value, "");
    assert.equal(dom.all(".phase.open").length, 1, "見本の開く行が操作で変わった");
    assert.equal(dom.all("#help").length, 0);
    assert.equal(dom.one<HTMLSelectElement>("select#target").value, "self:");
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
    assert.ok(dom.one("#changed").classList.contains("hidden"), "案内の間に外の変更の帯が出た");
    assert.equal(dom.one("#status").textContent, "");
    assert.deepEqual(extraPosts(dom), []);

    // 範囲・成果物の欄は打っても文字が変わらず、補足の見出しを押しても開閉しない
    await next(dom);
    await next(dom);
    await next(dom);
    assert.equal(dom.one("#tour-title").textContent, "編集可能な範囲を決める");
    const scope = dom.one<HTMLInputElement>(`${added} input.f-scope-globs`);
    assert.equal(scope.value, "wip/security/*");
    dom.type(scope, "zzz");
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>(`${added} input.f-scope-globs`).value, "wip/security/*");
    const more = dom.one(`${added} details.more`);
    assert.ok(!more.hasAttribute("open"));
    dom.click(dom.one(`${added} details.more > summary`));
    await dom.settle();
    assert.ok(!dom.one(`${added} details.more`).hasAttribute("open"), "見本の補足が押して開いた");
    assert.deepEqual(extraPosts(dom), []);

    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    // 閉じたあとは実際の画面。案内の間に届いた外の変更の帯は、ここで出る
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    assert.deepEqual(ids(dom), REAL_IDS);
    dom.click(dom.one('[data-action="add"]'));
    await dom.settle();
    assert.equal(dom.all(".phase").length, 3);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D243 手順の scene が画面の様子を替える（開く定義・補足・絞り込み・追加した行）。scene は次の手順へ引き継がない", async () => {
  // 開く定義と補足。phase-* の印は開いた最初の定義にだけ付く
  let dom = await openReal();
  try {
    await goto(dom, "guide-agent", 0);
    assert.equal(dom.all(".phase.open").length, 1);
    assert.equal(dom.one(".phase.open .sum-id").textContent, "acceptance");
    assert.ok(!dom.one(".phase.open details.more").hasAttribute("open"), "補足を開く scene がない手順では畳む");
    await next(dom);
    assert.ok(dom.one(".phase.open details.more").hasAttribute("open"));
    assert.equal(dom.one<HTMLInputElement>('[data-tour="phase-agent"] input').value, "acceptance-tester");
    await next(dom);
    assert.match(dom.one<HTMLInputElement>('[data-tour="phase-when"] input').value, /実装より先に書く/);
    // 絞り込み。開いた定義も補足も、次の手順へ引き継がない
    await next(dom);
    assert.equal(dom.one<HTMLInputElement>("#find").value, "実装より先");
    assert.equal(dom.all(".phase:not(.hidden-by-find)").length, 1);
    assert.equal(dom.one("#phase-count").textContent, "1 / 6");
    assert.equal(dom.all(".phase.open").length, 0, "手順をまたいで開いた定義が残った");
    await next(dom);
    assert.equal(dom.all(".phase.hidden-by-find").length, 0, "手順をまたいで絞り込みが残った");
    assert.equal(dom.one("#phase-count").textContent, "6");
    // ヘルプの説明
    assert.equal(dom.all("#help").length, 1);
    assert.equal(dom.one('[data-tour="help-panel"]').id, "help");
    await next(dom);
    assert.equal(dom.all("#help").length, 0, "手順をまたいでヘルプが残った");
  } finally {
    await dom.close();
  }

  // 足した行。空の行は既定の値で出て、書いた行は見本の値で出る。両方とも開いた行で、末尾に付く
  dom = await openReal();
  try {
    await goto(dom, "add-phase", 1);
    assert.deepEqual(ids(dom).slice(-2), ["implement-feedback", ""]);
    const added = dom.one('.phase[data-key="s-add"]');
    assert.ok(added.classList.contains("open"));
    assert.equal(dom.one<HTMLSelectElement>('[data-tour="phase-kind"] select').value, "work");
    assert.equal(dom.one<HTMLSelectElement>('[data-tour="phase-id"] input').closest(".phase")?.getAttribute("data-key"), "s-add", "印が追加した行に付いていない");
    await next(dom);
    await next(dom);
    await next(dom);
    assert.equal(dom.one<HTMLInputElement>('[data-tour="phase-scope"] input.f-scope-globs').value, "wip/security/*");
    assert.deepEqual(ids(dom).slice(-1), ["security-review"]);
    await next(dom);
    assert.equal(dom.one<HTMLInputElement>('[data-tour="phase-deliverables"] input').value, "wip/security/report.md");
    await next(dom);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1, "未保存の印が出ない");
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, false);
    await next(dom);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0);
  } finally {
    await dom.close();
  }

  // 保存の様子。未保存の印・検証が通らない文・作業中・外の変更
  dom = await openReal();
  try {
    await goto(dom, "save", 0);
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, false);
    await next(dom);
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, false);
    await next(dom);
    assert.match(dom.one("#status").textContent ?? "", /design-feedback は feedback なので review は mr/);
    assert.ok(dom.one(".foot").classList.contains("error"));
    await next(dom);
    assert.equal(dom.one("#status").textContent, "", "手順をまたいで検証の文が残った");
    assert.match(dom.one("#lock").textContent ?? "", /作業中のチケットがあります/);
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, "作業中は保存できない");
    await next(dom);
    assert.ok(dom.one("#lock").classList.contains("hidden"));
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true);
    await next(dom);
    assert.ok(dom.one("#changed").classList.contains("hidden"), "手順をまたいで外の変更の帯が残った");
  } finally {
    await dom.close();
  }
});

test("CB-D244 設定の切り替えの案内は、見本のプロジェクトの定義を描く。実際の対象は動かず、閉じたら実際の設定に戻る", async () => {
  const dom = await openPage({
    kind: "page",
    page: page({ model: readPhases(REAL_TEXT).model, phasesPath: "real/phases.yml", target: { kind: "project", name: "web" }, targets: [{ kind: "self", name: "", label: "ワークスペース" }, { kind: "project", name: "web", label: "プロジェクト web" }] }),
  });
  try {
    assert.equal(dom.one<HTMLSelectElement>("select#target").value, "project:web");
    await goto(dom, "project", 0);
    // 1 番目の手順はワークスペースの見本。欄の選択肢は見本のもの
    assert.equal(dom.one<HTMLSelectElement>("select#target").value, "self:");
    assert.deepEqual(
      dom.all<HTMLElement>("select#target option").map((option) => option.textContent),
      ["ワークスペース", "プロジェクト lib"],
    );
    assert.deepEqual(ids(dom), SAMPLE_IDS);
    assert.equal(dom.one(".path").textContent, "（見本）.ccnavi/config/project/phases.yml");
    await next(dom);
    await next(dom);
    assert.equal(dom.one<HTMLSelectElement>("select#target").value, "project:lib");
    assert.deepEqual(ids(dom), LIB_IDS);
    await next(dom);
    assert.equal(dom.one(".path").textContent, "（見本）projects/lib/.ccnavi/config/project/phases.yml");
    await next(dom);
    await next(dom);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1);
    await next(dom);
    assert.match(dom.one("#lock").textContent ?? "", /プロジェクト lib に作業中のチケット/);
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true);
    assert.deepEqual(extraPosts(dom), []);
    await next(dom);
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.one<HTMLSelectElement>("select#target").value, "project:web");
    assert.deepEqual(ids(dom), REAL_IDS);
    assert.equal(dom.one(".path").textContent, "real/phases.yml");
    assert.deepEqual(extraPosts(dom), []);
  } finally {
    await dom.close();
  }
});

test("CB-D245 案内の最中に読み込み中やエラーへ替わったら、案内を閉じて tourDone を返す。中身が戻っても案内は出直さない", async () => {
  for (const next of [
    { kind: "loading", text: "読み込み中…" },
    { kind: "error", error: "読み込めません" },
  ] as const) {
    const dom = await openReal();
    try {
      await goto(dom, "feedback", 2);
      assert.equal(dom.all(".tour").length, 1);
      await dom.send({ type: "data", data: next });
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, "指す先がないのに案内が残った");
      assert.equal(dom.all(".banner.tour-sample").length, 0);
      assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
      await dom.send({ type: "data", data: { kind: "page", page: page({ model: readPhases(REAL_TEXT).model }) } });
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, "中身が戻って案内が出直した");
      assert.deepEqual(ids(dom), REAL_IDS);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D246 実際に編集中の定義と未保存の印は、案内の間も閉じたあとも変わらない。案内の間に開いていたヘルプの説明も戻る", async () => {
  const dom = await openReal();
  try {
    dom.click(dom.one('[data-action="add"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="help"]'));
    await dom.settle();
    assert.equal(dom.all(".phase").length, 3);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1);
    assert.equal(dom.all("#help").length, 1);
    await goto(dom, "add-phase", 1);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 0, "実際の未保存の印が案内の間に出ている");
    assert.equal(dom.all("#help").length, 0, "実際のヘルプの説明が案内の間に出ている");
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, true, "案内の間に実際の保存が押せる");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".phase").length, 3);
    assert.equal(dom.all("#dirty:not(.hidden)").length, 1);
    assert.equal(dom.all("#help").length, 1);
    assert.equal(dom.one<HTMLButtonElement>("#save").disabled, false);
    // `dirty` は実際の編集で変わったときの 1 度だけ。案内は送らない
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), [{ type: "dirty", dirty: true }]);
  } finally {
    await dom.close();
  }
});
