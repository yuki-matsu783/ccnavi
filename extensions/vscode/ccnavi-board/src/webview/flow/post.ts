/**
 * フロー編集画面が拡張ホストへ返す操作。送れるのは契約（`core/flow-view.ts` の `FlowMessage`）にあるものだけ。
 *
 * 案内の間（`muteForTour`）は、案内を閉じたことを知らせる `tourDone` のほか、何も送らない。案内は見本を描くだけで、
 * 実際の編集・保存・エージェントの下書きの操作を拡張ホストへ届けないため。画面の各操作も案内の間は動かさない（ここは最後の守り）。
 */
import { poster } from "../vscode.js";
import type { FlowMessage } from "../../core/flow-view.js";

const send = poster<FlowMessage>();
let muted = false;

/** 案内の間だけ真にする。真の間は `tourDone` 以外を送らない */
export function muteForTour(on: boolean): void {
  muted = on;
}

export function post(message: FlowMessage): void {
  if (muted && message.type !== "tourDone") {
    return;
  }
  send(message);
}
