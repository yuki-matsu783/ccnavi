/**
 * フロー編集画面の「元に戻す・やり直す」の履歴。画面（`webview/flow/App.tsx`）の `edit()` だけが積む。
 *
 * 積むのは**直す前のコピー**（`FlowDoc` は書き換えないコピーなので、そのまま持てばよい）。上限を超えたら古いほうから捨てる。
 *
 * 1 回の操作で 1 件にする。
 * - ドラッグ・線を引く・足す・消すは、呼び手が 1 回だけ `record` を呼ぶので 1 件
 * - 欄に打つ文字は 1 字ごとに `record` が呼ばれる。同じ欄（`key`）に `MERGE_MS` 以内に続けて打ったものは
 *   最初の 1 件にまとめる。欄からフォーカスが外れた・別の操作をした・戻した（`seal`）ら区切る
 *
 * 読み直し・保存・外で変わったときは、呼び手が `emptyHistory()` に替える（ファイルの中身が替わった後に、
 * 前の中身へ戻せてしまわないように）。
 *
 * ここには VS Code の API も DOM も node も入れない。
 */
import type { FlowDoc } from "./flow-doc.js";

/** 積む件数の上限 */
export const HISTORY_LIMIT = 100;
/** 同じ欄への打ち込みを 1 件にまとめる間（ミリ秒）。これより空けば別の 1 件 */
export const MERGE_MS = 1000;

export interface FlowHistory {
  readonly past: readonly FlowDoc[];
  readonly future: readonly FlowDoc[];
  /** 直前に積んだ打ち込みの欄。まとめてよいのはこれと同じ欄だけ */
  readonly key?: string;
  /** 直前に打った時刻 */
  readonly at?: number;
}

export function emptyHistory(): FlowHistory {
  return { past: [], future: [] };
}

export interface RecordOptions {
  /** 打ち込みの欄の名前（`ノードの id:欄` など）。渡したときだけ続けて打ったものをまとめる */
  readonly key?: string;
  /** いまの時刻（テストが渡す） */
  readonly now?: number;
  readonly limit?: number;
}

/**
 * 直す前のコピー `before` を積む。やり直しのリストは捨てる（戻してから別の操作をしたら、その先は無くなる）。
 * 同じ欄に続けて打っているときは積まずに時刻だけ進める（最初の 1 字の前のコピーが残る）。
 */
export function record(history: FlowHistory, before: FlowDoc, options: RecordOptions = {}): FlowHistory {
  const now = options.now ?? Date.now();
  const { key } = options;
  if (key !== undefined && history.key === key && history.at !== undefined && now - history.at <= MERGE_MS && history.past.length > 0) {
    return { past: history.past, future: [], key, at: now };
  }
  const limit = options.limit ?? HISTORY_LIMIT;
  const past = [...history.past, before];
  return { past: past.length > limit ? past.slice(past.length - limit) : past, future: [], ...(key === undefined ? {} : { key, at: now }) };
}

/** 打ち込みのまとまりを区切る（欄からフォーカスが外れた）。次に打つ字は別の 1 件になる */
export function seal(history: FlowHistory): FlowHistory {
  return history.key === undefined && history.at === undefined ? history : { past: history.past, future: history.future };
}

export function canUndo(history: FlowHistory): boolean {
  return history.past.length > 0;
}

export function canRedo(history: FlowHistory): boolean {
  return history.future.length > 0;
}

/** 1 件戻す。戻せなければ undefined。`current` はいまのコピーで、やり直しのリストに積む */
export function undo(history: FlowHistory, current: FlowDoc): { readonly history: FlowHistory; readonly doc: FlowDoc } | undefined {
  const doc = history.past[history.past.length - 1];
  if (doc === undefined) {
    return undefined;
  }
  return { history: { past: history.past.slice(0, -1), future: [...history.future, current] }, doc };
}

/** 1 件やり直す。やり直せなければ undefined */
export function redo(history: FlowHistory, current: FlowDoc): { readonly history: FlowHistory; readonly doc: FlowDoc } | undefined {
  const doc = history.future[history.future.length - 1];
  if (doc === undefined) {
    return undefined;
  }
  return { history: { past: [...history.past, current], future: history.future.slice(0, -1) }, doc };
}
