/**
 * フェーズ管理画面の、拡張ホストと Webview の間の契約。リスク管理（`risk-view.ts`）と同じ作り。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0060）。渡すのは「いま何を見せるか」
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
import type { Appearance } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";

// ---- 種類の形（画面と読み書きで分け合う）

/** 種類の区分。ccnavi の phasetypes.KINDS と同じ並び */
export const PHASE_KINDS = ["work", "feedback"] as const;
export type PhaseKind = (typeof PHASE_KINDS)[number];

/** レビューの既定。phasetypes.REVIEWS と同じ並び */
export const REVIEWS = ["none", "mr"] as const;
export type Review = (typeof REVIEWS)[number];

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
  /** 案内にだけ使う。空なら欄を書かない */
  readonly agent: string;
  readonly when: string;
}

export interface PhasesForm {
  readonly phases: readonly PhaseForm[];
}

export interface PhasesModel {
  readonly version: number | null;
  readonly form: PhasesForm;
  /** 読み込み時の苦情。形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

/** 区分の説明。select の札 */
export const KIND_LABELS: Readonly<Record<PhaseKind, string>> = {
  work: "work（全体計画 plan: に並べる種類）",
  feedback: "feedback（フィードバック計画 feedback: に並べる種類。レビューは mr 固定）",
};

/** レビューの既定の説明。select の札 */
export const REVIEW_LABELS: Readonly<Record<Review, string>> = {
  none: "none（既定。レビューを求めない。ただし実績のリスクが HIGH 以上なら要る）",
  mr: "mr（マージリクエストのレビューを受ける）",
};

// ---- 画面に見せる形

export interface PhasesPage {
  readonly root: string;
  /** 種類の定義のファイル（ワークスペースルートからの相対で見せる） */
  readonly phasesPath: string;
  /** ファイルが在るか。無ければ空の画面を見せ、共通層なら「雛形で作る」だけができる */
  readonly exists: boolean;
  readonly model: PhasesModel;
  readonly lock: Lock;
  /**
   * 層（自身の層かプロジェクト）の種類か。層はファイルが無くても編集でき、最初の保存でファイルを作る。
   * 雛形は置かない（雛形の id は共通層の種類と重なりやすい）
   */
  readonly layer?: boolean;
  /** 上部に出す注意（実行ファイルがこの層を読めていない、など） */
  readonly notices?: readonly string[];
}

/** 欄を触れるか。共通層はファイルが無ければ「雛形で作る」まで触れない。層は無くても足して保存できる */
export function editable(page: PhasesPage): boolean {
  return page.exists || page.layer === true;
}

// ---- やり取り

/** 画面に見せる中身。読み直せなかったときは種類の代わりに文面を渡す */
export type PhasesData =
  | { readonly kind: "page"; readonly page: PhasesPage }
  | { readonly kind: "error"; readonly error: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToPhases =
  | DataMessage<PhasesData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  | { readonly type: "appearance"; readonly value: Appearance };

/** 画面 → 拡張ホスト。受け側（phases-panel の `asMessage`）が形を確かめてから使う */
export type PhasesMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "create" }
  | { readonly type: "save"; readonly form: PhasesForm };

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-phases-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: PhasesData): string {
  return embedJson(data);
}
