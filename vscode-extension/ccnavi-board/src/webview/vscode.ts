/**
 * Webview が持つ VS Code の窓口。`acquireVsCodeApi` は 1 つの Webview で 1 度しか呼べないので、
 * ここで 1 度だけ呼んで配る。
 *
 * どの画面の部品からも読むので、特定の画面の契約（`BoardMessage` など）には依存しない。
 * 送り口は画面ごとに `poster<その画面のメッセージ>()` で型を付けて作る（`board/post.ts`）。
 */

export interface VsCodeApi {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
}

/** 画面が拡張ホストへ返すもの。何であれ `type` で見分ける */
export interface ScreenMessage {
  readonly type: string;
}

declare function acquireVsCodeApi(): VsCodeApi;

const api = acquireVsCodeApi();

/**
 * 画面の契約で型を付けた送り口を作る。画面は自分の契約に無いものを送れない
 * （`post({ type: "打ち間違い" })` は型で止まる）
 */
export function poster<M extends ScreenMessage>(): (message: M) => void {
  return (message) => api.postMessage(message);
}

/** 画面が覚えておくもの（ボードなら絞り込み・畳んだ列・列の幅）。HTML を作り直しても残る */
export function getState(): unknown {
  return api.getState();
}

export function setState(state: unknown): void {
  api.setState(state);
}
