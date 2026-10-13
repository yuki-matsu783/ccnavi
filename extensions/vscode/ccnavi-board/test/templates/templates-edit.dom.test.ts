/**
 * テンプレート管理画面の編集（欄の出し方・下見と実行・錠・使えないときの見せ方）を happy-dom で動かす。
 * 下見と実行の答えは拡張ホストが返すものなので、ここでは `editPreview` と `edited` を直接送って、画面の動きを見る。
 * 見本の下書きは `login`（本文は見本に無いので、テストで入れる）、共通の層のテンプレートは `feature`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { EditAnswer, EditRequest } from "../../src/core/templates-view.js";
import { editAnswer, fixtureJson, openTemplates } from "../helpers/templates.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLTextAreaElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は除く */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

/** 見本の login の本文（テストで入れる） */
const LOGIN_TEXT = "name: login\n";

/** 下書き login の本文を入れた画面。見本の他の中身はそのまま */
async function loginPage(): Promise<DomPage> {
  const base = fixtureJson();
  const drafts = base.drafts.map((draft) => (draft.name === "login" ? { ...draft, text: LOGIN_TEXT } : draft));
  return openTemplates({ data: { ...base, drafts } });
}

/** 行の操作のボタンを押す（欄を開く・下見を頼む・実行する、どれも押すだけ） */
async function clickAction(dom: DomPage, selector: string, action: string): Promise<void> {
  dom.click(dom.one(`${selector} [data-action="${action}"]`));
  await dom.settle();
}

/** 「この内容で…」を押す（下見の答えが出ているとき） */
async function clickYes(dom: DomPage): Promise<void> {
  dom.click(dom.one('[data-action="edit-yes"]'));
  await dom.settle();
}

/** 下見を頼み、頼んだ要求と同じ要求で答えを返す */
async function previewOf(dom: DomPage, request: EditRequest, overrides: Partial<EditAnswer> = {}): Promise<void> {
  dom.click(dom.one('[data-action="preview-edit"]'));
  await dom.settle();
  await dom.send({ type: "editPreview", request, answer: editAnswer(overrides), error: "" });
  await dom.settle();
}

const SAVE: EditRequest = { op: "save", name: "login", on: "draft", layer: "", newName: "", to: "", toLayer: "", body: LOGIN_TEXT };
const RENAME: EditRequest = { op: "rename", name: "login", on: "draft", layer: "", newName: "login2", to: "", toLayer: "", body: "" };
const DELETE_FEATURE: EditRequest = { op: "delete", name: "feature", on: "common", layer: "", newName: "", to: "", toLayer: "", body: "" };
const FEATURE_PATH = ".ccnavi/config/common/templates/feature.yml";
const FEATURE_DELETE_ANSWER: Partial<EditAnswer> = { op: "delete", name: "feature", on: "common", source: FEATURE_PATH, target: "", sha: "d".repeat(64) };

test("CB-D351 下書きの「編集」で編集の欄が開き、本文に下書きの text が入る。行は開かず、プレビューは body を載せた previewEdit を送る", async () => {
  const dom = await loginPage();
  try {
    await clickAction(dom, 'li.draft[data-name="login"]', "edit-draft");
    const panel = dom.one('[data-role="edit-panel"]');
    assert.equal(panel.getAttribute("data-op"), "save");
    assert.equal(panel.getAttribute("data-name"), "login");
    assert.equal(dom.one<HTMLTextAreaElement>("#edit-body").value, LOGIN_TEXT);
    assert.equal(dom.all("li.draft.open").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty").at(-1), { type: "dirty", dirty: true });
    dom.type(dom.one("#edit-body"), "name: login\nx: 1\n");
    dom.click(dom.one('[data-action="preview-edit"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "previewEdit"), [
      { type: "previewEdit", request: { ...SAVE, body: "name: login\nx: 1\n" } },
    ]);
  } finally {
    await dom.close();
  }
});

test("CB-D352 プレビューの答えが同じ要求なら差分を行ごとの色で出し、「この内容で保存」は見せた sha を expect にして editYes を送る。要求が違う答えは捨てる", async () => {
  const dom = await loginPage();
  try {
    await clickAction(dom, 'li.draft[data-name="login"]', "edit-draft");
    dom.click(dom.one('[data-action="preview-edit"]'));
    await dom.settle();
    // 本文が違う要求の答えは古い往復なので捨てる
    await dom.send({ type: "editPreview", request: { ...SAVE, body: "other\n" }, answer: editAnswer({ diff: ["+other"] }), error: "" });
    assert.equal(dom.all('[data-role="edit-diff"]').length, 0);
    // 頼んだ要求と同じ答えは出す
    await dom.send({ type: "editPreview", request: SAVE, answer: editAnswer({ diff: ["@@ -1 +1 @@", "-name: old", "+name: login"] }), error: "" });
    const rows = dom.all('[data-role="edit-diff"] > div');
    assert.deepEqual(rows.map((row) => row.className), ["diff-hunk", "diff-del", "diff-add"]);
    assert.equal(text(dom.one('[data-role="edit-sha"]')), "c".repeat(64));
    const yes = dom.one('[data-action="edit-yes"]');
    assert.equal(yes.hasAttribute("disabled"), false);
    assert.equal(text(yes), "この内容で保存");
    dom.click(yes);
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "editYes"), [
      { type: "editYes", request: SAVE, expect: "c".repeat(64) },
    ]);
  } finally {
    await dom.close();
  }
});

test("CB-D353 欄の入力を変えるとプレビューの答えは消え、実行のボタンも消える", async () => {
  const dom = await loginPage();
  try {
    await clickAction(dom, 'li.draft[data-name="login"]', "edit-draft");
    await previewOf(dom, SAVE, { diff: ["+name: login"] });
    assert.equal(dom.all('[data-role="edit-diff"]').length, 1);
    dom.type(dom.one("#edit-body"), "name: changed\n");
    assert.equal(dom.all('[data-role="edit-diff"]').length, 0);
    assert.equal(dom.all('[data-action="edit-yes"]').length, 0);
    assert.equal(dom.all('[data-role="edit-panel"]').length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D354 書けたら欄を閉じて結果を状態の欄に出す。書けなかった理由は欄を残して状態の欄に出す", async () => {
  const written = await loginPage();
  try {
    await clickAction(written, 'li.draft[data-name="login"]', "edit-draft");
    await previewOf(written, SAVE);
    await clickYes(written);
    await written.send({ type: "edited", request: SAVE, answer: editAnswer({ written: true }), error: "" });
    assert.equal(written.all('[data-role="edit-panel"]').length, 0);
    assert.equal(text(written.one("#status")), "保存しました: wip/proposals/templates/login.yml");
    assert.deepEqual(written.posted.filter((message) => message.type === "dirty").at(-1), { type: "dirty", dirty: false });
  } finally {
    await written.close();
  }
  const failed = await loginPage();
  try {
    await clickAction(failed, 'li.draft[data-name="login"]', "edit-draft");
    await previewOf(failed, SAVE);
    await clickYes(failed);
    await failed.send({ type: "edited", request: SAVE, answer: null, error: "書けませんでした: 権限がありません" });
    assert.equal(failed.all('[data-role="edit-panel"]').length, 1);
    assert.equal(text(failed.one("#status")), "書けませんでした: 権限がありません");
    assert.match(failed.one("#status").className, /failed/);
  } finally {
    await failed.close();
  }
});

test("CB-D355 共通のテンプレートの「削除」はプレビューから実行まで進む。作業中の錠が掛かると錠の帯が出て実行は押せず、下書きの削除には錠の帯が出ない", async () => {
  const free = await openTemplates({ target: { kind: "workspace", name: "" } });
  try {
    await clickAction(free, 'li.template[data-name="feature"][data-layer="common"]', "delete-template");
    assert.equal(free.one('[data-role="edit-panel"]').getAttribute("data-op"), "delete");
    assert.equal(text(free.one('[data-role="edit-confirm"]')), "feature を削除します。削除した中身は戻せません（git に入れていなければ復元できません）。");
    await previewOf(free, DELETE_FEATURE, FEATURE_DELETE_ANSWER);
    await clickYes(free);
    assert.deepEqual(free.posted.filter((message) => message.type === "editYes"), [
      { type: "editYes", request: DELETE_FEATURE, expect: "d".repeat(64) },
    ]);
    await free.send({ type: "edited", request: DELETE_FEATURE, answer: editAnswer({ ...FEATURE_DELETE_ANSWER, removed: true }), error: "" });
    assert.equal(free.all('[data-role="edit-panel"]').length, 0);
    assert.equal(text(free.one("#status")), `削除しました: ${FEATURE_PATH}`);
  } finally {
    await free.close();
  }
  const locked = await openTemplates({ target: { kind: "workspace", name: "" }, lock: { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] } });
  try {
    await clickAction(locked, 'li.template[data-name="feature"][data-layer="common"]', "delete-template");
    await previewOf(locked, DELETE_FEATURE, FEATURE_DELETE_ANSWER);
    assert.match(text(locked.one('[data-banner="edit-lock"]')), /この操作は止めています: 作業中のチケットがあります（T1）/);
    assert.equal(locked.one('[data-action="edit-yes"]').hasAttribute("disabled"), true);
    // 下書きの削除は、錠があっても止めない（錠の帯も出ない）
    await clickAction(locked, 'li.draft[data-name="login"]', "delete-draft");
    assert.equal(locked.all('[data-banner="edit-lock"]').length, 0);
    assert.equal(locked.one('[data-action="preview-edit"]').hasAttribute("disabled"), false);
  } finally {
    await locked.close();
  }
});

test("CB-D356 編集が使えない（古い版）と、下書きの編集・改名・削除と、テンプレートの改名・移動・削除は押せず、理由を title に出す", async () => {
  const dom = await openTemplates({ target: { kind: "workspace", name: "" }, edit: { enabled: false, reason: "古い版です" } });
  try {
    for (const action of ["edit-draft", "rename-draft", "delete-draft"]) {
      const button = dom.one(`li.draft[data-name="login"] [data-action="${action}"]`);
      assert.equal(button.hasAttribute("disabled"), true);
      assert.equal(button.getAttribute("title"), "古い版です");
    }
    for (const action of ["rename-template", "move-template", "delete-template"]) {
      const button = dom.one(`li.template[data-name="feature"][data-layer="common"] [data-action="${action}"]`);
      assert.equal(button.hasAttribute("disabled"), true);
      assert.equal(button.getAttribute("title"), "古い版です");
    }
  } finally {
    await dom.close();
  }
});

test("CB-D358 改名が途中で失敗（書けたが元を削除できなかった）と、欄を残して途中で失敗した旨を状態の欄に出す。成功の扱いにはしない", async () => {
  const dom = await loginPage();
  try {
    await clickAction(dom, 'li.draft[data-name="login"]', "rename-draft");
    dom.type(dom.one("#edit-new-name"), "login2");
    await dom.settle();
    await previewOf(dom, RENAME, { op: "rename", new_name: "login2" });
    await clickYes(dom);
    await dom.send({
      type: "edited",
      request: RENAME,
      answer: editAnswer({ op: "rename", ok: false, written: true, removed: false, errors: ["元の i0001.yml を削除できなかった"] }),
      error: "",
    });
    assert.equal(dom.all('[data-role="edit-panel"]').length, 1);
    assert.match(text(dom.one("#status")), /途中で失敗しました/);
    assert.match(dom.one("#status").className, /failed/);
  } finally {
    await dom.close();
  }
});
