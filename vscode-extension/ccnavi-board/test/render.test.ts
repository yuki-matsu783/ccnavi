import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { parseApprovePreview, type ApprovePreview } from "../src/core/approvemodel.js";
import { buildBoard } from "../src/core/board.js";
import { escapeHtml, renderBoard } from "../src/core/render.js";
import { fixture } from "./fixture.js";

const OPTIONS = { nonce: "TEST-NONCE-123" };

function approvePreview(): ApprovePreview {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "test", "fixtures", "approve-preview.json"), "utf8");
  const parsed = parseApprovePreview(text);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}

test("CB-T73 承認のオーバーレイに束・本文・対象外を出し、見せた識別子を承認ボタンに持たせる", () => {
  const preview = approvePreview();
  const html = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "preview", preview } });
  assert.ok(html.includes('class="approval-backdrop" data-approval="preview"'));
  assert.ok(html.includes("Ticket 承認リクエスト: 2 件"));
  assert.ok(html.includes('data-action="approve-confirm" data-tickets="i0001,i0001-01"'));
  assert.ok(html.includes("この 2 件を承認する"));
  assert.ok(html.includes('data-action="approve-cancel"'));
  assert.ok(html.includes('<pre class="approval-text">Ticket 承認リクエスト'));
  assert.ok(html.includes("承認の対象にしない"));
  assert.ok(html.includes("i0001-02"));
  assert.ok(html.includes("超えている"));
  assert.ok(!html.includes("読めない提案・写し"));
  // 本文は実体参照にする。
  const spiked = { ...preview, text: "<script>alert(1)</script>" };
  const escaped = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "preview", preview: spiked } });
  assert.ok(!escaped.includes("<script>alert(1)</script>"));
  assert.ok(escaped.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
});

test("CB-T74 束が空なら承認ボタンを出さず、承認中はボタンを押せず、食い違いの注意を出す", () => {
  const preview = approvePreview();
  const empty = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "preview", preview: { ...preview, batch: [], text: "承認待ちのチケットは無い。" } },
  });
  assert.ok(empty.includes("承認待ちのチケットは無い"));
  assert.ok(!empty.includes('data-action="approve-confirm"'));
  const approving = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "approving", preview } });
  assert.ok(approving.includes('data-approval="approving"'));
  assert.ok(approving.includes("承認している…"));
  assert.ok(/data-action="approve-confirm"[^>]*disabled/.test(approving));
  const noticed = renderBoard(buildBoard(fixture()), {
    ...OPTIONS,
    approval: { kind: "preview", preview, notice: "見せた束と今の束が違った" },
  });
  assert.ok(noticed.includes('class="approval-note warn">見せた束と今の束が違った'));
  const failed = renderBoard(buildBoard(fixture()), { ...OPTIONS, approval: { kind: "error", error: "実行ファイルが無い" } });
  assert.ok(failed.includes('class="approval-note error">実行ファイルが無い'));
});

test("CB-T75 オーバーレイを渡さなければ出ない", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  // スタイルとスクリプトには名前が残るので、要素そのものが無いことを見る。
  assert.ok(!html.includes('class="approval-backdrop"'));
  assert.ok(!html.includes('data-action="approve-confirm"'));
  assert.ok(html.includes('data-action="approve"'));
});

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

test("CB-T13 カードにバッジ・フェーズ・操作を出す", () => {
  const html = renderBoard(buildBoard(fixture()), OPTIONS);
  assert.ok(html.includes("承認済"));
  assert.ok(html.includes("未承認"));
  assert.ok(html.includes("作業ツリーあり"));
  assert.ok(html.includes("作業ツリーなし"));
  assert.ok(html.includes("2 か所にコピーあり"));
  assert.ok(html.includes("親 i0001 / フェーズ 2"));
  assert.ok(html.includes('class="phases"'));
  assert.ok(html.includes('data-action="wrapup" data-parent="i0001"'));
  assert.ok(html.includes("base "));
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
