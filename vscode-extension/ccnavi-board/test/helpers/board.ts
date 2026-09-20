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
import { parseApprovePreview, type ApprovePreview } from "../../src/core/approvemodel.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage } from "./dom.js";
import { fixture } from "./fixture.js";

export const NONCE = "TEST-NONCE-123";

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function boardPage(data: BoardData, options: Partial<RenderOptions> = {}): string {
  return renderBoardPage(data, { nonce: NONCE, script: screenScript("board"), style: screenStyle("board"), ...options });
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

/** 承認画面の見本（`--approve --preview --json` の出力そのもの）。Python 側の tests/ticket/test_approve_json.py が書き出す */
export function approvePreview(): ApprovePreview {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", "approve-preview.json"), "utf8");
  const parsed = parseApprovePreview(text);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}
