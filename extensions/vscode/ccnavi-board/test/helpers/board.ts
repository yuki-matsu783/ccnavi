/**
 * ボード画面（React）をhappy-domで動かす。
 *
 * 画面はバンドルした1本（`out/webview/board.js`）で、拡張はそれを`<script nonce>`に流し込む。
 * ここでも同じ1本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * バンドルするのは`pnpm test`の中の`scripts/bundle-webview.js`。
 *
 * Reactは押した直後には描き直さない。操作のあとは`await page.settle()`を挟んでから見る。
 */
import * as fs from "node:fs";
import * as path from "node:path";

import { buildBoard } from "../../src/core/board.js";
import type { ApprovalOverlay, BoardData } from "../../src/core/board-view.js";
import type { MergeView } from "../../src/core/merge.js";
import type { BoardJson } from "../../src/core/model.js";
import { renderBoardPage, type RenderOptions } from "../../src/core/render.js";
import { parseApprovePreview, type ApprovePreview } from "../../src/core/approvemodel.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage, type LoadOptions } from "./dom.js";
import { fixture } from "./fixture.js";

export const NONCE = "TEST-NONCE-123";

/** 画面のHTML。CSSやnonceのように、文字列のまま見たいものはこれを見る */
export function boardPage(data: BoardData, options: Partial<RenderOptions> = {}): string {
  return renderBoardPage(data, { nonce: NONCE, script: screenScript("board"), style: screenStyle("board"), ...options });
}

/** HTMLをhappy-domに読ませ、Reactがマウントし終わるまで待つ */
export async function openPage(data: BoardData, initialState?: unknown, options: LoadOptions = {}): Promise<DomPage> {
  const page = await loadPage(boardPage(data), initialState, options);
  await page.settle();
  return page;
}

/** 見本（`test/fixtures/board.json`）のボードを開く。差し替えたいところだけ渡す */
export async function openBoard(
  json: BoardJson = fixture(),
  extra: {
    readonly approval?: ApprovalOverlay;
    readonly filter?: string;
    readonly state?: unknown;
    readonly prepare?: LoadOptions["prepare"];
    /** 図（承認のオーバーレイの計画の図）を描かせるときは真。大きさを測れるようにする（`dom.ts`の`measure`） */
    readonly measure?: boolean;
    /** 計画編集タブに未保存の変更がある親（拡張ホストが渡す） */
    readonly unsaved?: readonly string[];
    /** マージ状況の確認の結果（拡張ホストが渡す） */
    readonly merge?: MergeView;
  } = {},
): Promise<DomPage> {
  return openPage(
    { kind: "board", board: buildBoard(json), approval: extra.approval, filter: extra.filter, ...(extra.unsaved === undefined ? {} : { unsaved: extra.unsaved }), ...(extra.merge === undefined ? {} : { merge: extra.merge }) },
    extra.state,
    { prepare: extra.prepare, measure: extra.measure },
  );
}

/** 承認画面の見本（`--agree --preview --json`の出力そのもの）。Python側のtests/ticket/test_approve_json.pyが書き出す */
export function approvePreview(): ApprovePreview {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "..", "test", "fixtures", "approve-preview.json"), "utf8");
  const parsed = parseApprovePreview(text);
  if (!parsed.ok) {
    throw new Error(parsed.error);
  }
  return parsed.value;
}
