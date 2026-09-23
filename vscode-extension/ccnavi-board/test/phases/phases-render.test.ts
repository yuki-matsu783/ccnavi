/**
 * フェーズ管理画面の入れ物（HTML）。中身は画面（React）が作るので、ここで見るのは
 * 守り（CSP）・埋め込む中身・束ねた画面の流し込みだけ。描くものは phases.dom.test.ts。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { NONCE, page, phasesHtml } from "../helpers/phases.js";
import { screenScript } from "../helpers/bundle.js";

/** 束ねた画面を除いた入れ物。外を読んでいないことは、拡張が書いたところだけを見て確かめる */
function shell(rendered: string): string {
  return rendered.split(screenScript("phases")).join("（束ねた画面）");
}

test("CB-T95 フェーズ管理画面は外部資源を持たず、種類を JSON で埋め込む", () => {
  const rendered = phasesHtml({ kind: "page", page: page() }, { nonce: "n0nce" });
  assert.match(rendered, /<meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
  assert.match(rendered, /style-src 'nonce-n0nce'; script-src 'nonce-n0nce'/);
  assert.doesNotMatch(shell(rendered), /https?:\/\//);
  assert.doesNotMatch(shell(rendered), /<(?:script|img|iframe)[^>]*\ssrc=|<link\s/);
  const embedded = /<script type="application\/json" id="ccnavi-phases-data">(.*?)<\/script>/s.exec(rendered);
  assert.ok(embedded !== null);
  const data = JSON.parse(embedded[1]) as { page: { exists: boolean; model: { form: { phases: { id: string }[] } } } };
  assert.deepEqual(
    data.page.model.form.phases.map((phase) => phase.id),
    ["research", "design", "acceptance", "implement", "implement-feedback"],
  );
  assert.equal(data.page.exists, true);
});

test("CB-T121 束ねた画面を nonce 付きの script に流し込み、資源としては読ませない", () => {
  const rendered = phasesHtml({ kind: "page", page: page() });
  assert.ok(rendered.includes(`<script nonce="${NONCE}">\n${screenScript("phases")}\n</script>`));
  assert.match(rendered, /<div id="root"><p class="empty" id="ccnavi-loading">フェーズを読み込み中\.\.\.<\/p><\/div>/);
  assert.doesNotMatch(shell(rendered), /<script[^>]*\ssrc=/);
});

test("CB-T156 読み直せなかったときは種類の代わりに理由を渡す", () => {
  const rendered = phasesHtml({ kind: "error", error: "種類のファイルを読めない" });
  assert.match(rendered, /"kind":"error"/);
  assert.match(rendered, /種類のファイルを読めない/);
});
