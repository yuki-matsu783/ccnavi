import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import { renderPhasesPage } from "../../src/core/phases-render.js";

test("CB-T95 フェーズ管理画面は外部資源を持たず、種類を JSON で埋め込み、無いときは作る帯を出す", () => {
  const doc = readPhases(TEMPLATE_PHASES_TEXT);
  const html = renderPhasesPage(
    {
      root: "/ws",
      phasesPath: ".ccnavi/common/phases.yml",
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
  // 作る帯は無いときだけ（スクリプトの文言には常に語が入るので、帯の要素で見る）
  assert.ok(!html.includes('class="banner missing"'));
  // チケット制御の帯は常に書き、enable なら隠す（開いたまま disable になったら出す指示を送る）
  assert.ok(html.includes('<div id="ticket-off" class="banner warn hidden">'));

  const missing = renderPhasesPage(
    {
      root: "/ws",
      phasesPath: ".ccnavi/common/phases.yml",
      exists: false,
      ticketControl: "disable",
      model: { version: null, form: { phases: [] }, problems: ["<苦情>"] },
      lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] },
    },
    { nonce: "n0nce" },
  );
  assert.ok(missing.includes('class="banner missing"'));
  assert.ok(missing.includes("雛形でファイルを作る"));
  assert.ok(missing.includes('<div id="ticket-off" class="banner warn">このワークスペースはチケット制御が <code>disable</code>'));
  assert.ok(missing.includes("&lt;苦情&gt;"));
  assert.ok(missing.includes("作業中のチケットがある（i0001-02）"));
});

test("CB-T121 種類の一覧は 1 件 1 行で既定は畳み、関係と案内の 4 欄は値があるときだけ開く", () => {
  const doc = readPhases(TEMPLATE_PHASES_TEXT);
  const html = renderPhasesPage(
    { root: "/ws", phasesPath: ".ccnavi/config/phases.yml", exists: true, ticketControl: "enable", model: doc.model, lock: { locked: false, reason: "", doing: [] } },
    { nonce: "n" },
  );
  assert.match(html, /<ul class="list" id="phases"><\/ul>/);
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  assert.match(body, /class: "row-head"/);
  assert.match(body, /if \(moreOpen\.has\(key\) \? moreOpen\.get\(key\) : hasRelations\(phase\)\) \{ more\.setAttribute\("open", ""\); \}/);
  assert.match(html, /<input id="find" type="search"/);
  assert.match(body, /if \(phase && phase\.id !== ""\) \{ ids\.push\(phase\.id\); \}/);
  assert.doesNotThrow(() => new Function(body));
});

test("CB-T125 種類の欄名は日本語で、YAML のキー名は欄名の title に載せる", () => {
  const doc = readPhases(TEMPLATE_PHASES_TEXT);
  const html = renderPhasesPage(
    { root: "/ws", phasesPath: ".ccnavi/config/phases.yml", exists: true, ticketControl: "enable", model: doc.model, lock: { locked: false, reason: "", doing: [] } },
    { nonce: "n" },
  );
  const body = html.split('<script nonce="n">')[1].split("</script>")[0];
  for (const [label, key] of [["題", "title"], ["区分", "kind"], ["範囲", "scope"], ["成果物", "deliverables"], ["並行できる種類", "overlap"], ["置く目安", "when"]]) {
    assert.match(body, new RegExp(`captioned\\("${label}", [\\s\\S]*?"${key}"\\)`), label);
  }
});
