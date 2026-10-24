/**
 * ボードの状態と副作用（src/board/controller.ts）。`document` も `chrome` も無い Node の上で、外界を差し替えて回す。
 * 書く・読む中身（approveFamily・collectRepo など）も差し替え、ここでは状態の移り変わりと、確かめてから書く順だけを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { FamilyBoard, RepoBoard } from "../src/core/snapshot.js";
import type { Outcome } from "../src/core/write.js";
import type { RepoConfig } from "../src/core/settings.js";
import { createBoardController, sayDecide, sayOutcome, type BoardEnv, type BoardOps, type DecideOutcome } from "../src/board/controller.js";

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

function harness(opts: { confirm?: boolean; ops?: Partial<BoardOps>; repos?: RepoConfig[]; tokenNotice?: { level: string; text: string } | null } = {}) {
  const store: Record<string, unknown> = { repos: opts.repos ?? [REPO] };
  const log = { confirm: [] as string[], set: [] as Record<string, unknown>[], get: [] as string[], sent: [] as unknown[], collect: 0, options: 0 };
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
    env,
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
    [WRITTEN, "written", "承認を反映しました（コミット：0123456）。反映後の内容も確認しました。"],
    [{ ...WRITTEN, rounds: 3 } as Outcome, "written", "承認を反映しました（コミット：0123456。途中で親のブランチの先頭が動いたので、3 回目で反映しました）。反映後の内容も確認しました。"],
    [{ kind: "changed", message: "動いた" }, "changed", "承認は反映されていません：動いた"],
    [{ kind: "conflict", message: "競合" }, "conflict", "承認を反映できませんでした。自動再試行は行いません：競合"],
    [{ kind: "attention", message: "残った" } as Outcome, "attention", "承認は確認が必要な状態です。反映されたかどうかと残っている内容を、ホストの履歴で確認してください：残った"],
    [{ kind: "refused", message: "断った" }, "refused", "承認は反映されていません：断った"],
    [{ kind: "failed", message: "落ちた" }, "failed", "承認は反映されていません：落ちた"],
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
  assert.deepEqual(s.status, { text: "Pyodide 0.test：起動 10 ms・読み込み 20 ms", bootMs: 10, importMs: 20, violations: 0 });
  assert.deepEqual(s.banner, [{ level: "soon", className: "notice warn", text: "あと 3 日で切れる" }]);
  assert.equal(s.repos.length, 1);
  assert.ok(s.boards.has(KEY));
  assert.ok(seen.length > 1 && seen.at(-1) === "done", seen.join(","));
  // 登録が無ければ欄を消して言う
  h.store.repos = [];
  await h.c.refresh();
  assert.equal(h.c.snapshot().sections.size, 0);
  assert.equal(h.c.snapshot().status.text, "リポジトリが登録されていません。設定画面で登録してください。");
  h.c.openOptions();
  assert.equal(h.log.options, 1);
});

test("CX-T221 確認のダイアログで断れば何も書かない（承認・レビュー済み・始める）。押せる状態に戻し、読み直さない", async () => {
  const calls: string[] = [];
  const ops: Partial<BoardOps> = {
    approveFamily: async () => (calls.push("approve"), WRITTEN),
    confirmPhase: async () => (calls.push("review"), WRITTEN),
    startIssue: async () => (calls.push("start"), { kind: "started", name: "feature-1-x", head: "abcdef0123", mr: { number: 7, url: "https://example.invalid/7" } }),
  };
  const h = harness({ confirm: false, ops });
  await h.c.refresh();
  const loads = h.log.collect;
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  for (const press of [
    () => h.c.actions.approve(b, family()),
    () => h.c.actions.review?.(b, family(), 1),
    () => h.c.actions.start?.(b, { number: 1, title: "x" } as never),
  ]) {
    press();
    assert.equal(h.c.snapshot().busy, true);
    assert.equal(h.c.snapshot().state, "writing");
    await until(() => !h.c.snapshot().busy, "押せる状態");
    assert.equal(h.c.snapshot().state, "done");
  }
  assert.equal(h.log.confirm.length, 3);
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
  assert.deepEqual(h.c.snapshot().result, { kind: "written", className: "notice ok", text: "承認を反映しました（コミット：0123456）。反映後の内容も確認しました。" });
  assert.deepEqual(h.log.set, []);
  // 書く途中で投げたら「書けなかった」と失敗の見た目で言う
  const thrower = harness({ ops: { approveFamily: async () => Promise.reject(new Error("壊れた")) } });
  await thrower.c.refresh();
  thrower.c.actions.approve(thrower.c.snapshot().boards.get(KEY) as RepoBoard, family());
  await until(() => thrower.c.snapshot().result.kind === "failed" && thrower.c.snapshot().state === "done", "書けなかった");
  assert.deepEqual(thrower.c.snapshot().result, { kind: "failed", className: "notice error", text: "反映できませんでした：壊れた" });
});

test("CX-T225 書く途中で投げたら、直前が成功（緑）でも失敗の見た目（赤）で言う。その後に書ければ緑に戻る", async () => {
  const plan: (() => Promise<Outcome>)[] = [async () => WRITTEN, async () => Promise.reject(new Error("壊れた")), async () => WRITTEN];
  const h = harness({ ops: { approveFamily: () => (plan.shift() as () => Promise<Outcome>)() } });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  const press = async (kind: string, what: string) => {
    h.c.actions.approve(b, family());
    await until(() => h.c.snapshot().result.kind === kind && h.c.snapshot().state === "done" && !h.c.snapshot().busy, what);
  };
  await press("written", "書いた");
  assert.equal(h.c.snapshot().result.className, "notice ok");
  await press("failed", "書けなかった");
  assert.deepEqual(h.c.snapshot().result, { kind: "failed", className: "notice error", text: "反映できませんでした：壊れた" });
  await press("written", "書き直した");
  assert.deepEqual(h.c.snapshot().result, { kind: "written", className: "notice ok", text: "承認を反映しました（コミット：0123456）。反映後の内容も確認しました。" });
  assert.equal(plan.length, 0);
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
  assert.match(h.c.snapshot().result.text, /^i0002 は確認が必要な状態のままです/);
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

test("CX-T231 読み直しの失敗: 手動の読み直しでも、書いた後の読み直しでも、state=error と文面を出し、描いた欄は残す。投げ捨てない", async () => {
  const unhandled: unknown[] = [];
  const onUnhandled = (e: unknown) => void unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    const h = harness({ ops: { approveFamily: async () => WRITTEN } });
    let broken = false;
    const get = h.env.storage.get;
    (h.env.storage as { get: typeof get }).get = async (k) => {
      if (broken && String(k) === "repos") throw new Error("storage 壊れた");
      return get(k);
    };
    await h.c.refresh();
    assert.equal(h.c.snapshot().state, "done");
    assert.deepEqual([...h.c.snapshot().sections.keys()], [KEY]);
    broken = true;
    // main.tsx の「更新」と同じ呼び方（void controller.refresh()）
    void h.c.refresh();
    await until(() => h.c.snapshot().state === "error", "手動の読み直しの失敗");
    assert.equal(h.c.snapshot().status.text, "ボードを読み込めませんでした：storage 壊れた");
    assert.deepEqual([...h.c.snapshot().sections.keys()], [KEY]);
    // 書いた後の読み直しの失敗
    broken = false;
    await h.c.refresh();
    assert.equal(h.c.snapshot().state, "done");
    const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
    broken = true;
    h.c.actions.approve(b, family());
    await until(() => h.log.confirm.length === 1 && !h.c.snapshot().busy && h.c.snapshot().state === "error", "書いた後の読み直しの失敗");
    assert.equal(h.c.snapshot().status.text, "ボードを読み込めませんでした：storage 壊れた");
    assert.equal(h.c.snapshot().result.kind, "written");
    assert.deepEqual([...h.c.snapshot().sections.keys()], [KEY]);
    // 次の読み直しで戻る
    broken = false;
    await h.c.refresh();
    assert.equal(h.c.snapshot().state, "done");
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});

test("CX-T232 境界の外で描画が落ちたら、controller が #status に言って state を error にする", () => {
  const h = harness();
  h.c.reportRenderError(new Error("描けない"));
  assert.equal(h.c.snapshot().state, "error");
  assert.equal(h.c.snapshot().status.text, "ボードを表示できませんでした：描けない");
});

const PREVIEW = {
  version: 2,
  parent: "i0001",
  phase: 1,
  mr: { number: 3, url: "https://example.test/pr/3" },
  digest: "v2.aaa",
  threads: [{ key: "k0", url: "", path: "a.ts", line: 1, body: "指摘", version: "v", last_comment: { id: "", at: "", author: "" } }],
};

test("CX-T273 決める: 開くとプレビューを読み、確定は鍵→行き先とダイジェストを ops.decide に渡す。成功の一言と画面の閉じ方", async () => {
  const seen: unknown[][] = [];
  let out: DecideOutcome = { kind: "written", oid: "0123456789abcdef", reviewed: false, followup: "i0001-f1", issues: ["https://example.test/issues/9"] };
  const h = harness({
    ops: {
      decidePreview: async (_r, family, phase) => (seen.push(["preview", family, phase]), PREVIEW),
      decide: async (_r, family, phase, choices, digest) => (seen.push(["decide", family, phase, choices, digest]), out),
    },
  });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.decideOpen?.(b, family(), 1);
  assert.deepEqual(h.c.snapshot().decide, { repo: KEY, token: 1, family: "i0001", phase: 1, preview: null, deciding: false, notice: "" });
  await until(() => h.c.snapshot().decide?.preview !== null, "プレビュー");
  assert.deepEqual(seen, [["preview", "i0001", 1]]);
  // 決める画面を開いている間は自動の読み直しを飛ばす
  h.c.actions.decideConfirm?.(b, family(), 1, { k0: "issue" }, "v2.aaa");
  await until(() => h.c.snapshot().result.kind === "written" && !h.c.snapshot().busy, "確定");
  assert.deepEqual(seen.at(-1), ["decide", "i0001", 1, { k0: "issue" }, "v2.aaa"]);
  assert.equal(h.c.snapshot().decide, undefined);
  assert.match(h.c.snapshot().result.text, /行き先を反映しました。続きの子：i0001-f1。作った issue：https:\/\/example.test\/issues\/9（コミット：0123456）/);
  // 見せた指摘が変わった: 画面は開いたまま読み直して選び直させる
  out = { kind: "mismatch", message: "指摘が増えました" };
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide?.preview !== null && h.c.snapshot().decide !== undefined, "再度開く");
  h.c.actions.decideConfirm?.(b, family(), 1, { k0: "fix" }, "v2.aaa");
  await until(() => !h.c.snapshot().busy && (h.c.snapshot().decide?.notice ?? "") !== "", "mismatch");
  assert.match(h.c.snapshot().decide?.notice ?? "", /指摘が変わりました（指摘が増えました）。.*選び直してください/);
  assert.equal(h.c.snapshot().decide?.deciding, false);
  h.c.actions.decideCancel?.();
  assert.equal(h.c.snapshot().decide, undefined);
});

test("CX-T274 決める: ホストへの書き込みが途中で止まったら、済んだ分と理由と読み直しの案内を出し、画面を閉じる。断れば何も書かない", async () => {
  let decides = 0;
  const h = harness({
    confirm: false,
    ops: {
      decidePreview: async () => PREVIEW,
      decide: async () => (
        decides++,
        { kind: "halted", done: { issues: ["https://example.test/issues/9"], replied: 1, resolved: 0 }, message: "解決に失敗（403）" } as DecideOutcome
      ),
    },
  });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide?.preview !== null, "プレビュー");
  h.c.actions.decideConfirm?.(b, family(), 1, { k0: "issue" }, "v2.aaa");
  await until(() => !h.c.snapshot().busy, "断る");
  assert.equal(decides, 0);
  assert.notEqual(h.c.snapshot().decide, undefined);
  (h.env as { confirm: (m: string) => boolean }).confirm = () => true;
  h.c.actions.decideConfirm?.(b, family(), 1, { k0: "issue" }, "v2.aaa");
  await until(() => h.c.snapshot().result.kind === "halted" && !h.c.snapshot().busy, "途中で止まる");
  assert.equal(decides, 1);
  const r = h.c.snapshot().result;
  assert.equal(r.className, "notice error");
  assert.match(r.text, /作った issue：https:\/\/example.test\/issues\/9。返信 1 件・解決 0 件）。止まった理由：解決に失敗（403）。.*未解決の指摘だけ決め直してください/);
  assert.equal(h.c.snapshot().decide, undefined);
});

test("CX-T295 決める: ホストへ書き込み済みの失敗（conflict・refused・changed・mismatch）は、画面を閉じる。書いていない失敗と mismatch は開いたまま", async () => {
  const cases: [DecideOutcome, boolean][] = [
    [{ kind: "conflict", message: "ホストは済み", hostWritten: true }, true],
    [{ kind: "refused", message: "ホストは済み", hostWritten: true }, true],
    [{ kind: "changed", message: "ホストは済み", hostWritten: true }, true],
    [{ kind: "mismatch", message: "ホストは済み", hostWritten: true }, true],
    [{ kind: "refused", message: "何も書いていない" }, false],
  ];
  for (const [out, closes] of cases) {
    const h = harness({ ops: { decidePreview: async () => PREVIEW, decide: async () => out } });
    await h.c.refresh();
    const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
    h.c.actions.decideOpen?.(b, family(), 1);
    await until(() => h.c.snapshot().decide?.preview !== null, "プレビュー");
    h.c.actions.decideConfirm?.(b, family(), 1, { k0: "issue" }, "v2.aaa");
    await until(() => !h.c.snapshot().busy && h.c.snapshot().result.text !== "", "確定");
    assert.equal(h.c.snapshot().decide === undefined, closes, JSON.stringify(out));
    assert.match(h.c.snapshot().result.text, /ホストは済み|何も書いていない/);
  }
});

test("CX-T296 決める: 再利用した issue は『作った』に数えず、増えた指摘が判断待ちなら文面で言う", () => {
  const text = sayDecide({ kind: "written", oid: "", reviewed: false, followup: "", issues: [], reusedIssues: ["https://example.test/issues/3"], pendingExtra: 2 }).text;
  assert.match(text, /行き先は反映しましたが、見せた後に増えた指摘 2 件が判断待ちで、フェーズはレビュー済みにしていません/);
  assert.match(text, /既にあった issue 1 件：https:\/\/example.test\/issues\/3/);
  assert.ok(!text.includes("作った issue"));
  const halted = sayDecide({ kind: "halted", done: { issues: [], reusedIssues: ["https://example.test/issues/3"], replied: 0, resolved: 0 }, message: "x" }).text;
  assert.match(halted, /作った issue：なし。既にあった issue 1 件/);
});

const settled = (h: ReturnType<typeof harness>) => !h.c.snapshot().busy && h.c.snapshot().state === "done";
const previewOf = (over: Partial<typeof PREVIEW> = {}) => ({ ...PREVIEW, ...over });

test("CX-T275 決める: 既定で実物に配線され、入口の操作が付く", () => {
  assert.equal(typeof harness().c.actions.decideOpen, "function");
  const w = harness({ ops: { decidePreview: async () => PREVIEW, decide: async () => ({ kind: "failed", message: "x" }) } });
  assert.equal(typeof w.c.actions.decideOpen, "function");
});

test("CX-T276 決める: 開くたびの番号で、遅れて届いた読み取りの結果と失敗を、開き直した画面や別のフェーズに付けない。状態はリポジトリの鍵を持つ", async () => {
  const waits: { resolve: (p: typeof PREVIEW) => void; reject: (e: Error) => void }[] = [];
  const h = harness({
    ops: {
      decidePreview: () => new Promise((resolve, reject) => void waits.push({ resolve, reject })),
      decide: async () => ({ kind: "failed", message: "x" }),
    },
  });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.decideOpen?.(b, family(), 1);
  assert.equal(h.c.snapshot().decide?.repo, KEY);
  await until(() => waits.length === 1, "1 回目");
  h.c.actions.decideCancel?.();
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => waits.length === 2, "2 回目");
  waits[1].resolve(previewOf({ digest: "v2.new" }));
  await until(() => h.c.snapshot().decide?.preview !== null, "2 回目の結果");
  waits[0].resolve(previewOf({ digest: "v2.old" }));
  await new Promise((r) => setImmediate(r));
  assert.equal(h.c.snapshot().decide?.preview?.digest, "v2.new");
  // 別のフェーズを開いたあとに、前のフェーズの失敗が届く
  h.c.actions.decideCancel?.();
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => waits.length === 3, "3 回目");
  h.c.actions.decideCancel?.();
  h.c.actions.decideOpen?.(b, family(), 2);
  await until(() => waits.length === 4, "4 回目");
  waits[2].reject(new Error("前の失敗"));
  await new Promise((r) => setImmediate(r));
  assert.equal(h.c.snapshot().decide?.phase, 2);
  assert.equal(h.c.snapshot().decide?.notice, "");
});

test("CX-T277 決める: 確認文に内訳と続きの子の注意が入る。0 件は従来のレビュー済みと同じ文面。失敗は画面を開いたまま理由を残す。要確認なら開かない", async () => {
  let out: DecideOutcome = { kind: "refused", message: "権限が足りません" };
  const two = previewOf({
    threads: [
      { ...PREVIEW.threads[0], key: "a" },
      { ...PREVIEW.threads[0], key: "b" },
      { ...PREVIEW.threads[0], key: "c" },
    ],
  });
  let shown: typeof PREVIEW = two;
  const called: string[] = [];
  const h = harness({ ops: { decidePreview: async () => shown, decide: async (_r, _f, _p, c) => (called.push(JSON.stringify(c)), out) } });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide?.preview !== null, "プレビュー");
  // 未知の鍵・足りない鍵は ops に渡さない
  h.c.actions.decideConfirm?.(b, family(), 1, { a: "fix", b: "keep", z: "issue" }, "v2.aaa");
  await until(() => settled(h), "照合");
  assert.deepEqual(called, []);
  assert.match(h.c.snapshot().decide?.notice ?? "", /行き先を選んでいない指摘か/);
  h.c.actions.decideConfirm?.(b, family(), 1, { a: "fix", b: "keep", c: "issue" }, "v2.aaa");
  await until(() => called.length === 1 && settled(h), "失敗");
  assert.match(h.log.confirm.at(-1) ?? "", /このフェーズで直す 1 件・issue に回す 1 件・対応しない 1 件）。「このフェーズで直す」が 1 件以上あるので、続きの子チケットを作り/);
  // 失敗は画面を開いたまま理由を残す（直後の読み直しでも消えない）
  assert.ok(h.c.snapshot().decide?.preview);
  assert.equal(h.c.snapshot().decide?.deciding, false);
  assert.match(h.c.snapshot().decide?.notice ?? "", /権限が足りません/);
  h.c.actions.decideCancel?.();
  // 0 件
  shown = previewOf({ threads: [] });
  out = { kind: "written", oid: "0123456789abcdef", reviewed: true, followup: "", issues: [] };
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide?.preview !== null, "0 件");
  h.c.actions.decideConfirm?.(b, family(), 1, {}, "v2.aaa");
  await until(() => called.length === 2 && settled(h), "0 件の確定");
  assert.match(h.log.confirm.at(-1) ?? "", /^i0001 のフェーズ 1 をレビュー済みにする。レビュー待ちの子チケットを done\/ に移動し/);
  assert.equal(h.c.snapshot().decide, undefined);
  // 要確認のあいだは開かない
  h.store.attention = { [KEY]: { i0001: "競合が残った" } };
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide === undefined && h.c.snapshot().result.kind === "refused", "要確認");
});

test("CX-T278 決める: 鍵が空・重複の指摘は画面を出さず読み取り不能。見せ直しで鍵と版が同じ指摘の選択を引き継ぐ", async () => {
  let shown = previewOf({ threads: [PREVIEW.threads[0], PREVIEW.threads[0]] });
  let out: DecideOutcome = { kind: "mismatch", message: "増えた" };
  const h = harness({ ops: { decidePreview: async () => shown, decide: async () => out } });
  await h.c.refresh();
  const b = h.c.snapshot().boards.get(KEY) as RepoBoard;
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => (h.c.snapshot().decide?.notice ?? "") !== "", "重複");
  assert.equal(h.c.snapshot().decide?.preview, null);
  assert.match(h.c.snapshot().decide?.notice ?? "", /鍵が重複/);
  h.c.actions.decideCancel?.();
  const t = (key: string, version: string) => ({ ...PREVIEW.threads[0], key, version });
  shown = previewOf({ threads: [t("a", "1"), t("b", "1")] });
  h.c.actions.decideOpen?.(b, family(), 1);
  await until(() => h.c.snapshot().decide?.preview !== null, "プレビュー");
  shown = previewOf({ digest: "v2.next", threads: [t("a", "1"), t("b", "2"), t("c", "1")] });
  h.c.actions.decideConfirm?.(b, family(), 1, { a: "keep", b: "issue" }, "v2.aaa");
  await until(() => h.c.snapshot().decide?.preview?.digest === "v2.next" && settled(h), "見せ直し");
  assert.deepEqual(h.c.snapshot().decide?.inherited, { a: "keep" });
  assert.match(h.c.snapshot().decide?.notice ?? "", /新しい指摘は「このフェーズで直す」に戻っています/);
  out = { kind: "written", oid: "0123456789abcdef", reviewed: true, followup: "", issues: [] };
});
