/**
 * Webview が持つ VS Code の窓口。`acquireVsCodeApi` は 1 つの Webview で 1 度しか呼べないので、
 * ここで 1 度だけ呼んで配る。
 */
import type { BoardMessage } from "../core/board-view.js";

export interface VsCodeApi {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const api = acquireVsCodeApi();

/** 人が押した操作を拡張ホストへ返す。判定も実行ファイルの呼び出しも画面はしない */
export function post(message: BoardMessage): void {
  api.postMessage(message);
}

/** 画面が覚えておくもの（絞り込み・畳んだ列・列の幅）。HTML を作り直しても残る */
export function getState(): unknown {
  return api.getState();
}

export function setState(state: unknown): void {
  api.setState(state);
}
