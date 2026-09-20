/**
 * ルール設定画面（React）を happy-dom で動かす。`test/helpers/risk.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/rules.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * この入口の綴り（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { parseHooks } from "../../src/core/hooks.js";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRulesPage, type RenderOptions } from "../../src/core/rules-render.js";
import type { RulesData, RulesPage } from "../../src/core/rules-view.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

const SCRIPT_PATH = path.join(__dirname, "..", "..", "webview", "rules.js");

let cached: string | undefined;

/** 束ねた画面。無ければ何を通せばよいかを言う（テストだけ先に走らせたときに出る） */
export function rulesScript(): string {
  if (cached === undefined) {
    if (!fs.existsSync(SCRIPT_PATH)) {
      throw new Error(`画面が束ねられていない: ${SCRIPT_PATH}（node scripts/bundle-webview.js を通す）`);
    }
    cached = fs.readFileSync(SCRIPT_PATH, "utf8");
  }
  return cached;
}

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function rulesHtml(data: RulesData, options: Partial<RenderOptions> = {}): string {
  return renderRulesPage(data, { nonce: NONCE, script: rulesScript(), ...options });
}

/** 見本のルール。deny 2 件（1 件は刻みと渡す文を持つ）と、初回だけ渡す文を持つ ask 1 件 */
export const RULES = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: "push は人が行う"
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
    rulesPath: ".ccnavi/common/rules.yml",
    mode: "enable",
    model: readRules(RULES).model,
    hooks: parseHooks(JSON.stringify({ hooks: {} }), "settings"),
    hookFiles: { settings: true, settingsLocal: false },
    samplesPath: ".ccnavi/common/rule-samples.yml",
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: RulesData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(rulesHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本のルールを開く。差し替えたいところだけ渡す */
export async function openRules(overrides: Partial<RulesPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}

/** ルール 1 行の中の要素。id で名指しする（画面は `data-id` に今の id を出す） */
export function rowSelector(id: string): string {
  return `.rule[data-id="${id}"]`;
}
