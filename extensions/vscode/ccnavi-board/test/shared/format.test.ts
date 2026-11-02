/**
 * 画面に出す最終更新の時刻の形。`src/webview/shared/format.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { lastUpdatedText } from "../../src/webview/shared/format.js";

test("CB-T900 最終更新はISO形式の秒とタイムゾーンを落とす。読めない形はそのまま出す", () => {
  assert.equal(lastUpdatedText("2026-10-29T09:00:00+0900"), "最終更新: 2026-10-29 09:00");
  assert.equal(lastUpdatedText("昨日"), "最終更新: 昨日");
});
