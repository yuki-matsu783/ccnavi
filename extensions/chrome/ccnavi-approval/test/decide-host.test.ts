/**
 * decide のホスト書き込み層。模擬の GitHub・GitLab（test/helpers/mock-write.ts）に service worker の `dispatch` 経由で書く。
 *
 * - 順序（issue → 返信 → 解決 → コメント）、`{issue_url}`・`{issue_links}` の置き換え、続きのコメント
 * - 途中で失敗したらそこで止まり、済んだ分と理由を返す。同じキーは二度書かない
 * - 事前検査: 権限不足・Chrome で扱えない種類のスレッドなら何も書かない
 * - 操作 ID の印で issue を再利用する。他人が書いた印は拾わない
 * - 権限ごとの 403 の文面、protocol の検査（拒否される引数）、issue に assignee などを入れない
 * - GitLab の目印のスレッドは、書き手が全部同じときだけ機構自身のものと見る
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { callOps, describeHostResult, markerOf, oneLine, partMarker, planProblems, precheckHostPlan, runHostPlan, type HostPlan, type HostStepEvent } from "../src/core/decide-host.js";
import * as gh from "../src/core/github.js";
import * as gl from "../src/core/gitlab.js";
import { dispatch } from "../src/core/protocol.js";
import { fixture } from "./fixtures/repo.js";
import { BOARD, deps, GITLAB_REPO, hostCall, HOSTS, newStats, OPTIONS, REPOS } from "./helpers/host.js";
import { TOKEN } from "./helpers/mock-github.js";
import { thread, WriteGitHub, WriteGitLab } from "./helpers/mock-write.js";

const REPO = { owner: "acme", repo: "widgets" };
const TOKENS = new Map([
  ["github.com", TOKEN],
  ["gitlab.com", TOKEN],
]);
const noWait = async () => undefined;
const T1 = "PRRT_kwDOAAA1";
const T2 = "PRRT_kwDOAAA2";
const T3 = "PRRT_kwDOAAA3";
const D1 = "a1".repeat(20);
const D2 = "b2".repeat(20);
const MARK1 = "<!-- ccnavi:issue op=0123456789abcdef0123456789abcdef -->";
const MARK2 = "<!-- ccnavi:issue op=fedcba9876543210fedcba9876543210 -->";

function hub(): { mock: WriteGitHub; ops: ReturnType<typeof callOps> } {
  const mock = new WriteGitHub(fixture());
  mock.w.threads = [thread(T1), thread(T2), thread(T3)];
  const d = { ...deps(mock, TOKENS, new Map(), () => new Date(), [...REPOS, GITLAB_REPO]), sleep: noWait };
  return { mock, ops: callOps(hostCall(d, newStats(), "github.com")) };
}

function lab(): { mock: WriteGitLab; ops: ReturnType<typeof callOps> } {
  const mock = new WriteGitLab(fixture());
  mock.w.threads = [thread(D1), thread(D2)];
  const d = { ...deps(mock, TOKENS, new Map(), () => new Date(), [...REPOS, GITLAB_REPO]), sleep: noWait };
  return { mock, ops: callOps(hostCall(d, newStats(), "gitlab.com")) };
}

const reply = (key: string, id: string, issue = false) => ({
  key,
  thread_id: id,
  body: `<!-- ccnavi:accept -->\nccnavi: 受け入れて進める。${issue ? `別の issue に回した: ${"{issue_url}"}。` : ""}\n`,
});

/** issue 2 件（キー k1・k2）、k3 は対応しない。返信 3 件、解決 3 件、コメント */
function plan(ids: [string, string, string] = [T1, T2, T3]): HostPlan {
  return {
    issues: [
      { key: "k1", op: "0123456789abcdef0123456789abcdef", title: "指摘 1 の対応", body: `${MARK1}\n\n本文 1`, comments: ["続き 1"] },
      { key: "k2", op: "fedcba9876543210fedcba9876543210", title: "指摘 2 の対応", body: `${MARK2}\n\n本文 2` },
    ],
    replies: [reply("k1", ids[0], true), reply("k2", ids[1], true), reply("k3", ids[2])],
    resolves: [
      { key: "k1", thread_id: ids[0] },
      { key: "k2", thread_id: ids[1] },
      { key: "k3", thread_id: ids[2] },
    ],
    comment: "<!-- ccnavi:comment -->\n決めた内容\nissue に回した分:\n{issue_links}\n",
  };
}

test("CX-T320 順序は issue → 返信 → 解決 → コメント。返信の {issue_url} は同じキーの issue の URL に、コメントの {issue_links} は一覧に置き換わる（GitHub）", async () => {
  const { mock, ops } = hub();
  const events: HostStepEvent[] = [];
  const out = await runHostPlan(ops, REPO, 7, plan(), (e) => void events.push(e));
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.deepEqual(mock.w.log, ["issue 指摘 1 の対応", "issueComment 500", "issue 指摘 2 の対応", `reply ${T1}`, `reply ${T2}`, `reply ${T3}`, `resolve ${T1}`, `resolve ${T2}`, `resolve ${T3}`, "comment"]);
  assert.deepEqual(out.issues.map((i) => [i.key, i.number, i.reused]), [["k1", 500, false], ["k2", 501, false]]);
  assert.ok(mock.w.threads[0].replies[0].includes("https://github.com/acme/widgets/issues/500。"));
  assert.ok(mock.w.threads[1].replies[0].includes("https://github.com/acme/widgets/issues/501。"));
  assert.ok(!mock.w.threads[2].replies[0].includes("issues/"));
  assert.ok(mock.w.threads.every((t) => t.resolved));
  assert.equal(mock.w.comments.length, 1);
  assert.ok(mock.w.comments[0].includes("- https://github.com/acme/widgets/issues/500\n- https://github.com/acme/widgets/issues/501"));
  assert.ok(!mock.w.comments[0].includes("{issue_links}"));
  assert.deepEqual(out.replied, ["k1", "k2", "k3"]);
  assert.deepEqual(out.resolved, ["k1", "k2", "k3"]);
  assert.equal(out.commented, true);
  assert.deepEqual(events.filter((e) => e.state === "done").map((e) => e.step), ["issue", "issue", "reply", "reply", "reply", "resolve", "resolve", "resolve", "comment"]);
  assert.match(describeHostResult(out), /済んだこと: issue .*返信 3 件.*解決 3 件.*MR へのコメント/);
});

test("CX-T321 GitLab も同じ順序で書く（discussion への返信は notes、解決は PUT、issue は projects/:id/issues、コメントは MR のメモ）", async () => {
  const { mock, ops } = lab();
  const p: HostPlan = {
    issues: [{ key: "k1", title: "タイトル", body: `${MARK1}\n\n本文`, comments: ["続き"] }],
    replies: [reply("k1", D1, true), reply("k2", D2)],
    resolves: [
      { key: "k1", thread_id: D1 },
      { key: "k2", thread_id: D2 },
    ],
    comment: "決めた\n{issue_links}",
  };
  const out = await runHostPlan(ops, REPO, 7, p);
  assert.equal(out.ok, true, JSON.stringify(out));
  assert.deepEqual(mock.w.log, ["issue タイトル", "issueComment 500", `reply ${D1}`, `reply ${D2}`, `resolve ${D1}`, `resolve ${D2}`, "comment"]);
  assert.ok(mock.w.threads[0].replies[0].includes("https://gitlab.com/acme/widgets/-/issues/500。"));
  assert.ok(mock.w.threads.every((t) => t.resolved));
  assert.ok(mock.w.comments[0].endsWith("- https://gitlab.com/acme/widgets/-/issues/500"));
  assert.deepEqual(mock.w.issues[0].comments, [`${partMarker("0123456789abcdef0123456789abcdef", 1)}\n続き`]);
});

test("CX-T322 issue にはタイトルと本文だけを送る（assignee・labels・milestone は入れない）。タイトルは 1 行にする", async () => {
  const targets = [
    { ...hub(), field: "body" },
    { ...lab(), field: "description" },
  ];
  for (const { mock, ops, field } of targets) {
    const p: HostPlan = { issues: [{ key: "k", title: "タイトル\n二行目\u2028 `x`", body: `${MARK1}\n本文` }], replies: [], resolves: [], comment: null };
    assert.equal((await runHostPlan(ops, REPO, 7, p)).ok, true);
    const sent = (mock.w.writes[0].raw ?? {}) as Record<string, unknown>;
    assert.deepEqual(Object.keys(sent).sort(), field === "body" ? ["body", "title"] : ["description", "title"]);
    assert.equal(sent.title, "タイトル 二行目 `x`");
  }
  assert.equal(oneLine("a".repeat(400)).length, 255);
});

test("CX-T323 途中で失敗したらそこで止まり、済んだ分（作った issue・返信・解決の済んだキー）と理由を返す。例外にしない。止まった先は書かない", async () => {
  // 2 件目の返信で失敗: issue は 2 件作成済み、返信は 1 件、解決は 0、コメントなし
  const a = hub();
  a.mock.w.failures.push({ op: "reply", status: 502, nth: 2, message: "bad gateway" });
  const r1 = await runHostPlan(a.ops, REPO, 7, plan());
  assert.equal(r1.ok, false);
  assert.equal(r1.failed?.step, "reply");
  assert.equal(r1.failed?.key, "k2");
  assert.match(r1.failed?.reason ?? "", /bad gateway|502/);
  assert.deepEqual(r1.issues.map((i) => i.number), [500, 501]);
  assert.deepEqual([r1.replied, r1.resolved, r1.commented], [["k1"], [], false]);
  assert.deepEqual(a.mock.w.log.filter((x) => x.startsWith("resolve") || x === "comment"), []);
  assert.match(describeHostResult(r1), /スレッドへの返信で止まりました（k2）.*済んだこと: issue .*返信 1 件.*決め直せば/);

  // 解決で失敗: 返信は済み、コメントは書かない
  const b = hub();
  b.mock.w.failures.push({ op: "resolve", status: 502, nth: 2 });
  const r2 = await runHostPlan(b.ops, REPO, 7, plan());
  assert.deepEqual([r2.failed?.step, r2.failed?.key, r2.replied, r2.resolved, r2.commented], ["resolve", "k2", ["k1", "k2", "k3"], ["k1"], false]);

  // 1 件目の issue で失敗: 何も済んでいない
  const c = hub();
  c.mock.w.failures.push({ op: "issue", status: 404 });
  const r3 = await runHostPlan(c.ops, REPO, 7, plan());
  assert.deepEqual([r3.failed?.step, r3.failed?.key, r3.issues.length, c.mock.w.log.length], ["issue", "k1", 0, 0]);
  assert.match(r3.failed?.reason ?? "", /Issues: Read and write/);

  // issue の続きのコメントで失敗: issue は済みとして返す
  const d = hub();
  d.mock.w.failures.push({ op: "issueComment", status: 502 });
  const r4 = await runHostPlan(d.ops, REPO, 7, plan());
  assert.deepEqual([r4.failed?.step, r4.failed?.key, r4.issues.map((i) => i.key)], ["issue-comment", "k1", ["k1"]]);

  // コメントで失敗: 返信も解決も済んでいる
  const e = hub();
  e.mock.w.failures.push({ op: "comment", status: 502 });
  const r5 = await runHostPlan(e.ops, REPO, 7, plan());
  assert.deepEqual([r5.failed?.step, r5.resolved.length, r5.commented], ["comment", 3, false]);
});

test("CX-T324 同じキー・同じスレッドを二度書かない（重なった計画は何も書かずに止める）。形の悪い計画も同じ", async () => {
  const { mock, ops } = hub();
  const dup = plan();
  const bad: HostPlan = { ...dup, replies: [...dup.replies, reply("k1", T2)] };
  const out = await runHostPlan(ops, REPO, 7, bad);
  assert.deepEqual([out.ok, out.failed?.step, mock.w.log.length], [false, "plan", 0]);
  assert.match(out.failed?.reason ?? "", /返信が重なっています/);
  const cases: [Partial<Record<keyof HostPlan, unknown>>, RegExp][] = [
    [{ issues: [{ key: "k", title: "t", body: "印が無い" }] }, /操作 ID の印/],
    [{ issues: [{ key: "k", op: "x", title: "t", body: `${MARK1}\nb` }] }, /操作 ID が本文の印と違います/],
    [{ issues: [{ key: "k", title: "t", body: `${MARK1}\nb` }, { key: "k", title: "u", body: `${MARK2}\nb` }] }, /キーが重なっています/],
    [{ issues: [{ key: "k", title: "t", body: `${MARK1}\nb` }, { key: "j", title: "u", body: `${MARK1}\nb` }] }, /操作 ID が重なっています/],
    [{ replies: [{ key: "z", thread_id: T1, body: "{issue_url}" }] }, /同じキーの issue が計画にありません/],
    [{ replies: [{ key: "z", thread_id: "a b", body: "x" }] }, /スレッドの id の形/],
    [{ resolves: [{ key: "z", thread_id: T1 }, { key: "y", thread_id: T1 }] }, /解決が重なっています/],
    [{ comment: "" }, /コメントが空/],
    [{ replies: [{ key: "z", thread_id: T1, body: "x".repeat(60001) }] }, /返信の本文が空か長すぎます/],
  ];
  for (const [patch, want] of cases) {
    const p = { issues: [], replies: [], resolves: [], comment: null, ...patch };
    assert.match(planProblems(p).join(" / "), want, JSON.stringify(patch).slice(0, 80));
  }
  assert.deepEqual(planProblems({ issues: [], replies: [], resolves: [], comment: null }), []);
});

test("CX-T325 操作 ID の印の issue が既にあれば作らずに使い、続きのコメントは足りない part だけ付ける。他人が書いた印・PR・1 行目以外の印は拾わない", async () => {
  const { mock, ops } = hub();
  const mine = mock.seedIssue(`${MARK1}\n\n前の回に作った`);
  mock.seedIssue(`${MARK2}\n\n他人が印だけ書いた`, "mallory");
  mock.seedPr(`${MARK2}\n\nPR の本文`);
  const out = await runHostPlan(ops, REPO, 7, plan());
  assert.equal(out.ok, true, JSON.stringify(out));
  const k2 = out.issues[1];
  assert.deepEqual(out.issues.map((i) => [i.key, i.reused]), [["k1", true], ["k2", false]]);
  assert.equal(out.issues[0].number, mine.number);
  assert.ok(k2.number > mine.number + 2, "他人の issue と PR は使わず、新しく作る");
  assert.deepEqual(mock.w.log.filter((x) => x.startsWith("issue ")), ["issue 指摘 2 の対応"]);
  // 前の回に続きのコメントが付かなかったので、使い回した issue にも part=1 が付く
  assert.deepEqual(mine.comments, [`${partMarker("0123456789abcdef0123456789abcdef", 1)}\n続き 1`]);
  assert.ok(mock.w.threads[0].replies[0].includes(`issues/${mine.number}。`));
  assert.match(describeHostResult(out), /うち既にあったものを使用/);
  // 2 回目: part=1 が付いているので、何も足さない
  const again = await runHostPlan(ops, REPO, 7, { issues: plan().issues.slice(0, 1), replies: [], resolves: [], comment: null });
  assert.equal(again.ok, true);
  assert.equal(mine.comments.length, 1);
  // 印が 1 行目でない issue は拾わない
  const b = hub();
  b.mock.seedIssue(`前置き\n${MARK1}\n本文`);
  const r = await runHostPlan(b.ops, REPO, 7, { issues: plan().issues.slice(0, 1), replies: [], resolves: [], comment: null });
  assert.equal(r.issues[0].reused, false);
  assert.equal(markerOf(`前置き\n${MARK1}`), "");
  assert.equal(markerOf(`${MARK1}\r\n本文`), "0123456789abcdef0123456789abcdef");
  // GitLab も PAT の持ち主が作ったものだけを再利用する
  const l = lab();
  const own = l.mock.seedIssue(`${MARK1}\n前の回`, 7);
  l.mock.seedIssue(`${MARK2}\n偽物`, 99);
  const lo = await runHostPlan(l.ops, REPO, 7, { issues: plan().issues, replies: [], resolves: [], comment: null });
  assert.deepEqual(lo.issues.map((i) => [i.number === own.number, i.reused]), [[true, true], [false, false]]);
});

test("CX-T335 issue の検索: 404 だけが『無い』。5xx・429・読めない応答は作り直さずに step=issue で止める。上限まで読んで見つからなければ作るが searchTruncated を返す。使い回した issue のコメントを読めなければ止める", async () => {
  for (const status of [500, 502, 429]) {
    const { mock, ops } = hub();
    mock.w.failures.push({ op: "find", status });
    const out = await runHostPlan(ops, REPO, 7, plan());
    assert.equal(out.ok, false, String(status));
    assert.deepEqual([out.failed?.step, out.failed?.key, mock.w.log.length], ["issue", "k1", 0], String(status));
  }
  const gone = hub();
  gone.mock.w.failures.push({ op: "find", status: 404 });
  const ok404 = await runHostPlan(gone.ops, REPO, 7, { issues: plan().issues.slice(0, 1), replies: [], resolves: [], comment: null });
  assert.deepEqual([ok404.ok, ok404.issues[0].reused, ok404.searchTruncated], [true, false, false]);
  // 300 件を超えて古い側にある印は見えない: 作るが、探し切れていないと言う
  const big = hub();
  big.mock.seedIssue(`${MARK1}\n古い`);
  for (let i = 0; i < 300; i += 1) big.mock.seedIssue(`別の issue ${i}`);
  const out = await runHostPlan(big.ops, REPO, 7, { issues: plan().issues.slice(0, 1), replies: [], resolves: [], comment: null });
  assert.deepEqual([out.ok, out.issues[0].reused, out.searchTruncated], [true, false, true]);
  assert.match(describeHostResult(out), /探し切れていません/);
  // 閉じた issue も印で見つかる（state=all）
  const closed = hub();
  closed.mock.seedIssue(`${MARK1}\n閉じた`).closed = true;
  assert.equal((await runHostPlan(closed.ops, REPO, 7, { issues: plan().issues.slice(0, 1), replies: [], resolves: [], comment: null })).issues[0].reused, true);
  // 使い回した issue のコメントを読めなければ止める（読めないまま足りない part を決めない）
  const c = hub();
  c.mock.seedIssue(`${MARK1}\n前の回`);
  c.mock.w.failures.push({ op: "listComments", status: 502 });
  const r = await runHostPlan(c.ops, REPO, 7, plan());
  assert.deepEqual([r.ok, r.failed?.step, r.failed?.key, r.replied.length], [false, "issue-comment", "k1", 0]);
  // GitLab
  const l = lab();
  l.mock.w.failures.push({ op: "find", status: 500 });
  const lr = await runHostPlan(l.ops, REPO, 7, plan());
  assert.equal(lr.failed?.step, "issue");
});

test("CX-T336 再実行: 1 回目が返信の途中で失敗しても、2 回目は issue を作り直さず、返信済みのスレッドに返信を重ねず、続きを足りない分だけ付ける（GitHub・GitLab）", async () => {
  const a = hub();
  a.mock.w.failures.push({ op: "reply", status: 502, nth: 2 });
  const first = await runHostPlan(a.ops, REPO, 7, plan());
  assert.equal(first.failed?.step, "reply");
  const second = await runHostPlan(a.ops, REPO, 7, plan());
  assert.equal(second.ok, true, JSON.stringify(second));
  assert.deepEqual(second.issues.map((i) => i.reused), [true, true]);
  assert.equal(a.mock.w.issues.length, 2);
  assert.deepEqual(a.mock.w.threads.map((t) => t.replies.length), [1, 1, 1]);
  assert.ok(a.mock.w.threads.every((t) => t.resolved));
  assert.ok(a.mock.w.threads[0].replies[0].includes("<!-- ccnavi:reply op="));
  assert.equal(a.mock.w.issues[0].comments.length, 1);
  assert.equal(a.mock.w.comments.length, 1);

  const gp: HostPlan = {
    issues: [{ key: "k1", title: "タイトル", body: `${MARK1}\n\n本文`, comments: ["続き"] }],
    replies: [reply("k1", D1, true), reply("k2", D2)],
    resolves: [{ key: "k1", thread_id: D1 }, { key: "k2", thread_id: D2 }],
    comment: null,
  };
  const l = lab();
  l.mock.w.failures.push({ op: "resolve", status: 502, nth: 1 });
  assert.equal((await runHostPlan(l.ops, REPO, 7, gp)).failed?.step, "resolve");
  const again = await runHostPlan(l.ops, REPO, 7, gp);
  assert.equal(again.ok, true, JSON.stringify(again));
  assert.deepEqual(l.mock.w.threads.map((t) => t.replies.length), [1, 1]);
  assert.equal(l.mock.w.issues.length, 1);
  assert.equal(l.mock.w.issues[0].comments.length, 1);
});

test("CX-T337 同じ計画の二重実行（連打）は、実行中なら何も書かずに断る。終われば再び実行できる", async () => {
  const { mock, ops } = hub();
  const p = plan();
  const first = runHostPlan(ops, REPO, 7, p);
  const second = await runHostPlan(ops, REPO, 7, p);
  assert.deepEqual([second.ok, second.failed?.step], [false, "plan"]);
  assert.match(second.failed?.reason ?? "", /実行中/);
  assert.equal((await first).ok, true);
  assert.equal(mock.w.issues.length, 2, "二重に作らない");
  const third = await runHostPlan(ops, REPO, 7, p);
  assert.equal(third.ok, true);
  assert.equal(mock.w.issues.length, 2);
});

test("CX-T338 外から来た本文の無害化: フェンスの外の行頭 / （GitLab）と @ メンションにゼロ幅の空白を入れる。フェンスの中は変えない", async () => {
  const body = "<!-- ccnavi:accept -->\n/close\n  /assign @bob\n```text\n/close\n@alice\n```\n@carol と a@b.com と `@x`\n~~~~\n/merge\n~~~\n/unlock\n~~~~\n/after";
  const ZW = "\u200b";
  const g = lab();
  await runHostPlan(g.ops, REPO, 7, { issues: [], replies: [{ key: "k", thread_id: D1, body }], resolves: [], comment: null });
  const sent = g.mock.w.threads[0].replies[0];
  assert.ok(sent.includes(`${ZW}/close\n`));
  assert.ok(sent.includes(`${ZW}  /assign @${ZW}bob`));
  assert.ok(sent.includes("```text\n/close\n@alice\n```"), "フェンスの中は変えない");
  assert.ok(sent.includes(`@${ZW}carol と a@b.com`), "メールの @ は変えない");
  assert.ok(sent.includes("~~~~\n/merge\n~~~\n/unlock\n~~~~"), "短い閉じ線ではフェンスは閉じない");
  assert.ok(sent.includes(`${ZW}/after`));
  assert.ok(sent.startsWith("<!-- ccnavi:accept -->\n"));
  // GitHub は行頭 / を変えず、メンションだけ直す
  const h = hub();
  await runHostPlan(h.ops, REPO, 7, { issues: [{ key: "k", title: "タイトル @dave", body: `${MARK1}\n/close @eve\n\`\`\`\n@frank\n\`\`\`` }], replies: [], resolves: [], comment: "/x @gina" });
  assert.equal(h.mock.w.writes[0].title, `タイトル @${ZW}dave`);
  assert.ok(h.mock.w.issues[0].body.includes(`\n/close @${ZW}eve\n\`\`\`\n@frank`));
  assert.equal(h.mock.w.comments[0], `/x @${ZW}gina`);
});

test("CX-T339 GitHub のスレッドは、登録したリポジトリのこの MR のものだけに書く。別のリポジトリ・別の PR・存在しない id は 400 で断る", async () => {
  const { mock } = hub();
  mock.w.threads = [thread(T1), thread(T2, { repo: "other/widgets" }), thread(T3, { number: 99 })];
  const d = deps(mock, TOKENS, new Map(), () => new Date(), [...REPOS, GITLAB_REPO]);
  const call = (op: string, args: unknown[]) => dispatch({ kind: "host", host: "github.com", op, args }, BOARD, d);
  for (const id of [T2, T3, "PRRT_nothing"]) {
    for (const [op, args] of [["replyThread", ["acme", "widgets", 7, id, "本文"]], ["resolveThread", ["acme", "widgets", 7, id]]] as const) {
      const res = await call(op, [...args]);
      assert.ok(!res.ok && res.status === 400 && /のものではない/.test(res.error), `${op} ${id}`);
    }
  }
  assert.deepEqual(mock.w.log, []);
  assert.equal((await call("replyThread", ["acme", "widgets", 7, T1, "本文"])).ok, true);
  assert.equal((await call("resolveThread", ["acme", "widgets", 7, T1])).ok, true);
  // 解決済みのスレッドを重ねて解決しても通る（実 API と同じ）
  assert.equal((await call("resolveThread", ["acme", "widgets", 7, T1])).ok, true);
});

test("CX-T340 権限不足の文面は decide のホスト書き込みだけ。commit・createBranch・読み取りの 403 は元の文面のまま", async () => {
  const forbidden = async () => ({ status: 403, ok: false, json: async () => ({ message: "x" }), headers: { get: () => null } });
  const ghc = { host: HOSTS[0], token: TOKEN, fetch: forbidden, counter: { rest: 0, graphql: 0 }, sleep: noWait };
  await assert.rejects(gh.createBranch(ghc, "acme", "widgets", "feature-1-x", "a".repeat(40)), (e: Error) => e.message.includes("PAT の権限（リポジトリ・Contents など）を見直してください") && !e.message.includes("Read and write"));
  await assert.rejects(gh.createDraftMr(ghc, "acme", "widgets", "feature-1-x", "main", "t", "Closes #1"), /Contents など/);
  const glc = { host: HOSTS[1], token: TOKEN, fetch: forbidden, counter: { rest: 0, graphql: 0 }, sleep: noWait };
  await assert.rejects(gl.createBranch(glc, "acme", "widgets", "feature-1-x", "a".repeat(40)), (e: Error) => e.message.includes("PAT の権限（api スコープ・プロジェクトのメンバー）を見直してください"));
  await assert.rejects(gl.replyDiscussion(glc, "acme", "widgets", 7, D1, "b"), /read_api では書けません/);
});

test("CX-T326 ホストが返した URL が通信先の外や不正な形なら、返信に入れず止まる", async () => {
  const { mock, ops } = hub();
  // 模擬の応答の URL を、別のホストのものに差し替える
  (mock as unknown as { web: string }).web = "https://evil.example.com";
  const out = await runHostPlan(ops, REPO, 7, plan());
  assert.equal(out.ok, false);
  assert.equal(out.failed?.step, "issue");
  assert.match(out.failed?.reason ?? "", /通信先の画面の外/);
  assert.equal(mock.w.threads.every((t) => t.replies.length === 0), true);
  // 直接も: 空白・引用符・括弧を含む URL は本文に入れない
  const fake = { ...callOps(async () => null), createIssue: async () => ({ number: 1, url: "https://github.com/a b)" }), findIssue: async () => ({ issue: null, truncated: false }) };
  const r = await runHostPlan(fake, REPO, 7, { issues: [{ key: "k", title: "t", body: `${MARK1}\nb` }], replies: [], resolves: [], comment: null });
  assert.match(r.failed?.reason ?? "", /URL を本文に入れられません/);
});

test("CX-T327 事前検査: 全部の権限があれば通り、fine-grained の PAT の権限は『確かめられなかった』として残す", async () => {
  const { mock, ops } = hub();
  const ok = await precheckHostPlan(ops, REPO, 7, plan());
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.ok && ok.unknown, ["Issues: Read and write", "Pull requests: Read and write"]);
  assert.deepEqual(mock.w.log, [], "確かめるだけで何も書かない");
  mock.scopes = "repo, read:org";
  const classic = await precheckHostPlan(ops, REPO, 7, plan());
  assert.deepEqual(classic.ok && classic.unknown, []);
});

test("CX-T328 事前検査（GitHub）: 返信・解決の権限が無いスレッド、classic の repo スコープ無し、Issues 無効は、何も書かずに必要な権限名を言う", async () => {
  const a = hub();
  a.mock.w.threads[1].canResolve = false;
  const r1 = await precheckHostPlan(a.ops, REPO, 7, plan());
  assert.equal(r1.ok, false);
  assert.ok(!r1.ok && r1.kind === "permission" && r1.missing.includes("Pull requests: Read and write") && r1.keys.includes("k2"));
  assert.match(!r1.ok ? r1.reason : "", /何も書きませんでした。必要な権限: Pull requests: Read and write/);

  const b = hub();
  b.mock.scopes = "read:user";
  const r2 = await precheckHostPlan(b.ops, REPO, 7, plan());
  assert.ok(!r2.ok && r2.kind === "permission" && r2.missing.some((m) => /repo スコープ/.test(m)));

  const c = hub();
  c.mock.hasIssues = false;
  const r3 = await precheckHostPlan(c.ops, REPO, 7, plan());
  assert.ok(!r3.ok && r3.kind === "permission" && r3.missing.includes("リポジトリの Issues の有効化"));
  // issue を作らない計画なら、Issues の無効は関係ない
  const noIssue: HostPlan = { issues: [], replies: [reply("k3", T3)], resolves: [{ key: "k3", thread_id: T3 }], comment: null };
  assert.equal((await precheckHostPlan(c.ops, REPO, 7, noIssue)).ok, true);
  for (const m of [a.mock, b.mock, c.mock]) assert.deepEqual(m.w.log, []);
});

test("CX-T329 事前検査（GitHub）: 保留中のレビューのスレッドは『Chrome では扱えない。VS Code で』として、権限不足とは別に返す", async () => {
  const { mock, ops } = hub();
  mock.w.threads[0].pending = true;
  mock.w.threads[1].canReply = false;
  const r = await precheckHostPlan(ops, REPO, 7, plan());
  assert.ok(!r.ok && r.kind === "unsupported");
  assert.match(!r.ok ? r.reason : "", /Chrome では扱えない。VS Code で.*保留中のレビューのスレッド/);
  assert.deepEqual(!r.ok && r.keys, ["k1"]);
  // スレッドが MR に無い（消えた）なら読み直しを促す
  const gone = await precheckHostPlan(ops, REPO, 7, plan(["PRRT_missing", T2, T3]));
  assert.ok(!gone.ok && gone.kind === "unreadable");
  assert.deepEqual(mock.w.log, []);
});

test("CX-T330 事前検査（GitLab）: 差分以外の discussion・resolvable でない note は Chrome では扱えない。api スコープ無し・ロール不足・Issues 無効は権限として言う", async () => {
  const gp: HostPlan = {
    issues: [{ key: "k1", title: "t", body: `${MARK1}\nb` }],
    replies: [reply("k1", D1, true), reply("k2", D2)],
    resolves: [{ key: "k1", thread_id: D1 }, { key: "k2", thread_id: D2 }],
    comment: "c",
  };
  const ok = lab();
  assert.equal((await precheckHostPlan(ok.ops, REPO, 7, gp)).ok, true);

  const non = lab();
  non.mock.w.threads[0].nonDiff = true;
  const r1 = await precheckHostPlan(non.ops, REPO, 7, gp);
  assert.ok(!r1.ok && r1.kind === "unsupported");
  assert.match(!r1.ok ? r1.reason : "", /Chrome では扱えない。VS Code で.*差分以外の discussion/);

  const nr = lab();
  nr.mock.w.threads[1].notResolvable = true;
  const r2 = await precheckHostPlan(nr.ops, REPO, 7, gp);
  assert.ok(!r2.ok && r2.kind === "unsupported" && /解決できない種類のスレッド/.test(r2.reason));

  const scope = lab();
  scope.mock.scopes = ["read_api"];
  const r3 = await precheckHostPlan(scope.ops, REPO, 7, gp);
  assert.ok(!r3.ok && r3.kind === "permission" && r3.missing.some((m) => /api スコープ/.test(m)));

  const role = lab();
  role.mock.role = 20;
  const r4 = await precheckHostPlan(role.ops, REPO, 7, gp);
  assert.ok(!r4.ok && r4.kind === "permission");
  assert.ok(!r4.ok && r4.missing.some((m) => /Developer/.test(m)));
  // Reporter でも、解決しない計画（issue だけ）なら通る
  const only: HostPlan = { issues: gp.issues, replies: [], resolves: [], comment: null };
  assert.equal((await precheckHostPlan(role.ops, REPO, 7, only)).ok, true);

  const noIssues = lab();
  noIssues.mock.issuesEnabled = false;
  const r5 = await precheckHostPlan(noIssues.ops, REPO, 7, gp);
  assert.ok(!r5.ok && r5.missing.includes("プロジェクトの Issues の有効化"));

  // プロジェクトのトークンなど、スコープを読めないときは通して『確かめられなかった』に残す
  const unknown = lab();
  unknown.mock.scopes = null;
  const r6 = await precheckHostPlan(unknown.ops, REPO, 7, gp);
  assert.deepEqual(r6.ok && r6.unknown, ["api スコープ"]);
  for (const m of [non.mock, nr.mock, scope.mock, role.mock, noIssues.mock]) assert.deepEqual(m.w.log, []);
});

test("CX-T331 権限が足りないときの 403 は、書く操作ごとに要る権限の名前を言う（Contents 前提の文面にしない）", async () => {
  const forbidden = (msg: string) => async () => ({ status: 403, ok: false, json: async () => ({ message: msg }), headers: { get: () => null } });
  const ghc = (fetch: gh.Fetch) => ({ host: HOSTS[0], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait });
  await assert.rejects(gh.createIssue(ghc(forbidden("x")), "acme", "widgets", "t", "b"), /Issues: Read and write/);
  await assert.rejects(gh.issueComment(ghc(forbidden("x")), "acme", "widgets", 7, "b"), /Pull requests: Read and write/);
  await assert.rejects(gh.issueComment(ghc(forbidden("x")), "acme", "widgets", 7, "b", "issue"), /Issues: Read and write/);
  await assert.rejects(gh.repoInfo(ghc(forbidden("x")), "acme", "widgets"), /Contents など/);
  // GraphQL が errors で権限不足を返す形（200 で FORBIDDEN）
  const graphqlForbidden: gh.Fetch = async () => ({ status: 200, ok: true, json: async () => ({ data: null, errors: [{ type: "FORBIDDEN", message: "Resource not accessible by personal access token" }] }), headers: { get: () => null } });
  await assert.rejects(gh.replyThread(ghc(graphqlForbidden), T1, "b"), /Pull requests: Read and write/);
  await assert.rejects(gh.resolveThread(ghc(graphqlForbidden), T1), /Pull requests: Read and write/);
  const glc = (fetch: gh.Fetch) => ({ host: HOSTS[1], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait });
  await assert.rejects(gl.createIssue(glc(forbidden("x")), "acme", "widgets", "t", "b"), /api スコープ.*Developer/);
  await assert.rejects(gl.repoInfo(glc(forbidden("x")), "acme", "widgets"), /api スコープ・プロジェクトのメンバー/);
});

test("CX-T332 protocol: 新しい書き込みはボードからだけ、登録したリポジトリだけ。形の悪い引数は 400 で断り、ホストには出ない", async () => {
  const { mock } = hub();
  const d = deps(mock, TOKENS, new Map(), () => new Date(), [...REPOS, GITLAB_REPO]);
  const call = (host: string, op: string, args: unknown[], sender = BOARD) => dispatch({ kind: "host", host, op, args }, sender, d);
  const ok = [
    ["replyThread", ["acme", "widgets", 7, T1, "本文"]],
    ["resolveThread", ["acme", "widgets", 7, T1]],
    ["createIssue", ["acme", "widgets", "タイトル", `${MARK1}\n本文`]],
    ["findIssue", ["acme", "widgets", MARK1]],
    ["postComment", ["acme", "widgets", 7, "本文"]],
    ["commentIssue", ["acme", "widgets", 500, "続き"]],
  ] as const;
  // 設定画面からは受けない
  for (const [op, args] of ok) {
    const res = await call("github.com", op, [...args], OPTIONS);
    assert.ok(!res.ok && /ボードからのみ/.test(res.error), op);
  }
  // 登録していないリポジトリ
  for (const [op, args] of ok) {
    const res = await call("github.com", op, ["acme", "other", ...args.slice(2)]);
    assert.ok(!res.ok && res.status === 400 && /登録されていない/.test(res.error), op);
  }
  const before = mock.calls.length;
  const bad: [string, unknown[], RegExp][] = [
    ["replyThread", ["acme", "widgets", 7, "x/../y", "本文"], /スレッドの id/],
    ["replyThread", ["acme", "widgets", 7, "a b", "本文"], /スレッドの id/],
    ["replyThread", ["acme", "widgets", 7, "x\n", "本文"], /スレッドの id/],
    ["replyThread", ["acme", "widgets", 0, T1, "本文"], /MR の番号/],
    ["replyThread", ["acme", "widgets", "7", T1, "本文"], /MR の番号/],
    ["replyThread", ["acme", "widgets", 7, T1, ""], /返信の本文/],
    ["replyThread", ["acme", "widgets", 7, T1, "x".repeat(65001)], /返信の本文/],
    ["replyThread", ["acme", "widgets", 7, T1, "a\0b"], /返信の本文/],
    ["resolveThread", ["acme", "widgets", 7, `${"A".repeat(201)}`], /スレッドの id/],
    ["createIssue", ["acme", "widgets", "改行\nあり", "本文"], /1 行/],
    ["createIssue", ["acme", "widgets", "x".repeat(256), "本文"], /1 行/],
    ["createIssue", ["acme", "widgets", "タイトル", "x".repeat(65001)], /issue の本文/],
    ["createIssue", ["acme", "widgets", "タイトル", ""], /issue の本文/],
    ["findIssue", ["acme", "widgets", "<!-- ccnavi:issue op=x y -->"], /印の形/],
    ["findIssue", ["acme", "widgets", "任意の検索語"], /印の形/],
    ["postComment", ["acme", "widgets", -1, "本文"], /MR の番号/],
    ["postComment", ["acme", "widgets", 7, "x".repeat(65001)], /コメントの本文/],
    ["commentIssue", ["acme", "widgets", 1.5, "続き"], /issue の番号/],
  ];
  for (const [op, args, want] of bad) {
    const res = await call("github.com", op, args);
    assert.ok(!res.ok && res.status === 400 && want.test(res.error), `${op} ${JSON.stringify(args).slice(0, 60)}: ${JSON.stringify(res).slice(0, 120)}`);
  }
  // GitLab の discussion の id は 16 進だけ（GitHub の node id は通らない）
  const lres = await call("gitlab.com", "resolveThread", ["acme", "widgets", 7, T1]);
  assert.ok(!lres.ok && lres.status === 400 && /discussion の id/.test(lres.error));
  assert.equal(mock.calls.length, before, "断った頼みはホストに出ない");
  // 通る形は通る
  mock.w.threads = [thread(T1)];
  assert.equal((await call("github.com", "replyThread", [...ok[0][1]])).ok, true);
  assert.equal((await call("github.com", "resolveThread", [...ok[1][1]])).ok, true);
  const made = await call("github.com", "createIssue", [...ok[2][1]]);
  assert.ok(made.ok && (made.value as { number: number }).number === 500);
  assert.ok(!JSON.stringify([made]).includes(TOKEN));
});

test("CX-T333 GitHub の PAT の権限・スレッド・Issues の確かめ（writeInfo）は、読むだけ。登録したリポジトリだけ受ける", async () => {
  const { mock } = hub();
  const d = deps(mock, TOKENS, new Map(), () => new Date(), [...REPOS, GITLAB_REPO]);
  const res = await dispatch({ kind: "host", host: "github.com", op: "writeInfo", args: ["acme", "widgets", 7] }, BOARD, d);
  assert.ok(res.ok);
  const info = (res as { value: { threads: { id: string; kind: string }[] } }).value;
  assert.deepEqual(info.threads.map((t) => t.id), [T1, T2, T3]);
  assert.deepEqual(mock.w.log, []);
  const other = await dispatch({ kind: "host", host: "github.com", op: "writeInfo", args: ["acme", "other", 7] }, BOARD, d);
  assert.ok(!other.ok);
  const badNumber = await dispatch({ kind: "host", host: "github.com", op: "writeInfo", args: ["acme", "widgets", "7"] }, BOARD, d);
  assert.ok(!badNumber.ok);
});

test("CX-T334 GitLab の目印のスレッドは、書き手が全部同じときだけ機構自身のもの（own）と見る。依頼の投稿者のスレッドに他人が目印の返信を足しても隠せない", () => {
  const note = (id: number, author: number, body: string, resolvable = true) => ({
    id,
    body,
    system: false,
    resolvable,
    resolved: false,
    author: { id: author, username: `u${author}` },
    created_at: "2026-09-29T05:00:00.000Z",
  });
  const discussions = (notes: ReturnType<typeof note>[]) => [{ id: "d".repeat(40), notes }];
  const run = async (notes: ReturnType<typeof note>[]) => {
    const fetch: gh.Fetch = async (url) => {
      const page = new URL(url).searchParams.get("page");
      return { status: 200, ok: true, json: async () => (page === "1" ? discussions(notes) : []), headers: { get: () => null } };
    };
    const t = await gl.discussions({ host: HOSTS[1], token: TOKEN, fetch, counter: { rest: 0, graphql: 0 }, sleep: noWait }, "acme", "widgets", 7, "https://gitlab.com/acme/widgets/-/merge_requests/7");
    return t[0];
  };
  return (async () => {
    const mark = "<!-- ccnavi:request x -->\n依頼";
    // 依頼の投稿者だけ: 機構自身
    assert.equal((await run([note(1, 201, mark)])).own, true);
    // 他人が目印で始まる返信を足した: 機構自身ではない
    const mixed = await run([note(1, 201, mark), note(2, 203, "<!-- ccnavi:request x -->\n偽の返信")]);
    assert.equal(mixed.own, false);
    // 他人が目印で始まる投稿だけで始めた: 書き手が違うので依頼の投稿者とは一致しない（author は代表の書き手）
    const alone = await run([note(5, 203, mark)]);
    assert.deepEqual([alone.own, alone.author], [true, "203"]);
    // 書き手が分からない目印の投稿は機構自身と見ない
    const unknown = await run([{ ...note(6, 0, mark), author: null } as never]);
    assert.equal(unknown.own, false);
  })();
});
