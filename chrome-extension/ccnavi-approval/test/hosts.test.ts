/**
 * 通信先の一覧から manifest を組む（ADR-0093 の D24・5.5 の 3・5）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { csp, manifest, parseHosts } from "../src/core/hosts.js";
import { HERE } from "./helpers/python.js";

const defaults = () => parseHosts(fs.readFileSync(path.join(HERE, "hosts.json"), "utf8"));

test("CX-T001 既定の通信先は api.github.com と gitlab.com だけ。host_permissions と connect-src が同じ順序", () => {
  const m = manifest(defaults(), "0.1.0") as { host_permissions: string[]; content_security_policy: { extension_pages: string } };
  assert.deepEqual(m.host_permissions, ["https://api.github.com/*", "https://gitlab.com/*"]);
  assert.equal(
    m.content_security_policy.extension_pages,
    "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; img-src 'self'; form-action 'none'; base-uri 'none'; connect-src 'self' https://api.github.com https://gitlab.com",
  );
});

test("CX-T002 CSP に 'unsafe-eval'・'unsafe-inline'・外のスクリプトを入れない。足すのは 'wasm-unsafe-eval' だけ", () => {
  const text = csp(defaults());
  assert.ok(!text.includes("'unsafe-eval'"));
  assert.ok(!text.includes("unsafe-inline"));
  const script = /script-src ([^;]*)/.exec(text)?.[1];
  assert.equal(script, "'self' 'wasm-unsafe-eval'");
});

test("CX-T003 manifest は externally_connectable を宣言しない。権限は storage と PAT の期限を比べる alarms だけ", () => {
  const m = manifest(defaults(), "0.1.0");
  assert.equal("externally_connectable" in m, false);
  assert.deepEqual(m.permissions, ["storage", "alarms"]);
  assert.equal(m.manifest_version, 3);
});

test("CX-T004 一覧の誤りでビルドを止める（http・資格情報つき・重なった id・知らない kind）", () => {
  const one = (h: Record<string, unknown>) => JSON.stringify({ hosts: [h] });
  const ok = { id: "ghe.example.com", kind: "github", api: "https://ghe.example.com/api/v3", graphql: "https://ghe.example.com/api/graphql", web: "https://ghe.example.com" };
  assert.equal(parseHosts(one(ok))[0].api, "https://ghe.example.com/api/v3");
  assert.throws(() => parseHosts(one({ ...ok, api: "http://ghe.example.com/api/v3" })), /https/);
  assert.throws(() => parseHosts(one({ ...ok, api: "https://u:p@ghe.example.com/api/v3" })), /資格情報/);
  assert.throws(() => parseHosts(one({ ...ok, kind: "bitbucket" })), /kind/);
  assert.throws(() => parseHosts(JSON.stringify({ hosts: [ok, ok] })), /重なって/);
  assert.throws(() => parseHosts(JSON.stringify({ hosts: [] })), /空/);
  // 試験用の loopback だけ http を許す
  assert.equal(parseHosts(one({ ...ok, api: "http://127.0.0.1:18787", graphql: "http://127.0.0.1:18787/graphql" }))[0].api, "http://127.0.0.1:18787");
});

test("CX-T005 組織ごとのビルド: GHES の一覧なら通信先はそのホストだけになる", () => {
  const hosts = parseHosts(
    JSON.stringify({ hosts: [{ id: "ghe.example.com", kind: "github", api: "https://ghe.example.com/api/v3", graphql: "https://ghe.example.com/api/graphql", web: "https://ghe.example.com" }] }),
  );
  const m = manifest(hosts, "0.1.0") as { host_permissions: string[] };
  assert.deepEqual(m.host_permissions, ["https://ghe.example.com/*"]);
  assert.match(csp(hosts), /connect-src 'self' https:\/\/ghe\.example\.com$/);
});

test("CX-T157 セルフホストの GitLab を足したビルド: 通信先はそのホストだけで、既定の gitlab.com は入らない（D24。段階 5）", () => {
  const hosts = parseHosts(
    JSON.stringify({
      hosts: [
        { id: "github.com", kind: "github", api: "https://api.github.com", graphql: "https://api.github.com/graphql", web: "https://github.com" },
        { id: "gitlab.example.com", kind: "gitlab", api: "https://gitlab.example.com/api/v4", web: "https://gitlab.example.com" },
      ],
    }),
  );
  const m = manifest(hosts, "0.4.0") as { host_permissions: string[]; content_security_policy: { extension_pages: string } };
  assert.deepEqual(m.host_permissions, ["https://api.github.com/*", "https://gitlab.example.com/*"]);
  assert.match(m.content_security_policy.extension_pages, /connect-src 'self' https:\/\/api\.github\.com https:\/\/gitlab\.example\.com$/);
  assert.ok(!JSON.stringify(m).includes("https://gitlab.com"));
  // GitLab の graphql 欄は読まない（REST だけ。通信先に余計なオリジンを足さない）
  assert.equal(hosts[1].graphql, "https://gitlab.example.com/api/v4");
  // http のセルフホストは試験用の loopback のほかは受けない
  assert.throws(
    () => parseHosts(JSON.stringify({ hosts: [{ id: "gl.local", kind: "gitlab", api: "http://gl.local/api/v4", web: "http://gl.local" }] })),
    /https/,
  );
});
