/**
 * リスク管理画面（React）を happy-dom で動かす。`test/helpers/projects.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/risk.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * 束ねるのは `pnpm test` の中の `scripts/bundle-webview.js`。この入口の綴り（`test/helpers/<画面の名前>.ts`）が
 * 約束で、`scripts/test-groups.js` はそれを辿って「この画面を読むグループ」を決める。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { readRisk } from "../../src/core/risk-doc.js";
import { renderRiskPage, type RenderOptions } from "../../src/core/risk-render.js";
import type { RiskData, RiskPage } from "../../src/core/risk-view.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

const SCRIPT_PATH = path.join(__dirname, "..", "..", "webview", "risk.js");

let cached: string | undefined;

/** 束ねた画面。無ければ何を通せばよいかを言う（テストだけ先に走らせたときに出る） */
export function riskScript(): string {
  if (cached === undefined) {
    if (!fs.existsSync(SCRIPT_PATH)) {
      throw new Error(`画面が束ねられていない: ${SCRIPT_PATH}（node scripts/bundle-webview.js を通す）`);
    }
    cached = fs.readFileSync(SCRIPT_PATH, "utf8");
  }
  return cached;
}

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function riskHtml(data: RiskData, options: Partial<RenderOptions> = {}): string {
  return renderRiskPage(data, { nonce: NONCE, script: riskScript(), ...options });
}

/** 見本の配点。4 つの当て方が 1 件ずつ */
export const RISK = `version: 1
factors:
  - id: big-diff
    points: 25
    lines_over: 300
    message: "行数が多い"
  - id: ci
    points: 35
    glob: ".github/**"
    max: 35
    message: "CI に触った"
  - id: sh
    points: 10
    script: ".ccnavi/common/scripts/risk.sh"
  - id: q
    points: 20
    judge: "テストの無い変更を含むか"
`;

/** 見本の中身。差し替えたいところだけ渡す */
export function page(overrides: Partial<RiskPage> = {}): RiskPage {
  return {
    root: "/ws",
    riskPath: ".ccnavi/common/risks.yml",
    exists: true,
    model: readRisk(RISK).model,
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: RiskData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(riskHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本の配点を開く。差し替えたいところだけ渡す */
export async function openRisk(overrides: Partial<RiskPage> = {}, initialState?: unknown): Promise<DomPage> {
  return openPage({ kind: "page", page: page(overrides) }, initialState);
}

/** 項目 1 行の中の要素。`li.factor[data-key=…]` の下だけを見る */
export function rowSelector(key: string): string {
  return `.factor[data-key="${key}"]`;
}
