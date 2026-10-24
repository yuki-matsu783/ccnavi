/**
 * ボードの描画（src/board/App.tsx。React）を jsdom の上で描く。controller は差し替え（状態を直に入れる）、
 * ここでは状態がどう画面に出るか（書く操作の最中に押せなくするボタン、入力中の値、ページの決まった枠）だけを見る。
 * 状態の移り変わりは test/board-controller.test.ts、欄の中身（DOM）は Pyodide で組んだボードを描く各試験が見る。
 */
import fs from "node:fs";
import path from "node:path";
import { after, afterEach, before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import { createBoardController, type Actions, type BoardController, type BoardEnv, type BoardOps, type BoardSnapshot, type Section } from "../src/board/controller.js";
import type { App as AppType } from "../src/board/App.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { FamilyBoard, RepoBoard } from "../src/core/snapshot.js";
import type { RepoConfig } from "../src/core/settings.js";
import type { Outcome } from "../src/core/write.js";
import type { DecideChoice, DecidePreview } from "../src/core/reviewed.js";
import { HERE } from "./helpers/python.js";

// 本物のページ（static/board.html）。見出しの 2 つのボタンや枠の ID は写しではなく実物で確かめる（scripts は動かさない）
const PAGE = fs.readFileSync(path.join(HERE, "static", "board.html"), "utf8");
const dom = new JSDOM(PAGE);
const g = globalThis as unknown as Record<string, unknown>;
const saved = new Map<string, PropertyDescriptor | undefined>();

function install(name: string, value: unknown): void {
  if (!saved.has(name)) saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

let App: typeof AppType;
let root: Root | undefined;
const doc = dom.window.document;
const $ = <T extends Element = HTMLElement>(selector: string): T => {
  const el = doc.querySelector<T>(selector);
  assert.ok(el, `${selector} が無い`);
  return el;
};

before(async () => {
  for (const name of ["window", "document", "navigator", "Event", "EventTarget", "HTMLElement", "HTMLInputElement", "Node"]) {
    install(name, (dom.window as unknown as Record<string, unknown>)[name]);
  }
  install("IS_REACT_ACT_ENVIRONMENT", true);
  ({ App } = await import("../src/board/App.js"));
});

after(() => {
  for (const [name, desc] of saved) {
    if (desc) Object.defineProperty(globalThis, name, desc);
    else delete g[name];
  }
  dom.window.close();
});

afterEach(async () => {
  const r = root;
  root = undefined;
  if (r) await act(async () => r.unmount());
});

const REPO: RepoConfig = { host: "github.com", owner: "acme", repo: "widgets", integration: "", recentDays: 7, extraBranches: [], project: "", workspace: "" };
const KEY = "github.com/acme/widgets";

/** 書くボタンがひととおり出る親子のチケット（承認・レビュー済み） */
function writable(name: string): FamilyBoard {
  return {
    family: { name, title: `見本 ${name}`, family: name },
    error: "",
    result: {
      closure: { families: [name], absent: [] },
      undecided: "",
      refused: "",
      batch: [{ ticket: `${name}-01`, title: "子", revision: false, phase: 1, path: `p/${name}-01.md`, overflow: [], body: "# 本文\n\n**太字**" }],
      text: "承認の画面",
      digest: "0123456789abcdef",
      only: null,
      write: { allowed: true, reason: "" },
      rejected: [],
      problems: [],
    },
    reviews: [{ phase: 1, mr: 3, children: [`${name}-01`], copy: { host: "github", mr: { url: "https://example.test/pr/3", number: 3 }, threads: [], reviews: [] }, problems: [], error: "" }],
  } as unknown as FamilyBoard;
}

function board(recentDays = 7): RepoBoard {
  return {
    repo: { ...REPO, recentDays },
    integration: { name: "main", source: "default", head: "abcdef0123456789" },
    compat: null,
    candidates: ["i0001", "i0002"],
    missingExtras: [],
    families: [writable("i0001"), writable("i0002")],
    error: "",
    stats: { rest: 1, graphql: 2, blobsFetched: 3, blobsCached: 4 },
  } as unknown as RepoBoard;
}

const pressed: string[] = [];
const actions: Actions = {
  approve: (_r, f) => void pressed.push(`approve ${f.family.name}`),
  review: (_r, f, n) => void pressed.push(`review ${f.family.name} ${n}`),
  loadIssues: () => void pressed.push("issues"),
  start: (_r, issue) => void pressed.push(`start ${issue.number}`),
  setRecentDays: (_r, v) => void pressed.push(`days ${v}`),
  dismiss: (_r, f) => void pressed.push(`dismiss ${f}`),
};

/** 状態を直に入れる controller */
function fake(initial: Partial<BoardSnapshot> = {}) {
  let snap: BoardSnapshot = {
    repos: [REPO],
    boards: new Map(),
    issues: new Map(),
    attention: {},
    sections: new Map(),
    banner: [],
    status: { text: "読み込み中…" },
    result: { kind: undefined, className: "", text: "" },
    busy: false,
    state: "loading",
    ...initial,
  };
  const listeners = new Set<() => void>();
  const controller: Pick<BoardController, "snapshot" | "subscribe" | "actions"> = {
    snapshot: () => snap,
    subscribe(l) {
      listeners.add(l);
      return () => void listeners.delete(l);
    },
    actions,
  };
  return {
    controller,
    async set(patch: Partial<BoardSnapshot>) {
      await act(async () => {
        snap = { ...snap, ...patch };
        for (const l of [...listeners]) l();
      });
    },
  };
}

function sections(...list: Section[]): Map<string, Section> {
  return new Map(list.map((s) => [`${s.board.repo.host}/${s.board.repo.owner}/${s.board.repo.repo}`, s]));
}

async function mount(initial: Partial<BoardSnapshot> = {}) {
  const f = fake(initial);
  await mountOn(f.controller);
  return f;
}

async function mountOn(controller: Pick<BoardController, "snapshot" | "subscribe" | "actions">) {
  const frame = { body: doc.body, status: $("#status"), banner: $("#banner"), result: $("#result") };
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  const r = createRoot($("#boards"));
  root = r;
  await act(async () => r.render(createElement(App, { controller, md, frame })));
}

const actionButtons = () => [...doc.querySelectorAll<HTMLButtonElement>("main button.action")];

test("CX-T226 書く操作の最中は、欄のボタン（button.action）をすべて押せなくする。最中に描き直した欄のボタンも。見出しの 2 つは巻き込まない", async () => {
  pressed.length = 0;
  const all = { board: board(), attention: { i0002: "競合が残った" }, issues: undefined };
  const f = await mount({ sections: sections(all), state: "done" });
  // 欄のボタンはすべて action（押せなくする対象に入っていないボタンが無い）
  assert.equal(actionButtons().length, doc.querySelectorAll("main button").length);
  assert.deepEqual(
    actionButtons().map((b) => b.dataset.action),
    ["recent-days", "approve", "review", "dismiss", "issues"],
  );
  assert.ok(actionButtons().every((b) => !b.disabled));
  await f.set({ busy: true, state: "writing" });
  assert.ok(actionButtons().every((b) => b.disabled));
  // 最中に「始める」の一覧を読み終えて欄を描き直しても、新しいボタンは押せない
  await f.set({ sections: sections({ ...all, issues: { list: [{ number: 12, title: "題", url: "https://example.test/12" }], error: "" } }) });
  assert.deepEqual(
    actionButtons().map((b) => b.dataset.action),
    ["recent-days", "approve", "review", "dismiss", "start"],
  );
  assert.ok(actionButtons().every((b) => b.disabled));
  // 書き終えて読み直しが始まるまで（busy は外れたが state は writing）も押せない
  await f.set({ busy: false });
  assert.ok(actionButtons().every((b) => b.disabled));
  await f.set({ state: "loading" });
  assert.ok(actionButtons().every((b) => !b.disabled));
  assert.ok(!$<HTMLButtonElement>("#refresh").disabled && !$<HTMLButtonElement>("#options").disabled);
  assert.equal(doc.querySelectorAll("header.top button").length, 2);
  assert.equal(doc.querySelectorAll("header.top button.action").length, 0);
  // 押せば渡した操作を呼ぶ
  for (const b of actionButtons()) await act(async () => b.click());
  assert.deepEqual(pressed, ["days 7", "approve i0001", "review i0001 1", "dismiss i0002", "start 12"]);
});

test("CX-T227 直近の日数の入力欄は、自動の読み直しで欄を描き直しても、入力中の値を消さない", async () => {
  pressed.length = 0;
  const f = await mount({ sections: sections({ board: board(), attention: {}, issues: undefined }), state: "done" });
  const input = $<HTMLInputElement>("form.recent-days input[name=recentDays]");
  assert.equal(input.value, "7");
  input.value = "12";
  // 自動の読み直し: 同じリポジトリの欄を新しいボードで差し替える
  await f.set({ sections: sections({ board: board(), attention: {}, issues: undefined }) });
  assert.equal($<HTMLInputElement>("form.recent-days input[name=recentDays]"), input);
  assert.equal(input.value, "12");
  await act(async () => $<HTMLButtonElement>("form.recent-days button[type=submit]").click());
  assert.deepEqual(pressed, ["days 12"]);
});

test("CX-T228 ページの決まった枠: 本物の controller が出す状態を App が描く（#status の文字と data 属性、PAT の期限の帯、#result の data-kind と見た目、body の data-state）", async () => {
  let outcome: Outcome | Error = { kind: "written", oid: "0123456789abcdef", rounds: 1, lines: [] };
  const ops: Partial<BoardOps> = {
    collectRepo: async () => board(),
    approveFamily: async () => {
      if (outcome instanceof Error) throw outcome;
      return outcome;
    },
  };
  const c = createBoardController(stubEnv({ ops }));
  await mountOn(c);
  // 読み込み前
  assert.equal(doc.body.dataset.state, "loading");
  assert.equal($("#status").textContent, "読み込み中…");
  assert.equal($("#banner").children.length, 0);
  assert.equal($("#result").getAttribute("class"), null);
  await act(async () => c.refresh());
  assert.equal(doc.body.dataset.state, "done");
  assert.equal($("#status").outerHTML, '<p id="status" role="status" data-boot-ms="10" data-import-ms="20" data-violations="0">Pyodide 0.test：起動 10 ms・読み込み 20 ms</p>');
  assert.equal($("#banner").innerHTML, '<p class="notice warn" data-level="soon">あと 3 日で切れる</p>');
  assert.equal($("#result").getAttribute("class"), null);
  const press = async () => {
    await act(async () => $<HTMLButtonElement>("button[data-action=approve]").click());
    for (let i = 0; i < 200 && (c.snapshot().busy || c.snapshot().state !== "done"); i++) await act(async () => new Promise((r) => setImmediate(r)));
    assert.equal(c.snapshot().state, "done");
  };
  // 書けた: 緑
  await press();
  assert.equal($("#result").outerHTML, '<p id="result" role="status" aria-live="polite" data-kind="written" class="notice ok">承認を反映しました（コミット：0123456）。反映後の内容も確認しました。</p>');
  // 書く途中で投げた: 赤で言う
  outcome = new Error("壊れた");
  await press();
  assert.equal($("#result").dataset.kind, "failed");
  assert.equal($("#result").className, "notice error");
  assert.equal($("#result").textContent, "反映できませんでした：壊れた");
  // 読み直しで一言を消す: data-kind を外し、見た目は前のまま
  await act(async () => c.refresh());
  assert.equal($("#result").outerHTML, '<p id="result" role="status" aria-live="polite" class="notice error"></p>');
  // 次に書けたら緑に戻る
  outcome = { kind: "written", oid: "fedcba9876543210", rounds: 1, lines: [] };
  await press();
  assert.equal($("#result").dataset.kind, "written");
  assert.equal($("#result").className, "notice ok");
  assert.match($("#result").textContent ?? "", /^承認を反映しました（コミット：fedcba9）/);
});

/** 差し替えた外界で、本物の controller を動かす（board-controller.test.ts の harness の要約） */
function stubEnv(over: { ops?: Partial<BoardOps> } = {}): BoardEnv {
  const store: Record<string, unknown> = { repos: [REPO] };
  return {
    hosts: [{ id: "github.com", kind: "github", api: "https://api.github.com", graphql: "https://api.github.com/graphql", web: "https://github.com", label: "GitHub" }] as unknown as BoardEnv["hosts"],
    version: "0.0.0-test",
    send: async (message) => {
      const m = message as { kind: string; op?: string };
      if (m.kind === "token.status") return { ok: true, value: { set: true, notice: { level: "soon", text: "あと 3 日で切れる" } } };
      if (m.op === "pullApprovals") return { ok: true, value: [] };
      return { ok: false, error: `想定外の依頼: ${JSON.stringify(message)}` };
    },
    storage: {
      get: async (k) => {
        const key = String(k);
        return key in store ? { [key]: structuredClone(store[key]) } : {};
      },
      set: async (items) => void Object.assign(store, structuredClone(items)),
    },
    openOptions: () => undefined,
    confirm: () => true,
    startWorker: () => ({ call: async () => ({}), init: async () => ({ boot_ms: 10, import_ms: 20, total_ms: 30, pyodide: "0.test", violations: [] }) }),
    blobCache: async () => ({ get: async () => undefined, put: async () => undefined }),
    now: () => new Date("2026-01-01T00:00:00Z"),
    editing: () => false,
    timers: { doc: { visibilityState: "visible", addEventListener: () => undefined }, now: () => 0, setTimeout: () => 0, clearTimeout: () => undefined },
    ops: over.ops,
  };
}

/** `owner/repo` を指定した欄の材料。`bad` は描く途中で投げる（batch の overflow が無い） */
function sectionOf(repo: string, bad = false): Section {
  const b = board();
  const fam = writable("i0001");
  if (bad) (fam.result as unknown as { batch: unknown[] }).batch = [{ ticket: "x", title: "", revision: false, phase: null, path: "p", body: "b" }];
  return { board: { ...b, repo: { ...REPO, repo }, families: [fam] } as RepoBoard, attention: {}, issues: undefined };
}

test("CX-T229 欄を描く途中で投げても、その欄だけがエラー表示になり、ほかの欄は残る。次の状態で正しければ欄が戻る", async () => {
  const errors: string[] = [];
  const orig = console.error;
  console.error = (...a: unknown[]) => void errors.push(String(a[0]));
  try {
    const f = await mount({ sections: sections(sectionOf("a")), state: "done" });
    assert.equal(doc.querySelectorAll("section.repo").length, 1);
    await f.set({ sections: sections(sectionOf("a"), sectionOf("b", true)) });
    const repos = [...doc.querySelectorAll<HTMLElement>("section.repo")];
    assert.deepEqual(repos.map((r) => r.dataset.repo), ["github.com/acme/a", "github.com/acme/b"]);
    assert.ok(repos[0].querySelector("article.family"), "壊れていない欄は残る");
    const alert = repos[1].querySelector<HTMLElement>("p.notice.error[role=alert]");
    assert.ok(alert, "壊れた欄にエラー表示");
    assert.match(alert.textContent ?? "", /acme\/b の欄を表示できませんでした/);
    assert.equal(repos[1].querySelector("article.family"), null);
    // 壊れた欄があっても、ほかの状態変化は描く（購読は残る）
    await f.set({ status: { text: "続き" }, state: "done" });
    assert.equal($("#status").textContent, "続き");
    // 同じ材料のまま状態だけ変わっても、描き直しは試さない（投げ続けない）
    const before = errors.length;
    await f.set({ busy: true });
    await f.set({ busy: false });
    assert.equal(errors.length, before);
    // 次に流した snapshot が正しければ、欄が戻る
    await f.set({ sections: sections(sectionOf("a"), sectionOf("b")) });
    const back = [...doc.querySelectorAll<HTMLElement>("section.repo")];
    assert.equal(back.length, 2);
    assert.ok(back[1].querySelector("article.family"));
    assert.equal(doc.querySelectorAll("p.notice.error").length, 0);
  } finally {
    console.error = orig;
  }
});

test("CX-T230 直近の日数の入力欄は、保存されている値が変わった読み直しでは新しい値を出し、同じ値なら入力中の値と要素を保つ", async () => {
  const f = await mount({ sections: sections({ board: board(7), attention: {}, issues: undefined }), state: "done" });
  const input = $<HTMLInputElement>("form.recent-days input[name=recentDays]");
  assert.equal(input.value, "7");
  input.value = "12";
  // 同じ値（7）: 入力中の文字も要素も保つ
  await f.set({ sections: sections({ board: board(7), attention: {}, issues: undefined }) });
  assert.equal($<HTMLInputElement>("form.recent-days input[name=recentDays]"), input);
  assert.equal(input.value, "12");
  // 設定画面で 30 に変えた後の読み直し: 欄は 30
  await f.set({ sections: sections({ board: board(30), attention: {}, issues: undefined }) });
  assert.equal($<HTMLInputElement>("form.recent-days input[name=recentDays]").value, "30");
  pressed.length = 0;
  await act(async () => $<HTMLButtonElement>("form.recent-days button[type=submit]").click());
  assert.deepEqual(pressed, ["days 30"]);
});

/** 残った指摘 n 件の決める画面の材料 */
function preview(n: number, over: { digest?: string; unsupported?: Record<number, string> } = {}): DecidePreview {
  return {
    version: 2,
    parent: "i0001",
    phase: 1,
    mr: { number: 3, url: "https://example.test/pr/3" },
    digest: over.digest ?? "v2.aaa",
    threads: Array.from({ length: n }, (_, i) => ({
      key: `k${i}`,
      url: i === 0 ? "https://example.test/c/0" : "javascript:alert(1)",
      path: `src/f${i}.ts`,
      line: i + 1,
      body: `**指摘${i}**\n\n詳しい説明<script>x</script>`,
      version: "v",
      last_comment: { id: "", at: "", author: "" },
      ...(over.unsupported?.[i] ? { unsupported: over.unsupported[i] } : {}),
    })),
  };
}

/** 未解決の指摘が残るフェーズの欄 */
function withThreads(n: number): Section {
  const b = board();
  const fam = writable("i0001");
  const panel = fam.reviews![0] as unknown as { copy: { threads: unknown[] }; problems: string[] };
  panel.copy.threads = Array.from({ length: n }, (_, i) => ({ url: "https://example.test/c", path: "a.ts", line: i, body: "指摘", resolved: false }));
  panel.problems = ["未解決のスレッドが残っています"];
  return { board: { ...b, families: [fam] } as RepoBoard, attention: {}, issues: undefined };
}

const decided: string[] = [];
function decideActions(): Actions {
  return {
    ...actions,
    decideOpen: (_r, f, n) => void decided.push(`open ${f.family.name} ${n}`),
    decideConfirm: (_r, f, n, choices, digest) => void decided.push(`confirm ${f.family.name} ${n} ${JSON.stringify(choices)} ${digest}`),
    decideCancel: () => void decided.push("cancel"),
  };
}

const radio = (key: string, choice: DecideChoice) => $<HTMLInputElement>(`li[data-key=${key}] input[data-choice=${choice}]`);
const checkedOf = (key: string) => [...doc.querySelectorAll<HTMLInputElement>(`li[data-key=${key}] input`)].find((i) => i.checked)?.dataset.choice;

test("CX-T270 決める画面: 既定は全件 fix で確定が押せる。一括で全件が切り替わり、個別の選び直しが一括を上書きする。確定は鍵→行き先とダイジェストを渡す", async () => {
  decided.length = 0;
  const fakeActions = decideActions();
  const f = fake({ sections: sections(withThreads(2)), state: "done" });
  (f.controller as { actions: Actions }).actions = fakeActions;
  await mountOn(f.controller);
  // 閉じているときは入口のボタンだけ（従来の「レビュー済みにする」は出さない）
  assert.deepEqual([...doc.querySelectorAll<HTMLElement>("section.review button")].map((b) => b.dataset.action), ["decide-open"]);
  await act(async () => $<HTMLButtonElement>("button[data-action=decide-open]").click());
  assert.deepEqual(decided, ["open i0001 1"]);
  await f.set({ decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2), deciding: false, notice: "" } });
  assert.equal(checkedOf("k0"), "fix");
  assert.equal(checkedOf("k1"), "fix");
  assert.equal($<HTMLButtonElement>("button[data-action=decide-confirm]").disabled, false);
  // 本文は消毒して描く（script は残らない）。1 行目＋全文の details。http(s) 以外の URL はリンクにしない
  assert.equal(doc.querySelectorAll(".decide script").length, 0);
  assert.ok(doc.querySelector("li[data-key=k0] details summary"));
  assert.ok(doc.querySelector("li[data-key=k0] a.decide-url"));
  assert.equal(doc.querySelector("li[data-key=k1] a.decide-url"), null);
  // 一括
  await act(async () => $<HTMLButtonElement>("button[data-bulk=issue]").click());
  assert.deepEqual([checkedOf("k0"), checkedOf("k1")], ["issue", "issue"]);
  // 個別の選び直しが一括を上書きする
  await act(async () => radio("k1", "keep").click());
  assert.deepEqual([checkedOf("k0"), checkedOf("k1")], ["issue", "keep"]);
  await act(async () => $<HTMLButtonElement>("button[data-action=decide-confirm]").click());
  assert.deepEqual(decided.at(-1), 'confirm i0001 1 {"k0":"issue","k1":"keep"} v2.aaa');
  await act(async () => $<HTMLButtonElement>("button[data-action=decide-cancel]").click());
  assert.equal(decided.at(-1), "cancel");
});

test("CX-T271 決める画面: 見せ直す（ダイジェストが変わる）と選び直す。確定中は全部無効。1 件のときは一括の行を出さない", async () => {
  decided.length = 0;
  const f = fake({ sections: sections(withThreads(2)), state: "done", decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2), deciding: false, notice: "" } });
  (f.controller as { actions: Actions }).actions = decideActions();
  await mountOn(f.controller);
  await act(async () => radio("k0", "keep").click());
  assert.equal(checkedOf("k0"), "keep");
  // 同じダイジェストで描き直しても選びかけは保たれる
  await f.set({ decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2), deciding: false, notice: "" } });
  assert.equal(checkedOf("k0"), "keep");
  // 見せ直した
  await f.set({ decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2, { digest: "v2.bbb" }), deciding: false, notice: "指摘が変わりました" } });
  assert.equal(checkedOf("k0"), "fix");
  assert.match($(".decide .notice").textContent ?? "", /指摘が変わりました/);
  // 確定中
  await f.set({ decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2, { digest: "v2.bbb" }), deciding: true, notice: "" } });
  assert.ok([...doc.querySelectorAll<HTMLInputElement | HTMLButtonElement>(".decide input, .decide button")].every((e) => e.disabled));
  // 1 件
  await f.set({ decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(1), deciding: false, notice: "" } });
  assert.equal(doc.querySelector(".decide-bulk"), null);
});

test("CX-T272 決める画面: 扱えない指摘は選択肢を出さず理由を言い、確定を出さない。0 件は従来の「レビュー済みにする」", async () => {
  decided.length = 0;
  const f = fake({ sections: sections(withThreads(2)), state: "done", decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(2, { unsupported: { 1: "差分の外のコメントです" } }), deciding: false, notice: "" } });
  (f.controller as { actions: Actions }).actions = decideActions();
  await mountOn(f.controller);
  assert.equal(doc.querySelectorAll("li[data-key=k1] input").length, 0);
  assert.match($("li[data-key=k1] .decide-unsupported").textContent ?? "", /Chrome では扱えません。VS Code で。差分の外のコメントです/);
  assert.equal(doc.querySelector("button[data-action=decide-confirm]"), null);
  assert.equal(doc.querySelectorAll("li[data-key=k0] input").length, 3);
  // 指摘が 0 件のフェーズ（従来どおり）
  await f.set({ sections: sections({ board: board(), attention: {}, issues: undefined }), decide: undefined });
  assert.deepEqual([...doc.querySelectorAll<HTMLElement>("section.review button")].map((b) => b.dataset.action), ["review", "review"]);
});

test("CX-T279 決める画面: 入口の操作を渡さない画面では従来の「レビュー済みにする」が出る。同じ名前の親が別のリポジトリにあっても、開いたリポジトリにだけ画面が出る", async () => {
  const plain = withThreads(1);
  (plain.board.families[0].reviews![0] as unknown as { problems: string[] }).problems = [];
  const f = fake({ sections: sections(plain), state: "done" });
  await mountOn(f.controller);
  assert.deepEqual([...doc.querySelectorAll<HTMLElement>("section.review button")].map((b) => b.dataset.action), ["review"]);
  const other = withThreads(1);
  const second: Section = { ...other, board: { ...other.board, repo: { ...REPO, repo: "other" } } as RepoBoard };
  (f.controller as { actions: Actions }).actions = decideActions();
  await f.set({ sections: sections(withThreads(1), second), decide: { repo: KEY, token: 1, family: "i0001", phase: 1, preview: preview(1), deciding: false, notice: "" } });
  assert.equal(doc.querySelectorAll(".decide").length, 1);
  assert.ok(doc.querySelector('section.repo[data-repo="' + KEY + '"] .decide'));
  assert.equal(doc.querySelectorAll('section.repo[data-repo="github.com/acme/other"] button[data-action=decide-open]').length, 1);
});
