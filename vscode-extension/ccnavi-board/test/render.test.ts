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

test("CB-T12b 列ごとに畳むボタンを出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  for (const state of ["todo", "doing", "done", "cancelled"]) {
    assert.ok(html.includes(`data-fold="${state}" aria-expanded="true"`), state);
  }
  assert.equal((html.match(/class="fold"/g) ?? []).length, 4);
});

test("CB-T12c 絞り込み後の件数は見えているカードで数え、畳んだ列は固定幅に縛られない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 絞り込みのたびに列の見出しの .count を .hidden でないカードの数で書き直す
  assert.ok(html.includes('column.querySelector(":scope > h2 > .count")'), "列の見出しの件数を拾う");
  assert.ok(html.includes('column.querySelectorAll(".card:not(.hidden)").length'), "見えているカードで数える");
  // ドラッグで付けたインラインの width より畳んだ状態を優先する
  assert.ok(/\.column\.folded \{[^}]*width: auto !important/.test(html), "畳んだ列は固定幅より優先");
});

test("CB-T12d 承認ボタンは見えている承認待ちの数を出し、その識別子を --approve に渡す。上部の集計は絞らない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // 上部の集計は描いたときの全体の数のままで、script は触らない
  assert.ok(html.includes("承認待ち 1 件</span>"), "集計は全体の数");
  // ボタンの数と disabled は見えている承認待ちで決め、押すとその識別子を送る
  assert.ok(html.includes(`document.querySelector('.controls button[data-action="approve"]')`), "上部のボタンだけを書き換える");
  assert.ok(html.includes('document.querySelectorAll(".card.pending:not(.hidden)").length'), "見えている承認待ちで数える");
  // 識別子と「絞り込み中か」を別々に送る。空の並びを「全部」に読ませない
  assert.ok(html.includes('vscode.postMessage({ type: "approve", tickets: visiblePending(), filtered: filtering() })'), "識別子と絞り込みの有無を送る");
});

test("CB-T13 カードにバッジ・フェーズ・操作を出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(html.includes("承認済"));
  assert.ok(html.includes("未承認"));
  assert.ok(html.includes("作業ツリーあり"));
  assert.ok(html.includes("作業ツリーなし"));
  assert.ok(html.includes("2 か所にコピーあり"));
  assert.ok(html.includes("親 i0001 / フェーズ 2"));
  assert.ok(html.includes('class="phases"'));
  // 締める（wrapup）のボタンは出さない
  assert.ok(!html.includes('data-action="wrapup"'));
  assert.ok(!html.includes("締める"));
  assert.ok(html.includes("base "));
});

test("CB-T13b 親の絞り込みを出し、カードに家族を付ける", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(html.includes('id="parent-filter"'));
  assert.ok(/<option value="i0001">i0001 [^<]+<\/option>/.test(html));
  assert.equal((html.match(/data-family="i0001"/g) ?? []).length, 4);
  const empty = { ...fixture(), tickets: [], parents: [], pending_approval: [] };
  assert.ok(!renderBoard(buildBoard(empty), OPTIONS).includes('id="parent-filter"'));
});

test("CB-T14 0 件のときは空の表示と無効な承認ボタン", () => {
  const empty = { ...fixture(), tickets: [], parents: [], pending_approval: [] };
  const html = renderBoard(buildBoard(empty), OPTIONS);
  assert.ok(html.includes("チケットはありません"));
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
