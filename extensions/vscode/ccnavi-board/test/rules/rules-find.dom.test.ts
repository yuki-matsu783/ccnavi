/**
 * ルール管理画面の絞り込み・並べ替え・保存後の warn・期待確認の根拠（#743）を happy-dom で見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { ExpectationJson, ExpectationsJson, RuleHitJson } from "../../src/core/testmodel.js";
import { openRules } from "../helpers/rules.js";
import type { HTMLButtonElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

function hit(section: string, id: string, source: "file" | "outside"): RuleHitJson {
  return { section, id, kind: "glob", written: "*", pattern: ".*", source };
}

test("CB-D1236 絞り込みが 3 タイプとも 0 件のときだけ案内を出す。全角英数字は半角と同じに探せる", async () => {
  const dom = await openRules();
  try {
    assert.equal(dom.all("#tab-rules > p.empty").length, 0);
    dom.type(dom.one("#find"), "存在しない文字列-zzz");
    await dom.settle();
    assert.match(dom.one("#tab-rules > p.empty").textContent ?? "", /絞り込みに一致するルールがありません/);
    // 1 件でも一致すれば出ない（全角の「ＧＩＴ」は git-push に一致する）
    dom.type(dom.one("#find"), "ＧＩＴ ＰＵＳＨ");
    await dom.settle();
    assert.equal(dom.all("#tab-rules > p.empty").length, 0);
    const hidden = dom.all(".rule").map((rule) => `${rule.getAttribute("data-id")}:${rule.classList.contains("hidden-by-find")}`);
    assert.deepEqual(hidden, ["git-push:false", "no-rm:true", "deps:true"]);
  } finally {
    await dom.close();
  }
});

test("CB-D1237 絞り込み中は上下ボタンを止め、title に理由を出す。空に戻せば戻る", async () => {
  const dom = await openRules();
  try {
    const down = (): HTMLButtonElement => dom.all<HTMLButtonElement>('.rule[data-id="git-push"] button[data-action], .rule[data-id="git-push"] .buttons button').find((b) => b.textContent === "↓") as HTMLButtonElement;
    assert.equal(down().disabled, false);
    dom.type(dom.one("#find"), "git");
    await dom.settle();
    assert.equal(down().disabled, true);
    assert.match(down().title, /絞り込み中は並べ替えできません/);
    const typeSelect = (): HTMLSelectElement => dom.one<HTMLSelectElement>('.rule[data-id="git-push"] select.f-section');
    assert.equal(typeSelect().disabled, true);
    assert.match(typeSelect().title, /絞り込み中は並べ替えできません/);
    dom.type(dom.one("#find"), "");
    await dom.settle();
    assert.equal(down().disabled, false);
    assert.equal(down().title, "下へ");
    assert.equal(typeSelect().disabled, false);
  } finally {
    await dom.close();
  }
});

test("CB-D1238 期待確認の「ヒットしたルール」に、ルールファイル外の根拠も並べて印を付ける", async () => {
  const dom = await openRules();
  try {
    const expectation: ExpectationJson = {
      expected: "allow",
      tool: "Write",
      subject: "/ws/.ccnavi/config/common/rules.yml",
      resolved_subject: "/ws/.ccnavi/config/common/rules.yml",
      why: "触れてよい",
      known: true,
      verdict: "deny",
      code: "",
      reason: "",
      rules: [hit("deny", "builtin-protect", "outside"), hit("deny", "no-rm", "file")],
      ok: false,
      skipped: false,
    };
    const result: ExpectationsJson = {
      version: 1,
      root: "/ws",
      rules_path: "",
      expectations_path: "",
      counts: { allow: { ok: 0, total: 1 } },
      mismatches: 1,
      skipped: 0,
      expectations: [expectation],
    };
    await dom.send({ type: "expectationsChecked", result });
    const cell = dom.all("#expectations-result tbody tr td")[4].textContent ?? "";
    assert.match(cell, /deny:builtin-protect（ルールファイル外の根拠）/);
    assert.match(cell, /deny:no-rm(?!（)/);
  } finally {
    await dom.close();
  }
});

test("CB-D1239 保存後の warn の本文を上部の帯に出し、次の編集で消す", async () => {
  const dom = await openRules();
  try {
    assert.equal(dom.all('[data-banner="save-warn"]').length, 0);
    await dom.send({ type: "saved", warns: ["warn: no-rm: 説明が空です", "warn: deps: 重なっています"] });
    const banners = dom.all('[data-banner="save-warn"]');
    assert.equal(banners.length, 2);
    assert.ok(banners[0].classList.contains("warn"));
    assert.match(banners[0].textContent ?? "", /no-rm: 説明が空です/);
    dom.click(dom.one('button[data-action="add"][data-section="allow"]'));
    await dom.settle();
    assert.equal(dom.all('[data-banner="save-warn"]').length, 0);
    // 中身の入れ替え（更新）でも消える
    await dom.send({ type: "saved", warns: ["warn: x: y"] });
    assert.equal(dom.all('[data-banner="save-warn"]').length, 1);
    const data = { kind: "page", page: (await import("../helpers/rules.js")).page() };
    await dom.send({ type: "data", data });
    assert.equal(dom.all('[data-banner="save-warn"]').length, 0);
  } finally {
    await dom.close();
  }
});
