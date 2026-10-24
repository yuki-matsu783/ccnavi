/**
 * 設定画面で足すホスト（`src/core/custom-hosts.ts`）: 種類と URL から通信先を組む規則、保存された行の読み方、組み込みとの合わせ方。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { CUSTOM_KEY, customHost, loadHosts, originPatterns, releasablePatterns, readCustomHosts, readCustomRows, rowOf } from "../src/core/custom-hosts.js";
import { HOSTS } from "./helpers/host.js";

test("CX-T243 足すホスト: GitLab のオリジンから API（/api/v4）と web を組む。GraphQL は読まない（REST だけ）", () => {
  const h = customHost("gitlab", "https://GitLab.Example.com/");
  assert.deepEqual(h, {
    id: "gitlab.example.com",
    kind: "gitlab",
    api: "https://gitlab.example.com/api/v4",
    graphql: "https://gitlab.example.com/api/v4",
    web: "https://gitlab.example.com",
  });
});

test("CX-T244 足すホスト: GitHub Enterprise Server は /api/v3 と /api/graphql。ポートつきの loopback は id に -ポートを付ける", () => {
  const ghe = customHost("github", "https://ghe.example.com");
  assert.deepEqual(
    [ghe.api, ghe.graphql, ghe.web],
    ["https://ghe.example.com/api/v3", "https://ghe.example.com/api/graphql", "https://ghe.example.com"],
  );
  const local = customHost("gitlab", "http://localhost:8080/");
  assert.deepEqual(local, {
    id: "localhost_8080_http",
    kind: "gitlab",
    api: "http://localhost:8080/api/v4",
    graphql: "http://localhost:8080/api/v4",
    web: "http://localhost:8080",
  });
  assert.equal(customHost("github", "http://127.0.0.1:18787").id, "127.0.0.1_18787_http");
});

test("CX-T245 足すホストの誤り: 種類・URL の形・http（loopback 以外）・資格情報・クエリ・パス・IPv6 は投げる", () => {
  const cases: [unknown, unknown, RegExp][] = [
    ["bitbucket", "https://x.example.com", /GitHub か GitLab/],
    ["gitlab", "", /URL を入れてください/],
    ["gitlab", "not a url", /Invalid URL/],
    ["gitlab", "http://gitlab.example.com", /https のみ指定できます/],
    ["gitlab", "https://u:p@gitlab.example.com", /資格情報/],
    ["gitlab", "https://gitlab.example.com/?x=1", /資格情報・クエリ/],
    ["gitlab", "https://gitlab.example.com/gitlab", /オリジンのみ/],
    ["gitlab", "http://[::1]:8080", /IPv6/],
    
  ];
  for (const [kind, url, pattern] of cases) {
    assert.throws(() => customHost(kind, url), pattern, `${String(kind)} ${String(url)}`);
  }
});

test("CX-T246 保存された行は、読めた分だけを組み直す。組み込みと同じ名前の行と、重なった行は捨てる", () => {
  const stored = [
    { kind: "gitlab", url: "https://gitlab.example.com" },
    { kind: "gitlab", url: "http://gitlab.example.com" }, // https でない（読めない）
    { kind: "github", url: "https://github.com" }, // 組み込みの github.com と同じ名前
    { kind: "gitlab", url: "https://GITLAB.example.com/" }, // 上と同じホスト（重なり）
    null,
    "not a row",
  ];
  const hosts = readCustomHosts(stored, HOSTS);
  assert.deepEqual(
    hosts.map((h) => h.id),
    ["gitlab.example.com"],
  );
  // 行の読み方は「組み立てられた分」だけ（https でない・読めない形は捨てる）。重なりと組み込みとの名前の重なりは hosts 側で捨てる
  assert.deepEqual(readCustomRows(stored), [
    { kind: "gitlab", url: "https://gitlab.example.com" },
    { kind: "github", url: "https://github.com" },
    { kind: "gitlab", url: "https://gitlab.example.com" },
  ]);
  assert.deepEqual(readCustomHosts(undefined, HOSTS), []);
});

test("CX-T247 通信先の一覧: 組み込み（ビルドのもの）が先で、設定画面で足したものが後。保存の形は種類と URL だけ", async () => {
  const area = {
    data: { [CUSTOM_KEY]: [{ kind: "gitlab", url: "http://localhost:8080" }] } as Record<string, unknown>,
    async get(keys: string | string[] | null) {
      const names = keys === null ? Object.keys(this.data) : typeof keys === "string" ? [keys] : keys;
      return Object.fromEntries(names.filter((k) => k in this.data).map((k) => [k, this.data[k]]));
    },
    async set(items: Record<string, unknown>) {
      Object.assign(this.data, items);
    },
  };
  const hosts = await loadHosts(area, HOSTS);
  assert.deepEqual(
    hosts.map((h) => h.id),
    ["github.com", "gitlab.com", "localhost_8080_http"],
  );
  // 行は種類と URL だけ（API の欄は読むたびに組み直す）
  assert.deepEqual(rowOf(hosts[2]), { kind: "gitlab", url: "http://localhost:8080" });
  // Chrome の一致パターンは port を書けない（port を外した形）
  assert.deepEqual(originPatterns(hosts[2]), ["http://localhost/*"]);
  assert.deepEqual(originPatterns(customHost("gitlab", "https://gitlab.example.com:8443")), ["https://gitlab.example.com/*"]);
});

test("CX-T248 足すホストの id は、同じサーバーに別の id を付けさせない: https と http・ポートの違いで別の id。末尾のドットと _ は拒否", () => {
  const ids = [
    customHost("gitlab", "https://gitlab.example.com").id,
    customHost("gitlab", "https://gitlab.example.com:8443").id,
    customHost("gitlab", "https://localhost:8443").id,
    customHost("gitlab", "http://localhost:8443").id,
    customHost("gitlab", "http://localhost").id,
  ];
  assert.equal(new Set(ids).size, ids.length, ids.join(" "));
  assert.throws(() => customHost("gitlab", "https://gitlab.example.com."), /末尾にドット/);
  assert.throws(() => customHost("gitlab", "https://gitlab_example.com"), /_ を使わない/);
});

test("CX-T249 許可を外す判断: 同じ許可を組み込みや別の行が使っていれば外さない", () => {
  const a = customHost("gitlab", "https://gitlab.example.com");
  const b = customHost("gitlab", "https://gitlab.example.com:8443");
  assert.deepEqual(releasablePatterns(a, [b]), []);
  assert.deepEqual(releasablePatterns(a, HOSTS), ["https://gitlab.example.com/*"]);
  assert.deepEqual(releasablePatterns(a, [...HOSTS, customHost("github", "https://gitlab.example.com")]), []);
});
