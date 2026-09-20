/**
 * フェーズ管理画面への入口のうち、プロジェクト管理画面（React）に出るぶん。
 * 画面を React に移したので、文字列ではなく DOM で見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { cardSelector, openProjects, row } from "../helpers/projects.js";

test("CB-T115 プロジェクト管理画面はカードと本体の枠からフェーズ管理を開ける。層の無いプロジェクトでは押せない", async () => {
  const dom = await openProjects([row(), row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false })]);
  try {
    const lib = dom.one(`${cardSelector("lib")} button[data-action="open-phases"][data-name="lib"]`);
    assert.ok(!lib.hasAttribute("disabled"));
    assert.ok(lib.hasAttribute("title"));
    assert.ok(dom.one(`${cardSelector("Self")} button[data-action="open-phases"][data-name="Self"]`).hasAttribute("disabled"));

    const selfPhases = dom.one('section.workspace button[data-action="open-self-phases"]');
    assert.equal(selfPhases.className, "action small");
    assert.match((dom.one("section.workspace").textContent ?? "").trim(), /自身の層のフェーズの種類 フェーズ管理/);

    // 押すと契約どおりの型で送る（拡張ホストは名前を持たない自身の層として受ける）
    dom.click(selfPhases);
    await dom.settle();
    assert.deepEqual(dom.posted, [{ type: "ready" }, { type: "openSelfPhases" }]);
  } finally {
    await dom.close();
  }
});
