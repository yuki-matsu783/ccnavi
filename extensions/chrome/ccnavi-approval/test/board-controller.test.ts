/**
 * ボードの状態と副作用（src/board/controller.ts）。`document` も `chrome` も無い Node の上で、外界を差し替えて回す。
 * 書く・読む中身（approveFamily・collectRepo など）も差し替え、ここでは状態の移り変わりと、確かめてから書く順だけを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { FamilyBoard, RepoBoard } from "../src/core/snapshot.js";
import type { Outcome } from "../src/core/write.js";
import type { RepoConfig } from "../src/core/settings.js";
import { createBoardController, sayOutcome, type BoardEnv, type BoardOps } from "../src/board/controller.js";

const HOSTS = [{ id: "github.com", kind: "github" as const, api: "https://api.github.com", graphql: "https://api.github.com/graphql", web: "https://github.com", label: "GitHub" }];
const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 7, extraBranches: [], project: "", workspace: "" };
const KEY = "github.com/acme/widgets";

function board(repo: RepoConfig = REPO): RepoBoard {
  return { repo, integration: null, compat: null, candidates: [], missingExtras: [], families: [], error: "", stats: { rest: 0, graphql: 0, blobsFetched: 0, blobsCached: 0 } };
}

function family(name = "i0001"): FamilyBoard {
  return { family: { name }, result: { digest: "d1", batch: [{ ticket: name }], only: null }, error: "" } as unknown as FamilyBoard;
}

const WRITTEN: Outcome = { kind: "written", oid: "0123456789abcdef", rounds: 1, lines: [] };

/** 押した後の非同期の処理が落ち着くまで待つ */
async function until(pred: () => boolean, what = "条件"): Promise<void> {
  for (let i = 0; i < 1000; i++) {
    if (pred()) return;
    await new Promise((r) => setImmediate(r));
  }
  assert.fail(`${what} にならなかった`);
}

function harness(opts: { confirm?: boolean; prompt?: string | null; ops?: Partial<BoardOps>; repos?: RepoConfig[]; tokenNotice?: { level: string; text: string } | null } = {}) {
  const store: Record<string, unknown> = { repos: opts.repos ?? [REPO] };
  const log = { confirm: [] as string[], prompt: [] as string[], set: [] as Record<string, unknown>[], get: [] as string[], sent: [] as unknown[], collect: 0, options: 0 };
  const timers: (() => void)[] = [];
  let editing = false;
  const env: BoardEnv = {
    hosts: HOSTS as unknown as BoardEnv["hosts"],
    version: "0.0.0-test",
    send: async (message) => {
      log.sent.push(message);
      const m = message as { kind: string; op?: string };
      if (m.kind === "token.status") return { ok: true, value: { set: true, notice: opts.tokenNotice ?? null } };
      if (m.op === "pullApprovals") return { ok: true, value: [] };
      return { ok: false, error: `想定外の依頼: ${JSON.stringify(message)}` };
    },
    storage: {
      get: async (k) => {
        log.get.push(String(k));
        const key = String(k);
        return key in store ? { [key]: structuredClone(store[key]) } : {};
      },
      set: async (items) => {
        log.set.push(structuredClone(items));
        Object.assign(store, structuredClone(items));
      },
    },
    openOptions: () => void log.options++,
    confirm: (m) => {
      log.confirm.push(m);
      return opts.confirm ?? true;
    },
    prompt: (m) => {
      log.prompt.push(m);
      return opts.prompt === undefined ? "理由" : opts.prompt;
    },
    startWorker: () => ({ call: async () => ({}), init: async () => ({ boot_ms: 10, import_ms: 20, total_ms: 30, pyodide: "0.test", violations: [] }) }),
    blobCache: async () => ({ get: async () => undefined, put: async () => undefined }),
    now: () => new Date("2026-01-01T00:00:00Z"),
    editing: () => editing,
    timers: {
      doc: { visibilityState: "visible", addEventListener: () => undefined },
      now: () => 0,
      setTimeout: (cb) => timers.push(cb),
      clearTimeout: () => undefined,
    },
    ops: {
      collectRepo: async (repo) => {
        log.collect++;
        return board(repo);
      },
      ...opts.ops,
    },
  };
  const c = createBoardController(env);
  return {
    c,
    log,
    store,
    timers,
    setEditing(v: boolean) {
      editing = v;
    },
  };
}

test("CX-T219 書いた結果の一言: 結果の種類ごとの文面と data-kind・見た目", () => {
  const cases: [Outcome, string, string][] = [
    [WRITTEN, "written", "承認を書いた（0123456）。書いた後の中身も確かめた"],
    [{ ...WRITTEN, rounds: 3 } as Outcome, "written", "承認を書いた（0123456。途中で親のブランチの先頭が動いたので、3 回目で書いた）。書いた後の中身も確かめた"],
    [{ kind: "changed", message: "動いた" }, "changed", "承認を書かなかった: 動いた"],
    [{ kind: "conflict", message: "競合" }, "conflict", "承認を書けなかった（自動ではやり直さない）: 競合"],
    [{ kind: "attention", message: "残った" } as Outcome, "attention", "承認は要確認になった。書けたかどうかと何が残ったかを、ホストの履歴で確かめてください: 残った"],
    [{ kind: "refused", message: "断った" }, "refused", "承認を書かなかった: 断った"],
    [{ kind: "failed", message: "落ちた" }, "failed", "承認を書かなかった: 落ちた"],
  ];
  for (const [outcome, kind, text] of cases) {
    assert.deepEqual(sayOutcome(outcome, "承認"), { kind, className: `notice ${kind === "written" ? "ok" : "error"}`, text });
  }
});

test("CX-T220 読み直し: document と chrome の無いところで、差し替えた外界だけで読み、欄・状態・PAT の期限の帯を出す。変わるたびに通知する", async () => {
  assert.equal(typeof (globalThis as { document?: unknown }).document, "undefined");
  assert.equal(typeof (globalThis as { chrome?: unknown }).chrome, "undefined");
  const h = harness({ tokenNotice: { level: "soon", text: "あと 3 日で切れる" } });
  const seen: string[] = [];
  h.c.subscribe(() => seen.push(h.c.snapshot().state));
  const first = h.c.snapshot();
  assert.equal(first.state, "loading");
  await h.c.refresh();
  const s = h.c.snapshot();
  assert.notEqual(s, first);
  assert.equal(s.state, "done");
  assert.deepEqual([...s.sections.keys()], [KEY]);
  assert.deepEqual(s.sections.get(KEY)?.attention, {});
  assert.deepEqual(s.status, { text: "Pyodide 0.test: 起動 10 ms・読み込み 20 ms", bootMs: 10, importMs: 20, violations: 0 });
  assert.deepEqual(s.banner, [{ level: "soon", className: "notice warn", text: "あと 3 日で切れる" }]);
  assert.equal(s.repos.length, 1);
  assert.ok(s.boards.has(KEY));
  assert.ok(seen.length > 1 && seen.at(-1) === "done", seen.join(","));
  // 登録が無ければ欄を消して言う
  h.store.repos = [];
  await h.c.refresh();
  assert.equal(h.c.snapshot().sections.size, 0);
  assert.equal(h.c.snapshot().status.text, "リポジトリが登録されていない。設定画面で登録してください");
  h.c.openOptions();
  assert.equal(h.log.options, 1);
});

test("CX-T221 確認のダイアログで断れば何も書かない（承認・レビュー済み・取り下げ・始める）。押せる状態に戻し、読み直さない", async () => {
  const calls: string[] = [];
  const ops: Partial<BoardOps> = {
    approveFamily: async () => (calls.push("approve"), WRITTEN),
    confirmPhase: async () => (calls.push("review"), WRITTEN),
    withdrawTicket: async () => (calls.push("withdraw"), WRITTEN),
    startIssue: async () => (calls.push("start"), { kind: "started", name: "feature-1-x", head: "abcdef0123" }),
  };
  const h = harness({ confirm: false, prompt: null, ops });
  await h.c.refresh();
  const loads = h.log.collect;
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  for (const press of [
    () => h.c.actions.approve(b, family()),
    () => h.c.actions.review?.(b, family(), 1),
    () => h.c.actions.withdraw(b, family(), "i0001"),
    () => h.c.actions.start?.(b, { number: 1, title: "x" } as never),
  ]) {
    press();
    assert.equal(h.c.snapshot().busy, true);
    assert.equal(h.c.snapshot().state, "writing");
    await until(() => !h.c.snapshot().busy, "押せる状態");
    assert.equal(h.c.snapshot().state, "done");
  }
  assert.equal(h.log.confirm.length, 3);
  assert.equal(h.log.prompt.length, 1);
  assert.deepEqual(calls, []);
  assert.deepEqual(h.log.set, []);
  assert.equal(h.log.collect, loads);
  assert.equal(h.c.snapshot().result.text, "");
});

test("CX-T222 書く操作の最中は、重ねて押しても走らない。書いたら結果を言い、読み直す", async () => {
  let release: (o: Outcome) => void = () => undefined;
  let approves = 0;
  const h = harness({
    ops: {
      approveFamily: () => {
        approves++;
        return new Promise<Outcome>((r) => (release = r));
      },
    },
  });
  await h.c.refresh();
  const loads = h.log.collect;
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.approve(b, family());
  await until(() => approves === 1, "承認が走る");
  h.c.actions.approve(b, family());
  h.c.actions.setRecentDays?.(b, "3");
  await new Promise((r) => setImmediate(r));
  assert.equal(h.log.confirm.length, 1);
  assert.equal(approves, 1);
  assert.equal(h.c.snapshot().busy, true);
  release(WRITTEN);
  await until(() => h.c.snapshot().state === "done" && !h.c.snapshot().busy, "読み直し");
  assert.equal(h.log.collect, loads + 1);
  // 操作の後の読み直しは、言ったことを消さない
  assert.deepEqual(h.c.snapshot().result, { kind: "written", className: "notice ok", text: "承認を書いた（0123456）。書いた後の中身も確かめた" });
  assert.deepEqual(h.log.set, []);
  // 書く途中で投げたら「書けなかった」と言う（見た目は前のまま）
  const thrower = harness({ ops: { approveFamily: async () => Promise.reject(new Error("壊れた")) } });
  await thrower.c.refresh();
  thrower.c.actions.approve(thrower.c.snapshot().boards.get(KEY) as RepoBoard, family());
  await until(() => thrower.c.snapshot().result.kind === "failed" && thrower.c.snapshot().state === "done", "書けなかった");
  assert.deepEqual(thrower.c.snapshot().result, { kind: "failed", className: "", text: "書けなかった: 壊れた" });
});

test("CX-T223 要確認: 書いて要確認になったら残し、押す前にも見て書かない。外すのは確認を挟み、断れば残る", async () => {
  let approves = 0;
  const h = harness({ ops: { approveFamily: async () => (approves++, { kind: "attention", message: "競合が残った" } as Outcome) } });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.approve(b, family("i0002"));
  await until(() => h.c.snapshot().result.kind === "attention" && h.c.snapshot().state === "done", "要確認");
  assert.deepEqual(h.store.attention, { [KEY]: { i0002: "競合が残った" } });
  // 読み直した欄には要確認が付く
  assert.deepEqual(h.c.snapshot().sections.get(KEY)?.attention, { i0002: "競合が残った" });
  // もう一度押しても、確認の前に断る
  h.c.actions.approve(b, family("i0002"));
  await until(() => h.c.snapshot().result.kind === "refused" && !h.c.snapshot().busy, "断る");
  assert.match(h.c.snapshot().result.text, /^i0002 は要確認のまま/);
  assert.equal(h.c.snapshot().result.className, "notice error");
  assert.equal(approves, 1);
  assert.equal(h.log.confirm.length, 1);

  // 外す: 断れば残る
  const confirmNo = harness({ confirm: false });
  confirmNo.store.attention = { [KEY]: { i0002: "残す" } };
  await confirmNo.c.refresh();
  confirmNo.c.actions.dismiss?.(b, "i0002");
  await until(() => confirmNo.log.confirm.length === 1, "確認");
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(confirmNo.log.set, []);
  assert.deepEqual(confirmNo.c.snapshot().sections.get(KEY)?.attention, { i0002: "残す" });
  // 受ければ外れ、欄を描き直す
  const before = h.c.snapshot().sections.get(KEY);
  h.c.actions.dismiss?.(b, "i0002");
  await until(() => h.c.snapshot().sections.get(KEY) !== before, "描き直し");
  assert.deepEqual(h.store.attention, { [KEY]: {} });
  assert.deepEqual(h.c.snapshot().sections.get(KEY)?.attention, {});
});

test("CX-T224 自動の読み直し: 入力欄を触っている間と書く操作の最中は飛ばす。自動では言ったことも欄も消さない", async () => {
  let release: (o: Outcome) => void = () => undefined;
  const h = harness({ ops: { approveFamily: () => new Promise<Outcome>((r) => (release = r)) } });
  h.c.start();
  await until(() => h.c.snapshot().state === "done", "最初の読み込み");
  const loads = h.log.collect;
  // 入力欄を触っている
  h.setEditing(true);
  h.timers.at(-1)?.();
  await new Promise((r) => setImmediate(r));
  assert.equal(h.log.collect, loads);
  // 触り終えた: 読み直す。欄を空にせず差し替え、言ったことは残す
  h.setEditing(false);
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.setRecentDays?.(b, "x");
  await until(() => h.c.snapshot().result.kind === "refused" && !h.c.snapshot().busy, "直近の日数を断る");
  const said = h.c.snapshot().result;
  const states: [number, string][] = [];
  h.c.subscribe(() => states.push([h.c.snapshot().sections.size, h.c.snapshot().state]));
  h.timers.at(-1)?.();
  await until(() => h.log.collect === loads + 1 && states.at(-1)?.[1] === "done", "自動の読み直し");
  assert.ok(states.every(([n, st]) => n === 1 && st === "done"), JSON.stringify(states));
  assert.equal(h.c.snapshot().result, said);
  // 書く操作の最中
  const now = h.log.collect;
  h.c.actions.approve(b, family());
  await until(() => h.log.confirm.length === 1, "承認の確認");
  h.timers.at(-1)?.();
  await new Promise((r) => setImmediate(r));
  assert.equal(h.log.collect, now);
  release(WRITTEN);
  await until(() => h.c.snapshot().state === "done" && !h.c.snapshot().busy, "書いた後の読み直し");
  assert.equal(h.log.collect, now + 1);
});
