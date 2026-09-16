/** フェーズ管理画面のスクリプトを happy-dom で動かす。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import { renderPhasesPage, type PhasesPage } from "../../src/core/phases-render.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

function html(overrides: Partial<PhasesPage> = {}): string {
  return renderPhasesPage(
    { root: "/ws", phasesPath: ".ccnavi/config/phases.yml", exists: true, model: readPhases(TEMPLATE_PHASES_TEXT).model, lock: { locked: false, reason: "", doing: [] }, ...overrides },
    { nonce: "n" },
  );
}

test("CB-D20 既定は畳み、行を押すと開いて state に id が入る。関係と案内は値がある種類だけ開く", async () => {
  const page = await loadPage(html());
  try {
    assert.equal(page.all(".phase").length, 5);
    assert.equal(page.all(".phase.open").length, 0);
    page.click(page.one('.phase[data-key="p2"] .row-head'));
    assert.ok(page.one('.phase[data-key="p2"]').classList.contains("open"));
    assert.deepEqual((page.state() as { open: string[] }).open, ["design"]);
    // 雛形の design は when を持つので開く。implement-feedback は関係も案内も無いので閉じる
    assert.ok(page.one('.phase[data-key="p2"] details.more').hasAttribute("open"));
    assert.ok(!page.one('.phase[data-key="p5"] details.more').hasAttribute("open"));
  } finally {
    await page.close();
  }
});

test("CB-D21 範囲を inherit に変えると glob の欄が消え、行は開いたまま。id が重なると保存できない", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.phase[data-key="p2"] .row-head'));
    page.change(page.one('.phase[data-key="p2"] select.f-scope'), "inherit");
    assert.ok(page.one('.phase[data-key="p2"]').classList.contains("open"));
    assert.equal(page.all('.phase[data-key="p2"] input.f-scope-globs').length, 0);
    assert.equal(page.one('.phase[data-key="p2"] .sum .mono').textContent, "inherit");
    assert.ok(!page.one<HTMLButtonElement>("#save").disabled);
    page.type(page.one('.phase[data-key="p2"] input.f-id'), "research");
    assert.ok(page.one<HTMLButtonElement>("#save").disabled);
    assert.match(page.one("#status").textContent ?? "", /id が重なっている（research）/);
    assert.ok(page.one<HTMLInputElement>('.phase[data-key="p2"] input.f-id').classList.contains("duplicate"));
  } finally {
    await page.close();
  }
});

test("CB-D22 絞り込みは title と scope にも当たり、開いている行は隠さず、一致した行だけを数える。足した種類は開いて焦点が id に来る", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.phase[data-key="p1"] .row-head'));
    page.type(page.one("#find"), "設計");
    assert.equal(page.one("#phase-count").textContent, "1 / 5（開いたまま 1）", "開いている research は一致しないので数えず、開いたままの数として添える");
    assert.ok(page.one('.phase[data-key="p1"]').classList.contains("hidden-by-find"));
    assert.ok(page.one('.phase[data-key="p1"]').classList.contains("open"));
    assert.ok(!page.one('.phase[data-key="p2"]').classList.contains("hidden-by-find"));
    page.type(page.one("#find"), "");
    page.click(page.one('button[data-action="add"]'));
    const rows = page.all(".phase");
    const added = rows[rows.length - 1];
    assert.ok(added.classList.contains("open"));
    assert.equal(page.document.activeElement, added.querySelector("input.f-id"));
    assert.equal(added.querySelector(".sum .mono")?.textContent, "inherit");
  } finally {
    await page.close();
  }
});

test("CB-D23 保存の往復の間は欄を止めるが、行の開閉のボタンは止めない。失敗が返れば欄は戻る", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.phase[data-key="p1"] .row-head'));
    page.type(page.one('.phase[data-key="p1"] input.f-title'), "調べる");
    page.click(page.one("#save"));
    assert.equal(page.posted.filter((m) => m.type === "save").length, 1);
    assert.ok(page.one<HTMLInputElement>('.phase[data-key="p1"] input.f-title').disabled);
    assert.ok(!page.one<HTMLButtonElement>('.phase[data-key="p1"] .row-head .twist').disabled);
    await page.send({ type: "failed", message: "lint error" });
    assert.ok(!page.one<HTMLInputElement>('.phase[data-key="p1"] input.f-title').disabled);
    assert.equal(page.one("#status").textContent, "lint error");
  } finally {
    await page.close();
  }
});
