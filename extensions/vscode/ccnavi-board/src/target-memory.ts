/**
 * 設定の4画面（ルール管理・リスク管理・フェーズ管理・テンプレート管理）で最後に選んだ対象を覚える。
 * 置き場はワークスペースごとの`workspaceState`。画面を開くときの対象は`openingTarget`（`core/targets.ts`）が決める。
 *
 * 覚えるのは、ユーザが画面の「設定」の欄で選び、切り替わったときだけ。既定で開いたこと（覚えた対象が選択肢に無かった）では書き換えない。
 * VS CodeのAPIに触れるので単体テストの対象外（docs/design/manual-checks.md）。
 */
import * as vscode from "vscode";

import { boardFeed } from "./board-feed.js";
import type { SettingsTarget } from "./core/screens.js";
import { openingTarget, targetFrom, targetKey } from "./core/targets.js";

const KEY = "ccnaviBoard.settingsTarget";

let memento: vscode.Memento | undefined;

/** `extension.ts`が1度だけ呼ぶ */
export function initTargetMemory(context: vscode.ExtensionContext): void {
  memento = context.workspaceState;
}

export function rememberTarget(target: SettingsTarget): void {
  void memento?.update(KEY, targetKey(target));
}

/** 画面を開くときの対象。既定で開いたときは、そのことを1行で知らせる */
export function targetToOpen(fallback: SettingsTarget): SettingsTarget {
  const saved = memento?.get<{ kind?: unknown; name?: unknown }>(KEY);
  const remembered = saved === undefined ? undefined : targetFrom({ kind: saved.kind, name: saved.name });
  const latest = boardFeed.latest();
  const { target, notice } = openingTarget(remembered, latest?.ok === true ? latest.board : undefined, fallback);
  if (notice !== "") {
    void vscode.window.showInformationMessage(notice);
  }
  return target;
}
