/**
 * リスク管理画面が編集中に持つもの。配点の写し（`Draft`）と、開いている項目。
 *
 * 契約の `RiskForm` は並びだけを持つが、画面は**行ごとに動かない鍵**が要る（足す・消す・
 * 並べ替えの間、React が同じ行を同じ行として描き直せるように）。id は人が打つもので、
 * 空にも重複にもなるので鍵には使えない。鍵は画面の中だけのもので、拡張ホストへは渡さない。
 *
 * 開いている項目の控えは Webview の state（`{ open: [id, …] }`）。移行前と同じ形にしてある。
 * 入れ替えたときに、開いていた行が畳まれないように。**控えるのは id** で、鍵は画面を
 * 作り直すと変わるため。id が空の行は控えられない（移行前と同じ）。
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

/** 鍵を配る。1 枚の画面の中で数え上げる（`f1`、`f2`、…。移行前の綴りと同じ） */
export function keyer(): () => string {
  let seq = 0;
  return () => {
    seq += 1;
    return `f${seq}`;
  };
}

/** 拡張ホストが渡した配点を、行に鍵を付けた写しにする */
export function draftOf(form: RiskForm, nextKey: () => string): Draft {
  return { levels: form.levels, rows: form.factors.map((factor) => ({ key: nextKey(), factor })) };
}

/** 拡張ホストへ返す形に戻す。鍵は落とす */
export function formOf(draft: Draft): RiskForm {
  return { levels: draft.levels, factors: draft.rows.map((row) => row.factor) };
}

/** 新しい項目。当て方の既定は移行前と同じ `lines_over` */
export function emptyFactor(): FactorForm {
  return { origin: null, id: "", points: "", kind: "lines_over", value: "", max: "", message: "" };
}

/** 控えてある「開いていた項目の id」。型が違うものは空に倒す */
export function loadOpen(): ReadonlySet<string> {
  const saved = (getState() ?? {}) as { open?: unknown };
  const ids = Array.isArray(saved.open) ? saved.open.filter((id): id is string => typeof id === "string") : [];
  return new Set(ids);
}

/** 開いている項目を控える。id が空の行は控えない（次に開き直す手がかりが無い） */
export function saveOpen(draft: Draft, open: ReadonlySet<string>): void {
  const ids = draft.rows.filter((row) => open.has(row.key) && row.factor.id !== "").map((row) => row.factor.id);
  setState({ ...((getState() ?? {}) as object), open: ids });
}

/** 控えてある id から、いまの行の鍵に直す。画面を作り直したあとに開き直すため */
export function openedFromIds(draft: Draft, ids: ReadonlySet<string>): ReadonlySet<string> {
  return new Set(draft.rows.filter((row) => row.factor.id !== "" && ids.has(row.factor.id)).map((row) => row.key));
}
