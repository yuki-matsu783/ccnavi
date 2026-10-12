/**
 * プロジェクト管理画面の案内（`src/webview/tours/projects.yml`）を happy-dom で動かす。案内の間は見本を描き、実際の一覧・
 * clone の欄・状態・拡張ホストへの送信には触れない。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { openProjects, page, row } from "../helpers/projects.js";
import { loadTour } from "../helpers/tour.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const def = loadTour("projects");
const SAMPLE_NAMES = ["app", "lib", "common"];

/** 実際の画面（見本とは別の名前・origin・clone の欄） */
const REAL_ROWS = [
  row({ name: "billing", rel: "projects/billing", origin: "https://real.example/billing.git" }),
  row({ name: "web", rel: "projects/web", origin: "https://real.example/web.git" }),
];
const REAL_STRAYS = [{ path: "real/stray", reason: "実際の認識されない git" }];
const REAL_CLONE = { url: "https://real.example/x.git", name: "x", nameTouched: true };

async function open(overrides: Parameters<typeof openProjects>[1] = {}): Promise<DomPage> {
  return openProjects(REAL_ROWS, { ignored: true, strays: REAL_STRAYS, ...overrides }, REAL_CLONE);
}

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

const cardNames = (dom: DomPage): string[] => dom.all("li.project").map((li) => li.getAttribute("data-name") ?? "");
const inputs = (dom: DomPage): { url: string; name: string } => ({ url: dom.one<HTMLInputElement>("#url").value, name: dom.one<HTMLInputElement>("#name").value });

test("CB-D321 プロジェクト管理画面の「?」は案内の一覧を開き、基本の案内が先頭。初回の依頼でも基本の案内が始まり、閉じたら tourDone を返す", async () => {
  const dom = await open();
  try {
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    const items = dom.all('[data-action="tour-course"]');
    assert.deepEqual(items.map((item) => item.dataset.course), def.courses.map((course) => course.id));
    assert.equal(items[0].dataset.course, "intro");
    assert.equal(dom.all(".tour").length, 0, "一覧を開いただけで案内が始まった");
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, def.courses[0].steps[0].title);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
});

test("CB-D322 全ユースケースの全段で、指す先が画面にあり、吹き出しが中ほどへ逃げない。見本だけを描き、閉じたら実際の表示と clone の欄に戻る", async () => {
  for (const course of def.courses) {
    const dom = await open();
    try {
      assert.deepEqual(cardNames(dom), ["billing", "web"]);
      assert.deepEqual(inputs(dom), { url: REAL_CLONE.url, name: REAL_CLONE.name });
      await startCourse(dom, course.id);
      for (const [index, step] of course.steps.entries()) {
        await dom.settle();
        const at = `${course.id} の ${index + 1} 段目（${step.title}）`;
        assert.equal(dom.one("#tour-title").textContent, step.title, at);
        assert.ok(dom.all(`[data-tour="${step.target}"]`).length >= 1, `${at}: 指す先 ${step.target} が画面に無い`);
        assert.equal(dom.all(`[data-tour="${step.target}"]`).length, 1, `${at}: 指す先 ${step.target} が複数ある`);
        assert.equal(dom.all(".tour-bubble.center").length, 0, `${at}: 吹き出しが中ほどに出た（指す先が無い）`);
        // 見本だけが描かれ、実際のプロジェクト・認識されない git・clone の欄は出ない
        const names = cardNames(dom);
        assert.ok(names.length > 0 && names.every((name) => SAMPLE_NAMES.includes(name)), `${at}: 実際のカードが出ている（${names.join(", ")}）`);
        assert.ok(!(dom.one("#root").textContent ?? "").includes("real.example"), `${at}: 実際の origin が出ている`);
        assert.ok(!(dom.one("#root").textContent ?? "").includes("real/stray"), `${at}: 実際の認識されない git が出ている`);
        assert.notEqual(inputs(dom).url, REAL_CLONE.url, `${at}: 実際の clone の欄が出ている`);
        assert.equal(dom.all(".banner.tour-sample").length, 1, at);
        await next(dom);
      }
      await dom.settle();
      assert.equal(dom.all(".tour").length, 0, `${course.id}: 最後の段で閉じない`);
      assert.deepEqual(cardNames(dom), ["billing", "web"], "閉じたあと実際のプロジェクトに戻らない");
      assert.deepEqual(inputs(dom), { url: REAL_CLONE.url, name: REAL_CLONE.name }, "閉じたあと実際の clone の欄に戻らない");
      assert.deepEqual(dom.all(".stray-list li .mono").map((el) => el.textContent), ["real/stray"]);
      assert.equal(dom.all(".banner.tour-sample").length, 0);
      assert.equal(dom.all(".banner.warn").length, 0, "案内の帯が残った");
      assert.equal(dom.one("#status").className, "status hidden");
      assert.deepEqual(dom.state(), REAL_CLONE, "案内が clone の欄の覚えを書き換えた");
      assert.deepEqual(extraPosts(dom), [], "案内の間に余計な送信があった");
      assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
    } finally {
      await dom.close();
    }
  }
});

test("CB-D323 案内の間に clone・更新・.gitignore を押しても、clone の欄を打っても、拡張ホストへ送らず実際の欄も変わらない。閉じたあとは実際の操作に戻る", async () => {
  const dom = await open({ ignored: false });
  try {
    await goto(dom, "ignore-projects", 1);
    assert.equal(dom.all('[data-action="fix-ignore"]').length, 1);
    for (const action of ["refresh", "clone", "fix-ignore"]) {
      dom.click(dom.one(`[data-action="${action}"]`));
    }
    dom.type(dom.one("#url"), "https://打ってみる.example/zzz.git");
    dom.type(dom.one("#name"), "zzz");
    await dom.settle();
    assert.deepEqual(extraPosts(dom), []);
    assert.deepEqual(dom.state(), REAL_CLONE, "案内の間の入力が欄の覚えに入った");
    assert.notEqual(inputs(dom).url, "https://打ってみる.example/zzz.git", "見本の欄が打った値で変わった");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(inputs(dom), { url: REAL_CLONE.url, name: REAL_CLONE.name });
    // 閉じたあとの実際の操作は送る
    dom.click(dom.one('[data-action="clone"]'));
    dom.click(dom.one('[data-action="refresh"]'));
    dom.click(dom.one('[data-action="fix-ignore"]'));
    await dom.settle();
    assert.deepEqual(extraPosts(dom), [
      { type: "clone", url: REAL_CLONE.url, name: REAL_CLONE.name },
      { type: "refresh" },
      { type: "fixIgnore" },
    ]);
  } finally {
    await dom.close();
  }
});

test("CB-D330 scene が見せるカード・認識されない git・帯を決める。cards は書いた順に並び、scene は次の段へ引き継がない", async () => {
  const dom = await open();
  try {
    // 認識されない git の案内: 見本の 2 件が出る
    await goto(dom, "fix-stray", 0);
    assert.equal(dom.all("section.strays").length, 1);
    assert.deepEqual(dom.all(".stray-list li .mono").map((el) => el.textContent), ["projects/group/deep", "reference/old-repo"]);
    for (let i = 0; i < 4; i += 1) {
      await next(dom);
    }
    // 最後の段は strays を書かない: 枠が消え、cards の順（app, lib）で並ぶ
    assert.equal(dom.all("section.strays").length, 0, "段をまたいで認識されない git が残った");
    assert.deepEqual(cardNames(dom), ["app", "lib"]);
    assert.equal(dom.one("section.list .count").textContent, "2");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();

    // 書いた順に並ぶ（見本の並びは app, lib, common）
    await goto(dom, "read-problems", 1);
    assert.deepEqual(cardNames(dom), ["common", "lib"]);
    assert.equal(dom.all('li.project[data-tour="project"]').length, 1);
    assert.equal(dom.one('li.project[data-tour="project"]').getAttribute("data-name"), "common");
    assert.ok(dom.one('li.project[data-name="common"] .badge.error').textContent === "error");
    assert.match(dom.one('li.project[data-name="common"] .lint').textContent ?? "", /予約してある/);
    await next(dom);
    assert.equal(dom.one('li.project[data-tour="project"]').getAttribute("data-name"), "lib");
    assert.equal(dom.all('li.project[data-name="lib"] .badge.warn').length, 1);
    assert.match(dom.one('li.project[data-name="lib"] .lint').textContent ?? "", /\.claude\/ があります/);
    assert.equal(dom.all('li.project[data-name="lib"] .lint li').length, 1, ".claude/ の指摘が重なっている");
    await next(dom);
    assert.equal(dom.all('li.project[data-name="app"] .ok').length >= 1, true);
    assert.equal(dom.one('li.project[data-tour="project"]').getAttribute("data-name"), "app");
    assert.match(dom.one('[data-tour="project-check"]').textContent ?? "", /問題なし/);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();

    // 帯: ignore は追加のボタン付き、tracked はボタンなしで苦情だけ。書かない段は帯なし
    await goto(dom, "ignore-projects", 0);
    assert.equal(dom.all('.banner [data-action="fix-ignore"]').length, 1);
    assert.equal(dom.all('.banner.warn[data-tour="banner"]').length, 1);
    await next(dom);
    await next(dom);
    assert.equal(dom.all(".banner.warn").length, 0, "段をまたいで .gitignore の帯が残った");
    assert.equal(dom.one("#status").textContent, ".gitignore に /projects/ を足しました。コミットは自分でしてください");
    assert.ok(!dom.one("#status").className.includes("failed"));
    await next(dom);
    assert.equal(dom.all('[data-action="fix-ignore"]').length, 0, "索引に載っているときに追加のボタンが出た");
    assert.equal(dom.all(".banner.warn").length, 1);
    assert.match(dom.one(".banner.warn").textContent ?? "", /追跡している/);
    assert.equal(dom.one("#status").className, "status hidden", "段をまたいで一言が残った");
  } finally {
    await dom.close();
  }
});

test("CB-D331 scene が clone の欄の例と一言を決める。失敗の一言は赤く出て、書かない段では空に戻る", async () => {
  const dom = await open();
  try {
    await goto(dom, "clone-refused", 0);
    assert.deepEqual(inputs(dom), { url: "/home/me/work/api", name: "" });
    assert.ok(dom.one("#status").className.includes("failed"));
    assert.match(dom.one("#status").textContent ?? "", /使える形式は/);
    await next(dom);
    assert.match(dom.one<HTMLInputElement>("#url").value, /glpat-xxxx@/);
    assert.match(dom.one("#status").textContent ?? "", /資格情報/);
    await next(dom);
    assert.deepEqual(inputs(dom), { url: "https://gitlab.example.com/team/api.git", name: "ライブラリ" });
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();

    // 基本の案内: clone の段だけ例が入り、次の段では空に戻る。送ったあとの一言は失敗の色ではない
    await goto(dom, "intro", 1);
    assert.deepEqual(inputs(dom), { url: "https://gitlab.example.com/team/api.git", name: "api" });
    assert.equal(dom.one("#status").className, "status hidden");
    await next(dom);
    assert.deepEqual(inputs(dom), { url: "", name: "" }, "段をまたいで clone の欄の例が残った");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();

    await goto(dom, "add-project", 3);
    assert.deepEqual(inputs(dom), { url: "", name: "" }, "送ったあとは URL と名前が消える");
    assert.match(dom.one("#status").textContent ?? "", /^git clone を「ccnavi」ターミナルに送りました（projects\/api）/);
    assert.ok(!dom.one("#status").className.includes("failed"));
  } finally {
    await dom.close();
  }
});

test("CB-D332 案内の間に届いた一覧・失敗の一言は見本に混ざらず、閉じたあとに実際の表示へ反映される。案内を選び直しても前の様子を引き継がない", async () => {
  const dom = await open();
  try {
    await goto(dom, "clone-refused", 0);
    await dom.send({ type: "failed", message: "実際の失敗" });
    await dom.send({ type: "data", data: { kind: "page", page: page([row({ name: "later", rel: "projects/later" })]) } });
    await dom.settle();
    assert.match(dom.one("#status").textContent ?? "", /使える形式は/);
    assert.deepEqual(cardNames(dom), ["app", "lib", "common"]);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.deepEqual(cardNames(dom), ["later"]);
    // 一覧が新しくなったので、その前の失敗は消えている（実際の画面の決まりのまま）
    assert.equal(dom.one("#status").className, "status hidden");
    await dom.send({ type: "failed", message: "閉じたあとの失敗" });
    assert.equal(dom.one("#status").textContent, "閉じたあとの失敗");

    // 別の案内を始めると、前の案内の様子が残らない
    await goto(dom, "ignore-projects", 0);
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    await startCourse(dom, "fix-stray");
    assert.equal(dom.all(".banner.warn").length, 0);
    assert.equal(dom.all("section.strays").length, 1);
    assert.deepEqual(extraPosts(dom), []);
  } finally {
    await dom.close();
  }
});
