/**
 * ルール管理画面（React）を happy-dom で動かす。`test/helpers/risk.ts` と同じ役割。
 *
 * 画面はバンドルした 1 本（`out/webview/rules.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * この入口のパス（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import { parseHooks } from "../../src/core/hooks.js";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRulesPage, type RenderOptions } from "../../src/core/rules-render.js";
import type { RulesData, RulesPage } from "../../src/core/rules-view.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage, type LoadOptions } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function rulesHtml(data: RulesData, options: Partial<RenderOptions> = {}): string {
  return renderRulesPage(data, { nonce: NONCE, script: screenScript("rules"), style: screenStyle("rules"), ...options });
}

/** 見本のルール。deny 2 件（1 件は刻みとプロンプトを持つ）と、初回だけ渡すプロンプトを持つ ask 1 件 */
export const RULES = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: "push はユーザが行う"
  - id: no-rm
    match: Bash
    glob: "*rm -rf*"
    every: 4
    message: "消さない"
    additionalContext: "代わりに ccnavi-git.sh rm"
ask:
  - id: deps
    match: Write|Edit
    regex: "pyproject\\\\.toml"
    additionalContextOnce: "依存が変わる"
allow: []
`;

/** 見本の中身。差し替えたいところだけ渡す */
export function page(overrides: Partial<RulesPage> = {}): RulesPage {
  return {
    root: "/ws",
    rulesPath: ".ccnavi/config/common/rules.yml",
    mode: "enable",
    model: readRules(RULES).model,
    hooks: parseHooks(JSON.stringify({ hooks: {} }), "settings"),
    hookFiles: { settings: true, settingsLocal: false },
    expectationsPath: ".ccnavi/config/common/rule-expectations.yml",
    lock: { locked: false, reason: "", doing: [], turns: [] },
    ...overrides,
  };
}

/**
 * HTML を happy-dom に読ませ、React がマウントし終わるまで待つ。組み合わせ条件の図を見るテストは
 * `{ measure: true }` を渡す（渡さないと React Flow は点を隠したまま線を描かない。`dom.ts` の `measure`）
 */
export async function openPage(data: RulesData, initialState?: unknown, options: LoadOptions = {}): Promise<DomPage> {
  const dom = await loadPage(rulesHtml(data), initialState, options);
  await dom.settle();
  return dom;
}

/** 見本のルールを開く。差し替えたいところだけ渡す */
export async function openRules(overrides: Partial<RulesPage> = {}, initialState?: unknown, options: LoadOptions = {}): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState, options);
}

/** ルール 1 行の中の要素。id で名指しする（画面は `data-id` に今の id を出す） */
export function rowSelector(id: string): string {
  return `.rule[data-id="${id}"]`;
}
