/**
 * テンプレート管理画面の取り込み（取り込み先の選択・下見・取り込みの実行）を happy-dom で動かす。
 * 下見と取り込みの答えは拡張ホストが返すものなので、ここでは `preview` と `imported` を直接送って、画面の動きを見る。
 * 見本の下書きは `login`（同名なし・取り込める）、`research`（同名あり・取り込める）、`broken`（取り込めない）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { ImportAnswer, ImportRequest } from "../../src/core/templates-view.js";
import { answer, fixtureJson, openTemplates, page } from "../helpers/templates.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は除く */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

/** 下書きの取り込みの欄を開く（見出しの「取り込む…」） */
async function openImport(dom: DomPage, name: string): Promise<void> {
  dom.click(dom.one(`li.draft[data-name="${name}"] [data-action="open-import"]`));
  await dom.settle();
}

/** 取り込み先を選ぶ（値は `common` か `project:<名前>`） */
async function chooseTo(dom: DomPage, value: string): Promise<void> {
  dom.change(dom.one<HTMLSelectElement>("#import-to"), value);
  await dom.settle();
}

/** 下見を頼み、答えを送る（頼んだ要求と同じ要求で返す） */
async function previewOf(dom: DomPage, request: ImportRequest, overrides: Partial<ImportAnswer> = {}): Promise<void> {
  dom.click(dom.one('[data-action="preview-import"]'));
  await dom.settle();
  await dom.send({ type: "preview", request, answer: answer(overrides), error: "" });
  await dom.settle();
}

const LOGIN_COMMON: ImportRequest = { draft: "login", to: "common", layer: "", replace: false };

test("CB-D335 「取り込む…」で下書きの取り込みの欄が開く。取り込み先は同名のある先頭の層（無ければ共通）、置き換えはその層に同名があるか。取り込めない下書きは押せない", async () => {
  const dom = await openTemplates();
  try {
    assert.equal(dom.all('[data-role="import-form"]').length, 0);
    await openImport(dom, "login");
    assert.equal(dom.one('[data-role="import-form"]').getAttribute("data-draft"), "login");
    assert.equal(dom.one<HTMLSelectElement>("#import-to").value, "common");
    assert.equal(dom.one<HTMLInputElement>("#import-replace").checked, false);
    // 見出しの開閉と混ざらない（欄を開いても行は開かない）
    assert.equal(dom.all("li.draft.open").length, 0);
    await openImport(dom, "research");
    // 同時に開けるのは 1 件。前の欄は閉じる
    assert.equal(dom.all('[data-role="import-form"]').length, 1);
    assert.equal(dom.one('[data-role="import-form"]').getAttribute("data-draft"), "research");
    assert.equal(dom.one<HTMLSelectElement>("#import-to").value, "common");
    assert.equal(dom.one<HTMLInputElement>("#import-replace").checked, true);
    const broken = dom.one('li.draft[data-name="broken"] [data-action="open-import"]');
    assert.equal(broken.hasAttribute("disabled"), true);
    assert.equal(broken.getAttribute("title"), "検査で error があるので取り込めません");
    assert.equal(dom.one('li.draft[data-name="login"] [data-action="open-import"]').hasAttribute("disabled"), false);
  } finally {
    await dom.close();
  }
});

test("CB-D336 下見は previewImport を送り、続けて dirty を伝える。下見の往復の間は下見を押せない。取り込み先を選び直すと、その要求で下見を頼む", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    dom.click(dom.one('[data-action="preview-import"]'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "previewImport"),
      [{ type: "previewImport", request: LOGIN_COMMON }],
    );
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty"),
      [{ type: "dirty", dirty: true }],
    );
    assert.equal(dom.one('[data-action="preview-import"]').hasAttribute("disabled"), true);
    // 取り込み先を変えると往復は捨てる（dirty は偽に戻る）
    await chooseTo(dom, "project:self");
    assert.equal(dom.one('[data-action="preview-import"]').hasAttribute("disabled"), false);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty").at(-1),
      { type: "dirty", dirty: false },
    );
    dom.click(dom.one('[data-action="preview-import"]'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "previewImport").at(-1),
      { type: "previewImport", request: { draft: "login", to: "project", layer: "self", replace: false } },
    );
  } finally {
    await dom.close();
  }
});

test("CB-D337 下見の答えを出し、「この内容で取り込む」は見せた sha を expect にして importYes を送る", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await previewOf(dom, LOGIN_COMMON);
    assert.equal(text(dom.one('[data-role="draft-sha"]')), "a".repeat(64));
    assert.equal(dom.one('[data-role="new-text"]').textContent, "name: login\n");
    assert.equal(dom.all('[data-role="diff"]').length, 0);
    const importYes = dom.one('[data-action="import-yes"]');
    assert.equal(importYes.hasAttribute("disabled"), false);
    dom.click(importYes);
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "importYes"),
      [{ type: "importYes", request: LOGIN_COMMON, expect: "a".repeat(64) }],
    );
  } finally {
    await dom.close();
  }
});

test("CB-D338 同名ありの下見は差分を行ごとの色で出し、先頭の記号を残す。本文は出さない。取り込み先を選び直すと置き換えも合わせ直す", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "research");
    const request: ImportRequest = { draft: "research", to: "common", layer: "", replace: true };
    await previewOf(dom, request, {
      draft: { name: "research", tree: "", path: "research.yml", sha: "b".repeat(64) },
      exists: true,
      replace: true,
      diff: ["--- a", "+++ b", "@@ -1 +1 @@", "-x", "+y"],
      new_text: "",
    });
    const rows = dom.all('[data-role="diff"] > div');
    assert.deepEqual(
      rows.map((row) => row.className),
      ["diff-del", "diff-add", "diff-hunk", "diff-del", "diff-add"],
    );
    assert.equal(text(rows[3]), "-x");
    assert.equal(text(rows[4]), "+y");
    assert.equal(dom.all('[data-role="new-text"]').length, 0);
    // 置き換えの既定は、選んだ取り込み先の層に同名があるか。lib には無いので偽になる
    await chooseTo(dom, "project:lib");
    assert.equal(dom.one<HTMLInputElement>("#import-replace").checked, false);
    await chooseTo(dom, "project:self");
    assert.equal(dom.one<HTMLInputElement>("#import-replace").checked, true);
  } finally {
    await dom.close();
  }
});

test("CB-D339 錠は取り込み先で選ぶ。共通は全体の錠で止まる。プロジェクトはそのプロジェクトの錠で止まり、無ければ全体の錠にそろう", async () => {
  // 共通への取り込みは全体の錠で止まる
  const common = await openTemplates({ lock: { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] } });
  try {
    await openImport(common, "login");
    await previewOf(common, LOGIN_COMMON);
    assert.equal(common.one('[data-action="import-yes"]').hasAttribute("disabled"), true);
    assert.match(text(common.one('[data-banner="import-lock"]')), /作業中のチケットがあります（T1）/);
  } finally {
    await common.close();
  }
  // プロジェクトの錠は、そのプロジェクトの分だけ止める
  const project = await openTemplates({
    projectLocks: { self: { locked: true, reason: "プロジェクト self で作業中（T2）", doing: ["T2"] } },
  });
  try {
    await openImport(project, "login");
    await chooseTo(project, "project:self");
    await previewOf(project, { draft: "login", to: "project", layer: "self", replace: false }, { target: ".ccnavi/config/project/templates/login.yml" });
    assert.equal(project.one('[data-action="import-yes"]').hasAttribute("disabled"), true);
    assert.match(text(project.one('[data-banner="import-lock"]')), /プロジェクト self で作業中（T2）/);
    // lib には錠が無い。全体の錠も無いので取り込める
    await chooseTo(project, "project:lib");
    await previewOf(project, { draft: "login", to: "project", layer: "lib", replace: false }, { target: ".ccnavi/config/project/templates/login.yml" });
    assert.equal(project.one('[data-action="import-yes"]').hasAttribute("disabled"), false);
    assert.equal(project.all('[data-banner="import-lock"]').length, 0);
  } finally {
    await project.close();
  }
  // 錠が projectLocks に無いプロジェクトは、全体の錠にそろう
  const global = await openTemplates({ lock: { locked: true, reason: "作業中のチケットがあります（T3）", doing: ["T3"] } });
  try {
    await openImport(global, "login");
    await chooseTo(global, "project:lib");
    await previewOf(global, { draft: "login", to: "project", layer: "lib", replace: false }, { target: ".ccnavi/config/project/templates/login.yml" });
    assert.equal(global.one('[data-action="import-yes"]').hasAttribute("disabled"), true);
    assert.match(text(global.one('[data-banner="import-lock"]')), /作業中のチケットがあります（T3）/);
  } finally {
    await global.close();
  }
});

test("CB-D340 答えが ok でなければ、error の行を [error] の文字つきで出し、「この内容で取り込む」は押せない", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await previewOf(dom, LOGIN_COMMON, { ok: false, errors: ["フェーズ定義に無い種類です"] });
    const row = dom.one('[data-role="import-preview"] li.sev-error');
    assert.match(text(row.querySelector(".sev-label")), /\[error\]/);
    assert.match(text(row), /フェーズ定義に無い種類です/);
    assert.equal(dom.one('[data-action="import-yes"]').hasAttribute("disabled"), true);
  } finally {
    await dom.close();
  }
});

test("CB-D341 取り込み先を変えるとプレビューが消え、dirty を偽にして伝える。違う要求の preview は古い往復として無視する", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await previewOf(dom, LOGIN_COMMON);
    assert.equal(dom.all('[data-role="import-preview"]').length, 1);
    await chooseTo(dom, "project:self");
    assert.equal(dom.all('[data-role="import-preview"]').length, 0);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty").at(-1),
      { type: "dirty", dirty: false },
    );
    // 取り込み先を変えたあとに、前の取り込み先への答えが届いても出さない
    await dom.send({ type: "preview", request: LOGIN_COMMON, answer: answer(), error: "" });
    await dom.settle();
    assert.equal(dom.all('[data-role="import-preview"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D342 取り込みが書けたら、結果を帯に出して欄とプレビューを閉じる。書けなかった理由は状態の欄に出し、プレビューは残す", async () => {
  const written = await openTemplates();
  try {
    await openImport(written, "login");
    await previewOf(written, LOGIN_COMMON);
    clickImport(written);
    await written.settle();
    await written.send({
      type: "imported",
      request: LOGIN_COMMON,
      answer: answer({ written: true, removed: true, target: ".ccnavi/config/common/templates/login.yml" }),
      error: "",
    });
    await written.settle();
    assert.equal(text(written.one("#status")), "取り込みました: .ccnavi/config/common/templates/login.yml。下書きを消しました");
    assert.equal(written.all('[data-role="import-preview"]').length, 0);
    assert.equal(written.all('[data-role="import-form"]').length, 0);
    assert.deepEqual(
      written.posted.filter((message) => message.type === "dirty").at(-1),
      { type: "dirty", dirty: false },
    );
  } finally {
    await written.close();
  }
  const failed = await openTemplates();
  try {
    await openImport(failed, "login");
    await previewOf(failed, LOGIN_COMMON);
    clickImport(failed);
    await failed.settle();
    await failed.send({ type: "imported", request: LOGIN_COMMON, answer: null, error: "書けませんでした: 権限がありません" });
    await failed.settle();
    assert.equal(text(failed.one("#status")), "書けませんでした: 権限がありません");
    assert.match(failed.one("#status").className, /failed/);
    assert.equal(failed.all('[data-role="import-preview"]').length, 1);
  } finally {
    await failed.close();
  }
});

test("CB-D343 実行ファイルの文字列は文字としてそのまま出す。HTML として解釈せず、b 要素も作らない", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await previewOf(dom, LOGIN_COMMON, { new_text: "<b>x</b>\\u202e" });
    const pre = dom.one('[data-role="new-text"]');
    assert.equal(pre.textContent, "<b>x</b>\\u202e");
    assert.equal(pre.querySelectorAll("b").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D344 案内の間は「取り込む…」を押しても欄を開かず、開いていた実際の欄は見本に替わって下見も出ない", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "research");
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    dom.click(dom.one('[data-action="tour-course"][data-course="import"]'));
    await dom.settle();
    // 案内の間は実際の取り込みの欄を描かず（見本を描く）、「下見」も出ない
    assert.equal(dom.all('[data-role="import-form"]').length, 0);
    assert.equal(dom.all('[data-action="preview-import"]').length, 0);
    await openImport(dom, "login");
    assert.equal(dom.all('[data-role="import-form"][data-draft="login"]').length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type !== "ready"), []);
  } finally {
    await dom.close();
  }
});

test("CB-D345 取り込みが書かずに返ったら、その答えを下見に入れ替えて「取り込めませんでした」と出す", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await previewOf(dom, LOGIN_COMMON);
    clickImport(dom);
    await dom.settle();
    await dom.send({
      type: "imported",
      request: LOGIN_COMMON,
      answer: answer({ ok: false, errors: ["同じ名前のテンプレートが既にあります"], written: false }),
      error: "",
    });
    await dom.settle();
    assert.equal(text(dom.one("#status")), "取り込めませんでした");
    assert.match(text(dom.one('[data-role="import-preview"] li.sev-error')), /同じ名前のテンプレートが既にあります/);
    assert.equal(dom.one('[data-action="import-yes"]').hasAttribute("disabled"), true);
  } finally {
    await dom.close();
  }
});

/** この下見の「この内容で取り込む」を押す（テストの読みやすさのため） */
function clickImport(dom: DomPage): void {
  dom.click(dom.one('[data-action="import-yes"]'));
}

test("CB-D347 案内の「取り込む…」は取り込める最初の 1 件（login）に付く。先頭の broken（取り込めない）には付かず、案内の下見の段は押せるボタンを指す", async () => {
  const dom = await openTemplates();
  try {
    const marks = dom.all('[data-tour="draft-import"]');
    assert.equal(marks.length, 1);
    assert.equal(marks[0].closest("li.draft")?.getAttribute("data-name"), "login");
    assert.equal(marks[0].hasAttribute("disabled"), false);
    assert.equal(dom.one('li.draft[data-tour="draft"]').getAttribute("data-name"), "broken");
  } finally {
    await dom.close();
  }
});

test("CB-D349 取り込みが書けたあとの読み直し（data）が先に届いても、「取り込みました」の帯は残る。プレビューと欄は閉じている", async () => {
  const dom = await openTemplates();
  try {
    await openImport(dom, "login");
    await chooseTo(dom, "common");
    await previewOf(dom, LOGIN_COMMON);
    clickImport(dom);
    await dom.settle();
    // 拡張ホストの送信順は、読み直しの data が先、取り込みの答え（imported）が後
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.send({
      type: "imported",
      request: LOGIN_COMMON,
      answer: answer({ written: true, removed: true, target: ".ccnavi/config/common/templates/login.yml" }),
      error: "",
    });
    await dom.settle();
    assert.equal(text(dom.one("#status")), "取り込みました: .ccnavi/config/common/templates/login.yml。下書きを消しました");
    assert.equal(dom.all('[data-role="import-preview"]').length, 0);
    assert.equal(dom.all('[data-role="import-form"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D350 同名の下書きが別のツリーにもあると、その名前の 2 行とも「取り込む…」は押せず、同名の札を出し、取り込みの欄は開かない。案内は取り込める別の下書きを指す", async () => {
  const base = fixtureJson();
  const login = base.drafts.find((draft) => draft.name === "login");
  assert.ok(login !== undefined);
  const dom = await openTemplates({ data: { ...base, drafts: [...base.drafts, { ...login, tree: "lib", sha: "c".repeat(64), also_in: [] }] } });
  try {
    const rows = dom.all('li.draft[data-name="login"]');
    assert.equal(rows.length, 2);
    for (const row of rows) {
      assert.match(text(row), /同名が別のツリーにもある/);
      const button = row.querySelector('[data-action="open-import"]');
      assert.ok(button !== null);
      assert.equal(button.hasAttribute("disabled"), true);
      assert.match(button.getAttribute("title") ?? "", /名前では指せません/);
    }
    dom.click(dom.one('li.draft[data-name="login"] [data-action="open-import"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="import-form"]').length, 0);
    // 案内の「取り込む…」は、取り込める別の下書き（research）に付く
    assert.equal(dom.one('[data-tour="draft-import"]').closest("li.draft")?.getAttribute("data-name"), "research");
  } finally {
    await dom.close();
  }
});
