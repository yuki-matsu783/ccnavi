import { renderHandoffPage, type RenderOptions } from "../../src/core/handoff-render.js";
import type { HandoffData, HandoffEntry, HandoffList, HandoffPage } from "../../src/core/handoff-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function handoffHtml(data: HandoffData, options: Partial<RenderOptions> = {}): string {
  return renderHandoffPage(data, { nonce: NONCE, script: screenScript("handoff"), style: screenStyle("handoff"), ...options });
}

/** 取り込める新規のテンプレート 1 件。差し替えたいところだけ渡す */
export function entry(overrides: Partial<HandoffEntry> = {}): HandoffEntry {
  return {
    path: ".ccnavi/config/project/templates/login.yml",
    kind: "template",
    state: "new",
    board: true,
    selected: false,
    errors: [],
    warnings: [],
    diff: "+name: login\n+description: ログイン\n",
    entry_sha: "a".repeat(16),
    ...overrides,
  };
}

/** 各種類と状態を 1 本ずつ並べた見本の対象 */
export function sampleEntries(): HandoffEntry[] {
  return [
    entry(),
    entry({ path: ".ccnavi/config/project/rules.yml", kind: "layer-config", state: "changed", diff: "-a\n+b\n", entry_sha: "b".repeat(16) }),
    entry({ path: ".ccnavi/docs/guide.md", kind: "docs", state: "changed", warnings: ["見出しが無い"], diff: "-x\n+y\n", entry_sha: "c".repeat(16) }),
    entry({ path: ".claude/hooks/guard.sh", kind: "defense", state: "changed", board: false, diff: "-old\n+new\n", entry_sha: "d".repeat(16) }),
    entry({ path: ".ccnavi/docs/broken.md", kind: "docs", state: "new", errors: ["UTF-8 として読めない"], entry_sha: "f".repeat(16) }),
    entry({ path: ".ccnavi/docs/same.md", kind: "docs", state: "unchanged", entry_sha: "1".repeat(16) }),
  ];
}

export function list(entries: readonly HandoffEntry[] = sampleEntries(), overrides: Partial<HandoffList> = {}): HandoffList {
  return { version: 1, tree: "/ws", ignored: 2, ignored_paths: [{ path: "repro.sh", why: "not-allowed" }, { path: "linked", why: "linked-dir" }], entries, expect: "x".repeat(64), ok: true, ...overrides };
}

/** 見本の中身。差し替えたいところだけ渡す */
export function page(overrides: Partial<HandoffPage> = {}): HandoffPage {
  return {
    root: "/ws",
    generatedAt: "2026-10-18T00:00:00+0900",
    tree: "main:",
    trees: [
      { key: "main:", label: "ワークスペース" },
      { key: "project:lib", label: "プロジェクト lib" },
    ],
    list: list(),
    lock: { locked: false, reason: "", doing: [] },
    notices: [],
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: HandoffData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(handoffHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本の画面を開く。差し替えたいところだけ渡す */
export async function openHandoff(overrides: Partial<HandoffPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}
