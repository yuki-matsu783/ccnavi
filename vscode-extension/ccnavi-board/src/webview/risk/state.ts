/**
 * リスク管理画面が編集中に持つもの。編集用にコピーした配点（`Draft`）と、開いている項目。
 *
 * 契約の `RiskForm` は配列だけを持つが、画面は**行ごとに動かない鍵**が要る（足す・消す・
 * 並べ替えの間、React が同じ行を同じ行として描き直せるように）。id はユーザが打つもので、
 * 空にも重複にもなるので鍵には使えない。鍵は画面の中だけのもので、拡張ホストへは渡さない。
 *
 * 開いている項目は Webview の state（`{ open: [id, …] }`）に保存する。**保存するのは id** で、
 * 鍵は画面を作り直すと変わるため。id が空の行は保存できない。
 */
import type { FactorForm, LevelName, RiskForm } from "../../core/risk-view.js";
import { getState, setState } from "../vscode.js";

/** 行 1 つ。`key` は画面の中だけの鍵で、拡張ホストへは渡さない */
export interface Row {
  readonly key: string;
  readonly factor: FactorForm;
}

export interface Draft {
  readonly levels: Readonly<Record<LevelName, string>>;
  readonly rows: readonly Row[];
}

/** 鍵を配る。1 枚の画面の中で数え上げる（`f1`、`f2`、…） */
export function keyer(): () => string {
  let seq = 0;
  return () => {
    seq += 1;
    return `f${seq}`;
  };
}

/** 拡張ホストが渡した配点を、行に鍵を付けたコピーにする */
export function draftOf(form: RiskForm, nextKey: () => string): Draft {
  return { levels: form.levels, rows: form.factors.map((factor) => ({ key: nextKey(), factor })) };
}

/** 拡張ホストへ返す形に戻す。鍵は落とす */
export function formOf(draft: Draft): RiskForm {
  return { levels: draft.levels, factors: draft.rows.map((row) => row.factor) };
}

/** 新しい項目。加点条件の既定は `lines_over` */
export function emptyFactor(): FactorForm {
  return { origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", message: "" };
}

/** 保存してある「開いていた項目の id」。型が違うものは空として扱う */
export function loadOpen(): ReadonlySet<string> {
  const saved = (getState() ?? {}) as { open?: unknown };
  const ids = Array.isArray(saved.open) ? saved.open.filter((id): id is string => typeof id === "string") : [];
  return new Set(ids);
}

/** 開いている項目を保存する。id が空の行は保存しない（次に開き直す手がかりが無い） */
export function saveOpen(draft: Draft, open: ReadonlySet<string>): void {
  const ids = draft.rows.filter((row) => open.has(row.key) && row.factor.id !== "").map((row) => row.factor.id);
  setState({ ...((getState() ?? {}) as object), open: ids });
}

/** 保存してある id から、いまの行の鍵に直す。画面を作り直したあとに開き直すため */
export function openedFromIds(draft: Draft, ids: ReadonlySet<string>): ReadonlySet<string> {
  return new Set(draft.rows.filter((row) => row.factor.id !== "" && ids.has(row.factor.id)).map((row) => row.key));
}
