/**
 * PAT の期限。ホストの応答ヘッダから読み、読めなければ登録のときの日付、
 * どちらも無ければ「期限不明」。切れる 7 日前から知らせる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { badgeText, expiryNotice, WARN_DAYS } from "../src/core/expiry.js";
import { parseExpiration } from "../src/core/github.js";
import { dispatch } from "../src/core/protocol.js";
import type { TokenStatus } from "../src/core/protocol.js";
import type { TokenMeta } from "../src/core/expiry.js";
import { fixture } from "./fixtures/repo.js";
import { BOARD, deps, OPTIONS } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";

const NOW = new Date("2026-09-30T00:00:00Z");

test("CX-T114 期限のヘッダの表記を読む（UTC・時差つき）。読めなければ空", () => {
  assert.equal(parseExpiration("2026-12-31 00:00:00 UTC"), "2026-12-31T00:00:00.000Z");
  assert.equal(parseExpiration("2026-12-31 09:00:00 +0900"), "2026-12-31T00:00:00.000Z");
  assert.equal(parseExpiration("2026-12-31T00:00:00Z"), "2026-12-31T00:00:00.000Z");
  assert.equal(parseExpiration("そのうち"), "");
});

test(`CX-T115 切れる ${WARN_DAYS} 日前から知らせ、切れたら差し替えを促す。どちらも無ければ期限不明`, () => {
  const at = (days: number) => new Date(NOW.getTime() + days * 86400000).toISOString();
  assert.equal(expiryNotice("github.com", { host: at(30) }, NOW).level, "ok");
  const soon = expiryNotice("github.com", { host: at(3) }, NOW);
  assert.deepEqual([soon.level, soon.daysLeft, soon.source], ["soon", 3, "host"]);
  assert.match(soon.text, /あと 3 日で切れる/);
  assert.equal(expiryNotice("github.com", { host: at(-1) }, NOW).level, "expired");
  const manual = expiryNotice("github.com", { manual: "2026-10-02" }, NOW);
  assert.deepEqual([manual.level, manual.source], ["soon", "manual"]);
  // ホストの値を優先する（登録のときの日付より先に使う）
  assert.equal(expiryNotice("github.com", { host: at(60), manual: "2026-10-02" }, NOW).level, "ok");
  const unknown = expiryNotice("github.com", {}, NOW);
  assert.equal(unknown.level, "unknown");
  assert.match(unknown.text, /期限が分からない/);
  assert.equal(badgeText([expiryNotice("a", { host: at(30) }, NOW)]), "");
  assert.equal(badgeText([expiryNotice("a", { host: at(5) }, NOW), expiryNotice("b", { host: at(2) }, NOW)]), "2d");
  assert.equal(badgeText([expiryNotice("a", { host: at(-1) }, NOW)]), "PAT!");
});

test("CX-T116 service worker はホストの応答から期限を読んで保存し、画面には期限だけを返す。登録のときの日付も受ける", async () => {
  const m = new MockGitHub(fixture());
  m.expiration = "2026-10-03 00:00:00 UTC";
  const metas = new Map<string, TokenMeta>();
  const tokens = new Map([["github.com", TOKEN]]);
  const d = deps(m, tokens, metas, () => NOW);
  const before = (await dispatch({ kind: "token.status", host: "github.com" }, BOARD, d)) as { value: TokenStatus };
  assert.equal(before.value.notice?.level, "unknown");
  await dispatch({ kind: "host", host: "github.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
  assert.equal(metas.get("github.com")?.host, "2026-10-03T00:00:00.000Z");
  const after = (await dispatch({ kind: "token.status", host: "github.com" }, BOARD, d)) as { value: TokenStatus };
  assert.equal(after.value.notice?.level, "soon");
  assert.equal(after.value.notice?.daysLeft, 3);
  // 差し替えると前のトークンの期限は捨て、入れた日付を保存する
  const set = await dispatch({ kind: "token.set", host: "github.com", token: TOKEN, expires: "2026-12-01" }, OPTIONS, d);
  assert.equal(set.ok, true);
  assert.deepEqual(metas.get("github.com"), { manual: "2026-12-01" });
  const bad = await dispatch({ kind: "token.set", host: "github.com", token: TOKEN, expires: "12/01" }, OPTIONS, d);
  assert.match((bad as { error: string }).error, /YYYY-MM-DD/);
});
