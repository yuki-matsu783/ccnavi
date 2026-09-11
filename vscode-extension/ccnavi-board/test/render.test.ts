import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBoard } from "../src/core/board.js";
import { escapeHtml, renderBoard } from "../src/core/render.js";
import { fixture } from "./fixture.js";

const OPTIONS = { nonce: "TEST-NONCE-123" };

test("CB-T12 4 列と件数と承認ボタンを出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  for (const label of ["未着手", "作業中", "完了", "取り消し"]) {
    assert.ok(html.includes(label), label);
  }
  assert.equal((html.match(/class="column"/g) ?? []).length, 4);
  assert.ok(html.includes("残り 3 件"));
  assert.ok(html.includes("全 4 件"));
  assert.ok(html.includes("承認待ち 1 件を承認"));
  assert.ok(!html.includes('data-action="approve" disabled'));
  assert.ok(html.includes(`nonce="${OPTIONS.nonce}"`));
});

test("CB-T13 カードにバッジ・フェーズ・操作を出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(html.includes("承認済"));
  assert.ok(html.includes("未承認"));
  assert.ok(html.includes("作業ツリーあり"));
  assert.ok(html.includes("作業ツリー無し"));
  assert.ok(html.includes("2 か所に写っている"));
  assert.ok(html.includes("親 i0001 / フェーズ 2"));
  assert.ok(html.includes('class="phases"'));
  assert.ok(html.includes('data-action="wrapup" data-parent="i0001"'));
  assert.ok(html.includes("base "));
});

test("CB-T14 0 件のときは空の表示と無効な承認ボタン", () => {
  const empty = { ...fixture(), tickets: [], parents: [], pending_approval: [] };
  const html = renderBoard(buildBoard(empty), OPTIONS);
  assert.ok(html.includes("チケットが 1 枚もありません"));
  assert.equal((html.match(/class="empty"/g) ?? []).length, 4);
  assert.ok(html.includes('data-action="approve" disabled'));
});

test("CB-T15 問題とプロジェクトの絞り込みを出す", () => {
  const json = { ...fixture(), problems: ["写し x を読めない"], projects: ["lib", "app"] };
  const html = renderBoard(buildBoard(json), OPTIONS);
  assert.ok(html.includes('class="problems"'));
  assert.ok(html.includes("写し x を読めない"));
  assert.ok(html.includes('id="project-filter"'));
  assert.ok(html.includes('<option value="lib">lib</option>'));
  const without = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(!without.includes('id="project-filter"'));
});

test("CB-T16 本文の文字列で表示を壊さない", () => {
  const base = fixture();
  const evil = { ...base.tickets[0], title: `<script>alert("x")</script>` };
  const html = renderBoard(buildBoard({ ...base, tickets: [evil, ...base.tickets.slice(1)] }), OPTIONS);
  assert.ok(!html.includes(`<script>alert("x")</script>`));
  assert.ok(html.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"));
  assert.equal(escapeHtml(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});
