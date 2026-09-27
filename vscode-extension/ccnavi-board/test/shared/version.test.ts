/**
 * 実行ファイルの版の JSON（`ccnavi --version --json`）の読み方と、拡張との食い違いの言い分け（`core/version.ts`）。
 * 実行ファイルの答えは組んで渡す偽物。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  EXTENSION_COMPAT,
  missingFlags,
  parseVersionJson,
  skewMessage,
  UPDATE_EXTENSION_HINT,
  VERSION_SCHEMA,
  type VersionInfo,
  type VersionProbe,
} from "../../src/core/version.js";

function info(overrides: Partial<VersionInfo> = {}): VersionInfo {
  return {
    schema: VERSION_SCHEMA,
    version: "0.1.0",
    commit: "0123456789abcdef0123",
    built: true,
    compat: EXTENSION_COMPAT,
    flags: ["--flow", "--json", "--lint", "--version"],
    ...overrides,
  };
}

function ok(overrides: Partial<VersionInfo> = {}): VersionProbe {
  return { kind: "ok", info: info(overrides) };
}

test("CB-T266 版の JSON を読む。形の版が違う・compat か flags が無いものは読まない", () => {
  const body = { schema: VERSION_SCHEMA, version: "0.1.0", commit: "unknown", built: false, compat: 1, flags: ["--lint", 3], formats: {}, outputs: {} };
  const parsed = parseVersionJson(JSON.stringify(body));
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.value, { schema: VERSION_SCHEMA, version: "0.1.0", commit: "unknown", built: false, compat: 1, flags: ["--lint"] });
  assert.equal(parseVersionJson(JSON.stringify({ ...body, schema: VERSION_SCHEMA + 1 })).ok, false);
  assert.equal(parseVersionJson(JSON.stringify({ ...body, compat: "1" })).ok, false);
  assert.equal(parseVersionJson(JSON.stringify({ ...body, flags: undefined })).ok, false);
  assert.equal(parseVersionJson("usage: ccnavi").ok, false);
});

test("CB-T267 起動のときの知らせ。揃っていれば言わず、古い側と直し方を名指しする", () => {
  assert.equal(skewMessage(ok(), false), undefined);
  // 聞けなかったことは起動では言わない（画面を開いたときに言う）
  assert.equal(skewMessage({ kind: "failed", error: "x" }, false), undefined);

  const old = skewMessage({ kind: "old" }, false) ?? "";
  assert.match(old, /--version を知りません/);
  assert.match(old, /scripts\/ccnavi-setup\.sh/);
  // ccnavi のリポジトリ（build.py とソースがある）では組み立て直し
  const oldHere = skewMessage({ kind: "old" }, true) ?? "";
  assert.match(oldHere, /build\.py を回して組み立て直して/);
  assert.doesNotMatch(oldHere, /ccnavi-setup/);

  const exeOld = skewMessage(ok({ compat: EXTENSION_COMPAT - 1 }), true) ?? "";
  assert.match(exeOld, /実行ファイルが古い版/);
  assert.match(exeOld, /0123456789ab/);
  assert.match(exeOld, /組み立て直して/);

  const extOld = skewMessage(ok({ compat: EXTENSION_COMPAT + 1, commit: "unknown" }), true) ?? "";
  assert.match(extOld, /拡張が古い版/);
  assert.match(extOld, /コミット不明/);
  assert.ok(extOld.endsWith(UPDATE_EXTENSION_HINT), extOld);
});

test("CB-T268 使うフラグを実行ファイルが知っているかを版の JSON の flags で見る。確かめられなければ進めない", () => {
  const what = "ccnavi --lint --json --flow";
  assert.equal(missingFlags(ok(), ["--flow"], what, false), undefined);

  const missing = missingFlags(ok({ flags: ["--lint", "--version"] }), ["--flow"], what, false) ?? "";
  assert.match(missing, /--flow を知りません（古い版です）/);
  assert.match(missing, /ccnavi --lint --json --flow で確かめられない/);
  assert.match(missing, /ccnavi-setup\.sh/);

  // `--version` を知らない実行ファイルは、`--flow` を知っていても確かめられないとして止める
  const old = missingFlags({ kind: "old" }, ["--flow"], what, true) ?? "";
  assert.match(old, /--version を知りません/);
  assert.match(old, /組み立て直して/);

  const failed = missingFlags({ kind: "failed", error: "打ち切った" }, ["--flow"], what, false) ?? "";
  assert.match(failed, /打ち切った/);
});
