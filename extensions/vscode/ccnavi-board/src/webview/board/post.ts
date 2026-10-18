/**
 * ボード画面が拡張ホストへ返す操作。
 * 送れるのは契約（`core/board-view.ts` の `BoardMessage`）に書いてあるものだけ。
 *
 * 案内の間（`mutePosts`）は、見本のカードやオーバーレイの操作が実際のチケットへ届かないよう、
 * 案内を閉じたことを伝える `tourDone` のほかは送らない。
 */
import { poster } from "../vscode.js";
import type { BoardMessage } from "../../core/board-view.js";

const send = poster<BoardMessage>();

let muted = false;

/** 案内の間だけ真にする。画面（`App`）が案内の出入りに合わせて切り替える */
export function mutePosts(on: boolean): void {
  muted = on;
}

export function post(message: BoardMessage): void {
  if (muted && message.type !== "tourDone") {
    return;
  }
  send(message);
}
