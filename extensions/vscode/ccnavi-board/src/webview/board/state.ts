/**
 * 画面が覚えておくもの。絞り込み（プロジェクト・親・ユーザ待ちのみ・アーカイブ済みを表示するか）、折りたたんだ列、
 * ドラッグで決めた列の幅、詳細を開いているカード。
 *
 * 置き場は Webview の state で、拡張が HTML を作り直しても（非表示になって作り直されても）残る。
 */
import { getState, setState } from "../vscode.js";

export interface ViewState {
  readonly project: string;
  readonly parent: string;
  readonly attention: boolean;
  /** アーカイブ済みのチケット（手元の退避）を表示するか。既定は表示しない */
  readonly archived: boolean;
  readonly folded: readonly string[];
  readonly widths: Readonly<Record<string, number>>;
  /**
   * 詳細を開いているカード（`detailsKey`）。更新や列の移動では閉じない。カードごとに覚えるので、
   * 同じ識別子の別プロジェクトのカードとは別
   */
  readonly details: readonly string[];
}

export const EMPTY: ViewState = { project: "*", parent: "*", attention: false, archived: false, folded: [], widths: {}, details: [] };

/** 詳細の開閉を覚えるキー。プロジェクトと識別子の組（どちらも文字列なので JSON にすれば取り違えない） */
export function detailsKey(card: { readonly project: string; readonly id: string }): string {
  return JSON.stringify([card.project, card.id]);
}

/** 覚えていた値を読む。型が違うもの・知らないものは既定を使う */
export function loadState(): ViewState {
  const saved = (getState() ?? {}) as Partial<Record<keyof ViewState, unknown>>;
  const widths = typeof saved.widths === "object" && saved.widths !== null ? (saved.widths as Record<string, unknown>) : {};
  return {
    project: typeof saved.project === "string" ? saved.project : EMPTY.project,
    parent: typeof saved.parent === "string" ? saved.parent : EMPTY.parent,
    attention: saved.attention === true,
    archived: saved.archived === true,
    folded: Array.isArray(saved.folded) ? saved.folded.filter((f): f is string => typeof f === "string") : [],
    widths: Object.fromEntries(Object.entries(widths).filter((entry): entry is [string, number] => typeof entry[1] === "number" && entry[1] > 0)),
    details: Array.isArray(saved.details) ? saved.details.filter((d): d is string => typeof d === "string") : [],
  };
}

/**
 * 覚える。呼ぶのはユーザが動かしたときだけで、描くたびには書かない。
 * 読み直せなかった画面には絞り込みの部品が無く、そこで書くと覚えていた絞り込みが既定で上書きされる。
 */
export function saveState(state: ViewState): void {
  setState({
    project: state.project,
    parent: state.parent,
    attention: state.attention,
    archived: state.archived,
    folded: state.folded,
    widths: state.widths,
    details: state.details,
  });
}
