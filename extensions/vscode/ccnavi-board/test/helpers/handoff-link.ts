/**
 * 「取り込み待ちを開く」（`src/webview/shared/HandoffLink.tsx`）の確かめ。ルール管理・リスク管理・フェーズ管理が同じ部品を置くので、
 * 確かめる中身も 1 つにして、画面ごとの `open` だけを差し替える。
 */
import assert from "node:assert/strict";
import type { HandoffEntry } from "../../src/core/handoff-entry.js";
import type { DomPage } from "./dom.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

export async function checkHandoffLink(open: (handoff: HandoffEntry) => Promise<DomPage>): Promise<void> {
  const dom = await open({ enabled: true, reason: "" });
  try {
    const button = dom.one<HTMLButtonElement>('[data-action="open-handoff"]');
    assert.equal(button.disabled, false);
    dom.click(button);
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "openHandoff"), [{ type: "openHandoff" }]);
  } finally {
    await dom.close();
  }
  const reason = "取り込みの案内の間は開けません";
  const old = await open({ enabled: false, reason });
  try {
    const button = old.one<HTMLButtonElement>('[data-action="open-handoff"]');
    assert.equal(button.disabled, true);
    assert.equal(old.one('[data-role="handoff-reason"]').textContent?.trim(), reason);
    old.click(button);
    await old.settle();
    assert.equal(old.posted.filter((message) => message.type === "openHandoff").length, 0);
  } finally {
    await old.close();
  }
}
