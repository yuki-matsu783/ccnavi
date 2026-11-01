/**
 * フェーズ管理画面が編集中に持つもの。定義のコピー（`Draft`）と、開いている行。
 * 作りはリスク管理（`webview/risk/state.ts`）と同じで、キーの表記だけ`p1`、`p2`、… と違う。
 *
 * 契約の`PhasesForm`はリストだけを持つが、画面は行ごとに動かないキーが要る（足す・消す・
 * 並べ替えの間、Reactが同じ行を同じ行として描き直せるように）。idはユーザが打つもので、
 * 空にも重複にもなるのでキーには使えない（この画面は重複を保存前に止める）。
 *
 * 開いている行はWebviewのstate（`{ open: [id, …] }`）に残す。
 */
import type { CommonPhase, PhaseForm, PhasesForm } from "../../core/phases-view.js";
import { getState, setState } from "../vscode.js";

/** 行1つ。`key`は画面の中だけのキーで、拡張ホストへは渡さない */
export interface Row {
  readonly key: string;
  readonly phase: PhaseForm;
}

export interface Draft {
  readonly rows: readonly Row[];
}

/** キーを配る。1枚の画面の中で数え上げる（`p1`、`p2`、…） */
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

/** 拡張ホストへ返す形に戻す。キーは除く */
export function formOf(draft: Draft): PhasesForm {
  return { phases: draft.rows.map((row) => row.phase) };
}

/**
 * 新しい定義。既定の範囲はinherit（`scope: []`の定義を、globを埋め忘れただけで作らないため）。
 * レビューはtrue（足した定義が気づかないうちにレビュー無しにならないように）。
 */
export function emptyPhase(): PhaseForm {
  return { origin: null, id: "", title: "", kind: "work", review: true, inherit: true, scope: [], deliverables: [], agent: "", skill: "", when: "" };
}

/**
 * 同じidの定義。実行ファイルは後ろで何も出さずに上書きするので、画面で止める。
 * 前後の空白は除いて見る（`--lint`が見るのと同じ形）。
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

/** stateに残してある「開いていた定義のid」。型が違うものは空として扱う */
export function loadOpen(): ReadonlySet<string> {
  const saved = (getState() ?? {}) as { open?: unknown };
  const ids = Array.isArray(saved.open) ? saved.open.filter((id): id is string => typeof id === "string") : [];
  return new Set(ids);
}

/** 開いている行をstateに残す。idが空の行は残さない（次に開き直す手がかりが無い） */
export function saveOpen(draft: Draft, open: ReadonlySet<string>): void {
  const ids = draft.rows.filter((row) => open.has(row.key) && row.phase.id !== "").map((row) => row.phase.id);
  setState({ ...((getState() ?? {}) as object), open: ids });
}

/** stateに残してあるidから、いまの行のキーに直す */
export function openedFromIds(draft: Draft, ids: ReadonlySet<string>): ReadonlySet<string> {
  return new Set(draft.rows.filter((row) => row.phase.id !== "" && ids.has(row.phase.id)).map((row) => row.key));
}

/** 編集中の定義と共通の設定の定義の重なり */
export interface CommonClash {
  /** 共通の設定にも同じidがある定義のid。全欄が同じなら共通の側が使われ、違えば実行ファイルがerrorにする */
  readonly ids: ReadonlySet<string>;
  /** 共通の設定の別のidと表示名（title）が重なる定義のid。実行ファイルがerrorにするので、画面で止める */
  readonly titles: ReadonlySet<string>;
}

/**
 * 共通の設定の定義との重なり。表示名は空ならidを使う（実行ファイルと同じ）。前後の空白は除いて見る。
 * idが空の行は数えない（打っている途中の行を重なりとして赤くしない）。
 * 同じidの中身が同じかは、実行ファイルの`--lint`に任せる（共通の定義は全欄を持たない）。
 */
export function commonClashes(draft: Draft, common: readonly CommonPhase[]): CommonClash {
  const ids = new Set(common.map((c) => c.id));
  const titles = new Map(common.map((c) => [c.title || c.id, c.id] as const));
  const sameId = new Set<string>();
  const sameTitle = new Set<string>();
  for (const row of draft.rows) {
    const id = row.phase.id.trim();
    // idが空の行は、まだ打っている途中。重なりの印はidで行に戻すので、空のidを入れると
    // 空のidの行がみな赤くなる。共通の定義と比べるのはidを打ってから。
    if (id === "") {
      continue;
    }
    if (ids.has(id)) {
      sameId.add(id);
      continue;
    }
    const title = row.phase.title.trim() || id;
    const owner = titles.get(title);
    if (owner !== undefined && owner !== id) {
      sameTitle.add(id);
    }
  }
  return { ids: sameId, titles: sameTitle };
}
