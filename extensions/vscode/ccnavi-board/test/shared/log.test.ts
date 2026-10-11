/**
 * 診断ログ（`src/log.ts`）。行の形・logfmt のエスケープのしかた・レベルの絞り込み・置き場・書けないときに何も出さないこと・
 * リンクをたどらないこと・出どころの名前・0600・URL と scp 形式の資格情報の伏せ方。
 * sh と Python が同じ行を出すことは Python のテスト（tests/sh/test_diaglog_sh.py）が見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
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

test("CB-T283 書けないときはエラーにせず捨て、console にも何も出さない。root が空なら書かない", () => {
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
