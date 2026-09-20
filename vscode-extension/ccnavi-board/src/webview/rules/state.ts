/**
 * ルール設定画面が編集中に持つもの。ルールの写し（`Draft`）と、開いている行・開いているタブ。
 *
 * 契約の `Sections` はタイプごとの並びだけを持つが、画面は**行ごとに動かない鍵**が要る
 * （足す・消す・タイプを移す・並べ替えの間、React が同じ行を同じ行として描き直せるように）。
 * id は人が打つもので、空にも重複にもなるので鍵には使えない。鍵は画面の中だけのもので、
 * 拡張ホストへ渡すのは、ファイルを選ぶとき（`pickFile` → `picked`）に行を名指しするときだけ。
 *
 * 控えは Webview の state（`{ open: [id, …], tab }`）。移行前と同じ形にしてある。
 * **控えるのは id** で、鍵は画面を作り直すと変わるため。id が空の行は控えられない（移行前と同じ）。
 */
import { SECTIONS, type RuleForm, type Section, type Sections } from "../../core/rules-view.js";
import { getState, setState } from "../vscode.js";

/** 行 1 つ。`key` は画面の中だけの鍵 */
export interface Row {
  readonly key: string;
  readonly rule: RuleForm;
}

export type Draft = Readonly<Record<Section, readonly Row[]>>;

export const EMPTY_DRAFT: Draft = { deny: [], ask: [], allow: [] };

/** 鍵を配る。1 枚の画面の中で数え上げる（`r1`、`r2`、…。移行前の綴りと同じ） */
export function keyer(): () => string {
  let seq = 0;
  return () => {
    seq += 1;
    return `r${seq}`;
  };
}

/** 拡張ホストが渡したルールを、行に鍵を付けた写しにする */
export function draftOf(sections: Sections, nextKey: () => string): Draft {
  const draft = {} as Record<Section, Row[]>;
  for (const section of SECTIONS) {
    draft[section] = sections[section].map((rule) => ({ key: nextKey(), rule }));
  }
  return draft;
}

/** 拡張ホストへ返す形に戻す。鍵は落とす */
export function sectionsOf(draft: Draft): Sections {
  const sections = {} as Record<Section, RuleForm[]>;
  for (const section of SECTIONS) {
    sections[section] = draft[section].map((row) => row.rule);
  }
  return sections;
}

/** 新しいルール。刻み（`every`）は空で始める。空は「刻み無し」＝ 当たるたびに渡す */
export function emptyRule(): RuleForm {
  return {
    origin: null,
    id: "",
    match: "",
    kind: "glob",
    pattern: "",
    message: "",
    additionalContext: "",
    additionalContextOnce: "",
    additionalContextFile: "",
    additionalContextOnceFile: "",
    every: "",
  };
}

/** 鍵からタイプと行を引く */
export function findRow(draft: Draft, key: string): { readonly section: Section; readonly row: Row } | undefined {
  for (const section of SECTIONS) {
    const row = draft[section].find((item) => item.key === key);
    if (row !== undefined) {
      return { section, row };
    }
  }
  return undefined;
}

/** 控えてある「開いていた行の id」。型が違うものは空に倒す */
export function loadOpen(): ReadonlySet<string> {
  const saved = (getState() ?? {}) as { open?: unknown };
  const ids = Array.isArray(saved.open) ? saved.open.filter((id): id is string => typeof id === "string") : [];
  return new Set(ids);
}

/** 控えてあるタブ。知らない値は既定（ルール） */
export function loadTab(): TabName {
  const saved = (getState() ?? {}) as { tab?: unknown };
  return saved.tab === "judge" || saved.tab === "hooks" ? saved.tab : "rules";
}

export const TABS = ["rules", "judge", "hooks"] as const;
export type TabName = (typeof TABS)[number];

/** 開いている行を控える。id が空の行は控えない（次に開き直す手がかりが無い） */
export function saveOpen(draft: Draft, open: ReadonlySet<string>): void {
  const ids: string[] = [];
  for (const section of SECTIONS) {
    for (const row of draft[section]) {
      if (open.has(row.key) && row.rule.id !== "") {
        ids.push(row.rule.id);
      }
    }
  }
  setState({ ...((getState() ?? {}) as object), open: ids });
}

/** 開いているタブを控える。開いた行の控えは消さない（同じ state に足す） */
export function saveTab(tab: TabName): void {
  setState({ ...((getState() ?? {}) as object), tab });
}

/** 控えてある id から、いまの行の鍵に直す。画面を作り直したあとに開き直すため */
export function openedFromIds(draft: Draft, ids: ReadonlySet<string>): ReadonlySet<string> {
  const keys = new Set<string>();
  for (const section of SECTIONS) {
    for (const row of draft[section]) {
      if (row.rule.id !== "" && ids.has(row.rule.id)) {
        keys.add(row.key);
      }
    }
  }
  return keys;
}
