/**
 * 画面が覚えておくもの。絞り込み（プロジェクト・親・要対応のみ）、畳んだ列、ドラッグで決めた列の幅。
 *
 * 置き場は Webview の state で、拡張が HTML を作り直しても（裏に回って作り直されても）残る。
 */
import { getState, setState } from "../vscode.js";

export interface ViewState {
  readonly project: string;
  readonly parent: string;
  readonly attention: boolean;
  readonly folded: readonly string[];
  readonly widths: Readonly<Record<string, number>>;
}

export const EMPTY: ViewState = { project: "*", parent: "*", attention: false, folded: [], widths: {} };

/** 覚えていた値を読む。型が違うもの・知らないものは既定を使う */
export function loadState(): ViewState {
  const saved = (getState() ?? {}) as Partial<Record<keyof ViewState, unknown>>;
  const widths = typeof saved.widths === "object" && saved.widths !== null ? (saved.widths as Record<string, unknown>) : {};
  return {
    project: typeof saved.project === "string" ? saved.project : EMPTY.project,
    parent: typeof saved.parent === "string" ? saved.parent : EMPTY.parent,
    attention: saved.attention === true,
    folded: Array.isArray(saved.folded) ? saved.folded.filter((f): f is string => typeof f === "string") : [],
    widths: Object.fromEntries(Object.entries(widths).filter((entry): entry is [string, number] => typeof entry[1] === "number" && entry[1] > 0)),
  };
}

/**
 * 覚える。呼ぶのは人が動かしたときだけで、描くたびには書かない。
 * 読み直せなかった画面には絞り込みの部品が無く、そこで書くと覚えていた絞り込みが既定で上書きされる。
 */
export function saveState(state: ViewState): void {
  setState({
    project: state.project,
    parent: state.parent,
    attention: state.attention,
    folded: state.folded,
    widths: state.widths,
  });
}
