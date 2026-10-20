/**
 * origin の URL からのホストの分類（`src/core/hostclass.ts`）。自動判定の表。
 * Python・sh と同じ表（tests/fixtures/host-kinds.json）で突き合わせる。
 * ユーザが設定画面で明示した種類は、自動判定より優先する（`custom-hosts.ts`）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { customHost, originPatterns, releasablePatterns } from "../src/core/custom-hosts.js";
import { autoKind, classifyOrigin } from "../src/core/hostclass.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const TABLE = path.join(here, "..", "..", "..", "..", "..", "tests", "fixtures", "host-kinds.json");

interface Case {
  origin: string;
  kind: string;
  host: string;
  api?: string;
  graphql?: string | null;
  path: string;
  problem?: string;
}

test("CX-T260 ホストの分類は、Python・sh と共通の表（tests/fixtures/host-kinds.json）と一致する", () => {
  const cases = (JSON.parse(fs.readFileSync(TABLE, "utf8")) as { cases: Case[] }).cases;
  assert.ok(cases.length > 10);
  for (const c of cases) {
    const got = classifyOrigin(c.origin);
    assert.equal(got.kind, c.kind, c.origin);
    assert.equal(got.host, c.host, c.origin);
    assert.equal(got.path, c.path, c.origin);
    if (c.problem) {
      assert.match(got.problem, new RegExp(c.problem), c.origin);
      assert.deepEqual([got.api, got.graphql], ["", ""], c.origin);
    } else {
      assert.equal(got.problem, "", c.origin);
      assert.equal(got.api, c.api, c.origin);
      assert.equal(got.graphql, c.graphql ?? "", c.origin);
    }
  }
  assert.equal(classifyOrigin("not a url").kind, "");
  assert.equal(classifyOrigin("ssh://user@host.example.com/g/p.git").kind, "");
});

test("CX-T261 ホスト名だけの自動判定は `.` の区切りで照合する。ghe.com とそのサブドメイン・github.com が GitHub、ほかは GitLab", () => {
  for (const h of ["github.com", "GITHUB.COM", "nri.ghe.com", "NRI.GHE.COM", "ghe.com", "a.b.ghe.com"]) assert.equal(autoKind(h), "github", h);
  for (const h of ["evilgithub.com", "github.com.evil", "evilghe.com", "ghe.com.evil", "foo.aslead.cloud", "gitlab.com", "git.unknown.example", ".ghe.com"]) {
    assert.equal(autoKind(h), "gitlab", h);
  }
});

test("CX-T262 登録した GHE.com は API が https://api.<ホスト>（GraphQL は /graphql）で、許可は画面と API の origin の両方。ポート付きは登録できない", () => {
  const h = customHost("github", "https://nri.ghe.com");
  assert.deepEqual(h, {
    id: "nri.ghe.com",
    kind: "github",
    api: "https://api.nri.ghe.com",
    graphql: "https://api.nri.ghe.com/graphql",
    web: "https://nri.ghe.com",
  });
  assert.deepEqual(originPatterns(h), ["https://nri.ghe.com/*", "https://api.nri.ghe.com/*"]);
  assert.throws(() => customHost("github", "https://nri.ghe.com:8443"), /ポート/);
  // 同じ api の origin を使う別のホストがあれば、その許可は外さない
  const other = customHost("github", "https://api.nri.ghe.com");
  assert.deepEqual(releasablePatterns(h, [other]), ["https://nri.ghe.com/*"]);
});

test("CX-T263 ユーザが明示した種類は自動判定より優先する: GHE.com を GitLab で登録すれば GitLab、未知のホストを GitHub で登録すれば Enterprise Server（/api/v3）", () => {
  const asGitlab = customHost("gitlab", "https://nri.ghe.com");
  assert.deepEqual([asGitlab.kind, asGitlab.api, asGitlab.graphql], ["gitlab", "https://nri.ghe.com/api/v4", "https://nri.ghe.com/api/v4"]);
  const ghes = customHost("github", "https://git.unknown.example");
  assert.deepEqual([ghes.kind, ghes.api, ghes.graphql], ["github", "https://git.unknown.example/api/v3", "https://git.unknown.example/api/graphql"]);
  // 自動判定の表は登録していないホストを黙って GitHub にしない（evil な名前は GitLab 扱い）
  assert.equal(autoKind("evilghe.com"), "gitlab");
  assert.deepEqual(originPatterns(ghes), ["https://git.unknown.example/*"]);
});
