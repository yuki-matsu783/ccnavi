/**
 * フェーズ管理画面（React）を happy-dom で動かす。`test/helpers/risk.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/phases.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * この入口の綴り（`test/helpers/<画面の名前>.ts`）が約束で、`scripts/test-groups.js` はそれを辿る。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import { renderPhasesPage, type RenderOptions } from "../../src/core/phases-render.js";
import type { PhasesData, PhasesPage } from "../../src/core/phases-view.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

const SCRIPT_PATH = path.join(__dirname, "..", "..", "webview", "phases.js");

let cached: string | undefined;

/** 束ねた画面。無ければ何を通せばよいかを言う（テストだけ先に走らせたときに出る） */
export function phasesScript(): string {
  if (cached === undefined) {
    if (!fs.existsSync(SCRIPT_PATH)) {
      throw new Error(`画面が束ねられていない: ${SCRIPT_PATH}（node scripts/bundle-webview.js を通す）`);
    }
    cached = fs.readFileSync(SCRIPT_PATH, "utf8");
  }
  return cached;
}

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function phasesHtml(data: PhasesData, options: Partial<RenderOptions> = {}): string {
  return renderPhasesPage(data, { nonce: NONCE, script: phasesScript(), ...options });
}

/** 見本の中身（雛形の 5 種類）。差し替えたいところだけ渡す */
export function page(overrides: Partial<PhasesPage> = {}): PhasesPage {
  return {
    root: "/ws",
    phasesPath: ".ccnavi/config/phases.yml",
    exists: true,
    model: readPhases(TEMPLATE_PHASES_TEXT).model,
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: PhasesData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(phasesHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本の種類を開く。差し替えたいところだけ渡す */
export async function openPhases(overrides: Partial<PhasesPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}

/** 種類 1 行の中の要素。`li.phase[data-key=…]` の下だけを見る */
export function rowSelector(key: string): string {
  return `.phase[data-key="${key}"]`;
}
