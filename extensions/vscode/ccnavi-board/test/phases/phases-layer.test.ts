/**
 * ワークスペースの設定とプロジェクトの設定の定義（共通の設定には置けない）。ファイルが無いときの見せ方と、無いファイルへの書き戻し、共通の設定に phases.yml があるときの error の帯。
 * 画面の側は React なので happy-dom で動かして見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../../src/core/phases-doc.js";
import type { PhasesPage } from "../../src/core/phases-view.js";
import { openPhases } from "../helpers/phases.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const MISSING: Partial<PhasesPage> = { exists: false, model: { version: null, form: { phases: [] }, problems: [], unread: [] } };

test("CB-T114 定義のファイルが無いのは正常で、不備の帯も作るボタンも出さず、欄を触れるようにして最初の保存で作らせる", async () => {
  const dom = await openPhases({ ...MISSING, notices: ["読めない <理由>"] });
  try {
    assert.equal(dom.all(".banner.missing").length, 0, "ファイルが無いことを不備として出さない");
    assert.equal(dom.all('button[data-action="create"]').length, 0, "雛形は置かない");
    assert.equal(dom.all('button[data-action="open-self"]').length, 0, "共通向けの「自身のレイヤーを開く」は無い");
    // 文面はそのまま出る（React が文字として入れるので、実体参照に変わらない）
    assert.equal(dom.all(".banner.warn:not(#changed)").length, 1);
    assert.equal(dom.one(".banner.warn:not(#changed)").textContent, "読めない <理由>");
    // 無くても定義を足して保存できる
    assert.ok(!dom.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.match(dom.one("#phases .empty").textContent, /定義を足して保存すると、ファイルが作られます/);
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    assert.ok(!dom.one<HTMLInputElement>(".phase input.f-id").disabled);
  } finally {
    await dom.close();
  }
});

test("CB-T239 共通の設定に phases.yml があるときは error の帯を出し、画面は開いたまま触れる。画面から消す・直す手段は出さない", async () => {
  const dom = await openPhases({ errors: ["共通の設定に phases.yml があります（.ccnavi/common/phases.yml）。共通の設定には置けない。使われない。"] });
  try {
    assert.match(dom.one(".banner.error").textContent, /共通の設定には置けない。使われない/);
    assert.equal(dom.all(".banner.error").length, 1);
    // 画面は開いている。欄は触れる（保存を止めるのは作業中のチケットだけ）
    assert.ok(!dom.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.equal(dom.all('button[data-action="create"], button[data-action="open-self"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-T116 無いファイル（空の本文）に定義を足して書き戻すと、version と定義を持つ読めるファイルになる", () => {
  const text = readPhases("").apply({
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
        agent: "",
        when: "",
      },
    ],
  });
  assert.match(text, /^version: 1$/m);
  assert.match(text, /^phases:\n {2}notes:\n/m);
  assert.ok(!text.includes("{}"));
  // 作るときは先頭に説明のコメントを足す。足しても読み直して苦情が出ない
  const again = readPhases(`# 説明\n${text}`);
  assert.deepEqual(again.model.problems, []);
  assert.deepEqual(again.model.form.phases.map((p) => [p.id, p.title, p.inherit]), [["notes", "メモ", true]]);
});
