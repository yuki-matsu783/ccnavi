/**
 * ボードのカードの「テンプレートの下書きを作る」ボタン。出すのは、完了した計画付きの親のカードだけ。
 * 出すかは実行ファイルの `template_draft` で決まり、画面は計画を読まない。押すと拡張ホストへ親の識別子を
 * 返すだけ（拡張ホストはいまのボードで確かめてから下書きを作る）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { BoardJson, TicketJson } from "../../src/core/model.js";
import { openBoard } from "../helpers/board.js";
import { fixture } from "../helpers/fixture.js";

/**
 * 見本に完了した親を足す。i0004 は完了した計画付きの親（`template_draft` が真）、i0005 は完了した親だが
 * 真でない（下書きを作れない）。子 i0001-02-03 には真を付けても出ない（親だけ）
 */
function withDone(): BoardJson {
  const board = fixture();
  const base = board.tickets.find((t) => t.ticket === "i0001-02-03") as TicketJson;
  const done = (ticket: string, draft: boolean): TicketJson => ({
    ...base,
    ticket,
    parent: "",
    phase: null,
    title: `完了 ${ticket}`,
    flow: null,
    proposal: null,
    copy: { status: "closed" },
    completed_at: "2026-10-01T00:00:00+09:00",
    template_draft: draft,
  });
  return {
    ...board,
    tickets: [
      ...board.tickets.map((t) => (t.ticket === "i0001-02-03" ? { ...t, template_draft: true } : t)),
      done("i0004", true),
      done("i0005", false),
    ],
  };
}

test("CB-D357 完了した計画付きの親のカードにだけ「テンプレートの下書きを作る」を出し、押すと親の識別子だけを返す", async () => {
  const dom = await openBoard(withDone());
  try {
    assert.deepEqual(
      dom.all('[data-action="template-draft"]').map((b) => b.getAttribute("data-ticket")),
      ["i0004"],
    );
    assert.equal(dom.all('.card.child [data-action="template-draft"]').length, 0, "子には出ない");
    dom.click(dom.one('[data-action="template-draft"][data-ticket="i0004"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "templateDraft"), [{ type: "templateDraft", ticket: "i0004" }]);
    assert.equal(dom.posted.filter((m) => m.type === "open").length, 0);
  } finally {
    await dom.close();
  }
  const plain = await openBoard();
  try {
    assert.equal(plain.all('[data-action="template-draft"]').length, 0, "見本（完了した計画付きの親が無い）には出ない");
  } finally {
    await plain.close();
  }
});
