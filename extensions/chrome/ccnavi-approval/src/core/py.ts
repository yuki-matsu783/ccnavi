/**
 * Pyodide の上の入口（`py/ccnavi_chrome.py`）との約束。判定はすべて Python が出し、
 * ここは形だけを持つ。TS で判定し直すと、手元の hook・lint と答えが 2 か所に分かれるため。
 */
import type { HostPlan } from "./decide-host.js";
import type { DecidePreview } from "./reviewed.js";

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
  /** プロジェクトのリポジトリで、プロジェクトの統合先から読むもの（閉じたもの・プロジェクトのレイヤー） */
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

/** プロジェクトのリポジトリのワークスペースの統合先の中身（共通レイヤー・自身のレイヤー・設定・互換のマーカー） */
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
  /**
   * 親子のチケットの識別子 → 親のブランチ名。閉包の先行の親子のチケットを読みに行くブランチの見当に使う
   */
  readonly hints?: Readonly<Record<string, string>>;
}

export interface Family {
  /** 親のブランチ名（拡張が読み書きするブランチ） */
  readonly name: string;
  /** 親子のチケットの識別子（ブランチ名の `/` を `-` に置き換えたもの。`/` を含むブランチ名と違う） */
  readonly family?: string;
  readonly title: string;
  readonly state: string;
  readonly closed: boolean;
  /** 親子のチケットが決まらない理由（同じ識別子になるブランチが 2 本以上ある など）。あればこの親子のチケットは判定しない */
  readonly conflict?: string;
}

export interface Closure {
  /** 親のブランチ名のリスト（先頭が判定する親子のチケット） */
  readonly families: readonly string[];
  /** ブランチ名 → 親子のチケットの識別子 */
  readonly idents?: Readonly<Record<string, string>>;
  /** 同じ識別子になる別のブランチが無いかを確かめるために読むブランチ（判定の入力には入れない） */
  readonly rivals?: readonly string[];
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
  /** 親子のチケットの識別子（親のブランチ名と違うことがある） */
  readonly ident?: string;
  readonly closure: Closure;
  readonly undecided?: string;
  readonly refused?: string;
  /** 書けるかどうか。互換の版と書く先の名前をもとに Python が決める */
  readonly write?: { readonly allowed: boolean; readonly reason: string };
  /** レビュー済みを付けられる候補 */
  readonly reviewable?: readonly Reviewable[];
  readonly batch?: readonly BatchEntry[];
  readonly text?: string;
  /** 見せた画面のダイジェスト（承認のときに Python が読み直した中身と比べる） */
  readonly digest?: string;
  /** 承認するときに `plan` へ渡す絞り（ダイジェストを出したときの絞り。null なら絞らない） */
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

/**
 * 通らなかった根拠の種別（付録 A の `CORE_*`）。通れば null。文面の頭の `[<コード>]` と同じもの。
 * 分岐は文面の言い回しではなく、このキーで決める
 */
export type ReasonCode = string | null;

/** 承認: 見せた一覧（識別子）と今の一覧が違う */
export const APPROVE_LIST_CHANGED = "CORE_APPROVE_LIST_CHANGED";
/** 承認: 識別子は同じで、見せた中身（ダイジェスト）が今と違う */
export const APPROVE_DIGEST_CHANGED = "CORE_APPROVE_DIGEST_CHANGED";

/** 文面の頭にコードを載せる（Python の `core_base.tagged` の、`ccnavi: ` で始まらない行の形） */
export function tagged(code: string, text: string): string {
  return `[${code}] ${text}`;
}

export interface PlanResult extends Written {
  readonly identifiers: readonly string[];
  readonly digest: string;
  readonly mismatch: unknown;
  readonly refused: string;
  readonly rejected: readonly { readonly ticket: string; readonly problems: readonly string[] }[];
  /** `CORE_APPROVE_*`（見せたものと違う・承認するものが無い） */
  readonly code: ReasonCode;
}

export interface ConfirmResult extends Written {
  readonly problems: readonly string[];
  /** `CORE_CONFIRM_*` */
  readonly code: ReasonCode;
  /** 依頼の後に親のブランチが動いていれば、比べる 2 つ（拡張が compare API で読んで呼び直す） */
  readonly need_compare?: { readonly base: string; readonly head: string };
  /** 手で done/ に動かされた子があれば、その done/ のファイル（リポジトリからの相対）。拡張が最初に加えたコミットの時刻を読んで呼び直す */
  readonly need_manual?: { readonly paths: readonly string[] };
}

/** `decide` の答え。`confirm` と同じ形に、見せる指摘（preview）かホストへ書く計画（apply）が付く */
export interface DecideResult extends ConfirmResult {
  /** mode=preview で通ったときだけ。`DecidePreview` と同じ形（`unsupported` は拡張が足す） */
  readonly preview: DecidePreview | null;
  /** mode=apply で通ったときだけ。拡張がこの順にホストへ書く */
  readonly host_plan: HostPlan | null;
  /** マーカーを置く（`fix` が無く、見せたあとに増えた指摘も無い） */
  readonly reviewed: boolean;
  /** 起こす続きの子の識別子（無ければ ""） */
  readonly followup: string;
  /** 見せたあとに増えた指摘のキー（未対応で残す） */
  readonly pending_extra: readonly string[];
  /** 見せた指摘と今の指摘の食い違い（`CORE_DECIDE_MISMATCH` のとき） */
  readonly mismatch: unknown;
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

/** 「始める」で既存の MR を採用してよいかの答え。`problems` が空なら `identifier` で続けられる */
export interface AdoptResult {
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
    throw new PyError(`Python の応答の形式の版が一致しません（${String(res.schema)}）`);
  }
  return (key === "" ? res : res[key]) as T;
}

export const py = {
  placement: (call: PyCall) => ask<Placement>(call, "placement", {}, "placement"),
  families: (call: PyCall, snapshot: Snapshot, candidates: readonly string[]) =>
    ask<Family[]>(call, "families", { snapshot, candidates }, "families"),
  closure: (call: PyCall, snapshot: Snapshot, family: string) =>
    ask<Closure>(call, "closure", { snapshot, family }, ""),
  board: (call: PyCall, snapshot: Snapshot, family: string) =>
    ask<BoardResult>(call, "board", { snapshot, family }, ""),
  compat: (call: PyCall, snapshot: Snapshot) => ask<Compat>(call, "compat", { snapshot }, "compat"),
  plan: (
    call: PyCall,
    body: { snapshot: Snapshot; family: string; only: readonly string[] | null; shown: { ids: readonly string[]; digest: string }; stamp: string; actor: Actor },
  ) => ask<PlanResult>(call, "plan", { ...body }, ""),
  start: (call: PyCall, body: { snapshot: Snapshot; issue: number; title: string; prefix?: string; taken: readonly string[] }) =>
    ask<StartResult>(call, "start", { ...body }, ""),
  adopt: (call: PyCall, body: { snapshot: Snapshot; branch: string; names: readonly string[] }) =>
    ask<AdoptResult>(call, "adopt", { ...body }, ""),
  confirm: (
    call: PyCall,
    body: {
      snapshot: Snapshot;
      family: string;
      phase: number;
      result: unknown;
      compare?: Compare;
      /** 手で done/ に動かされた子の移動の時刻（epoch 秒）。読めなければ 0 */
      manual_moved_at?: number;
      stamp: string;
      actor?: Actor;
    },
  ) => ask<ConfirmResult>(call, "confirm", { ...body }, ""),
  decide: (
    call: PyCall,
    body: {
      snapshot: Snapshot;
      family: string;
      phase: number;
      result: unknown;
      mode: "preview" | "apply";
      /** apply のとき必須。指摘のキー → 行き先 */
      choices?: Readonly<Record<string, string>>;
      /** apply のとき必須。preview が返した digest */
      shown_digest?: string;
      /** ホストで受け入れ済み（返信して解決済み）と確かめたキー */
      carry?: readonly string[];
      compare?: Compare;
      manual_moved_at?: number;
      stamp: string;
      actor?: Actor;
    },
  ) => ask<DecideResult>(call, "decide", { ...body }, ""),
};
