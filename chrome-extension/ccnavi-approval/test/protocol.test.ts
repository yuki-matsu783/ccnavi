/**
 * service worker の約束（ADR-0093 の 5.5 の 4）: PAT を画面へ返さない、送り手を限る、操作を名前で限る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dispatch } from "../src/core/protocol.js";
import { fixture } from "./fixtures/repo.js";
import { BOARD, deps, EXT_ID, OPTIONS } from "./helpers/host.js";
import { MockGitHub, TOKEN } from "./helpers/mock-github.js";

const mock = () => new MockGitHub(fixture());

test("CX-T010 PAT を書く・消すのは設定画面からだけ。ボードからは受けない", async () => {
  const tokens = new Map<string, string>();
  const d = deps(mock(), tokens);
  const fromBoard = await dispatch({ kind: "token.set", host: "github.com", token: TOKEN }, BOARD, d);
  assert.equal(fromBoard.ok, false);
  assert.equal(tokens.size, 0);
  const fromOptions = await dispatch({ kind: "token.set", host: "github.com", token: TOKEN }, OPTIONS, d);
  assert.equal(fromOptions.ok, true);
  assert.equal(tokens.get("github.com"), TOKEN);
  assert.equal((await dispatch({ kind: "token.clear", host: "github.com" }, BOARD, d)).ok, false);
  assert.equal((await dispatch({ kind: "token.clear", host: "github.com" }, OPTIONS, d)).ok, true);
  assert.equal(tokens.size, 0);
});

test("CX-T011 登録済みかを聞いても PAT そのものは返らない", async () => {
  const d = deps(mock(), new Map([["github.com", TOKEN]]));
  const res = await dispatch({ kind: "token.status", host: "github.com" }, BOARD, d);
  assert.deepEqual(res, { ok: true, value: { set: true } });
  assert.ok(!JSON.stringify(res).includes(TOKEN));
});

test("CX-T012 他の拡張・ウェブページ・別の拡張のオリジンからの頼みは受けない", async () => {
  const d = deps(mock(), new Map([["github.com", TOKEN]]));
  const msg = { kind: "host", host: "github.com", op: "repoInfo", args: ["acme", "widgets"] };
  for (const sender of [
    { id: "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", url: `chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/x.html` },
    { id: EXT_ID, url: "https://evil.example.com/" },
    { id: EXT_ID },
    {},
  ]) {
    const res = await dispatch(msg, sender, d);
    assert.equal(res.ok, false, JSON.stringify(sender));
  }
  assert.equal((await dispatch(msg, BOARD, d)).ok, true);
});

test("CX-T013 操作は名前で限る。知らない操作・焼き込んでいないホスト・形の悪い引数は受けない", async () => {
  const d = deps(mock(), new Map([["github.com", TOKEN]]));
  const call = (m: Record<string, unknown>) => dispatch(m, BOARD, d);
  assert.match(((await call({ kind: "host", host: "github.com", op: "createCommitOnBranch", args: ["acme", "widgets"] })) as { error: string }).error, /知らない操作/);
  assert.match(((await call({ kind: "host", host: "evil.example.com", op: "repoInfo", args: ["acme", "widgets"] })) as { error: string }).error, /通信先に無い/);
  assert.equal((await call({ kind: "host", host: "github.com", op: "repoInfo", args: ["acme/../x", "widgets"] })).ok, false);
  assert.equal((await call({ kind: "host", host: "github.com", op: "tree", args: ["acme", "widgets", "not-a-sha"] })).ok, false);
  assert.equal((await call({ kind: "host", host: "github.com", op: "pathObjects", args: ["acme", "widgets", "a".repeat(40), ["../etc"]] })).ok, false);
  assert.equal((await call({ kind: "host", host: "github.com", op: "branchHead", args: ["acme", "widgets", "a..b"] })).ok, false);
});

test("CX-T014 PAT が無ければ呼ばない。GitLab は段階 5 まで読まない", async () => {
  const m = mock();
  const d = deps(m, new Map());
  const res = await dispatch({ kind: "host", host: "github.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
  assert.equal(res.ok, false);
  assert.equal(m.calls.length, 0);
  const gl = await dispatch({ kind: "host", host: "gitlab.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, deps(m, new Map([["gitlab.com", "glpat-xxxxxxxxxx"]])));
  assert.match((gl as { error: string }).error, /段階 5/);
});

test("CX-T015 PAT が通らなければ（401）差し替えを促す", async () => {
  const d = deps(mock(), new Map([["github.com", "wrong-token-000"]]));
  const res = await dispatch({ kind: "host", host: "github.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
  assert.equal(res.ok, false);
  assert.match((res as { error: string }).error, /401/);
});
