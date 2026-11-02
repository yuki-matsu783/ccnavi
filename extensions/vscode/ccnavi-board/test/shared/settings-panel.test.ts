/**
 * 設定3画面の共通の段取り（settings-panel.ts）の、エラー画面の描き直し。
 * ファイルは`vscode`を読むので、読み込む間だけ空のスタブに差し替える（使うのは`reload`と`redraw`だけで、VS Codeの窓口は呼ばない）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import Module from "node:module";

import { LoadFailure, type ErrorFile } from "../../src/core/load-failure.js";
import type { SettingsPanel, SettingsScreen, SettingsState } from "../../src/settings-panel.js";

interface Internals {
  _load(request: string, parent: unknown, isMain: boolean): unknown;
}

function loadSettingsPanel(): typeof import("../../src/settings-panel.js") {
  const internals = Module as unknown as Internals;
  const original = internals._load;
  internals._load = function load(request, parent, isMain) {
    return request === "vscode" ? {} : original.call(this, request, parent, isMain);
  };
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("../../src/settings-panel.js");
  } finally {
    internals._load = original;
  }
}

test("CB-T1261 エラー画面は、読み直しでも描き直し（ready）でも、読めなかったファイルを付けて出す", async () => {
  const file: ErrorFile = { path: "/r/.ccnavi/config/project/rules.yml", rel: ".ccnavi/config/project/rules.yml" };
  const shown: Array<{ error: string; file: ErrorFile | undefined }> = [];
  const screen = {
    alive: () => true,
    read: async () => {
      throw new LoadFailure("読めない", file);
    },
    showError: (_current: unknown, error: string, given?: ErrorFile) => shown.push({ error, file: given }),
  } as unknown as SettingsScreen<unknown, string, unknown, unknown>;
  const panel: SettingsPanel<unknown, string, unknown> = loadSettingsPanel().settingsPanel(screen);
  const current = { seq: 0, target: "t", folder: { uri: { fsPath: "/r" } } } as unknown as SettingsState<unknown, string, unknown>;

  await panel.reload(current);
  assert.deepEqual(shown, [{ error: "読めない", file }]);

  // showErrorが入れる`error`を、ここでは手で入れる（`errorFile`は読み直しが保存済み）
  current.error = "読めない";
  panel.redraw(current);
  assert.deepEqual(shown[1], { error: "読めない", file }, "描き直しでも［エディタで開く］の行き先が付く");
});
