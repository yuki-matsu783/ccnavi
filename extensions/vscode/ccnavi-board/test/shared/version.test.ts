/**
 * 実行ファイルの版の JSON（`ccnavi --version --json`）の読み方と、使う前の対応の確かめ（`core/version.ts`）。
 * 実行ファイルの答えは組んで渡す偽物。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  EXTENSION_COMPAT,
  missingFeatures,
  missingFlags,
  parseVersionJson,
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
    features: [],
    ...overrides,
  };
}

function ok(overrides: Partial<VersionInfo> = {}): VersionProbe {
  return { kind: "ok", info: info(overrides) };
}

test("CB-T268 版の JSON を読む。形の版が違う・compat か flags がないものは読まない", () => {
  const body = { schema: VERSION_SCHEMA, version: "0.1.0", commit: "unknown", built: false, compat: 1, flags: ["--lint", 3], formats: {}, outputs: {} };
  const parsed = parseVersionJson(JSON.stringify(body));
  assert.ok(parsed.ok);
  assert.deepEqual(parsed.value, { schema: VERSION_SCHEMA, version: "0.1.0", commit: "unknown", built: false, compat: 1, flags: ["--lint"], features: [] });
  assert.equal(parseVersionJson(JSON.stringify({ ...body, schema: VERSION_SCHEMA + 1 })).ok, false);
  assert.equal(parseVersionJson(JSON.stringify({ ...body, compat: "1" })).ok, false);
  assert.equal(parseVersionJson(JSON.stringify({ ...body, flags: undefined })).ok, false);
  assert.equal(parseVersionJson("usage: ccnavi").ok, false);
});

test("CB-T270 使うフラグを実行ファイルが知っているかを版の JSON の flags で見る。確認できなければ進めない", () => {
  const what = "ccnavi --lint --json --flow";
  assert.equal(missingFlags(ok(), ["--flow"], what, false), undefined);

  const missing = missingFlags(ok({ flags: ["--lint", "--version"] }), ["--flow"], what, false) ?? "";
  assert.match(missing, /--flow に対応していません（古い版です）/);
  assert.match(missing, /「ccnavi --lint --json --flow」は使えない/);
  assert.match(missing, /ccnavi-setup\.sh/);

  const failed = missingFlags({ kind: "failed", error: "打ち切った" }, ["--flow"], what, false) ?? "";
  assert.match(failed, /打ち切った/);
});

test("CB-T567 版の JSON の features を読む。持たない古い JSON は features が空", () => {
  const body = { schema: VERSION_SCHEMA, version: "0.1.0", commit: "unknown", built: false, compat: 1, flags: ["--lint"] };
  const withFeatures = parseVersionJson(JSON.stringify({ ...body, features: ["handoff", 3] }));
  assert.ok(withFeatures.ok);
  assert.deepEqual(withFeatures.value.features, ["handoff"]);
  const without = parseVersionJson(JSON.stringify(body));
  assert.ok(without.ok);
  assert.deepEqual(without.value.features, []);
});

test("CB-T568 使う機能を実行ファイルが持つかを features で見る。足りなければ名指しし、確認できなければ進めない", () => {
  const what = "取り込み待ち画面";
  assert.equal(missingFeatures(ok({ features: ["handoff", "user-entry"] }), ["handoff"], what, false), undefined);

  const missing = missingFeatures(ok({ features: ["handoff"] }), ["handoff", "user-entry"], what, false) ?? "";
  assert.match(missing, /user-entry の機能に対応していません/);
  assert.doesNotMatch(missing, /handoff /);
  assert.match(missing, /「取り込み待ち画面」は使えない/);

  const failed = missingFeatures({ kind: "failed", error: "打ち切った" }, ["handoff"], what, false) ?? "";
  assert.match(failed, /打ち切った/);
});

test("CB-T569 features のない実行ファイルは、機能を求めると足りないと言う", () => {
  assert.match(missingFeatures(ok(), ["handoff"], "取り込み待ち画面", false) ?? "", /handoff の機能に対応していません/);
});

test("CB-T1137 handoff を持たない実行ファイルには、取り込み待ち画面を無効にして古い版と言い、組み立て直しを案内する", () => {
  const what = "取り込み待ち画面";
  assert.equal(missingFeatures(ok({ features: ["user-entry", "handoff"] }), ["handoff"], what, true), undefined);

  // user-entry だけを持つ（handoff の前の）実行ファイル
  const stale = missingFeatures(ok({ features: ["user-entry"] }), ["handoff"], what, true) ?? "";
  assert.match(stale, /handoff の機能に対応していません（古い版です）/);
  assert.match(stale, /組み立て直して/);
  // 配り直しの案内（ccnavi のリポジトリではないワークスペース）
  assert.match(missingFeatures(ok({ features: [] }), ["handoff"], what, false) ?? "", /ccnavi-setup\.sh/);
  // 確かめられない実行ファイルも進めない
  assert.match(missingFeatures({ kind: "failed", error: "打ち切った" }, ["handoff"], what, true) ?? "", /打ち切った/);
});

test("CB-T1100 user-entry を持たない実行ファイルには、ユーザ専用の操作の入口で古い版と言って組み立て直しを案内する", () => {
  const what = "ccnavi user agree --yes";
  assert.equal(missingFeatures(ok({ features: ["user-entry"] }), ["user-entry"], what, false), undefined);

  const stale = missingFeatures(ok({ compat: 12, features: ["handoff"] }), ["user-entry"], what, true) ?? "";
  assert.match(stale, /user-entry の機能に対応していません（古い版です）/);
  assert.match(stale, /「ccnavi user agree --yes」は使えない/);
  assert.match(stale, /組み立て直して/);
});
