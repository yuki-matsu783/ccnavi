/**
 * 設定の4画面（ルール管理・リスク管理・フェーズ管理・テンプレート管理）で最後に選んだレイヤーを覚える。
 * 置き場はワークスペースごとの`workspaceState`。画面を開くときのレイヤーは`openingLayer`（`core/targets.ts`）が決める。
 *
 * 覚えるのは、ユーザが画面の「設定」の欄で選び、切り替わったときだけ。既定で開いたこと（覚えたレイヤーが選択肢に無かった）では書き換えない。
 * VS CodeのAPIに触れるので単体テストの対象外（docs/design/manual-checks.md）。
 */
import * as vscode from "vscode";

import { boardFeed } from "./board-feed.js";
import type { LayerTarget } from "./core/screens.js";
import { layerFrom, layerKey, openingLayer } from "./core/targets.js";

const KEY = "ccnaviBoard.layer";

let memento: vscode.Memento | undefined;

/** `extension.ts`が1度だけ呼ぶ */
export function initLayerMemory(context: vscode.ExtensionContext): void {
  memento = context.workspaceState;
}

export function rememberLayer(target: LayerTarget): void {
  void memento?.update(KEY, layerKey(target));
}

/** 画面を開くときのレイヤー。既定で開いたときは、そのことを1行で知らせる */
export function layerToOpen(fallback: LayerTarget): LayerTarget {
  const saved = memento?.get<{ kind?: unknown; name?: unknown }>(KEY);
  const remembered = saved === undefined ? undefined : layerFrom({ kind: saved.kind, name: saved.name });
  const latest = boardFeed.latest();
  const { target, notice } = openingLayer(remembered, latest?.ok === true ? latest.board : undefined, fallback);
  if (notice !== "") {
    void vscode.window.showInformationMessage(notice);
  }
  return target;
}
