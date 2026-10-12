/**
 * ルール管理画面が拡張ホストへ返す操作。判定もせず、ファイルも書かない（判定は実行ファイル）。
 * 送れるのは契約（`core/rules-view.ts` の `RulesMessage`）に書いてあるものだけ。
 *
 * 案内の間（`mutePosts`）は、見本の上の操作が実際の設定へ届かないよう、案内を閉じたことを伝える `tourDone` のほかは送らない。
 */
import { poster } from "../vscode.js";
import type { RulesMessage } from "../../core/rules-view.js";

const send = poster<RulesMessage>();

let muted = false;

/** 案内の間だけ真にする。画面（`App`）が案内の出入りに合わせて切り替える */
export function mutePosts(on: boolean): void {
  muted = on;
}

export function post(message: RulesMessage): void {
  if (muted && message.type !== "tourDone") {
    return;
  }
  send(message);
}
