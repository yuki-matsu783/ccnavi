/**
 * 画面が覚えておくもの。絞り込み（プロジェクト・親・ユーザ待ちのみ・アーカイブ済みを表示するか）、折りたたんだ列、
 * ドラッグで決めた列の幅、選んだカード（右のパネルに出すもの）、開いた親の行。
 *
 * 置き場はWebviewのstateで、拡張がHTMLを作り直しても（非表示になって作り直されても）残る。
 */
import { FOLDED_FIRST } from "../../core/board.js";
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
   * 選んだカード（`cardKey`）。右のパネルに出す。空なら選んでいない。更新や列の移動では外さない。
   * プロジェクトと識別子の組で覚えるので、同じ識別子の別プロジェクトのカードとは別
   */
  readonly selected: string;
  /** 「フェーズを表示」で開いた親の行（`Lanes.tsx`の`laneKey`＝プロジェクトと親の識別子の組） */
  readonly opened: readonly string[];
}

export const EMPTY: ViewState = { project: "*", parent: "*", attention: false, archived: false, folded: FOLDED_FIRST, widths: {}, selected: "", opened: [] };

/** 選んだカードを覚えるキー。プロジェクトと識別子の組（どちらも文字列なのでJSONにすれば取り違えない） */
export function cardKey(card: { readonly project: string; readonly id: string }): string {
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
    folded: Array.isArray(saved.folded) ? saved.folded.filter((f): f is string => typeof f === "string") : EMPTY.folded,
    widths: Object.fromEntries(Object.entries(widths).filter((entry): entry is [string, number] => typeof entry[1] === "number" && entry[1] > 0)),
    selected: typeof saved.selected === "string" ? saved.selected : EMPTY.selected,
    opened: Array.isArray(saved.opened) ? saved.opened.filter((o): o is string => typeof o === "string") : EMPTY.opened,
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
    selected: state.selected,
    opened: state.opened,
  });
}
