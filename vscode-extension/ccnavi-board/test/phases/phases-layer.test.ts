/**
 * 層（自身の層・プロジェクト）と共通層の種類。ファイルが無いときの見せ方と、無いファイルへの書き戻し。
 * 画面の側は React なので happy-dom で動かして見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../../src/core/phases-doc.js";
import type { PhasesPage } from "../../src/core/phases-view.js";
import { openPhases } from "../helpers/phases.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const MISSING: Partial<PhasesPage> = { exists: false, model: { version: null, form: { order: "sequential", phases: [] }, problems: [] } };

test("CB-T114 層の種類のファイルが無いときは雛形を置かず、欄を触れるようにして最初の保存で作らせる", async () => {
  const layer = await openPhases({ ...MISSING, layer: true, notices: ["読めない <理由>"] });
  try {
    assert.match(layer.one(".banner.missing").textContent, /最初の保存でファイルが作られる/);
    assert.equal(layer.all('button[data-action="create"]').length, 0, "層に雛形は置かない");
    // 文面はそのまま出る（React が文字として入れるので、実体参照に変わらない）
    assert.equal(layer.all(".banner.warn:not(#changed)").length, 1);
    assert.equal(layer.one(".banner.warn:not(#changed)").textContent, "読めない <理由>");
    // 無い層でも種類を足して保存できる
    assert.ok(!layer.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.match(layer.one("#phases .empty").textContent, /種類を足して保存すると、ファイルが作られる/);
    layer.click(layer.one('button[data-action="add"]'));
    await layer.settle();
    assert.ok(!layer.one<HTMLInputElement>(".phase input.f-id").disabled);
  } finally {
    await layer.close();
  }
});

test("CB-T239 共通層のファイルが無いときは雛形を作らせず、種類は層に置くと案内して自身の層を開く道だけを出す", async () => {
  const common = await openPhases({ ...MISSING, phasesPath: ".ccnavi/common/phases.yml" });
  try {
    const banner = common.one(".banner.missing").textContent;
    assert.match(banner, /共通層に種類は無い/);
    assert.match(banner, /種類は各層（ワークスペース自身・プロジェクト）に置く/);
    assert.match(banner, /プロジェクト管理画面の「フェーズ管理」から開く/);
    assert.equal(common.all('button[data-action="create"]').length, 0, "共通層に雛形を作るボタンは出さない");
    assert.ok(!/雛形/.test(common.one("body").textContent), "雛形で作る道を案内しない");
    // 欄は触れない（画面から共通層のファイルを作らせない）。注意が無ければ帯を足さない
    assert.ok(common.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.match(common.one("#phases .empty").textContent, /種類は層に置く/);
    assert.equal(common.all(".banner.warn:not(#changed)").length, 0);
    // 自身の層を開くボタンは、拡張ホストへ openSelf だけを送る（ファイルは作らない）
    assert.equal(common.one('button[data-action="open-self"]').textContent, "自身の層を開く");
    common.click(common.one('button[data-action="open-self"]'));
    await common.settle();
    assert.deepEqual(
      common.posted.filter((message) => message.type !== "ready"),
      [{ type: "openSelf" }],
    );
  } finally {
    await common.close();
  }
});

test("CB-T116 無いファイル（空の本文）に種類を足して書き戻すと、version と種類を持つ読めるファイルになる", () => {
  const text = readPhases("").apply({
    order: "sequential",
    phases: [
      {
        origin: null,
        id: "notes",
        title: "メモ",
        kind: "work",
        review: "none",
        inherit: true,
        scope: [],
        deliverables: [],
        overlap: [],
        requires: [],
        after: [],
        agent: "",
        when: "",
      },
    ],
  });
  assert.match(text, /^version: 1$/m);
  assert.match(text, /^phases:\n {2}notes:\n/m);
  assert.ok(!text.includes("{}"));
  // 層に作るときは先頭に説明のコメントを足す。足しても読み直して苦情が出ない
  const again = readPhases(`# 説明\n${text}`);
  assert.deepEqual(again.model.problems, []);
  assert.deepEqual(again.model.form.phases.map((p) => [p.id, p.title, p.inherit]), [["notes", "メモ", true]]);
});
