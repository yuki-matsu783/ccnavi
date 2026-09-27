/**
 * 診断ログ（`src/log.ts`）。行の形・logfmt の逃がし方・レベルの絞り込み・置き場・書けないときの黙殺・
 * リンクを辿らないこと・出どころの名前・0600・URL と scp 形の伏せ字。
 * sh と Python が同じ行を出すことは Python のテスト（tests/sh/test_diaglog_sh.py）が見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { runLint, runTest } from "../../src/ccnavi.js";
import { formatLine, get, LEVEL_ENV, maskUserinfo, quote, stamp, threshold } from "../../src/log.js";

const HEAD = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} (DEBUG|INFO |WARN |ERROR) ([^[\s]+)\[(\d+)\] /;

function workspace(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-log-"));
}

function lines(root: string, name = "probe"): string[] {
  const file = path.join(root, "logs", "diag", `${name}.log`);
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8").split("\n").filter((x) => x !== "") : [];
}

function withLevel<T>(value: string | undefined, run: () => T): T {
  const saved = process.env[LEVEL_ENV];
  if (value === undefined) {
    delete process.env[LEVEL_ENV];
  } else {
    process.env[LEVEL_ENV] = value;
  }
  try {
    return run();
  } finally {
    if (saved === undefined) {
      delete process.env[LEVEL_ENV];
    } else {
      process.env[LEVEL_ENV] = saved;
    }
  }
}

test("CB-T280 診断ログの行は 時刻・5 字のレベル・出どころ[pid]・本文・logfmt の値 の形", () => {
  const root = workspace();
  try {
    withLevel(undefined, () => get("probe", root).info("push を拒否した", { reason: "unapproved", ticket: "T-12" }));
    const [line] = lines(root);
    const head = HEAD.exec(line);
    assert.ok(head !== null, line);
    assert.equal(head[1], "INFO ");
    assert.equal(head[2], "probe");
    assert.equal(head[3], String(process.pid));
    assert.equal(line.slice(head[0].length), "push を拒否した reason=unapproved ticket=T-12");
    assert.equal(formatLine("T", "ERROR", "n", 1, "m", { a: true, b: null, c: undefined, d: 3 }), "T ERROR n[1] m a=true b= c= d=3");
    assert.match(stamp(new Date()), /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("CB-T281 logfmt の値は空白・タブ・\"・=・改行を含むときだけ囲み、\\ と \" を逃がし、改行を \\n に畳む", () => {
  assert.equal(quote("abc"), "abc");
  assert.equal(quote("a\\b"), "a\\b");
  assert.equal(quote(""), "");
  assert.equal(quote("a b"), '"a b"');
  assert.equal(quote("a\tb"), '"a\tb"');
  assert.equal(quote("k=v"), '"k=v"');
  assert.equal(quote('say "hi"\\x'), '"say \\"hi\\"\\\\x"');
  assert.equal(quote("l1\nl2\r\nl3\rl4"), '"l1\\nl2\\nl3\\nl4"');
  assert.equal(formatLine("T", "INFO", "n", 1, "1 行目\n2 行目", {}), "T INFO  n[1] 1 行目\\n2 行目");
});

test("CB-T282 レベルは CCNAVI_LOG_LEVEL で絞る。大文字小文字を問わず、空と読めない値は INFO", () => {
  assert.equal(withLevel(undefined, () => threshold()), "INFO");
  assert.equal(withLevel("debug", () => threshold()), "DEBUG");
  assert.equal(withLevel("loud", () => threshold()), "INFO");
  const root = workspace();
  try {
    withLevel("Warn", () => {
      const log = get("probe", root);
      log.debug("見えない");
      log.info("見えない");
      log.warn("見える");
      log.error("見える");
      assert.equal(log.enabled("INFO"), false);
      assert.equal(log.enabled("ERROR"), true);
    });
    assert.deepEqual(
      lines(root).map((x) => HEAD.exec(x)?.[1]),
      ["WARN ", "ERROR"],
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("CB-T283 書けないときは黙って捨て、console にも何も出さない。root が空なら書かない", () => {
  const root = workspace();
  const spoken: unknown[] = [];
  const saved = { log: console.log, error: console.error, warn: console.warn };
  console.log = (...args: unknown[]) => spoken.push(args);
  console.error = (...args: unknown[]) => spoken.push(args);
  console.warn = (...args: unknown[]) => spoken.push(args);
  try {
    // logs がファイルなので logs/diag を作れない
    fs.writeFileSync(path.join(root, "logs"), "x");
    assert.doesNotThrow(() => get("probe", root).error("書けない", { a: "b" }));
    assert.doesNotThrow(() => get("probe", "").error("置き場が無い"));
  } finally {
    Object.assign(console, saved);
    fs.rmSync(root, { recursive: true, force: true });
  }
  assert.deepEqual(spoken, []);
});

function tryLink(target: string, link: string): boolean {
  try {
    fs.symlinkSync(target, link);
    return true;
  } catch {
    return false;
  }
}

test("CB-T284 logs・logs/diag・書き先のどれかがリンクなら、辿らずに捨てる", (t) => {
  const root = workspace();
  const outside = workspace();
  try {
    const diag = path.join(root, "logs", "diag");
    fs.mkdirSync(diag, { recursive: true });
    const victim = path.join(root, "logs", "decisions.jsonl");
    fs.writeFileSync(victim, "{}\n");
    if (!tryLink("../decisions.jsonl", path.join(diag, "probe.log"))) {
      t.skip("リンクを作れない");
      return;
    }
    withLevel(undefined, () => get("probe", root).error("x"));
    assert.equal(fs.readFileSync(victim, "utf8"), "{}\n");
    // logs/diag がリンク
    fs.rmSync(path.join(root, "logs"), { recursive: true, force: true });
    fs.mkdirSync(path.join(root, "logs"));
    tryLink(outside, diag);
    withLevel(undefined, () => get("probe", root).error("x"));
    assert.deepEqual(fs.readdirSync(outside), []);
    // logs そのものがリンク
    fs.rmSync(path.join(root, "logs"), { recursive: true, force: true });
    fs.mkdirSync(path.join(outside, "diag"));
    tryLink(outside, path.join(root, "logs"));
    withLevel(undefined, () => get("probe", root).error("x"));
    assert.deepEqual(fs.readdirSync(path.join(outside, "diag")), []);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test("CB-T285 出どころの名前が [A-Za-z0-9_-] 以外を含めば書かない。新しいファイルは 0600", () => {
  const root = workspace();
  try {
    for (const name of ["../escape", "a.b", "a b", "a/b", ""]) {
      withLevel(undefined, () => get(name, root).error("x"));
      assert.equal(fs.existsSync(path.join(root, "logs")), false, name);
      assert.equal(fs.existsSync(path.join(root, "escape.log")), false, name);
    }
    if (process.platform !== "win32") {
      const saved = process.umask(0o022);
      try {
        withLevel(undefined, () => get("probe", root).info("x"));
      } finally {
        process.umask(saved);
      }
      assert.equal(fs.statSync(path.join(root, "logs", "diag", "probe.log")).mode & 0o777, 0o600);
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("CB-T286 URL と scp 形に埋まった資格情報を *** に伏せる（本文と値の両方。sh と Python と同じ読み）", () => {
  const cases: Array<[string, string]> = [
    ["https://user:tok@host/x y", "https://***@host/x y"],
    ["oauth2:tok@gitlab.example:org/r.git", "***@gitlab.example:org/r.git"],
    ["git@github.com:org/r.git", "git@github.com:org/r.git"],
    ["  a://b://c@d/e\tu:p@h:x\nz", "  a://b://***@d/e\t***@h:x\nz"],
    ["no at", "no at"],
    ["x@y", "x@y"],
    ["@a:b", "@a:b"],
    ["https://@h", "https://***@h"],
    ["ssh://git@h:22/p https://a:b@c@d/p", "ssh://***@h:22/p https://***@d/p"],
  ];
  for (const [given, expected] of cases) {
    assert.equal(maskUserinfo(given), expected, given);
  }
  const root = workspace();
  try {
    withLevel(undefined, () => get("probe", root).info("clone https://u:SECRET@h/x", { url: "oauth2:SECRET@h:o/r.git" }));
    const [line] = lines(root);
    assert.equal(line.replace(HEAD, ""), "clone https://***@h/x url=***@h:o/r.git");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

/** `run` を走らせる間だけ閾値を置く（logger は書くときの閾値ではなく get の時点の閾値を見る。await を挟むので同期の withLevel では足りない） */
async function withLevelAsync<T>(value: string | undefined, run: () => Promise<T>): Promise<T> {
  const saved = process.env[LEVEL_ENV];
  if (value === undefined) {
    delete process.env[LEVEL_ENV];
  } else {
    process.env[LEVEL_ENV] = value;
  }
  try {
    return await run();
  } finally {
    if (saved === undefined) {
      delete process.env[LEVEL_ENV];
    } else {
      process.env[LEVEL_ENV] = saved;
    }
  }
}

/** 実行ファイルの代わりの sh。`body` をそのまま書く。実行の権限は `mode` */
function fakeBin(root: string, body: string, mode = 0o755): string {
  const file = path.join(root, "fake-ccnavi");
  fs.writeFileSync(file, `#!/bin/sh\n${body}\n`, { mode });
  fs.chmodSync(file, mode);
  return file;
}

test("CB-T287 実行ファイルの失敗は ERROR に sub・終了コード・標準エラーの長さだけを書く（中身は書かない）。利用者への文面は変わらない", { skip: process.platform === "win32" }, async () => {
  const root = workspace();
  try {
    const bin = fakeBin(root, "echo 'token=SECRET https://u:SECRET@h/x' >&2\nexit 3");
    const result = await withLevelAsync(undefined, () => runLint(root, bin, { kind: "risk", path: "risks.yml" }));
    assert.equal(result.ok, false);
    assert.ok(!result.ok && result.error.startsWith("ccnavi --lint が失敗しました: token=SECRET"), JSON.stringify(result));
    const found = lines(root, "ccnavi-board");
    assert.equal(found.length, 1, found.join("\n"));
    const head = HEAD.exec(found[0]);
    assert.ok(head !== null, found[0]);
    assert.equal(head[1], "ERROR");
    assert.match(found[0].slice(head[0].length), /^実行ファイルが失敗した sub=--lint code=3 ms=\d+ stderr_len=\d+$/);
    assert.ok(!found[0].includes("SECRET"), found[0]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("CB-T288 実行ファイルを起動できない・出力を読めないときは ERROR に理由の種類と長さだけを書く。成功は DEBUG でだけ書く", { skip: process.platform === "win32" }, async () => {
  const root = workspace();
  try {
    // 実行の権限が無い: 起動できない（errno）
    const denied = fakeBin(root, "exit 0", 0o644);
    const refused = await withLevelAsync(undefined, () => runTest(root, denied, { kind: "workspace", path: "rules.yml" }, "Bash", "ls"));
    assert.equal(refused.ok, false);
    // JSON でない出力: 読めない（kind=json）。中身は書かない
    fs.rmSync(denied);
    const garbled = fakeBin(root, "echo 'not json SECRET'");
    const unread = await withLevelAsync(undefined, () => runTest(root, garbled, { kind: "workspace", path: "rules.yml" }, "Bash", "ls"));
    assert.equal(unread.ok, false);
    const found = lines(root, "ccnavi-board").map((line) => line.replace(HEAD, ""));
    assert.deepEqual(found, [
      "実行ファイルを起動できない sub=\"--test --json\" errno=EACCES",
      "実行ファイルの出力を読めない sub=\"--test --json\" kind=json stdout_len=16",
    ]);
    // 既定の INFO では成功を書かない。DEBUG なら所要時間を書く
    fs.rmSync(path.join(root, "logs"), { recursive: true, force: true });
    const quiet = fakeBin(root, "echo '{}'");
    await withLevelAsync(undefined, () => runLint(root, quiet, { kind: "risk", path: "risks.yml" }));
    assert.deepEqual(lines(root, "ccnavi-board"), []);
    await withLevelAsync("DEBUG", () => runLint(root, quiet, { kind: "risk", path: "risks.yml" }));
    const debug = lines(root, "ccnavi-board").map((line) => line.replace(HEAD, ""));
    assert.equal(debug.length, 1, debug.join("\n"));
    assert.match(debug[0], /^実行ファイルが返った sub=--lint code=0 ms=\d+ stdout_len=3$/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
