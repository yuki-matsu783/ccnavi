/**
 * Chrome からの残った指摘の行き先（decide）。模擬の GitHub・GitLab と Node の上の Pyodide（拡張と同じ zip）で、
 * 読む（preview）から、ホストへ書く（issue・返信・解決）、親のブランチへの 1 コミット、MR へのコメントまでを通す。
 *
 * - 一気通貫: keep・issue・fix の混在、全 fix（ホストに書かない）、0 件（レビュー済み）。書いた順は issue → 返信 → 解決 → コミット → コメント
 * - 何も書かない: 見せた指摘が変わった・権限が足りない・Chrome で扱えない種類のスレッド
 * - 途中で止まる: ホストの書き込みが途中で止まればコミットしない。読み直して決め直せば続きが進み、同じ issue・返信を重ねない
 * - コミットが競合して再試行しても、ホストに二重に書かない。コメントだけ失敗してもコミットは戻さず、警告にする
 */
import { before, test } from "node:test";
import assert from "node:assert/strict";

import { sayDecide, type DecideOutcome } from "../src/board/controller.js";
import type { PyCall } from "../src/core/py.js";
import type { DecideChoice, DecidePreview } from "../src/core/reviewed.js";
import type { RepoConfig } from "../src/core/settings.js";
import { decidePhase, decidePreview, type WriteDeps } from "../src/core/write.js";
import { fixture, NOW, requestedMark, reviewFamilyFiles } from "./fixtures/repo.js";
import { deps, GITLAB_REPO, hostCall, memoryCache, newStats, REPOS } from "./helpers/host.js";
import { TOKEN } from "./helpers/mock-github.js";
import { thread, WriteGitHub, WriteGitLab, type MockThread } from "./helpers/mock-write.js";
import { pyodidePy } from "./helpers/python.js";

let py: PyCall;
before(async () => {
  py = (await pyodidePy()).call;
});

const FAMILY = "i0004";
const MARK = `.ccnavi/approved/phases/${FAMILY}/1.reviewed`;
const REQUESTED = `.ccnavi/approved/phases/${FAMILY}/1.requested`;
const GH_REPO: RepoConfig = REPOS[0];
const TOKENS = new Map([
  ["github.com", TOKEN],
  ["gitlab.com", TOKEN],
]);
const noWait = async () => undefined;

type Kind = "github" | "gitlab";

interface World {
  readonly kind: Kind;
  readonly mock: WriteGitHub | WriteGitLab;
  readonly d: WriteDeps;
  readonly repo: RepoConfig;
  /** ホストへの書き込みの記録と、そのときまでに積まれたコミットの数 */
  readonly at: { entry: string; commits: number }[];
}

/** 積まれたコミットの数 */
function commits(w: World): number {
  return w.mock instanceof WriteGitLab ? w.mock.glCommits.filter((c) => c.result === "written").length : w.mock.commitCalls.filter((c) => c.result === "written").length;
}

/** 依頼を済ませた親子のチケットと、MR（7 番）の未解決のスレッドを持つ世界 */
function world(kind: Kind, threads: MockThread[]): World {
  const mock = kind === "github" ? new WriteGitHub(fixture()) : new WriteGitLab(fixture());
  mock.branch(FAMILY, "main");
  const head = mock.push(FAMILY, reviewFamilyFiles(FAMILY), "作業とレビュー待ちの子");
  mock.push(FAMILY, { [REQUESTED]: requestedMark(head, 7, kind, kind === "gitlab" ? "7" : "") }, "ccnavi: レビューを依頼した");
  mock.pulls[FAMILY] = [{ number: 7, reviews: [] }];
  mock.w.threads = threads;
  const repo = kind === "github" ? GH_REPO : GITLAB_REPO;
  const stats = newStats();
  const base = { ...deps(mock, TOKENS, new Map(), () => new Date(NOW), [GH_REPO, GITLAB_REPO]), sleep: noWait };
  const d: WriteDeps = {
    call: hostCall(base, stats, kind === "github" ? "github.com" : "gitlab.com"),
    py,
    cache: memoryCache(),
    now: () => new Date(NOW),
    stats,
    version: "9.9.9",
    sleep: noWait,
    kind,
  };
  const w: World = { kind, mock, d, repo, at: [] };
  const push = mock.w.log.push.bind(mock.w.log);
  mock.w.log.push = (...items: string[]) => {
    for (const entry of items) w.at.push({ entry, commits: commits(w) });
    return push(...items);
  };
  return w;
}


const KINDS: readonly Kind[] = ["github", "gitlab"];

/** スレッドの id。GitLab は 16 進（discussion の id） */
function ids(kind: Kind): string[] {
  return kind === "github" ? ["PRRT_A", "PRRT_B", "PRRT_C", "PRRT_D"] : ["a1".repeat(20), "b2".repeat(20), "c3".repeat(20), "d4".repeat(20)];
}

/** 未解決の指摘 n 件（A から）。GitLab は代表のノートの id がキーになるので変える */
function threadsOf(kind: Kind, n = 3): MockThread[] {
  const names = ids(kind);
  return ["A", "B", "C", "D"].slice(0, n).map((x, i) => thread(names[i], { body: `${x} の指摘`, noteId: 101 + i }));
}

async function preview(w: World): Promise<DecidePreview> {
  return await decidePreview(w.repo, FAMILY, 1, w.d);
}

/** 画面の並びの順に行き先を付ける（足りない分は fix） */
function pick(pv: DecidePreview, ...choices: DecideChoice[]): Record<string, DecideChoice> {
  return Object.fromEntries(pv.threads.map((t, i) => [t.key, choices[i] ?? "fix"]));
}

function written(out: DecideOutcome): Extract<DecideOutcome, { kind: "written" }> {
  assert.equal(out.kind, "written", JSON.stringify(out));
  return out as Extract<DecideOutcome, { kind: "written" }>;
}

/** ホストへ書いた記録の種類だけ（`issue <題>` → `issue`、`reply <id>` → `reply`） */
const ops = (w: World): string[] => w.at.map((x) => x.entry.split(" ")[0]);

for (const kind of KINDS) {
  const label = kind === "github" ? "GitHub" : "GitLab";

  test(`CX-T350 ${label}: keep・issue・fix が混ざっていても、issue → 返信 → 解決 → 1 コミット → コメントの順に書く。fix には返信も解決もしない`, async () => {
    const w = world(kind, threadsOf(kind));
    const pv = await preview(w);
    assert.equal(pv.threads.length, 3);
    assert.ok(pv.threads.every((t) => !t.unsupported));
    // 開いただけでは何も書かない
    assert.equal(w.at.length, 0);
    assert.equal(commits(w), 0);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d));
    const [a, b, c] = ids(kind);
    assert.deepEqual(ops(w), ["issue", "reply", "reply", "resolve", "resolve", "comment"]);
    assert.deepEqual(w.at.map((x) => x.commits), [0, 0, 0, 0, 0, 1], "コミットはホストの返信・解決の後、コメントの前");
    assert.deepEqual(w.at.slice(1, 3).map((x) => x.entry), [`reply ${a}`, `reply ${b}`]);
    assert.deepEqual(w.at.slice(3, 5).map((x) => x.entry), [`resolve ${a}`, `resolve ${b}`]);
    const state = new Map(w.mock.w.threads.map((t) => [t.id, t]));
    assert.equal(state.get(c)?.resolved, false);
    assert.deepEqual(state.get(c)?.replies, []);
    // issue に回した指摘の返信は、作った issue の URL を持つ
    assert.equal(w.mock.w.issues.length, 1);
    assert.equal(out.issues.length, 1);
    assert.ok(state.get(b)?.replies[0].includes(out.issues[0]));
    // 親のブランチへは 1 コミット（続きの子。fix があるのでレビュー済みにしない）
    assert.equal(commits(w), 1);
    assert.equal(out.reviewed, false);
    assert.equal(out.followup, "i0004-01-02");
    assert.ok(`.ccnavi/approved/doing/${out.followup}.md` in w.mock.files(FAMILY));
    assert.ok(!(MARK in w.mock.files(FAMILY)));
    assert.equal(w.mock.w.comments.length, 1);
    assert.ok(!w.mock.w.comments[0].includes("{issue_links}"));
    assert.ok(w.mock.w.comments[0].includes(out.issues[0]), "コメント本文に作った issue の URL が入る");
    assert.equal(out.warning, undefined);
  });

  test(`CX-T351 ${label}: 全部 fix なら issue・返信・解決はせず、コミットを積む`, async () => {
    const w = world(kind, threadsOf(kind, 2));
    const pv = await preview(w);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv), pv.digest, w.d));
    // 決めた内容のコメントだけが付く
    assert.deepEqual(ops(w), ["comment"]);
    assert.equal(commits(w), 1);
    assert.deepEqual([out.reviewed, out.followup, out.issues], [false, "i0004-01-02", []]);
    assert.ok(w.mock.w.threads.every((t) => !t.resolved && t.replies.length === 0));
  });

  test(`CX-T352 ${label}: 指摘が 0 件なら、ホストには何も書かず（コメントも投稿しない）「レビュー済みにする」と同じコミットを積む`, async () => {
    const w = world(kind, []);
    const pv = await preview(w);
    assert.deepEqual(pv.threads, []);
    const out = written(await decidePhase(w.repo, FAMILY, 1, {}, pv.digest, w.d));
    assert.equal(out.reviewed, true);
    assert.equal(out.followup, "");
    assert.deepEqual(ops(w), []);
    assert.equal(commits(w), 1);
    assert.ok(MARK in w.mock.files(FAMILY));
    assert.ok(`.ccnavi/approved/done/${FAMILY}-01-01.md` in w.mock.files(FAMILY));
  });

  test(`CX-T353 ${label}: 見せた指摘が変わっていたら（版が変わった）、何も書かずに mismatch`, async () => {
    const w = world(kind, threadsOf(kind));
    const pv = await preview(w);
    w.mock.w.threads[0].body = "A の指摘を書き換えた";
    const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
    assert.equal(out.kind, "mismatch", JSON.stringify(out));
    assert.equal(w.at.length, 0);
    assert.equal(commits(w), 0);
    assert.equal(w.mock.w.issues.length, 0);
    // 読み直せば、新しい版を見せられる
    const again = await preview(w);
    assert.notEqual(again.digest, pv.digest);
  });

  test(`CX-T354 ${label}: PAT の権限が足りなければ（返信・解決できないスレッド）、何も書かずに止める。必要な権限名を言う`, async () => {
    const t = threadsOf(kind);
    t[1].canResolve = false;
    if (kind === "github") t[1].canReply = false;
    const w = world(kind, t);
    // GitLab は PAT のロール（Reporter では書けない）で見る
    if (w.mock instanceof WriteGitLab) w.mock.role = 20;
    const pv = await preview(w);
    const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "keep"), pv.digest, w.d);
    assert.equal(out.kind, "refused", JSON.stringify(out));
    assert.match(out.kind === "refused" ? out.message : "", kind === "github" ? /Pull requests: Read and write/ : /api スコープ/);
    assert.equal(w.at.length, 0);
    assert.equal(commits(w), 0);
  });

  test(`CX-T355 ${label}: Chrome で扱えない種類のスレッドは unsupported を付ける。そのスレッドを含む確定は、何も書かずに VS Code へ誘導する`, async () => {
    const t = threadsOf(kind);
    if (kind === "github") t[1].pending = true;
    else t[1].nonDiff = true;
    const w = world(kind, t);
    const pv = await preview(w);
    assert.deepEqual(pv.threads.map((x) => Boolean(x.unsupported)), [false, true, false]);
    assert.match(pv.threads[1].unsupported ?? "", kind === "github" ? /保留中のレビュー/ : /差分以外の discussion/);
    // 画面を通さず確定しても、書く前の確かめで止まる
    const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "keep", "keep"), pv.digest, w.d);
    assert.equal(out.kind, "refused", JSON.stringify(out));
    assert.match(out.kind === "refused" ? out.message : "", /VS Code/);
    assert.equal(w.at.length, 0);
    assert.equal(commits(w), 0);
  });

  test(`CX-T356 ${label}: ホストの書き込みが途中で止まったらコミットしない。読み直して決め直すと、同じ issue・返信を重ねずに完了する`, async () => {
    const w = world(kind, threadsOf(kind));
    const pv = await preview(w);
    const [a, b, c] = ids(kind);
    // A の解決は済み、B の解決で止まる
    w.mock.w.failures.push({ op: "resolve", status: 500, message: "boom", nth: 2 });
    const first = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
    assert.equal(first.kind, "halted", JSON.stringify(first));
    if (first.kind === "halted") {
      assert.equal(first.done.issues.length, 1);
      assert.deepEqual([first.done.replied, first.done.resolved], [2, 1]);
      assert.match(first.message, /スレッドの解決で止まりました/);
    }
    assert.equal(commits(w), 0);
    assert.equal(w.mock.w.comments.length, 0);
    // 読み直すと、解決した A は消え、B と C が残る
    const next = await preview(w);
    assert.deepEqual(next.threads.map((x) => x.body), ["B の指摘", "C の指摘"]);
    const second = written(await decidePhase(w.repo, FAMILY, 1, pick(next, "issue", "fix"), next.digest, w.d));
    assert.equal(w.mock.w.issues.length, 1, "同じ印の issue は作り直さない");
    // 使い回した issue は「作った」に数えない
    assert.deepEqual(second.issues, []);
    assert.equal(second.reusedIssues?.length, 1);
    assert.match(sayDecide(second).text, /既にあった issue 1 件/);
    assert.ok(!/作った issue/.test(sayDecide(second).text));
    const state = new Map(w.mock.w.threads.map((t) => [t.id, t]));
    assert.equal(state.get(b)?.replies.length, 1, "同じ返信を付け直さない");
    assert.equal(state.get(a)?.replies.length, 1);
    assert.deepEqual([state.get(a)?.resolved, state.get(b)?.resolved, state.get(c)?.resolved], [true, true, false]);
    assert.equal(commits(w), 1);
    assert.equal(w.mock.w.comments.length, 1);
    assert.equal(second.followup, "i0004-01-02");
  });

  test(`CX-T357 ${label}: コミットが競合して再試行しても、ホストには二重に書かない（issue・返信・解決・コメントは 1 回）`, async () => {
    const w = world(kind, threadsOf(kind));
    const pv = await preview(w);
    const PENDING = `.ccnavi/approved/phases/${FAMILY}/1.pending`;
    w.mock.beforeCommit = (b) => void w.mock.push(b, { [PENDING]: '{"at": "2026-09-29T00:00:00+0000"}\n' }, "置き場だけの push");
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d));
    // 書く直前に先頭が動いたので、コミットは再試行された（GitHub は 1 回目が stale、GitLab は書いた後の事後確認で戻して積み直す）
    const attempts = w.mock instanceof WriteGitLab ? w.mock.glCommits.length : w.mock.commitCalls.length;
    assert.ok(attempts >= 2, `コミットの試行 ${attempts}`);
    assert.ok(`.ccnavi/approved/doing/${out.followup}.md` in w.mock.files(FAMILY));
    assert.deepEqual(ops(w), ["issue", "reply", "reply", "resolve", "resolve", "comment"]);
    assert.equal(w.mock.w.issues.length, 1);
    assert.equal(w.mock.w.comments.length, 1);
    assert.equal(out.followup, "i0004-01-02");
  });

  test(`CX-T358 ${label}: コメントだけ失敗しても、コミットは戻さず警告にする`, async () => {
    const w = world(kind, threadsOf(kind));
    const pv = await preview(w);
    w.mock.w.failures.push({ op: "comment", status: 500, message: "boom" });
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d));
    assert.equal(commits(w), 1);
    assert.match(out.warning ?? "", /コメントだけ MR に投稿できませんでした/);
    assert.equal(w.mock.w.comments.length, 0);
    assert.ok(w.mock.w.threads.slice(0, 2).every((t) => t.resolved));
  });

  test(`CX-T359 ${label}: 見せたあとに指摘が増え、fix が無ければ、増えた分は未対応で残す。積むものが無いので空のコミットは作らない`, async () => {
    const w = world(kind, threadsOf(kind, 2));
    const pv = await preview(w);
    w.mock.w.threads.push(threadsOf(kind, 3)[2]);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "keep"), pv.digest, w.d));
    assert.equal(out.oid, "");
    assert.deepEqual([out.reviewed, out.followup], [false, ""]);
    assert.equal(commits(w), 0);
    assert.deepEqual(ops(w), ["reply", "reply", "resolve", "resolve", "comment"]);
    assert.deepEqual(w.mock.w.threads.map((t) => t.resolved), [true, true, false]);
    assert.ok(!(MARK in w.mock.files(FAMILY)));
  });

  test(`CX-T361 ${label}: 全 fix でコメントだけ権限不足（拒否）になっても、コミットは積み、警告にとどめる`, async () => {
    const w = world(kind, threadsOf(kind, 2));
    const pv = await preview(w);
    w.mock.w.failures.push({ op: "comment", status: 403, message: "Resource not accessible" });
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv), pv.digest, w.d));
    assert.equal(commits(w), 1);
    assert.match(out.warning ?? "", /コメントだけ MR に投稿できませんでした/);
    assert.equal(sayDecide(out).className, "notice error");
  });

  test(`CX-T362 ${label}: 見せたあとに指摘が増え、fix もあれば、続きの子を積み、増えた分の件数を返す。文面は判断待ちと言う`, async () => {
    const w = world(kind, threadsOf(kind, 2));
    const pv = await preview(w);
    w.mock.w.threads.push(threadsOf(kind, 3)[2]);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "fix"), pv.digest, w.d));
    assert.equal(commits(w), 1);
    assert.deepEqual([out.reviewed, out.followup, out.pendingExtra], [false, "i0004-01-02", 1]);
    assert.match(sayDecide(out).text, /増えた指摘 1 件は判断待ちで、フェーズはレビュー済みにしていません/);
    assert.equal(w.mock.w.threads[2].resolved, false);
  });

  test(`CX-T363 ${label}: 見せたあとに指摘が増えて積むものが無ければ、文面は「行き先は反映したが、増えた指摘が判断待ち」と言う`, async () => {
    const w = world(kind, threadsOf(kind, 2));
    const pv = await preview(w);
    w.mock.w.threads.push(threadsOf(kind, 3)[2]);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "keep"), pv.digest, w.d));
    assert.equal(out.pendingExtra, 1);
    assert.match(sayDecide(out).text, /行き先は反映しましたが、見せた後に増えた指摘 1 件が判断待ちで、フェーズはレビュー済みにしていません/);
  });

  test(`CX-T364 ${label}: ホストの読み取りが遅れて、書いた解決が次の取得に映らなくても、短く待って取り直して完了する`, async () => {
    const w = world(kind, threadsOf(kind));
    w.mock.w.resolveLag = 2;
    const pv = await preview(w);
    const out = written(await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d));
    assert.equal(commits(w), 1);
    assert.equal(out.followup, "i0004-01-02");
    assert.equal(w.mock.w.comments.length, 1);
  });

  test(`CX-T365 ${label}: 取り直しても映らなければ、attention で画面を閉じ、ホストには書けていると案内する。コメントは投稿しない`, async () => {
    const w = world(kind, threadsOf(kind));
    w.mock.w.resolveLag = 50;
    const pv = await preview(w);
    const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
    assert.equal(out.kind, "attention", JSON.stringify(out));
    const message = out.kind === "attention" ? out.message : "";
    assert.match(message, /ホストには書けています。読み直してから決め直してください/);
    assert.match(message, /MR のコメントは投稿していません/);
    assert.equal("hostWritten" in out && out.hostWritten, true);
    assert.equal(commits(w), 0);
    assert.equal(w.mock.w.comments.length, 0);
  });
}

/** 先頭が動く（置き場だけの push）。コミットを試みるたびに起こす */
function keepMoving(w: World): void {
  let n = 0;
  const hook = (b: string): void => {
    n += 1;
    w.mock.push(b, { [`.ccnavi/approved/phases/${FAMILY}/${n + 1}.pending`]: '{"at": "2026-09-29T00:00:00+0000"}\n' }, "置き場だけの push");
    w.mock.beforeCommit = hook;
  };
  w.mock.beforeCommit = hook;
}

test("CX-T366 ホスト書き込みの後で先頭が動き続けたら conflict。ホストは済んでいると言い、コメントは投稿せず、画面を閉じる印を付ける（GitHub）", async () => {
  const w = world("github", threadsOf("github"));
  const pv = await preview(w);
  keepMoving(w);
  const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
  assert.equal(out.kind, "conflict", JSON.stringify(out));
  const message = out.kind === "conflict" ? out.message : "";
  assert.match(message, /^ホストへの書き込み（issue 1 件・返信 2 件・解決 2 件）は済んでいますが/);
  assert.match(message, /MR のコメントは投稿していません/);
  assert.equal("hostWritten" in out && out.hostWritten, true);
  assert.deepEqual(ops(w), ["issue", "reply", "reply", "resolve", "resolve"]);
  assert.equal(w.mock.w.comments.length, 0);
});

test("CX-T367 ホスト書き込みの後の 2 周目でレビュー済みのマーカーが置かれていたら、refused（ALREADY_REVIEWED）。ホスト済みと言う（GitHub）", async () => {
  const w = world("github", threadsOf("github"));
  const pv = await preview(w);
  w.mock.beforeCommit = (b) => void w.mock.push(b, { [MARK]: '{"mr": 7, "accepted": [], "actor": "x", "via": "chrome", "at": "2026-09-29T00:00:00+0000"}\n' }, "別のブラウザでレビュー済み");
  const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
  assert.equal(out.kind, "refused", JSON.stringify(out));
  assert.match(out.kind === "refused" ? out.message : "", /^ホストへの書き込み.*は済んでいますが/);
  assert.equal("hostWritten" in out && out.hostWritten, true);
  assert.equal(w.mock.w.comments.length, 0);
});

test("CX-T368 ホスト書き込みの後の 2 周目で、見せた指摘が変わっていたら changed（mismatch の code）。ホスト済みと言う（GitHub）", async () => {
  const w = world("github", threadsOf("github"));
  const pv = await preview(w);
  w.mock.beforeCommit = (b) => {
    w.mock.w.threads[2].body = "C の指摘を書き換えた";
    w.mock.push(b, { [`.ccnavi/approved/phases/${FAMILY}/2.pending`]: '{"at": "2026-09-29T00:00:00+0000"}\n' }, "置き場だけの push");
  };
  const out = await decidePhase(w.repo, FAMILY, 1, pick(pv, "keep", "issue", "fix"), pv.digest, w.d);
  assert.equal(out.kind, "changed", JSON.stringify(out));
  assert.equal(out.kind === "changed" && out.code, "CORE_DECIDE_MISMATCH");
  assert.match(out.kind === "changed" ? out.message : "", /^ホストへの書き込み.*は済んでいます/);
  assert.equal("hostWritten" in out && out.hostWritten, true);
  assert.equal(commits(w), 0);
});

test("CX-T360 結果の一言: コミットが無いときはその旨を、コメントだけの失敗は警告として同じ欄に出す", () => {
  const base = { kind: "written", reviewed: false, followup: "", issues: [] } as const;
  const none = sayDecide({ ...base, oid: "" });
  assert.equal(none.className, "notice ok");
  assert.match(none.text, /親のブランチへ積むものはありませんでした/);
  const warned = sayDecide({ ...base, oid: "0123456789abcdef", warning: "コメントだけ MR に投稿できませんでした（boom）" });
  assert.equal(warned.className, "notice error");
  assert.match(warned.text, /コミット：0123456）/);
  assert.match(warned.text, /注意：コメントだけ MR に投稿できませんでした/);
});
