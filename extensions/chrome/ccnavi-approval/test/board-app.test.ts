/**
 * ボードの描画（src/board/App.tsx。React）を jsdom の上で描く。controller は差し替え（状態を直に入れる）、
 * ここでは状態がどう画面に出るか（書く操作の最中に押せなくするボタン、入力中の値、ページの決まった枠）だけを見る。
 * 状態の移り変わりは test/board-controller.test.ts、欄の中身（DOM）は Pyodide で組んだボードを描く各試験が見る。
 */
import { after, afterEach, before, test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import type { Actions, BoardController, BoardSnapshot, Section } from "../src/board/controller.js";
import type { App as AppType } from "../src/board/App.js";
import { createRenderer } from "../src/core/sanitize.js";
import type { FamilyBoard, RepoBoard } from "../src/core/snapshot.js";
import type { RepoConfig } from "../src/core/settings.js";

const PAGE = `<!doctype html><html><body data-state="loading">
<header class="top"><h1>ccnavi 承認ボード</h1><button type="button" id="refresh">更新</button><button type="button" id="options">設定</button></header>
<p id="status" role="status">読み込み中…</p><div id="banner"></div><p id="result" role="status" aria-live="polite"></p><main id="boards"></main>
</body></html>`;
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

/** 書くボタンがひととおり出る親子のチケット（承認・取り下げ・レビュー済み） */
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
      withdrawable: [{ ticket: name, title: "親", problems: [] }],
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
  withdraw: (_r, f, t) => void pressed.push(`withdraw ${f.family.name} ${t}`),
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
  const frame = { body: doc.body, status: $("#status"), banner: $("#banner"), result: $("#result") };
  const md = createRenderer(dom.window as unknown as Parameters<typeof createRenderer>[0]);
  const r = createRoot($("#boards"));
  root = r;
  await act(async () => r.render(createElement(App, { controller: f.controller, md, frame })));
  return f;
}

const actionButtons = () => [...doc.querySelectorAll<HTMLButtonElement>("main button.action")];

test("CX-T226 書く操作の最中は、欄のボタン（button.action）をすべて押せなくする。最中に描き直した欄のボタンも。見出しの 2 つは巻き込まない", async () => {
  pressed.length = 0;
  const all = { board: board(), attention: { i0002: "競合が残った" }, issues: undefined };
  const f = await mount({ sections: sections(all), state: "done" });
  // 欄のボタンはすべて action（押せなくする対象から漏れるボタンが無い）
  assert.equal(actionButtons().length, doc.querySelectorAll("main button").length);
  assert.deepEqual(
    actionButtons().map((b) => b.dataset.action),
    ["recent-days", "approve", "withdraw", "review", "dismiss", "issues"],
  );
  assert.ok(actionButtons().every((b) => !b.disabled));
  await f.set({ busy: true, state: "writing" });
  assert.ok(actionButtons().every((b) => b.disabled));
  // 最中に「始める」の一覧を読み終えて欄を描き直しても、新しいボタンは押せない
  await f.set({ sections: sections({ ...all, issues: { list: [{ number: 12, title: "題", url: "https://example.test/12" }], error: "" } }) });
  assert.deepEqual(
    actionButtons().map((b) => b.dataset.action),
    ["recent-days", "approve", "withdraw", "review", "dismiss", "start"],
  );
  assert.ok(actionButtons().every((b) => b.disabled));
  // 書き終えて読み直しが始まるまで（busy は外れたが state は writing）も押せない
  await f.set({ busy: false });
  assert.ok(actionButtons().every((b) => b.disabled));
  await f.set({ state: "loading" });
  assert.ok(actionButtons().every((b) => !b.disabled));
  assert.ok(!$<HTMLButtonElement>("#refresh").disabled && !$<HTMLButtonElement>("#options").disabled);
  assert.equal(doc.querySelectorAll("header.top button.action").length, 0);
  // 押せば渡した操作を呼ぶ
  for (const b of actionButtons()) await act(async () => b.click());
  assert.deepEqual(pressed, ["days 7", "approve i0001", "withdraw i0001 i0001", "review i0001 1", "dismiss i0002", "start 12"]);
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

test("CX-T228 ページの決まった枠: #status の文字と data 属性、PAT の期限の帯、#result の data-kind と見た目、body の data-state", async () => {
  const f = await mount();
  assert.equal(doc.body.dataset.state, "loading");
  assert.equal($("#status").textContent, "読み込み中…");
  assert.equal($("#banner").children.length, 0);
  assert.equal($("#result").getAttribute("class"), null);
  await f.set({
    status: { text: "Pyodide 0.test: 起動 10 ms・読み込み 20 ms", bootMs: 10, importMs: 20, violations: 0 },
    banner: [{ level: "soon", className: "notice warn", text: "あと 3 日で切れる" }],
    result: { kind: "written", className: "notice ok", text: "承認を書いた" },
    state: "done",
  });
  assert.equal(doc.body.dataset.state, "done");
  assert.equal($("#status").outerHTML, '<p id="status" role="status" data-boot-ms="10" data-import-ms="20" data-violations="0">Pyodide 0.test: 起動 10 ms・読み込み 20 ms</p>');
  assert.equal($("#banner").innerHTML, '<p class="notice warn" data-level="soon">あと 3 日で切れる</p>');
  assert.equal($("#result").outerHTML, '<p id="result" role="status" aria-live="polite" data-kind="written" class="notice ok">承認を書いた</p>');
  // 書く途中で投げた: 赤で言う
  await f.set({ result: { kind: "failed", className: "notice error", text: "書けなかった: 壊れた" } });
  assert.equal($("#result").dataset.kind, "failed");
  assert.equal($("#result").className, "notice error");
  // 読み直しで一言を消す: data-kind を外し、見た目は前のまま
  await f.set({ result: { kind: undefined, className: "notice error", text: "" }, banner: [] });
  assert.equal($("#result").outerHTML, '<p id="result" role="status" aria-live="polite" class="notice error"></p>');
  assert.equal($("#banner").children.length, 0);
});
