/**
 * 開発用の画面確認ツール（`scripts/render-board.js`）の、ブラウザを使わない部分。
 * 引数の解釈、`--out` が `--repo` の下のときの拒否、`--appearance` と `--width` の検証、失敗の条件と終了コード、
 * VS Code の窓口のスタブ。Chromium で描くところは `render-board.e2e.ts`。
 *
 * ツールは CommonJS の素の JS（型が無い）なので、`require` で読んで必要な形だけ型を付ける。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { APPEARANCES } from "../../src/core/appearance.js";
import { foldPath } from "../../src/core/handoffmodel.js";

const ROOT = path.join(__dirname, "..", "..", "..");
const TOOL = path.join(ROOT, "scripts", "render-board.js");

interface Options {
  repo?: string;
  fixture?: string;
  out: string;
  appearance: string;
  width: number;
}
type Parsed = { ok: true; options: Options } | { ok: false; error: string };
interface Entry {
  kind: string;
  type?: string;
  text: string;
  url?: string;
}
interface Tool {
  EXIT: { OK: number; FAILED: number; USAGE: number; NOT_READY: number };
  parseArgs(argv: string[]): Parsed;
  validateAppearance(value: string, appearances: readonly string[]): { ok: boolean; error?: string };
  isInside(child: string, parent: string, fold: (p: string) => string): boolean;
  failuresOf(entries: Entry[]): Entry[];
  logLine(entry: Entry): string;
  codeForFailures(failures: Entry[]): number;
}
interface FakeWindow {
  __posted?: unknown[];
  __csp?: string[];
  acquireVsCodeApi?: () => { postMessage(m: unknown): void; getState(): unknown; setState(s: unknown): void };
  document?: { addEventListener(name: string, listener: (event: { violatedDirective: string; blockedURI: string }) => void): void };
}

// eslint-disable-next-line @typescript-eslint/no-require-imports
const tool = require(TOOL) as Tool;
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { installVsCodeStub } = require(path.join(ROOT, "scripts", "lib", "vscode-stub.js")) as {
  installVsCodeStub(win: FakeWindow): void;
};

function parsed(...argv: string[]): Options {
  const result = tool.parseArgs(argv);
  assert.ok(result.ok, result.ok ? "" : result.error);
  return result.options;
}

function rejected(...argv: string[]): string {
  const result = tool.parseArgs(argv);
  assert.ok(!result.ok, "拒否されるはず");
  return result.ok ? "" : result.error;
}

test("CB-T1213 引数は --repo か --fixture のどちらか一方と --out が要り、既定の見た目は claude-light・幅は 1400", () => {
  assert.deepEqual(parsed("--repo", "r", "--out", "o"), { repo: "r", fixture: undefined, out: "o", appearance: "claude-light", width: 1400 });
  assert.equal(parsed("--fixture", "f.json", "--out", "o").fixture, "f.json");
  assert.match(rejected("--out", "o"), /どちらか一方/);
  assert.match(rejected("--repo", "r", "--fixture", "f", "--out", "o"), /どちらか一方/);
  assert.match(rejected("--repo", "r"), /--out/);
  assert.match(rejected(), /どちらか一方/);
  assert.match(rejected("--repo", "r", "--out", "o", "--nothing", "x"), /知らない引数/);
  assert.match(rejected("--repo", "--out", "o"), /--repo に値/);
  assert.match(rejected("--repo", "r", "--out"), /--out に値/);
  assert.match(rejected("--repo", "r", "--repo", "r2", "--out", "o"), /2 度/);
});

test("CB-T1214 --width は 320〜3000 の整数だけ。--appearance は拡張が持つ表（APPEARANCES）で確かめる", () => {
  assert.equal(parsed("--repo", "r", "--out", "o", "--width", "320").width, 320);
  assert.equal(parsed("--repo", "r", "--out", "o", "--width", "3000").width, 3000);
  for (const bad of ["319", "3001", "1400.5", "-1", "abc", "1e3", "0x10"]) {
    assert.match(rejected("--repo", "r", "--out", "o", "--width", bad), /--width/, bad);
  }
  assert.equal(parsed("--repo", "r", "--out", "o", "--appearance", "claude-dark").appearance, "claude-dark");
  for (const name of APPEARANCES) {
    assert.ok(tool.validateAppearance(name, APPEARANCES).ok, name);
  }
  const bad = tool.validateAppearance("Claude-Light", APPEARANCES);
  assert.ok(!bad.ok);
  assert.ok((bad.error ?? "").includes(APPEARANCES.join("|")));
});

test("CB-T1215 --out が --repo と同じか下のときは拒否し、兄弟（名前の前置だけ同じ）・外は通す。大文字小文字と区切りは区別しない", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "render-board-"));
  try {
    const repo = path.join(base, "Repo");
    fs.mkdirSync(path.join(repo, "sub"), { recursive: true });
    fs.mkdirSync(path.join(base, "Repo2"));
    assert.ok(tool.isInside(repo, repo, foldPath), "同じ場所");
    assert.ok(tool.isInside(path.join(repo, "sub"), repo, foldPath), "下");
    assert.ok(tool.isInside(path.join(repo, "not-yet", "deeper"), repo, foldPath), "まだ無い出力先も下");
    assert.ok(tool.isInside(path.join(base, "REPO", "x"), repo, foldPath), "大文字小文字違い");
    assert.ok(tool.isInside(`${repo}${path.sep}sub${path.sep}..${path.sep}out`, repo, foldPath), "..を畳む");
    assert.ok(tool.isInside(`${repo}\\sub\\out`, repo, foldPath), "バックスラッシュ区切り");
    assert.ok(!tool.isInside(path.join(base, "Repo2"), repo, foldPath), "名前の前置だけ同じ兄弟");
    assert.ok(!tool.isInside(path.join(base, "out"), repo, foldPath), "外");
    if (process.platform !== "win32") {
      const link = path.join(base, "link");
      fs.symlinkSync(repo, link);
      assert.ok(tool.isInside(path.join(link, "out"), repo, foldPath), "シンボリックリンク越し");
    }
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("CB-T1216 失敗の条件はページエラー・コンソールの error・CSP 違反。favicon の失敗と error 以外は数えない。終了コードは 0/1/2/3", () => {
  const entries: Entry[] = [
    { kind: "console", type: "log", text: "ok" },
    { kind: "console", type: "warning", text: "warn" },
    { kind: "console", type: "error", text: "Failed to load resource: net::ERR_FILE_NOT_FOUND", url: "file:///x/favicon.ico" },
    { kind: "posted", text: '{"type":"ready"}' },
  ];
  assert.deepEqual(tool.failuresOf(entries), []);
  assert.equal(tool.codeForFailures([]), 0);
  const withError = [...entries, { kind: "console", type: "error", text: "boom" }];
  assert.deepEqual(tool.failuresOf(withError).map((e) => e.text), ["boom"]);
  assert.deepEqual(tool.failuresOf([{ kind: "pageerror", text: "x" }, { kind: "csp", text: "script-src: inline" }]).length, 2);
  assert.equal(tool.codeForFailures(tool.failuresOf(withError)), 1);
  assert.deepEqual(tool.EXIT, { OK: 0, FAILED: 1, USAGE: 2, NOT_READY: 3 });
  assert.equal(tool.logLine({ kind: "console", type: "error", text: "a\nb" }), "[console.error] a\\nb");
  assert.equal(tool.logLine({ kind: "posted", text: "{}" }), "[posted] {}");
});

test("CB-T1217 VS Code の窓口のスタブは、送ったものを window.__posted に積み、状態を覚え、2 度目の取得は本物と同じく投げ、CSP 違反を積む", () => {
  const listeners: Record<string, (event: { violatedDirective: string; blockedURI: string }) => void> = {};
  const win: FakeWindow = {
    document: {
      addEventListener(name, listener) {
        listeners[name] = listener;
      },
    },
  };
  installVsCodeStub(win);
  assert.ok(win.acquireVsCodeApi !== undefined);
  const api = win.acquireVsCodeApi();
  assert.equal(api.getState(), undefined);
  api.setState({ collapsed: ["done"] });
  assert.deepEqual(api.getState(), { collapsed: ["done"] });
  api.postMessage({ type: "ready" });
  api.postMessage({ type: "refresh" });
  assert.deepEqual(win.__posted, [{ type: "ready" }, { type: "refresh" }]);
  assert.throws(() => win.acquireVsCodeApi?.(), /already been acquired/);
  listeners["securitypolicyviolation"]({ violatedDirective: "script-src", blockedURI: "inline" });
  assert.deepEqual(win.__csp, ["script-src: inline"]);
});

test("CB-T1218 使い方の誤りは終了コード 2: 引数なし・両方指定・--repo が無い・--out が --repo の下・--out がファイル", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "render-board-"));
  try {
    const repo = path.join(base, "repo");
    fs.mkdirSync(repo);
    const run = (...args: string[]) => spawnSync(process.execPath, [TOOL, ...args], { encoding: "utf8" });
    assert.equal(run().status, 2);
    assert.equal(run("--repo", repo, "--fixture", "f", "--out", base).status, 2);
    assert.equal(run("--repo", path.join(base, "none"), "--out", path.join(base, "out")).status, 2);
    const inside = run("--repo", repo, "--out", path.join(repo, "out"));
    assert.equal(inside.status, 2, inside.stderr);
    assert.match(inside.stderr, /--out が --repo の下/);
    assert.ok(!fs.existsSync(path.join(repo, "out")), "リポジトリの下に何も作らない");
    fs.writeFileSync(path.join(base, "file"), "");
    assert.equal(run("--repo", repo, "--out", path.join(base, "file")).status, 2, "--out がファイル");
    assert.equal(run("--repo", repo, "--out", path.join(base, "out"), "--appearance", "nope").status, 2);
    assert.equal(run("--repo", repo, "--out", path.join(base, "out"), "--width", "10").status, 2);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});
