/**
 * service worker の約束。PAT を画面へ返さない、送り手を限る、操作を名前で限る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dispatch } from "../src/core/protocol.js";
import { fixture } from "./fixtures/repo.js";
import { BOARD, deps, EXT_ID, OPTIONS, REPOS } from "./helpers/host.js";
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

test("CX-T014 PAT が無ければ呼ばない（GitHub も GitLab も。段階 5 から GitLab も読む）", async () => {
  const m = mock();
  const d = deps(m, new Map());
  for (const host of ["github.com", "gitlab.com"]) {
    const res = await dispatch({ kind: "host", host, op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
    assert.equal(res.ok, false, host);
    assert.equal((res as { status?: number }).status, 401, host);
  }
  assert.equal(m.calls.length, 0);
});

test("CX-T015 PAT が通らなければ（401）差し替えを促す", async () => {
  const d = deps(mock(), new Map([["github.com", "wrong-token-000"]]));
  const res = await dispatch({ kind: "host", host: "github.com", op: "repoInfo", args: ["acme", "widgets"] }, BOARD, d);
  assert.equal(res.ok, false);
  assert.match((res as { error: string }).error, /401/);
});

test("CX-T112 書く頼み（commit）はボードからだけ、登録したリポジトリだけ。保護された名前・統合先（自分で引く）・置き場の外は 400 で断る", async () => {
  const m = mock();
  const d = deps(m, new Map([["github.com", TOKEN]]));
  const head = m.head("i0001") as string;
  const add = (path = "wip/proposals/todo/x.md") => [{ path, contents: Buffer.from("x\n").toString("base64") }];
  const commit = (branch: string, adds: unknown[] = add(), owner = "acme") => ({
    kind: "host",
    host: "github.com",
    op: "commit",
    args: [owner, "widgets", branch, head, "見出し", "", adds, []],
  });
  const refused = async (msg: unknown, sender = BOARD, dd = d) => {
    const res = await dispatch(msg, sender, dd);
    assert.equal(res.ok, false, JSON.stringify(res));
    return res as { error: string; status?: number };
  };
  for (const branch of ["main", "Master", "develop", "release", "release-1.0", "release/2"]) {
    const res = await refused(commit(branch));
    assert.match(res.error, /保護されたブランチか統合先/, branch);
    assert.equal(res.status, 400);
  }
  // 統合先の名前はボードの値を信頼せず、設定（無ければホストのデフォルトブランチ）から引く
  const trunk = deps(m, new Map([["github.com", TOKEN]]), new Map(), () => new Date(), [{ ...REPOS[0], integration: "i0003" }]);
  assert.match((await refused(commit("I0003"), BOARD, trunk)).error, /統合先/);
  assert.match((await refused(commit("i0001"), OPTIONS)).error, /ボードからだけ/);
  assert.match((await refused(commit("i0001", []))).error, /書くものが無い/);
  assert.match((await refused(commit("i0001", add("src/app.py")))).error, /置き場.*の外/);
  assert.match((await refused(commit("i0001", add("../x")))).error, /パス/);
  assert.equal((await refused(commit("i0001", [{ path: "wip/proposals/x.md", contents: "not base64!" }]))).status, 400);
  assert.match((await refused(commit("i0001", add(), "other"))).error, /登録していない/);
  assert.equal(m.commitCalls.length, 0);
  const ok = await dispatch(commit("i0001"), BOARD, d);
  assert.equal(ok.ok, true, JSON.stringify(ok));
  assert.equal(m.files("i0001")["wip/proposals/todo/x.md"], "x\n");
});

test("CX-T117 置き場のパスは service worker が統合先の .claude/settings.json から自分で引く", async () => {
  const f = fixture();
  f.main.files[".claude/settings.json"] = JSON.stringify({ env: { CCNAVI_TICKETS_PROPOSAL: "wip/tickets" } });
  const m = new MockGitHub(f);
  const d = deps(m, new Map([["github.com", TOKEN]]));
  const commit = (path: string) => ({
    kind: "host",
    host: "github.com",
    op: "commit",
    args: ["acme", "widgets", "i0001", m.head("i0001"), "見出し", "", [{ path, contents: "eAo=" }], []],
  });
  assert.match(((await dispatch(commit("wip/proposals/todo/x.md"), BOARD, d)) as { error: string }).error, /置き場.*の外/);
  assert.equal((await dispatch(commit("wip/tickets/todo/x.md"), BOARD, d)).ok, true);
  assert.equal((await dispatch(commit(".ccnavi/approved/doing/x.md"), BOARD, d)).ok, true);
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
      { kind: "host", host: "github.com", op: "commit", args: ["acme", "widgets", "i0001", head, "見出し", "", [{ path: "wip/proposals/y.md", contents: "eQo=" }], []] },
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
