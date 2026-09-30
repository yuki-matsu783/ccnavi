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
  assert.equal(res.ok, true);
  assert.equal((res as { value: { set: boolean } }).value.set, true);
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

test("CX-T112 書く頼み（commit）はボードからだけ。保護されたブランチ・統合先の名前・空の書き込みは断る", async () => {
  const m = mock();
  const d = deps(m, new Map([["github.com", TOKEN]]));
  const head = m.head("i0001") as string;
  const add = [{ path: "x.md", contents: Buffer.from("x\n").toString("base64") }];
  const commit = (branch: string, integ = "main", adds: unknown[] = add) => ({
    kind: "host",
    host: "github.com",
    op: "commit",
    args: ["acme", "widgets", branch, head, "見出し", adds, [], integ],
  });
  for (const branch of ["main", "Master", "develop", "release", "release-1.0", "release/2"]) {
    const res = await dispatch(commit(branch), BOARD, d);
    assert.match((res as { error: string }).error, /保護されたブランチか統合先/, branch);
  }
  assert.match(((await dispatch(commit("trunk", "TRUNK"), BOARD, d)) as { error: string }).error, /統合先/);
  assert.match(((await dispatch(commit("i0001"), OPTIONS, d)) as { error: string }).error, /ボードからだけ/);
  assert.match(((await dispatch(commit("i0001", "main", []), BOARD, d)) as { error: string }).error, /書くものが無い/);
  assert.equal((await dispatch(commit("i0001", "main", [{ path: "../x", contents: "eA==" }]), BOARD, d)).ok, false);
  assert.equal((await dispatch(commit("i0001", "main", [{ path: "x.md", contents: "not base64!" }]), BOARD, d)).ok, false);
  assert.equal(m.commitCalls.length, 0);
  const ok = await dispatch(commit("i0001"), BOARD, d);
  assert.equal(ok.ok, true, JSON.stringify(ok));
  assert.equal(m.files("i0001")["x.md"], "x\n");
});

test("CX-T113 PAT は画面へ返さない（どの頼みの答えにも入らない）。書き込みの本文にも入れず、ヘッダだけで送る", async () => {
  const m = mock();
  const bodies: string[] = [];
  const d = { ...deps(m, new Map([["github.com", TOKEN]])) };
  const spy: typeof d.fetch = async (url, init) => {
    bodies.push(url + (init.body ?? ""));
    return m.fetch(url, init);
  };
  const withSpy = { ...d, fetch: spy };
  const head = m.head("i0001") as string;
  const answers = [
    await dispatch({ kind: "token.status", host: "github.com" }, BOARD, withSpy),
    await dispatch({ kind: "host", host: "github.com", op: "viewer", args: ["acme", "widgets"] }, BOARD, withSpy),
    await dispatch({ kind: "host", host: "github.com", op: "approvalCommit", args: ["acme", "widgets", head, ".ccnavi/approved/doing/i0001.md"] }, BOARD, withSpy),
    await dispatch({ kind: "host", host: "github.com", op: "pullApprovals", args: ["acme", "widgets", "i0001"] }, BOARD, withSpy),
    await dispatch(
      { kind: "host", host: "github.com", op: "commit", args: ["acme", "widgets", "i0001", head, "見出し", [{ path: "y.md", contents: "eQo=" }], [], "main"] },
      BOARD,
      withSpy,
    ),
    await dispatch({ kind: "token.get", host: "github.com" }, BOARD, withSpy),
  ];
  for (const a of answers) assert.ok(!JSON.stringify(a).includes(TOKEN), JSON.stringify(a));
  assert.equal(answers[5].ok, false);
  assert.ok(bodies.length > 0);
  for (const b of bodies) assert.ok(!b.includes(TOKEN), b);
});
