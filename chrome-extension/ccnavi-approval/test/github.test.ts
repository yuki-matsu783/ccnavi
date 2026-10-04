/**
 * GitHub の読み取り。模擬の GitHub で形を確かめる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import * as gh from "../src/core/github.js";
import { fixture } from "./fixtures/repo.js";
import { HOSTS } from "./helpers/host.js";
import { blobSha, MockGitHub, TOKEN } from "./helpers/mock-github.js";

function client(fetch: gh.Fetch) {
  return { host: HOSTS[0], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 } };
}

test("CX-T020 デフォルトブランチ・ブランチの先頭を読む。無いブランチは null", async () => {
  const m = new MockGitHub(fixture());
  const c = client(m.fetch);
  assert.deepEqual(await gh.repoInfo(c, "acme", "widgets"), { defaultBranch: "main" });
  assert.equal(await gh.branchHead(c, "acme", "widgets", "i0001"), m.head("i0001"));
  assert.equal(await gh.branchHead(c, "acme", "widgets", "nope"), null);
  assert.deepEqual(c.counter, { rest: 3, graphql: 0 });
});

test("CX-T021 直近 N 日のブランチだけを返す（表示用）", async () => {
  const m = new MockGitHub(fixture());
  const refs = await gh.recentRefs(client(m.fetch), "acme", "widgets", new Date("2026-09-26T00:00:00Z"));
  assert.deepEqual(refs.map((r) => r.name).sort(), ["feature-x", "i0001", "i0002"]);
});

test("CX-T022 パスを tree と blob に引き、tree は再帰で読む。無いパスは null", async () => {
  const m = new MockGitHub(fixture());
  const c = client(m.fetch);
  const head = m.head("main") as string;
  const objs = await gh.pathObjects(c, "acme", "widgets", head, [".ccnavi/common", ".claude/settings.json", "wip/proposals"]);
  assert.equal(objs[".ccnavi/common"]?.type, "tree");
  assert.equal(objs[".claude/settings.json"]?.type, "blob");
  assert.equal(objs["wip/proposals"], null);
  const entries = await gh.tree(c, "acme", "widgets", objs[".ccnavi/common"]?.oid as string);
  assert.deepEqual(entries.map((e) => e.path), ["phases.yml", "rules.yml"]);
  const texts = await gh.blobs(c, "acme", "widgets", [entries[0].sha]);
  assert.equal(blobSha(texts[entries[0].sha].text as string), entries[0].sha);
});

test("CX-T023 truncated の tree と、本文が大きさと合わない blob は「無い」と読ませず止める", async () => {
  const fake = (json: unknown): gh.Fetch => async () => ({ status: 200, ok: true, json: async () => json });
  await assert.rejects(gh.tree(client(fake({ truncated: true, tree: [] })), "acme", "widgets", "a".repeat(40)), /truncated/);
  const oid = "b".repeat(40);
  await assert.rejects(
    gh.blobs(client(fake({ data: { repository: { b0: { text: "abc", isBinary: false, byteSize: 10 } } } })), "acme", "widgets", [oid]),
    /取り切れていない/,
  );
  const bin = await gh.blobs(client(fake({ data: { repository: { b0: { text: null, isBinary: true, byteSize: 10 } } } })), "acme", "widgets", [oid]);
  assert.deepEqual(bin[oid], { text: null, binary: true });
});

test("CX-T024 GraphQL の文にユーザの値を継ぎ足さない（変数で渡す）", async () => {
  const bodies: string[] = [];
  const m = new MockGitHub(fixture());
  const spy: gh.Fetch = async (url, init) => {
    if (init.body) bodies.push(init.body);
    return m.fetch(url, init);
  };
  await gh.pathObjects(client(spy), "acme", "widgets", m.head("main") as string, [".ccnavi/common"]);
  const { query, variables } = JSON.parse(bodies[0]);
  assert.ok(!query.includes(".ccnavi/common"));
  assert.ok(!query.includes("acme"));
  assert.equal(variables.e0, `${m.head("main")}:.ccnavi/common`);
  await assert.rejects(gh.blobs(client(spy), "acme", "widgets", Array(51).fill("a".repeat(40))), /50/);
});
