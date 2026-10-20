/**
 * 「マージ状況を確認」の遷移（`src/core/merge.ts`）。確認の二重起動・遅れて返った答え・一部だけの失敗・
 * 片付けの再入と送信の失敗。外へ出る仕事は効果として返るだけなので、単体で全部試せる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { MERGE_IDLE, mergeStep, mergeView, type MergeEffect, type MergeInput, type MergeState } from "../../src/core/merge.js";
import type { IncompleteEntry, MrStateResult, RetiredEntry, RetiredParse } from "../../src/core/mrstate.js";

const AT = "2026-10-01 10:00:00";
const abandon = (parent: string): string => `sh /ws/.ccnavi/scripts/ccnavi-launcher.sh worktree retire --abandon ${parent}`;

function step(state: MergeState, input: MergeInput): { state: MergeState; effects: readonly MergeEffect[]; redraw: boolean } {
  return mergeStep(state, input);
}

const kinds = (effects: readonly MergeEffect[]): string[] => effects.map((e) => e.kind);

function listed(entries: readonly RetiredEntry[], incomplete: readonly IncompleteEntry[] = []): RetiredParse {
  return { ok: true, entries, incomplete };
}

const merged = (n: number): MrStateResult => ({ kind: "merged", number: n, url: `https://example.com/pull/${n}` });
const open = (n: number, draft = false): MrStateResult => ({ kind: "open", number: n, draft, url: `https://example.com/pull/${n}` });
const closed = (n: number): MrStateResult => ({ kind: "closed", number: n, url: `https://example.com/pull/${n}` });

/** 2 親（i1 は self の MR 11、i2 は web の MR 12）を確認し終えた状態を作る */
function checked(results: Record<string, MrStateResult>): MergeState {
  const started = step(MERGE_IDLE, { kind: "check" });
  const token = started.state.token;
  let { state } = step(started.state, {
    kind: "listed",
    token,
    at: AT,
    result: listed([
      { parent: "i1", repo: "self", mr: 11, branch: "a" },
      { parent: "i2", repo: "web", mr: 12, branch: "b" },
    ]),
  });
  for (const [parent, result] of Object.entries(results)) {
    state = step(state, { kind: "stated", token, parent, result, at: AT }).state;
  }
  return state;
}

test("CB-T630 確認を押すと一覧を読みに行く。確認中の「確認」は受けない（二重に走らせない）", () => {
  const first = step(MERGE_IDLE, { kind: "check" });
  assert.equal(first.state.checking, true);
  assert.equal(first.redraw, true);
  assert.deepEqual(first.effects, [{ kind: "loadList", token: 1 }]);
  const again = step(first.state, { kind: "check" });
  assert.equal(again.state, first.state);
  assert.equal(again.redraw, false);
  assert.deepEqual(again.effects, []);
  // 確認が終われば、もう一度押せて、世代は進む
  const done = step(first.state, { kind: "listed", token: 1, at: AT, result: listed([]) });
  assert.equal(done.state.checking, false);
  const next = step(done.state, { kind: "check" });
  assert.deepEqual(next.effects, [{ kind: "loadList", token: 2 }]);
});

test("CB-T631 一覧が読めたら対象ごとに状況を聞く（MR の番号があるものだけ）。0 件でも、incomplete だけでも確認は終わる", () => {
  const started = step(MERGE_IDLE, { kind: "check" }).state;
  const listedStep = step(started, {
    kind: "listed",
    token: 1,
    at: AT,
    result: listed(
      [
        { parent: "i1", repo: "self", mr: 11, branch: "" },
        { parent: "i2", repo: "web", mr: null, branch: "" },
      ],
      [{ parent: "i3", reason: "記録を読めない" }],
    ),
  });
  assert.deepEqual(listedStep.effects, [{ kind: "loadState", token: 1, parent: "i1", repo: "self", mr: 11 }]);
  assert.equal(listedStep.state.checking, true, "i1 の答えを待つ");
  // 番号の無い親と incomplete は、聞かずに確認できないものとして出る（その親だけに留める）
  const rows = listedStep.state.list.kind === "rows" ? listedStep.state.list.rows : [];
  assert.equal(rows[1].result?.kind === "unknown" ? rows[1].result.reason : "", "no-number");
  assert.equal(rows[2].result?.kind, "incomplete");
  // 0 件: 確認は終わり、帯は「ありません」を言える（一覧を取れた）
  const empty = step(started, { kind: "listed", token: 1, at: AT, result: listed([]) });
  assert.equal(empty.state.checking, false);
  assert.equal(empty.state.checkedAt, AT);
  assert.deepEqual(mergeView(empty.state, abandon), { checking: false, checkedAt: AT, error: "", rows: [] });
  // incomplete だけ: 聞くものが無いので、すぐ終わる。「ありません」ではなく確認できない行を出す
  const only = step(started, { kind: "listed", token: 1, at: AT, result: listed([], [{ parent: "i9", reason: "mr が無い" }]) });
  assert.equal(only.state.checking, false);
  assert.deepEqual(only.effects, []);
  const view = mergeView(only.state, abandon);
  assert.equal(view?.rows.length, 1);
  assert.equal(view?.rows[0].state, "incomplete");
  assert.equal(view?.rows[0].detail, "mr が無い");
});

test("CB-T632 一覧の形式の異常は「ありません」とは言わず、エラーとして出す", () => {
  const started = step(MERGE_IDLE, { kind: "check" }).state;
  const failed = step(started, { kind: "listed", token: 1, at: AT, result: { ok: false, error: "一覧を取れない（形式の異常: 頭の行が retired 1 ではありません）" } });
  assert.equal(failed.state.checking, false);
  assert.deepEqual(failed.effects, []);
  const view = mergeView(failed.state, abandon);
  assert.equal(view?.error, "一覧を取れない（形式の異常: 頭の行が retired 1 ではありません）");
  assert.deepEqual(view?.rows, []);
  assert.equal(view?.checkedAt, AT);
});

test("CB-T633 一部だけ失敗してもほかは続け、全部そろったら終える。期限切れ・古い sh もその親だけの結果", () => {
  const started = step(MERGE_IDLE, { kind: "check" }).state;
  let state = step(started, {
    kind: "listed",
    token: 1,
    at: AT,
    result: listed([
      { parent: "i1", repo: "self", mr: 11, branch: "" },
      { parent: "i2", repo: "web", mr: 12, branch: "" },
      { parent: "i3", repo: "web", mr: 13, branch: "" },
    ]),
  }).state;
  state = step(state, { kind: "stated", token: 1, parent: "i2", at: AT, result: { kind: "unknown", reason: "timeout", detail: "時間内に終わりませんでした" } }).state;
  assert.equal(state.checking, true, "ほかの親の答えを待つ");
  state = step(state, { kind: "stated", token: 1, parent: "i1", at: AT, result: merged(11) }).state;
  assert.equal(state.checking, true);
  state = step(state, { kind: "stated", token: 1, parent: "i3", at: "終わった時刻", result: { kind: "unknown", reason: "old-sh", detail: "sh が古い版です。配り直してください" } }).state;
  assert.equal(state.checking, false);
  assert.equal(state.checkedAt, "終わった時刻");
  const view = mergeView(state, abandon);
  assert.deepEqual(view?.rows.map((r) => [r.parent, r.state, r.reason]), [["i1", "merged", ""], ["i2", "unknown", "timeout"], ["i3", "unknown", "old-sh"]]);
});

test("CB-T634 頼んだ世代でない答えは受けない（閉じたボードや前の確認へ遅れて返った結果を捨てる）", () => {
  const started = step(MERGE_IDLE, { kind: "check" }).state; // 世代 1
  const loaded = step(started, { kind: "listed", token: 1, at: AT, result: listed([{ parent: "i1", repo: "self", mr: 11, branch: "" }]) }).state;
  // 別の世代・別の親・確認していないとき
  for (const input of [
    { kind: "stated", token: 2, parent: "i1", result: merged(11), at: AT },
    { kind: "stated", token: 0, parent: "i1", result: merged(11), at: AT },
    { kind: "stated", token: 1, parent: "ghost", result: merged(11), at: AT },
  ] as const) {
    const after = step(loaded, input);
    assert.equal(after.state, loaded, JSON.stringify(input));
    assert.equal(after.redraw, false);
  }
  assert.equal(step(MERGE_IDLE, { kind: "stated", token: 1, parent: "i1", result: merged(11), at: AT }).state, MERGE_IDLE);
  assert.equal(step(MERGE_IDLE, { kind: "listed", token: 1, result: listed([]), at: AT }).state, MERGE_IDLE);
  // 答えた親には答えを 2 度入れない（先の答えを上書きしない）
  const answered = step(loaded, { kind: "stated", token: 1, parent: "i1", result: merged(11), at: AT }).state;
  const dup = step(answered, { kind: "stated", token: 1, parent: "i1", result: closed(11), at: AT });
  assert.equal(dup.state, answered);
  // 確認が終わったあとの遅れた一覧も受けない。新しい確認を始めたら、前の世代の答えは捨てる
  assert.equal(step(answered, { kind: "listed", token: 1, result: listed([{ parent: "x", repo: "self", mr: 1, branch: "" }]), at: AT }).state, answered);
  const second = step(answered, { kind: "check" }).state;
  assert.equal(second.token, 2);
  assert.equal(step(second, { kind: "stated", token: 1, parent: "i1", result: closed(11), at: AT }).state, second);
});

test("CB-T635 mergeView: 一度も確認していなければ帯を出さない。確認中は行ごとに「確認中」。closed のときだけ記録を消すコマンドを持つ", () => {
  assert.equal(mergeView(MERGE_IDLE, abandon), undefined);
  const state = checked({ i1: closed(11) });
  const view = mergeView(state, abandon);
  assert.equal(view?.checking, true, "i2 の答えがまだ");
  assert.deepEqual(view?.rows.map((r) => [r.parent, r.state, r.project, r.mr]), [["i1", "closed", "", 11], ["i2", "checking", "web", 12]]);
  assert.equal(view?.rows[0].abandon, abandon("i1"));
  assert.equal(view?.rows[1].abandon, "");
  const all = mergeView(checked({ i1: open(11, true), i2: merged(12) }), abandon);
  assert.deepEqual(all?.rows.map((r) => [r.state, r.draft, r.abandon]), [["open", true, ""], ["merged", false, ""]]);
});

test("CB-T636 片付け: マージ済みと確認できた親だけ。確認のダイアログに答えるまで送らない。再入させない", () => {
  const state = checked({ i1: merged(11), i2: open(12) });
  // マージ待ちの親・知らない親には起こさない（言うだけ）
  for (const parent of ["i2", "ghost"]) {
    const refused = step(state, { kind: "retire", parent });
    assert.equal(refused.state, state);
    assert.deepEqual(kinds(refused.effects), ["warn"]);
  }
  // マージ済み: 確認のダイアログを出す。まだ送らない
  const asked = step(state, { kind: "retire", parent: "i1" });
  assert.deepEqual(asked.effects, [{ kind: "confirmRetire", parent: "i1" }]);
  assert.deepEqual(asked.state.confirming, ["i1"]);
  assert.equal(mergeView(asked.state, abandon)?.rows[0].busy, true);
  // ダイアログを開いている間の再入は受けない（確認が二重に出ない）
  const twice = step(asked.state, { kind: "retire", parent: "i1" });
  assert.equal(twice.state, asked.state);
  assert.deepEqual(twice.effects, []);
  // やめれば元に戻り、押し直せる
  const cancelled = step(asked.state, { kind: "retireAnswered", parent: "i1", accepted: false });
  assert.deepEqual(cancelled.state.confirming, []);
  assert.deepEqual(cancelled.effects, []);
  assert.deepEqual(kinds(step(cancelled.state, { kind: "retire", parent: "i1" }).effects), ["confirmRetire"]);
  // 「実行する」: 送る。送り終わるまで confirming に残る（その間も再入させない）
  const accepted = step(asked.state, { kind: "retireAnswered", parent: "i1", accepted: true });
  assert.deepEqual(accepted.effects, [{ kind: "sendRetire", parent: "i1" }]);
  assert.deepEqual(accepted.state.confirming, ["i1"]);
  assert.deepEqual(step(accepted.state, { kind: "retire", parent: "i1" }).effects, []);
  // 頼んでいない答え（ダイアログを開いていない親）は受けない
  assert.equal(step(state, { kind: "retireAnswered", parent: "i1", accepted: true }).state, state);
  assert.deepEqual(step(state, { kind: "retireAnswered", parent: "i1", accepted: true }).effects, [], "ダイアログの答えが無いのに送らない");
});

test("CB-T637 送信の結果: 成功すると、その行を「送信した」にして、次の確認まで押せない。失敗は「送信した」と言わず、理由を出して押し直せる", () => {
  const state = checked({ i1: merged(11), i2: merged(12) });
  const sending = step(step(state, { kind: "retire", parent: "i1" }).state, { kind: "retireAnswered", parent: "i1", accepted: true }).state;
  const ok = step(sending, { kind: "retireSent", parent: "i1", error: undefined });
  assert.deepEqual(ok.state.sent, ["i1"]);
  assert.deepEqual(ok.state.confirming, []);
  const viewOk = mergeView(ok.state, abandon)?.rows[0];
  assert.equal(viewOk?.sent, true);
  assert.equal(viewOk?.sendError, "");
  // 送ったあとは、同じ親の［片付ける］を受けない
  assert.deepEqual(step(ok.state, { kind: "retire", parent: "i1" }).effects, []);
  // ほかの親は押せる
  assert.deepEqual(kinds(step(ok.state, { kind: "retire", parent: "i2" }).effects), ["confirmRetire"]);
  // 失敗: 送ったことにせず、理由を残し、押し直せる
  const ng = step(sending, { kind: "retireSent", parent: "i1", error: "ターミナルに送れませんでした" });
  assert.deepEqual(ng.state.sent, []);
  assert.deepEqual(ng.state.confirming, []);
  const viewNg = mergeView(ng.state, abandon)?.rows[0];
  assert.equal(viewNg?.sent, false);
  assert.equal(viewNg?.sendError, "ターミナルに送れませんでした");
  assert.deepEqual(kinds(step(ng.state, { kind: "retire", parent: "i1" }).effects), ["confirmRetire"], "押し直せる");
  // 押し直して成功すれば、失敗の理由は消える
  const retry = step(step(step(ng.state, { kind: "retire", parent: "i1" }).state, { kind: "retireAnswered", parent: "i1", accepted: true }).state, { kind: "retireSent", parent: "i1", error: undefined });
  assert.equal(mergeView(retry.state, abandon)?.rows[0].sendError, "");
  assert.deepEqual(retry.state.sent, ["i1"]);
  // 次に「マージ状況を確認」を押すまで押せない（確認を始めると、送った印は消える）
  assert.deepEqual(retry.state.sent, ["i1"]);
  const recheck = step({ ...retry.state, checking: false }, { kind: "check" });
  assert.deepEqual(recheck.state.sent, []);
  assert.deepEqual(recheck.state.sendErrors, {});
  // 頼んでいない送信の結果は受けない
  assert.equal(step(state, { kind: "retireSent", parent: "i1", error: undefined }).state, state);
});

test("CB-T638 安全性: 読み取り（loadList / loadState）の効果は書き込みの効果を直接起こさない。送る効果は、ダイアログで「実行する」と答えた後にしか出ない", () => {
  const all: MergeEffect[] = [];
  let state = MERGE_IDLE;
  const feed = (input: MergeInput): void => {
    const next = step(state, input);
    state = next.state;
    all.push(...next.effects);
  };
  feed({ kind: "check" });
  feed({ kind: "listed", token: 1, at: AT, result: listed([{ parent: "i1", repo: "self", mr: 11, branch: "" }]) });
  feed({ kind: "stated", token: 1, parent: "i1", result: merged(11), at: AT });
  assert.deepEqual(kinds(all), ["loadList", "loadState"], "確認の間に送る効果は出ない");
  feed({ kind: "retire", parent: "i1" });
  assert.ok(!kinds(all).includes("sendRetire"), "ダイアログに答える前に送る効果は出ない");
  feed({ kind: "retireAnswered", parent: "i1", accepted: true });
  assert.deepEqual(kinds(all), ["loadList", "loadState", "confirmRetire", "sendRetire"]);
});
