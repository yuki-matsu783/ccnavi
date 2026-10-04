/**
 * Pyodide の上の入口（`py/ccnavi_chrome.py`）との約束。判定はすべて Python が出し、
 * ここは形だけを持つ。TS で判定し直すと、手元の hook・lint と答えが 2 か所に分かれるため。
 */

/** 要求と答えの形の版。`ccnavi_chrome.SCHEMA` と揃える */
export const PY_SCHEMA = 1;

export interface Placement {
  readonly tickets: string;
  readonly approved: string;
  readonly integration_paths: readonly string[];
  readonly integration_files: readonly string[];
  readonly branch_paths: readonly string[];
  /** プロジェクトのリポジトリで、ワークスペースの統合先から読むもの */
  readonly workspace_paths: readonly string[];
  readonly workspace_files: readonly string[];
  /** プロジェクトのリポジトリで、プロジェクトの統合先から読むもの（閉じたもの・プロジェクトの層） */
  readonly project_paths: readonly string[];
}

export interface Branch {
  readonly head: string;
  readonly files: Record<string, string>;
  readonly binary: readonly string[];
  /** シンボリックリンク（読まない。Python が「決まらない」にする） */
  readonly links?: readonly string[];
}

export interface Integration {
  readonly name: string;
  readonly source: "setting" | "default";
  readonly head: string;
}

/** プロジェクトのリポジトリのワークスペースの統合先の中身（共通層・自身の層・設定・互換のマーカー） */
export interface Workspace {
  readonly integration: Integration;
  readonly files: Record<string, string>;
  readonly binary: readonly string[];
  readonly links: readonly string[];
}

export interface Snapshot {
  readonly integration: Integration;
  readonly branches: Record<string, Branch>;
  readonly absent: readonly string[];
  /** プロジェクト名（`projects/<名前>`）。ワークスペース自身なら無い */
  readonly project?: string;
  readonly workspace?: Workspace;
}

export interface Family {
  readonly name: string;
  readonly title: string;
  readonly state: string;
  readonly closed: boolean;
}

export interface Closure {
  readonly families: readonly string[];
  readonly need: readonly string[];
  readonly absent: readonly string[];
  readonly over_limit: boolean;
  readonly message: string;
}

export interface BatchEntry {
  readonly ticket: string;
  readonly title: string;
  readonly parent: string | null;
  readonly phase: number | null;
  readonly revision: boolean;
  readonly tree: string;
  readonly path: string;
  readonly overflow: readonly string[];
  readonly body: string;
}

export interface Withdrawable {
  readonly ticket: string;
  readonly title: string;
  /** 取り下げられない理由（空なら取り下げを出す。承認コミットが引けるかは押したときに見る） */
  readonly problems: readonly string[];
}

/** 依頼済みでまだレビュー済みでないフェーズ。通るかは `confirm` が決める */
export interface Reviewable {
  readonly phase: number;
  readonly mr: number;
  /** 依頼のマーカーのホスト（`github`・`gitlab`） */
  readonly host: string;
  readonly children: readonly string[];
}

export interface BoardResult {
  readonly family: string;
  readonly closure: Closure;
  readonly undecided?: string;
  readonly refused?: string;
  /** 書けるか（互換の版・書く先の名前。Python が決める） */
  readonly write?: { readonly allowed: boolean; readonly reason: string };
  readonly withdrawable?: readonly Withdrawable[];
  /** レビュー済みを付けられる候補 */
  readonly reviewable?: readonly Reviewable[];
  readonly batch?: readonly BatchEntry[];
  readonly text?: string;
  /** 見せた画面の指紋（承認のときに Python が読み直した中身と比べる） */
  readonly digest?: string;
  /** 承認するときに `plan` へ渡す絞り（指紋を出したときの絞り。null なら絞らない） */
  readonly only?: readonly string[] | null;
  readonly rejected?: readonly { readonly ticket: string; readonly problems: readonly string[] }[];
  readonly problems?: readonly string[];
}

export interface Compat {
  readonly extension: number;
  readonly repository: number | null;
  readonly same: boolean;
  readonly message: string;
}

/** 書くもの 1 つ（Changes の 1 行）。中身は本文か base64 */
export interface ChangeRow {
  readonly op: "create" | "update" | "delete";
  readonly path: string;
  readonly content?: string;
  readonly base64?: string;
}

export interface Written {
  readonly changes: Record<string, readonly ChangeRow[]> | null;
  readonly lines: readonly string[];
  readonly stopped: { readonly ticket: string; readonly reason: string } | null;
}

export interface PlanResult extends Written {
  readonly identifiers: readonly string[];
  readonly digest: string;
  readonly mismatch: unknown;
  readonly refused: string;
  readonly rejected: readonly { readonly ticket: string; readonly problems: readonly string[] }[];
}

export interface WithdrawResult extends Written {
  readonly problems: readonly string[];
}

export interface ConfirmResult extends Written {
  readonly problems: readonly string[];
  /** 依頼の後に親のブランチが動いていれば、比べる 2 つ（拡張が compare API で読んで呼び直す） */
  readonly need_compare?: { readonly base: string; readonly head: string };
}

/** compare API の変更の一覧（`github.compareFiles` の答え。`files` が null なら読めない・打ち切られた） */
export interface Compare {
  readonly base: string;
  readonly head: string;
  readonly files: readonly string[] | null;
}

/** 「始める」の答え。`problems` が空なら `identifier` の名前でブランチを作れる */
export interface StartResult {
  readonly identifier: string;
  readonly integration: string;
  readonly problems: readonly string[];
}

export interface Actor {
  readonly account: string;
  readonly version: string;
}

/** Python を呼ぶ関数。Worker でも、試験の Node の Pyodide でも同じ形 */
export type PyCall = (request: Record<string, unknown>) => Promise<Record<string, unknown>>;

export class PyError extends Error {}

async function ask<T>(call: PyCall, op: string, body: Record<string, unknown>, key: string): Promise<T> {
  const res = await call({ schema: PY_SCHEMA, op, ...body });
  if (typeof res.error === "string") {
    throw new PyError(res.error);
  }
  if (res.schema !== PY_SCHEMA) {
    throw new PyError(`Python の応答の形の版が合わない（${String(res.schema)}）`);
  }
  return (key === "" ? res : res[key]) as T;
}

export const py = {
  placement: (call: PyCall, settings: string | null) => ask<Placement>(call, "placement", { settings }, "placement"),
  families: (call: PyCall, settings: string | null, snapshot: Snapshot, candidates: readonly string[]) =>
    ask<Family[]>(call, "families", { settings, snapshot, candidates }, "families"),
  closure: (call: PyCall, settings: string | null, snapshot: Snapshot, family: string) =>
    ask<Closure>(call, "closure", { settings, snapshot, family }, ""),
  board: (call: PyCall, settings: string | null, snapshot: Snapshot, family: string) =>
    ask<BoardResult>(call, "board", { settings, snapshot, family }, ""),
  compat: (call: PyCall, snapshot: Snapshot) => ask<Compat>(call, "compat", { snapshot }, "compat"),
  plan: (
    call: PyCall,
    body: { settings: string | null; snapshot: Snapshot; family: string; only: readonly string[] | null; shown: { ids: readonly string[]; digest: string }; stamp: string; actor: Actor },
  ) => ask<PlanResult>(call, "plan", { ...body }, ""),
  withdraw: (
    call: PyCall,
    body: { settings: string | null; snapshot: Snapshot; family: string; ids: readonly string[]; prior: Record<string, string>; reason: string; stamp: string; actor: Actor },
  ) => ask<WithdrawResult>(call, "withdraw", { ...body }, ""),
  start: (call: PyCall, body: { settings: string | null; snapshot: Snapshot; issue: number; taken: readonly string[] }) =>
    ask<StartResult>(call, "start", { ...body }, ""),
  confirm: (
    call: PyCall,
    body: {
      settings: string | null;
      snapshot: Snapshot;
      family: string;
      phase: number;
      result: unknown;
      compare?: Compare;
      stamp: string;
      actor?: Actor;
    },
  ) => ask<ConfirmResult>(call, "confirm", { ...body }, ""),
};
