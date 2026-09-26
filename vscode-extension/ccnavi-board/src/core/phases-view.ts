/**
 * フェーズ管理画面の、拡張ホストと Webview の間の契約。リスク管理（`risk-view.ts`）と同じ作り。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0064）。渡すのは「いま何を見せるか」
 * （`PhasesData`）だけで、画面が返すのは人が押した操作（`PhasesMessage`）だけ。画面は種類の意味を
 * 判定しない（子の範囲が上限に収まるか、レビューが要るかは実行ファイルが出す。ADR-0035）。
 *
 * **種類の形（`PHASE_KINDS`・`PhaseForm` など）もここに置く。** 読み書き（`phases-doc.ts`）の側に
 * 置いたままだと、画面がそこから `yaml` を辿ることになり、束ねたものに YAML の解析器が丸ごと入る。
 * 同じ理由で、ここには VS Code の API も DOM も node も入れない。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（ADR-0062）。中身が届くのは、画面の編集を捨ててよいときだけ。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";

// ---- 種類の形（画面と読み書きで分け合う）

/** 種類の区分。ccnavi の phasetypes.KINDS と同じ並び */
export const PHASE_KINDS = ["work", "feedback"] as const;
export type PhaseKind = (typeof PHASE_KINDS)[number];

/** レビューの既定。phasetypes.REVIEWS と同じ並び */
export const REVIEWS = ["none", "mr"] as const;
export type Review = (typeof REVIEWS)[number];

/** 全体計画の待ち方。phasetypes.ORDERS と同じ並び。sequential が既定（ファイルに書かない） */
export const ORDERS = ["sequential", "dag"] as const;
export type PhaseOrder = (typeof ORDERS)[number];

/** 待ち方の説明。select のラベル */
export const ORDER_LABELS: Readonly<Record<PhaseOrder, string>> = {
  sequential: "sequential（既定。全体計画は一直線で、前の番号を全部待つ）",
  dag: "dag（after を辺にしたワークフロー。祖先に当たる種類だけを待ち、他は並行して進む）",
};

/** 画面で編集する種類 1 件。`origin` は読み込んだときの位置で、新しい種類は null */
export interface PhaseForm {
  readonly origin: number | null;
  /** 対応表のキー。識別子として使える文字かは lint が言う */
  readonly id: string;
  /** 表示名。空なら欄を書かない（実行ファイルは id を使う） */
  readonly title: string;
  readonly kind: PhaseKind;
  readonly review: Review;
  /** 真なら `scope: inherit`（親の範囲そのまま）。偽なら `scope` の glob の並び */
  readonly inherit: boolean;
  /** 子の範囲の上限。inherit なら使わない */
  readonly scope: readonly string[];
  readonly deliverables: readonly string[];
  readonly overlap: readonly string[];
  readonly requires: readonly string[];
  /** order: dag のとき、先に閉じてレビューが済んでいるべき種類（依存）。work の種類だけが持てる */
  readonly after: readonly string[];
  /** 案内にだけ使う。空なら欄を書かない */
  readonly agent: string;
  readonly when: string;
}

export interface PhasesForm {
  readonly order: PhaseOrder;
  readonly phases: readonly PhaseForm[];
}

export interface PhasesModel {
  readonly version: number | null;
  readonly form: PhasesForm;
  /** 読み込み時の苦情。形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

/** 区分の説明。select のラベル */
export const KIND_LABELS: Readonly<Record<PhaseKind, string>> = {
  work: "work（全体計画 plan: に並べる種類）",
  feedback: "feedback（フィードバック計画 feedback: に並べる種類。レビューは mr 固定）",
};

/** レビューの既定の説明。select のラベル */
export const REVIEW_LABELS: Readonly<Record<Review, string>> = {
  none: "none（既定。レビューを求めない。ただし実績のリスクが HIGH 以上なら要る）",
  mr: "mr（マージリクエストのレビューを受ける）",
};

// ---- 画面に見せる形

export interface PhasesPage {
  readonly root: string;
  /** 種類の定義のファイル（ワークスペースルートからの相対で見せる） */
  readonly phasesPath: string;
  /**
   * ファイルが在るか。無ければ空の画面を見せる。共通層は画面から作らせず、種類は層に置くよう案内する
   * （共通層に雛形を置くと、層の同じ id と中身が食い違い、その層が空として扱われるため）
   */
  readonly exists: boolean;
  readonly model: PhasesModel;
  readonly lock: Lock;
  /**
   * 層（自身の層かプロジェクト）の種類か。層はファイルが無くても編集でき、最初の保存でファイルを作る
   */
  readonly layer?: boolean;
  /** 上部に出す注意（実行ファイルがこの層を読めていない、など） */
  readonly notices?: readonly string[];
}

/** 欄を触れるか。共通層はファイルが無ければ触れない（画面からは作らせない）。層は無くても足して保存できる */
export function editable(page: PhasesPage): boolean {
  return page.exists || page.layer === true;
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときは種類の代わりに文面を渡す。
 * `loading` は開いているタブの対象を切り替えて、新しい対象を読んでいる間（ルール設定と同じ）
 */
export type PhasesData =
  | { readonly kind: "page"; readonly page: PhasesPage }
  | { readonly kind: "error"; readonly error: string }
  | { readonly kind: "loading"; readonly text: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToPhases =
  | DataMessage<PhasesData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  /** 頼んだ往復が起きなかった（人が「破棄して読み直す？」をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** この画面の案内をまだ見ていない（拡張ホストの `globalState`）。画面は吹き出しの案内を出す */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（phases-panel の `asMessage`）が形を確かめてから使う */
export type PhasesMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  /** 未保存の変更の有無が変わった。別の対象へ切り替えるときに聞くかを拡張ホストが決める */
  | { readonly type: "dirty"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  /** 共通層のファイルが無いときの案内から、自身の層を開く（プロジェクト管理画面の入口と同じ道） */
  | { readonly type: "openSelf" }
  | { readonly type: "save"; readonly form: PhasesForm }
  /** 案内を閉じた。拡張ホストは見たことを残し、次からは初回の案内を送らない */
  | { readonly type: "tourDone" };

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-phases-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: PhasesData): string {
  return embedJson(data);
}
