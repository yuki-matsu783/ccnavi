/**
 * 画面が覚えておくもの。表示の切り替え（足し算か、層ごとか）だけ。
 *
 * 置き場は Webview の state で、拡張が HTML を作り直しても残る。
 */
import { getState, setState } from "../vscode.js";

export type View = "sum" | "raw";

/** 覚えていた表示を読む。型が違うもの・知らないものは既定（足し算）を使う */
export function loadView(): View {
  const saved = (getState() ?? {}) as { view?: unknown };
  return saved.view === "raw" ? "raw" : "sum";
}

/** 表示を覚える。ほかのキーは残す */
export function saveView(view: View): void {
  setState({ ...((getState() ?? {}) as Record<string, unknown>), view });
}
