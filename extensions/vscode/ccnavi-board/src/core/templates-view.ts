/**
 * テンプレート管理画面の、拡張ホストと Webview の間の契約。見せるのは `ccnavi --templates --json` の答え（テンプレートの一覧・層ごとの中身・足し算の索引・下書き）と、
 * 1 件の本文（`--templates --show <名前> --json`）と、取り込みの下見と実行（`--templates --import … --json`）の答え。形の見本は `tests/fixtures/templates-json.json`。
 *
 * 判定は実行ファイルが答え、画面は読むだけにする（有効か・捨てた理由・取り込めるかは実行ファイルが決める）。読み取りは `templatesmodel.ts` の側に置く。
 * 画面は React で組み、拡張ホストは HTML を組み立てない（`phases-view.ts` と同じ作り）。ここには VS Code の API も DOM も node も入れない。
 * 両側から import されるので、片方だけが持てるものを置くとバンドルできなくなる。
 */
import type { AppearanceMessage } from "./appearance.js";
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

/** 足し算で捨てた理由。reason は invalid / duplicate / conflict だが、知らない値も通す */
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
  /** 足し算で使われるか */
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

/** 足し算の索引の 1 行 */
export interface SumRow {
  readonly name: string;
  readonly layer: string;
  readonly effective: boolean;
  readonly dropped: Dropped | null;
}

/** レイヤーごとの足し算の索引 */
export interface Sum {
  readonly name: string;
  /** 足し算に入る層の名前 */
  readonly layers: readonly string[];
  readonly templates: readonly SumRow[];
}

/** 下書き 1 件。効力は無く、templates にも足し算にも入らない */
export interface DraftEntry {
  readonly name: string;
  /** 置かれたツリー。ワークスペースルートは空 */
  readonly tree: string;
  /** ツリーからの相対 */
  readonly path: string;
  /** 取り込みの `--expect` に使う */
  readonly sha: string;
  readonly valid: boolean;
  readonly description: string;
  readonly when: string;
  readonly plan: readonly PlanItem[];
  readonly feedback: readonly PlanItem[];
  readonly has_flows: boolean;
  readonly flows: readonly FlowSummary[];
  /** 深刻度は下書きの検査のまま。`--lint` は error も warn に下げる */
  readonly problems: readonly TemplateProblem[];
  /** 下書きの本文そのまま。編集の欄の初期値 */
  readonly text: string;
  /** 読めて error が無い（取り込める） */
  readonly importable: boolean;
  /** 検査に使ったフェーズ定義の層。templates の layer（ファイルが在る層）とは別 */
  readonly checked_with: string;
  /** 同じ名前のテンプレートが在るレイヤー。取り込むと置き換えになる */
  readonly exists_in: readonly string[];
  /** 同じ中身の下書きを持つほかのツリー（表記は tree と同じ） */
  readonly also_in: readonly string[];
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

// ---- 取り込み（`--templates --import … --json`）

/** 取り込もうとしている下書きの参照。`--expect` には sha を返す */
export interface ImportDraftRef {
  readonly name: string;
  readonly tree: string;
  readonly path: string;
  readonly sha: string;
}

/** 取り込みの下見と実行の答え */
export interface ImportAnswer {
  readonly ok: boolean;
  readonly draft: ImportDraftRef | null;
  /** 取り込み先（common か project） */
  readonly to: string;
  /** 取り込み先のレイヤー（project のとき） */
  readonly layer: string;
  /** 書く先のファイル */
  readonly target: string;
  /** 書く先に同じ名前のファイルが既に在るか */
  readonly exists: boolean;
  readonly replace: boolean;
  readonly errors: readonly string[];
  readonly infos: readonly string[];
  readonly problems: readonly TemplateProblem[];
  /** 書く前後の差分の行 */
  readonly diff: readonly string[];
  /** 書いたあとの中身 */
  readonly new_text: string;
  /** 実際に書いたか（実行のときだけ真になりうる） */
  readonly written: boolean;
  /** 下書きを消したか */
  readonly removed: boolean;
  /** 下書きを残したときの場所 */
  readonly kept: string;
}

/** 取り込み先。common は共通の設定、project は選んだ層 */
export type ImportTo = "common" | "project";

/** 取り込みの要求。下見と実行で同じ形を使う */
export interface ImportRequest {
  readonly draft: string;
  readonly to: ImportTo;
  /** `to` が project のときだけ使う。common のときは空でよい */
  readonly layer: string;
  readonly replace: boolean;
}

// ---- 編集（`--templates --save-draft` などの旗。取り込みと同じく下見と実行の2段）

/** 編集の種類 */
export type EditOp = "save" | "delete-draft" | "rename" | "move" | "delete";
/** 主語の場所。下書き・共通の設定・層（project）のどれか */
export type EditOn = "draft" | "common" | "project";

/** 編集の要求。下見と実行で同じ形を使う。使わない欄は空文字 */
export interface EditRequest {
  readonly op: EditOp;
  readonly name: string;
  readonly on: EditOn;
  /** `on` が project のときの層の名前 */
  readonly layer: string;
  /** rename の新しい名前 */
  readonly newName: string;
  /** move の行き先。それ以外は空 */
  readonly to: "" | "common" | "project";
  /** `to` が project のときの行き先の層 */
  readonly toLayer: string;
  /** save の本文 */
  readonly body: string;
}

/** 編集の下見と実行の答え（`template_edit.plan_json`） */
export interface EditAnswer {
  readonly ok: boolean;
  readonly op: string;
  readonly name: string;
  readonly on: string;
  readonly layer: string;
  readonly new_name: string;
  readonly to: string;
  readonly to_layer: string;
  /** 主語のファイル（ワークスペースルートからの相対） */
  readonly source: string;
  /** 書く先のファイル。消す操作は空 */
  readonly target: string;
  /** 主語の今のハッシュ。実行の `--expect` に使う */
  readonly sha: string;
  readonly errors: readonly string[];
  readonly infos: readonly string[];
  readonly problems: readonly TemplateProblem[];
  readonly diff: readonly string[];
  readonly new_text: string;
  readonly written: boolean;
  readonly removed: boolean;
  /** 書いたあとの中身のハッシュ（書いていなければ空） */
  readonly new_sha: string;
}

// ---- 画面に見せる形

export interface TemplatesPage {
  readonly root: string;
  readonly generatedAt: string;
  /** 開いている対象。kind は "workspace"（共通の設定）/ "self" / "project"。`--layer` には kind が project なら name、それ以外は "self" を渡す */
  readonly target: { readonly kind: string; readonly name: string };
  readonly targets: readonly TargetOption[];
  readonly data: TemplatesJson;
  /** 取り込みを止めるか。共通への取り込みと全体の判定はこちら（どのツリーの作業中でも止める） */
  readonly lock: Lock;
  /** プロジェクト名 → そのプロジェクトの作業中だけを見た Lock。`--to project --layer <名>` で使う。"self" も入れる */
  readonly projectLocks: Readonly<Record<string, Lock>>;
  /** 取り込み先に選べる層の名前（`data.layers` の name のうち common 以外） */
  readonly importLayers: readonly string[];
  /** 編集の旗を実行ファイルが持つか。持たなければ理由 */
  readonly edit: { readonly enabled: boolean; readonly reason: string };
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
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "changed" }
  | { readonly type: "lock"; readonly lock: Lock; readonly projectLocks: Readonly<Record<string, Lock>> }
  /** 頼んだ往復が起きなかった（ユーザが確認をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** この画面の案内をまだ見ていない。画面は吹き出しの案内を出す */
  | { readonly type: "tour" }
  /** 1 件の本文の答え。`error` があれば本文は null */
  | { readonly type: "detail"; readonly name: string; readonly layer: string; readonly template: TemplateDetail | null; readonly error: string }
  /** 取り込みの下見の答え。`answer` が null なら `error` に理由 */
  | { readonly type: "preview"; readonly request: ImportRequest; readonly answer: ImportAnswer | null; readonly error: string }
  /** 取り込みの実行の答え。`answer` が null なら `error` に理由 */
  | { readonly type: "imported"; readonly request: ImportRequest; readonly answer: ImportAnswer | null; readonly error: string }
  /** 編集の下見の答え。`answer` が null なら `error` に理由 */
  | { readonly type: "editPreview"; readonly request: EditRequest; readonly answer: EditAnswer | null; readonly error: string }
  /** 編集の実行の答え。`answer` が null なら `error` に理由 */
  | { readonly type: "edited"; readonly request: EditRequest; readonly answer: EditAnswer | null; readonly error: string }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側が形を確かめてから使う */
export type TemplatesMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  /** 画面を読み直す。`dirty` は編集の途中かどうか */
  | { readonly type: "refresh"; readonly dirty: boolean }
  /** 未保存の変更の有無が変わった */
  | { readonly type: "dirty"; readonly dirty: boolean }
  /** 案内を閉じた。拡張ホストは見たことを残す */
  | { readonly type: "tourDone" }
  /** 開いたまま別の対象へ切り替える */
  | { readonly type: "switchTarget"; readonly kind: string; readonly name: string }
  /** 1 件の本文を読む */
  | { readonly type: "showTemplate"; readonly name: string; readonly layer: string }
  /** 取り込みの下見を頼む（何も書かない） */
  | { readonly type: "previewImport"; readonly request: ImportRequest }
  /** 取り込みを実行する。`expect` は見せた下書きの sha */
  | { readonly type: "importYes"; readonly request: ImportRequest; readonly expect: string }
  /** 編集の下見を頼む（何も書かない） */
  | { readonly type: "previewEdit"; readonly request: EditRequest }
  /** 編集を実行する。`expect` は見せた主語のハッシュ */
  | { readonly type: "editYes"; readonly request: EditRequest; readonly expect: string };

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-templates-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: TemplatesData): string {
  return embedJson(data);
}
