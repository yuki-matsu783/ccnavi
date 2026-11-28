/**
 * 設定3画面の共通の段取り（settings-panel.ts）。エラー画面の描き直し、画面からの操作の受け付け、自分の保存の見分け。
 * ファイルは`vscode`を読むので、読み込む間だけ小さなスタブに差し替える（使う窓口だけを持たせ、中身はテストごとに入れ替える）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import Module from "node:module";

import { writeTreeFile } from "../../src/core/flow-write.js";
import { LoadFailure, type ErrorFile } from "../../src/core/load-failure.js";
import { OWN_WRITE_GRACE_MS } from "../../src/core/watch.js";
import type { SettingsLoaded, SettingsPanel, SettingsScreen, SettingsState } from "../../src/settings-panel.js";

interface Internals {
  _load(request: string, parent: unknown, isMain: boolean): unknown;
}

/** スタブの窓口。テストが中身を入れ替える（読み込んだ側は`vscode.window`をそのつど引くので、入れ替えが効く） */
const window = {
  showWarningMessage: async (..._args: unknown[]): Promise<string | undefined> => undefined,
  showTextDocument: async (): Promise<void> => undefined,
};
const opened: string[] = [];
const workspace = {
  openTextDocument: async (target: string): Promise<unknown> => {
    opened.push(target);
    return {};
  },
};

function loadSettingsPanel(): typeof import("../../src/settings-panel.js") {
  const internals = Module as unknown as Internals;
  const original = internals._load;
  internals._load = function load(request, parent, isMain) {
    return request === "vscode" ? { window, workspace } : original.call(this, request, parent, isMain);
  };
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("../../src/settings-panel.js");
  } finally {
    internals._load = original;
  }
}

type State = SettingsState<unknown, SettingsLoaded>;
type Screen = SettingsScreen<unknown, SettingsLoaded, unknown, { readonly type: string }>;

function stateOf(posted: unknown[]): State {
  return {
    seq: 0,
    target: { kind: "self" },
    folder: { uri: { fsPath: "/r" } },
    host: { post: (message: unknown) => posted.push(message) !== 0, send: () => "posted" },
  } as unknown as State;
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
  } as unknown as Screen;
  const panel: SettingsPanel<unknown, SettingsLoaded> = loadSettingsPanel().settingsPanel(screen);
  const current = stateOf([]);

  await panel.reload(current);
  assert.deepEqual(shown, [{ error: "読めない", file }]);

  // showErrorが入れる`error`を、ここでは手で入れる（`errorFile`は読み直しが保存済み）
  current.error = "読めない";
  panel.redraw(current);
  assert.deepEqual(shown[1], { error: "読めない", file }, "描き直しでも［エディタで開く］の行き先が付く");
});

test("CB-T1281 「更新」の確認を断ったとき、問いの間にパネルが閉じていればcancelledを送らない", async () => {
  let open = true;
  const screen = { alive: () => open, parse: (m: unknown) => m } as unknown as Screen;
  const panel = loadSettingsPanel().settingsPanel(screen);
  const posted: unknown[] = [];
  const current = stateOf(posted);

  // 問いを出している間に閉じられた
  window.showWarningMessage = async () => {
    open = false;
    return undefined;
  };
  await panel.handleMessage(current, { type: "reload", dirty: true });
  assert.deepEqual(posted, [], "閉じたパネルへ送っている");

  // 開いたままなら、やめたことを伝える（画面は欄を止めて待っている）
  open = true;
  window.showWarningMessage = async () => undefined;
  await panel.handleMessage(current, { type: "reload", dirty: true });
  assert.deepEqual(posted, [{ type: "cancelled" }]);
});

test("CB-T1282 読めていない画面（エラー画面）でも、対象の切り替えと読めなかったファイルを開く操作は受ける。画面だけの操作は受けない", async () => {
  const read: unknown[] = [];
  const handled: unknown[] = [];
  const file: ErrorFile = { path: "/r/.ccnavi/config/project/rules.yml", rel: ".ccnavi/config/project/rules.yml" };
  const screen = {
    alive: () => true,
    parse: (m: unknown) => m,
    handle: async (_c: unknown, _l: unknown, m: unknown) => handled.push(m),
    read: async (_root: string, target: unknown) => {
      read.push(target);
      throw new LoadFailure("読めない", file);
    },
    showError: () => undefined,
    loading: () => ({ kind: "loading" }),
  } as unknown as Screen;
  const panel = loadSettingsPanel().settingsPanel(screen);
  const current = { ...stateOf([]), fileWatchers: [], dirty: false, errorFile: file, panel: { title: "" } } as unknown as State;

  await panel.handleMessage(current, { type: "openFile" });
  assert.deepEqual(opened, [file.path], "読めなかったファイルを開いていない");

  await panel.handleMessage(current, { type: "save" });
  assert.deepEqual(handled, [], "読めていないのに画面だけの操作を渡している");

  await panel.handleMessage(current, { type: "switchTarget", kind: "workspace", name: "" });
  assert.deepEqual(current.target, { kind: "workspace" });
  assert.deepEqual(read, [{ kind: "workspace" }], "切り替え先を読んでいない");
});

test("CB-T1283 入れ替えで書いても、自分の保存は猶予の間に見え、そのあとの外の変更は見える", async () => {
  const screen = { alive: () => true } as unknown as Screen;
  const panel = loadSettingsPanel().settingsPanel(screen);
  const tree = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-settings-write-")));
  const dir = path.join(tree, ".ccnavi", "config", "common");
  const file = path.join(dir, "rules.yml");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(file, "version: 1\n");
  // VS Codeのファイル監視と同じく、ディレクトリを見張ってファイルの名前で拾う（inodeでは追わない）
  const seen: Array<{ name: string; at: number }> = [];
  const watcher = fs.watch(dir, (_event, name) => seen.push({ name: String(name), at: Date.now() }));
  const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 300));
  try {
    const current = { ...stateOf([]), wroteAt: Date.now() } as unknown as State;
    const written = writeTreeFile(tree, file, "version: 1\nrules: []\n", { exists: true, mtimeMs: fs.statSync(file).mtimeMs }, "ルール", undefined);
    assert.deepEqual(written, { ok: true });
    await settle();
    const mine = seen.filter((e) => e.name === "rules.yml");
    assert.ok(mine.length > 0, "入れ替えた書き込みが監視に見えない");
    for (const event of mine) {
      assert.ok(event.at - current.wroteAt < OWN_WRITE_GRACE_MS, "自分の保存の知らせが猶予の外に出た");
    }
    // 猶予の間の知らせは「変更を検知しました」にしない
    panel.scheduleChanged(current);
    assert.equal(current.timer, undefined, "自分の保存を外の変更として扱った");
    // 一時ファイルの名前は、見張っているファイルの名前（globは完全一致）に当たらない
    for (const event of seen.filter((e) => e.name !== "rules.yml")) {
      assert.match(event.name, /^\.rules\.yml\..*\.tmp$/);
    }

    // 入れ替えたあとも同じ名前で見張れている。外の変更は猶予を過ぎれば知らせる
    seen.length = 0;
    fs.writeFileSync(file, "version: 1\n# theirs\n");
    await settle();
    assert.ok(seen.some((e) => e.name === "rules.yml"), "入れ替えたあとの外の変更が監視に見えない");
    current.wroteAt = Date.now() - OWN_WRITE_GRACE_MS;
    panel.scheduleChanged(current);
    assert.notEqual(current.timer, undefined, "猶予を過ぎた外の変更を拾っていない");
    clearTimeout(current.timer);
  } finally {
    watcher.close();
    fs.rmSync(tree, { recursive: true, force: true });
  }
});
