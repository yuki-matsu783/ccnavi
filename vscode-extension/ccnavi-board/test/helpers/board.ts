/**
 * ボード画面（React）を happy-dom で動かす。
 *
 * 画面は束ねた 1 本（`out/webview/board.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * 束ねるのは `pnpm test` の中の `scripts/bundle-webview.js`。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { buildBoard } from "../../src/core/board.js";
import type { ApprovalOverlay, BoardData } from "../../src/core/board-view.js";
import type { BoardJson } from "../../src/core/model.js";
import { renderBoardPage, type RenderOptions } from "../../src/core/render.js";
import { loadPage, type DomPage } from "./dom.js";
import { fixture } from "./fixture.js";

export const NONCE = "TEST-NONCE-123";

const SCRIPT_PATH = path.join(__dirname, "..", "..", "webview", "board.js");

let cached: string | undefined;

/** 束ねた画面。無ければ何を通せばよいかを言う（テストだけ先に走らせたときに出る） */
export function boardScript(): string {
  if (cached === undefined) {
    if (!fs.existsSync(SCRIPT_PATH)) {
      throw new Error(`画面が束ねられていない: ${SCRIPT_PATH}（node scripts/bundle-webview.js を通す）`);
    }
    cached = fs.readFileSync(SCRIPT_PATH, "utf8");
  }
  return cached;
}

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function boardPage(data: BoardData, options: Partial<RenderOptions> = {}): string {
  return renderBoardPage(data, { nonce: NONCE, script: boardScript(), ...options });
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: BoardData, initialState?: unknown): Promise<DomPage> {
  const page = await loadPage(boardPage(data), initialState);
  await page.settle();
  return page;
}

/** 見本（`test/fixtures/board.json`）のボードを開く。差し替えたいところだけ渡す */
export async function openBoard(
  json: BoardJson = fixture(),
  extra: { readonly approval?: ApprovalOverlay; readonly filter?: string; readonly state?: unknown } = {},
): Promise<DomPage> {
  return openPage({ kind: "board", board: buildBoard(json), approval: extra.approval, filter: extra.filter }, extra.state);
}
