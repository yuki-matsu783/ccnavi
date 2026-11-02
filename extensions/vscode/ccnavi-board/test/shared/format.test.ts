/**
 * 画面に出す最終更新の時刻の形。`src/webview/shared/format.ts`。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { formatGeneratedAt } from "../../src/webview/shared/format.js";

test("CB-T900 最終更新はISO形式の秒とタイムゾーンを落とす。読めない形はそのまま出す", () => {
  assert.equal(formatGeneratedAt("2026-10-29T09:00:00+0900"), "2026-10-29 09:00");
  assert.equal(formatGeneratedAt("昨日"), "昨日");
});
