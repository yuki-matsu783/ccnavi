/**
 * フェーズ管理画面が編集中に持つもの。種類の写し（`Draft`）と、開いている行。
 * 作りはリスク管理（`webview/risk/state.ts`）と同じで、鍵の綴りだけ `p1`、`p2`、… と違う。
 *
 * 契約の `PhasesForm` は並びだけを持つが、画面は**行ごとに動かない鍵**が要る（足す・消す・
 * 並べ替えの間、React が同じ行を同じ行として描き直せるように）。id は人が打つもので、
 * 空にも重複にもなるので鍵には使えない（この画面は重複を保存前に止める）。
 *
 * 開いている行の控えは Webview の state（`{ open: [id, …] }`）。移行前と同じ形にしてある。
 */
import type { PhaseForm, PhasesForm } from "../../core/phases-view.js";
import { getState, setState } from "../vscode.js";

/** 行 1 つ。`key` は画面の中だけの鍵で、拡張ホストへは渡さない */
export interface Row {
  readonly key: string;
  readonly phase: PhaseForm;
}

export interface Draft {
  readonly rows: readonly Row[];
}

/** 鍵を配る。1 枚の画面の中で数え上げる（`p1`、`p2`、…。移行前の綴りと同じ） */
export function keyer(): () => string {
  let seq = 0;
  return () => {
    seq += 1;
    return `p${seq}`;
  };
}

export function draftOf(form: PhasesForm, nextKey: () => string): Draft {
  return { rows: form.phases.map((phase) => ({ key: nextKey(), phase })) };
}

/** 拡張ホストへ返す形に戻す。鍵は落とす */
export function formOf(draft: Draft): PhasesForm {
  return { phases: draft.rows.map((row) => row.phase) };
}

/**
 * 新しい種類。既定の範囲は inherit（`scope: []` の種類を、glob を埋め忘れただけで作らないため）。
 * レビューは mr（足した種類が黙ってレビュー無しにならないように）。
 */
export function emptyPhase(): PhaseForm {
  return { origin: null, id: "", title: "", kind: "work", review: "mr", inherit: true, scope: [], deliverables: [], overlap: [], requires: [], agent: "", when: "" };
}

/**
 * 同じ id の種類。実行ファイルは後ろで黙って上書きするので、画面で止める。
 * 前後の空白は落として見る（`--lint` が見るのと同じ形）。
 */
export function duplicates(draft: Draft): ReadonlySet<string> {
  const seen = new Set<string>();
  const dup = new Set<string>();
  for (const row of draft.rows) {
    const id = row.phase.id.trim();
    if (seen.has(id)) {
      dup.add(id);
    }
    seen.add(id);
  }
  return dup;
}

/** 控えてある「開いていた種類の id」。型が違うものは空に倒す */
export function loadOpen(): ReadonlySet<string> {
  const saved = (getState() ?? {}) as { open?: unknown };
  const ids = Array.isArray(saved.open) ? saved.open.filter((id): id is string => typeof id === "string") : [];
  return new Set(ids);
}

/** 開いている行を控える。id が空の行は控えない（次に開き直す手がかりが無い） */
export function saveOpen(draft: Draft, open: ReadonlySet<string>): void {
  const ids = draft.rows.filter((row) => open.has(row.key) && row.phase.id !== "").map((row) => row.phase.id);
  setState({ ...((getState() ?? {}) as object), open: ids });
}

/** 控えてある id から、いまの行の鍵に直す */
export function openedFromIds(draft: Draft, ids: ReadonlySet<string>): ReadonlySet<string> {
  return new Set(draft.rows.filter((row) => row.phase.id !== "" && ids.has(row.phase.id)).map((row) => row.key));
}

// ---- 図（`Graph.tsx`）が控えるもの

/** 一覧と図の、いま見ているほう */
export type View = "list" | "graph";

/**
 * 人がドラッグで動かした点の位置。**`phases.yml` には書かない**（人が持つ設定に座標は入れない）。
 * 控えるのは Webview の state で、鍵は種類の id。id を打ち替えれば控えは捨てられる（`Graph.tsx`）。
 *
 * 形と、形を動かす純関数（`withSpot` / `keepSpots`）は `core/phases-graph.ts` にある。
 * ここ（`state.ts`）は `acquireVsCodeApi` を読むので、node のテストからは import できない。
 */
export type { Spots } from "../../core/phases-graph.js";
import type { Spots } from "../../core/phases-graph.js";

/** いま見ているほう。控えが無いか、綴りが違えば一覧 */
export function loadView(): View {
  const saved = (getState() ?? {}) as { view?: unknown };
  return saved.view === "graph" ? "graph" : "list";
}

export function saveView(view: View): void {
  setState({ ...((getState() ?? {}) as object), view });
}

/** 控えてある点の位置。Webview の state は型を持たず、値はそのまま SVG の座標になるので、数でない値はここで落とす */
export function loadSpots(): Spots {
  const saved = (getState() ?? {}) as { spots?: unknown };
  const raw = typeof saved.spots === "object" && saved.spots !== null ? (saved.spots as Record<string, unknown>) : {};
  const spots: Spots = {};
  for (const [id, value] of Object.entries(raw)) {
    const spot = value as { x?: unknown; y?: unknown };
    if (typeof spot?.x === "number" && typeof spot?.y === "number" && Number.isFinite(spot.x) && Number.isFinite(spot.y)) {
      spots[id] = { x: spot.x, y: spot.y };
    }
  }
  return spots;
}

export function saveSpots(spots: Spots): void {
  setState({ ...((getState() ?? {}) as object), spots });
}

