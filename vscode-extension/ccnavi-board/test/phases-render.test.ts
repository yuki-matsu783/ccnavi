import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../src/core/phases-doc.js";
import { renderPhasesPage } from "../src/core/phases-render.js";

test("CB-T95 フェーズ管理画面は外部資源を持たず、種類を JSON で埋め込み、無いときは作る帯を出す", () => {
  const doc = readPhases(TEMPLATE_PHASES_TEXT);
  const html = renderPhasesPage(
    {
      root: "/ws",
      phasesPath: ".claude/ccnavi/phases.yml",
      exists: true,
      ticketControl: "enable",
      model: doc.model,
      lock: { locked: false, reason: "", doing: [] },
    },
    { nonce: "n0nce" },
  );
  assert.match(html, /<meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
  assert.ok(!/src="http/.test(html));
  const embedded = /<script nonce="n0nce" type="application\/json" id="page">(.*?)<\/script>/s.exec(html);
  assert.ok(embedded !== null);
  const page = JSON.parse(embedded[1]);
  assert.deepEqual(
    page.form.phases.map((p: { id: string }) => p.id),
    ["research", "design", "acceptance", "implement", "implement-feedback"],
  );
  assert.equal(page.exists, true);
  assert.ok(!html.includes("雛形でファイルを作る"));
  assert.ok(!html.includes("チケット制御が <code>disable</code>"));

  const missing = renderPhasesPage(
    {
      root: "/ws",
      phasesPath: ".claude/ccnavi/phases.yml",
      exists: false,
      ticketControl: "disable",
      model: { version: null, form: { phases: [] }, problems: ["<苦情>"] },
      lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] },
    },
    { nonce: "n0nce" },
  );
  assert.ok(missing.includes("雛形でファイルを作る"));
  assert.ok(missing.includes("チケット制御が <code>disable</code>"));
  assert.ok(missing.includes("&lt;苦情&gt;"));
  assert.ok(missing.includes("作業中のチケットがある（i0001-02）"));
});
