/**
 * フェーズ管理画面の絞り込み・並べ替え・保存後の warn・文言（#743）を happy-dom で見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { REVIEW_LABELS } from "../../src/core/phases-view.js";
import { openPhases, page } from "../helpers/phases.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

test("CB-D1240 全角英数字でも絞り込める（NFKC で正規化して比べる）", async () => {
  const dom = await openPhases();
  try {
    dom.type(dom.one("#find"), "ＤＥＳＩＧＮ");
    await dom.settle();
    const shown = dom.all(".phase").filter((row) => !row.classList.contains("hidden-by-find"));
    assert.deepEqual(shown.map((row) => row.querySelector(".sum-id")?.textContent), ["design"]);
  } finally {
    await dom.close();
  }
});

test("CB-D1241 絞り込み中は上下ボタンを止め、title に理由を出す", async () => {
  const dom = await openPhases();
  try {
    const buttons = (): HTMLButtonElement[] => dom.all<HTMLButtonElement>(".phase .buttons button").filter((b) => b.textContent === "↑" || b.textContent === "↓");
    assert.ok(buttons().some((b) => !b.disabled));
    dom.type(dom.one("#find"), "design");
    await dom.settle();
    assert.ok(buttons().every((b) => b.disabled));
    assert.ok(buttons().every((b) => /絞り込み中は並べ替えできません/.test(b.title)));
    dom.type(dom.one("#find"), "");
    await dom.settle();
    assert.ok(buttons().some((b) => !b.disabled));
  } finally {
    await dom.close();
  }
});

test("CB-D1242 保存後の warn の本文を上部の帯に出し、次の編集で消す", async () => {
  const dom = await openPhases();
  try {
    await dom.send({ type: "saved", warns: ["warn: design: 重なっています"] });
    assert.match(dom.one('[data-banner="save-warn"]').textContent ?? "", /design: 重なっています/);
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    assert.equal(dom.all('[data-banner="save-warn"]').length, 0);
    await dom.send({ type: "saved", warns: ["warn: a: b"] });
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    assert.equal(dom.all('[data-banner="save-warn"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D1243 review の none に「既定」と書かず、リスト欄の placeholder で区切りは半角 , と言う", async () => {
  assert.ok(!REVIEW_LABELS.none.includes("既定"), REVIEW_LABELS.none);
  const dom = await openPhases();
  try {
    dom.click(dom.one('.phase[data-key="p2"] .row-head'));
    await dom.settle();
    const lists = dom.all<HTMLInputElement>('.phase[data-key="p2"] input.f-scope-globs, .phase[data-key="p2"] input.f-deliverables');
    assert.equal(lists.length, 2);
    for (const input of lists) {
      assert.match(input.placeholder, /区切りは半角 ,/);
    }
  } finally {
    await dom.close();
  }
});
