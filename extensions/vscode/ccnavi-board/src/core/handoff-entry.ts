/**
 * 取り込み待ち画面へ進む入口の、各画面（ルール管理・リスク管理・フェーズ管理・テンプレート管理）に共通の契約。
 * 取り込み待ちはサイドパネルには並べず、設定を見ている画面からここを通って開く。
 * 画面ごとに作るのは、ページの `handoff` 欄とメッセージ `openHandoff` を契約に足すところまで。
 */
import { MAIN_TREE_KEY } from "./handoffmodel.js";

/** 取り込み待ち画面を開けるか。実行ファイルが `handoff` の機能を持たなければ `enabled` が偽で、理由を `reason` に入れる */
export interface HandoffEntry {
  readonly enabled: boolean;
  readonly reason: string;
}

/** 画面 → 拡張ホスト。取り込み待ち画面を開く */
export interface OpenHandoffMessage {
  readonly type: "openHandoff";
}

/** 開けない理由（古い実行ファイル）が無ければ開ける */
export function handoffEntryOf(reason: string | undefined): HandoffEntry {
  return { enabled: reason === undefined, reason: reason ?? "" };
}

/** 見ている設定の対象に合わせて開くツリーのキー。プロジェクトの設定ならそのプロジェクトのツリー、それ以外はワークスペース */
export function handoffTreeKey(target: { readonly kind: string; readonly name?: string }): string {
  return target.kind === "project" && target.name !== undefined ? `project:${target.name}` : MAIN_TREE_KEY;
}
