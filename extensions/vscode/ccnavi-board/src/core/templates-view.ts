/**
 * テンプレート管理画面の、拡張ホストと Webview の間の契約。見せるのは `ccnavi --templates --json` の答え（テンプレートの一覧・層ごとの中身・統合結果の索引・下書き）と、
 * 1 件の本文（`--templates --show <名前> --json`）。形の見本は `tests/fixtures/templates-json.json`。
 * 一覧・検索・表示と、テンプレートの編集（保存はユーザが押したときだけ）をする画面。下書きの取り込みは取り込み待ち画面（`handoff-view.ts`）が受け持つ。
 *
 * 判定は実行ファイルが答え、画面は読むだけにする（有効か・捨てた理由は実行ファイルが決める）。読み取りは `templatesmodel.ts` の側に置く。
 * 画面は React で組み、拡張ホストは HTML を組み立てない（`phases-view.ts` と同じ作り）。ここには VS Code の API も DOM も node も入れない。
 * 両側から import されるので、片方だけが持てるものを置くとバンドルできなくなる。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { HandoffEntry } from "./handoff-entry.js";
import type { ApprovePlan } from "./approvemodel.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";
import type { TargetOption } from "./targets.js";

// ---- 答えの形（`--templates --json`）

/** 深刻度。実行ファイルの答えが知らない値なら読み取りで `warn` に寄せる */
export type ProblemSeverity = "error" | "warn" | "info";

/** 苦情 1 件 */
export interface TemplateProblem {
  readonly severity: ProblemSeverity;
  readonly detail: string;
}

/** テンプレートの計画の 1 項。`index` は計画の中の添字、`number` は提案に写したときの番号、`after` は計画の中の添字 */
export interface PlanItem {
  readonly index: number;
  readonly number: number;
  readonly type: string;
  readonly review: string;
  readonly after: readonly number[];
}

/** フロー（流れ図）の要約 */
export interface FlowSummary {
  readonly index: number;
  readonly name: string;
  readonly nodes: number;
  readonly connections: number;
  /** ノードの種類ごとの数 */
  readonly node_types: Readonly<Record<string, number>>;
}

/** 統合結果で捨てた理由。reason は invalid / duplicate / conflict だが、知らない値も通す */
export interface Dropped {
  readonly reason: string;
  readonly detail: string;
}

/** 層の中の 1 テンプレート */
export interface TemplateEntry {
  readonly name: string;
  /** どの層のファイルか（common か、開いている層の名前）。`--layer` の名前と同じ */
  readonly layer: string;
  readonly path: string;
  /** 本文のハッシュ。取り込みの `--expect` などに使う */
  readonly sha: string;
  readonly valid: boolean;
  readonly description: string;
  readonly when: string;
  readonly plan: readonly PlanItem[];
  readonly feedback: readonly PlanItem[];
  readonly has_flows: boolean;
  readonly flows: readonly FlowSummary[];
  /** 統合結果で使われるか */
  readonly effective: boolean;
  /** 使われ、error も無い */
  readonly usable: boolean;
  /** 捨てた理由。捨てていなければ null */
  readonly dropped: Dropped | null;
  readonly writable: boolean;
  readonly problems: readonly TemplateProblem[];
}

/** 層ごとの生の中身（共通と各レイヤー） */
export interface TemplateLayer {
  readonly name: string;
  readonly path: string;
  readonly exists: boolean;
  /** 読めなかったときの理由。読めていれば空 */
  readonly unreadable: string;
  readonly writable: boolean;
  readonly problems: readonly TemplateProblem[];
  readonly templates: readonly TemplateEntry[];
}

/** 統合結果の索引の 1 行 */
export interface SumRow {
  readonly name: string;
  readonly layer: string;
  readonly effective: boolean;
  readonly dropped: Dropped | null;
}

/** レイヤーごとの統合結果の索引 */
export interface Sum {
  readonly name: string;
  /** 統合結果に入る層の名前 */
  readonly layers: readonly string[];
  readonly templates: readonly SumRow[];
}

/**
 * 下書き 1 件（`wip/handoff/` の `template` の下書き）。効力は無く、templates にも統合結果にも入らない。
 * 下書きは読むだけで、取り込みは取り込み待ち画面（`user handoff`）が受け持つ
 */
export interface DraftEntry {
  readonly name: string;
  /** 置かれたツリー。ワークスペースルートは空 */
  readonly tree: string;
  /** ツリーからの相対 */
  readonly path: string;
  readonly valid: boolean;
  readonly description: string;
  readonly when: string;
  readonly plan: readonly PlanItem[];
  readonly feedback: readonly PlanItem[];
  readonly has_flows: boolean;
  readonly flows: readonly FlowSummary[];
  /** 深刻度は下書きの検査のまま。`--lint` は error も warn に下げる */
  readonly problems: readonly TemplateProblem[];
}

/** `--templates --json` の答えの全体 */
export interface TemplatesJson {
  readonly version: number;
  /** ワークスペースのルート */
  readonly root: string;
  /** 一覧の対象のレイヤー */
  readonly layer: string;
  readonly keywords: readonly string[];
  readonly templates: readonly TemplateEntry[];
  readonly layers: readonly TemplateLayer[];
  readonly sums: readonly Sum[];
  readonly errors: number;
  readonly warns: number;
  readonly infos: number;
  readonly drafts: readonly DraftEntry[];
}

/** `--templates --show <名前> --json` の答え（`template` 欄）。`text` は生の本文 */
export interface TemplateDetail extends TemplateEntry {
  readonly text: string;
}

/** この層で使えるフェーズ定義 1 件（計画の項の型の選択肢） */
export interface PhaseTypeInfo {
  readonly id: string;
  readonly title: string;
  readonly kind: string;
  readonly review: string;
}

/**
 * `--templates --check-file <パス> --json` の答え。編集中の 1 本を実行ファイルに読ませた結果で、
 * 編集欄はこの答えを描く（読めるか・計画の順序・フローの形は実行ファイルが言い、画面は判定しない）。
 * 形の見本は `tests/fixtures/templates-check-json.json`
 */
export interface TemplateCheck {
  readonly version: number;
  readonly layer: string;
  readonly name: string;
  /** 読ませたファイルのハッシュ */
  readonly sha: string;
  readonly valid: boolean;
  readonly problems: readonly TemplateProblem[];
  /** 実行ファイルが読んだ中身（`Template.as_data()` の JSON）。読めなければ null。書こうとした中身と見比べる */
  readonly data: unknown;
  /** 図に描く計画ごとの中身（全体計画・フィードバック計画）。承認の図と同じ形 */
  readonly plans: readonly ApprovePlan[];
  readonly types: readonly PhaseTypeInfo[];
}

// ---- 画面に見せる形

export interface TemplatesPage {
  readonly root: string;
  readonly generatedAt: string;
  /** 開いている対象。kind は "workspace"（共通の設定）/ "self" / "project"。`--layer` には kind が project なら name、それ以外は "self" を渡す */
  readonly target: { readonly kind: string; readonly name: string };
  readonly targets: readonly TargetOption[];
  readonly data: TemplatesJson;
  /**
   * 取り込み待ち画面を開けるか。実行ファイルが `handoff` の機能を持たなければ `enabled` が偽で、理由を `reason` に入れる。
   * この画面は下書きを一覧するだけで、取り込み・編集は取り込み待ち画面が受け持つ
   */
  readonly handoff: HandoffEntry;
  readonly notices: readonly string[];
}

/** 画面に見せる中身。読み直せなかったときは一覧の代わりに文面を渡す */
export type TemplatesData =
  | { readonly kind: "page"; readonly page: TemplatesPage }
  /** `targets` は読めなかった画面から別の対象へ戻るための欄。ボードを読めていないので、ほかのプロジェクトは載せない */
  | { readonly kind: "error"; readonly error: string; readonly target?: { readonly kind: string; readonly name: string }; readonly targets?: readonly TargetOption[] }
  /** 対象を切り替えて新しい対象を読んでいる間の表示 */
  | { readonly kind: "loading"; readonly text: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToTemplates =
  | DataMessage<TemplatesData>
  /** この画面の案内をまだ見ていない。画面は吹き出しの案内を出す */
  | { readonly type: "tour" }
  /** 1 件の本文の答え。`error` があれば本文は null */
  | { readonly type: "detail"; readonly name: string; readonly layer: string; readonly template: TemplateDetail | null; readonly error: string }
  /** 編集を始めた答え（`editOpen` / `editNew`）。`error` があれば始められない */
  | { readonly type: "editOpened"; readonly session: number; readonly opened: EditOpened | null; readonly error: string }
  /** 編集中の中身を実行ファイルに読ませた答え（`editCheck`）。`seq` は頼んだ番号 */
  | { readonly type: "editChecked"; readonly session: number; readonly seq: number; readonly check: TemplateCheck | null; readonly error: string }
  /** 1 件を図で見せるための答え（`preview`）。`error` があれば `check` は null */
  | { readonly type: "preview"; readonly name: string; readonly layer: string; readonly check: TemplateCheck | null; readonly error: string }
  /** 作業中の錠が変わった（チケットが動いた）。錠が掛かっている間は保存できない */
  | { readonly type: "editLock"; readonly session: number; readonly lock: Lock }
  /** 保存の答え（`editSave`）。書けたら `sha` が新しいファイルのハッシュ。書かなかったら `error` に理由 */
  | { readonly type: "editSaved"; readonly session: number; readonly sha: string; readonly error: string }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側が形を確かめてから使う */
export type TemplatesMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  /** 画面を読み直す */
  | { readonly type: "refresh" }
  /** 案内を閉じた。拡張ホストは見たことを残す */
  | { readonly type: "tourDone" }
  /** 開いたまま別の対象へ切り替える */
  | { readonly type: "switchTarget"; readonly kind: string; readonly name: string }
  /** 1 件の本文を読む */
  | { readonly type: "showTemplate"; readonly name: string; readonly layer: string }
  /** 1 件を実行ファイルに読ませ、計画とフローの図の中身を求める（読むだけ。編集の錠は取らない） */
  | { readonly type: "preview"; readonly name: string; readonly layer: string }
  /** 取り込み待ち画面を開く（下書きの取り込みと編集は、そちらで行う） */
  | { readonly type: "openHandoff" }
  /** 既存のテンプレートの編集を始める。`session` は画面が付ける番号で、答えに返る */
  | { readonly type: "editOpen"; readonly session: number; readonly name: string; readonly layer: string }
  /** 開いている対象の層に新しいテンプレートを作る編集を始める（保存するまでファイルは作らない） */
  | { readonly type: "editNew"; readonly session: number; readonly name: string }
  /** 編集欄を閉じた。拡張ホストは作業中の錠を取り直すのをやめる */
  | { readonly type: "editClose"; readonly session: number }
  /** 編集中の中身（`toData` の形）を実行ファイルに読ませる */
  | { readonly type: "editCheck"; readonly session: number; readonly seq: number; readonly data: unknown }
  /** 編集中の中身を保存する。`expect` は開いたときのファイルのハッシュ（新規は空）。外で変わっていれば書かない */
  | { readonly type: "editSave"; readonly session: number; readonly name: string; readonly layer: string; readonly expect: string; readonly data: unknown };

/** 編集を始めた答えの中身 */
export interface EditOpened {
  readonly name: string;
  readonly layer: string;
  /** 新規なら true。ファイルはまだ無い */
  readonly created: boolean;
  /** 開いたときのファイルのハッシュ。新規は空。保存の `expect` に返す */
  readonly sha: string;
  /** 読み込んだ中身（`toData` の形）と、それを読ませた答え */
  readonly data: unknown;
  readonly check: TemplateCheck;
  /**
   * 作業中の錠。作業中のチケットがある間は、編集はできるが保存できない（hook は次の呼び出しからテンプレートと設定を読み直すため。
   * 取り込み待ち画面がテンプレートの取り込みを止めるのと同じ決まり）。保存を押した時点で取り直す
   */
  readonly lock: Lock;
}

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-templates-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: TemplatesData): string {
  return embedJson(data);
}

