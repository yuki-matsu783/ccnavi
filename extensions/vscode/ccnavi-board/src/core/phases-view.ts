/**
 * フェーズ管理画面の、拡張ホストと Webview の間の契約。編集する対象は共通の設定・ワークスペースの設定・プロジェクトの設定の 3 種。リスク管理（`risk-view.ts`）と同じ作り。
 * 判定に使われる定義は、共通の設定（`.ccnavi/config/common/phases.yml`）と、開いている設定の和。ワークスペースかプロジェクトを開いたときは、
 * 共通の設定の定義を読み取り専用で並べ（`PhasesPage.common`）、出どころが共通か開いている設定かを見分けられるようにする。共通の設定を開いたときは並べない。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない。更新のたびに画面を作り直さず、画面の中身にも型検査を効かせるため。渡すのは「いま何を見せるか」
 * （`PhasesData`）だけで、画面が返すのはユーザが押した操作（`PhasesMessage`）だけ。定義の意味
 * （子の範囲が上限に収まるか、レビューが要るか）は実行ファイルが出す。
 *
 * 定義の形（`PHASE_KINDS`・`PhaseForm` など）もここに置く。 読み書き（`phases-doc.ts`）の側に
 * 置いたままだと、画面がそこから `yaml` を辿ることになり、バンドルしたものに YAML の解析器がすべて入る。
 * 同じ理由で、ここには VS Code の API も DOM も node も入れない。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（入れ直すと打ちかけの編集が消える）。中身が届くのは、画面の編集を破棄してよいときだけ。
 */
import type { HandoffEntry, OpenHandoffMessage } from "./handoff-entry.js";
import type { AppearanceMessage } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";
import type { TargetOption } from "./targets.js";

// ---- 定義の形（画面と読み書きで分け合う）

/** 定義の区分。ccnavi の phasetypes.KINDS と同じ順 */
export const PHASE_KINDS = ["work", "feedback"] as const;
export type PhaseKind = (typeof PHASE_KINDS)[number];

/** レビューの並び。phasetypes.REVIEWS と同じ順 */
export const REVIEWS = ["none", "mr"] as const;
export type Review = (typeof REVIEWS)[number];

/** 画面で編集する定義 1 件。`origin` は読み込んだときの位置で、新しい定義は null */
export interface PhaseForm {
  readonly origin: number | null;
  /** 対応表のキー。識別子として使える文字かは lint が言う */
  readonly id: string;
  /** 表示名。空なら欄を書かない（実行ファイルは id を使う） */
  readonly title: string;
  readonly kind: PhaseKind;
  readonly review: Review;
  /** 真なら `scope: inherit`（親の範囲そのまま）。偽なら `scope` の glob のリスト */
  readonly inherit: boolean;
  /** 子の範囲の上限。inherit なら使わない */
  readonly scope: readonly string[];
  readonly deliverables: readonly string[];
  /** 案内にだけ使う。空なら欄を書かない */
  readonly agent: string;
  readonly when: string;
}

export interface PhasesForm {
  readonly phases: readonly PhaseForm[];
}

/**
 * 共通の設定の定義 1 件（読み取り専用）。実行ファイルの統合結果（`--explain --json` の `sums[].phases.types`）のうち、
 * 出どころ（`source`）が共通の設定のもの。拡張は足し直さず、そのまま見せる
 */
export interface CommonPhase {
  readonly id: string;
  readonly title: string;
  readonly kind: string;
  readonly review: string;
  /** `inherit` の 1 語なら親の範囲そのまま */
  readonly scope: readonly string[];
}

export interface PhasesModel {
  readonly version: number | null;
  readonly form: PhasesForm;
  /** 読み込み時の苦情。形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

/** 区分の説明。select のラベル */
export const KIND_LABELS: Readonly<Record<PhaseKind, string>> = {
  work: "work（全体計画 plan: に並べる定義）",
  feedback: "feedback（フィードバック計画 feedback: に並べる定義。レビューは mr 固定）",
};

/** レビューの説明。select のラベル。追加した定義の既定は mr で、none は既定ではない */
export const REVIEW_LABELS: Readonly<Record<Review, string>> = {
  none: "none（レビューを求めない。ただし実績のリスクが HIGH 以上ならレビューが必要です）",
  mr: "mr（マージリクエストのレビューを受ける）",
};

// ---- 画面に見せる形

export interface PhasesPage {
  readonly root: string;
  /** 定義のファイル（ワークスペースルートからの相対で見せる） */
  readonly phasesPath: string;
  /**
   * ファイルが在るか。無ければ空の画面を見せる（「設定が無い」正常な状態で、帯は出さない）。欄は触れ、
   * 検証を通った最初の保存でファイルを作る。雛形は置かない
   */
  readonly exists: boolean;
  readonly model: PhasesModel;
  readonly lock: Lock;
  /** 上部に出す注意（実行ファイルがこの設定を読めていない、など） */
  readonly notices?: readonly string[];
  /** 取り込み待ち画面へ進む入口（開けるか）。読み込めなかったときは無い */
  readonly handoff?: HandoffEntry;
  /** 上部に error の帯で出す文（共通の設定の phases.yml を読めない、など）。画面は開いたまま。`--lint` が error を言う間は保存できない */
  readonly errors?: readonly string[];
  /**
   * 共通の設定の定義（読み取り専用）。開いている設定の定義に足されて判定に使われる。同じ id で中身が違う定義や、
   * 表示名（title）の重なりは、実行ファイルが error にする（そのとき開いている設定のその定義だけが使われず、同じ id なら共通の定義が使われる）。
   * 共通の設定を開いているときは空（編集する定義がそのまま共通の定義）。無ければ欄を出さない
   */
  readonly common?: readonly CommonPhase[];
  /** 開いている対象と、切り替えられる対象。無ければ切り替えの欄を出さない */
  readonly target?: { readonly kind: string; readonly name: string };
  readonly targets?: readonly TargetOption[];
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときは定義の代わりに文面を渡す。
 * `loading` は開いているタブの対象を切り替えて、新しい対象を読んでいる間（ルール管理と同じ）
 */
export type PhasesData =
  | { readonly kind: "page"; readonly page: PhasesPage }
  /** `targets` は読めなかった画面から別の対象へ戻るための欄（共通・ワークスペースと、いま開いていた対象）。ボードを読めていないので、ほかのプロジェクトは載せない */
  | { readonly kind: "error"; readonly error: string; readonly target?: { readonly kind: string; readonly name: string }; readonly targets?: readonly TargetOption[] }
  | { readonly kind: "loading"; readonly text: string };

/** 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める */
export type ToPhases =
  | DataMessage<PhasesData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  /** 保存が通った（中身 `data` を渡した直後に送る）。検証の warn の本文。画面は上部の帯に出し、次の編集か更新で消す */
  | { readonly type: "saved"; readonly warns: readonly string[] }
  /** 頼んだ往復が起きなかった（ユーザが「破棄して読み直す？」をやめた）。画面は欄を戻す */
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
  | { readonly type: "save"; readonly form: PhasesForm }
  /** 案内を閉じた。拡張ホストは見たことを残し、次からは初回の案内を送らない */
  | { readonly type: "tourDone" }
  /** 開いたまま別の設定へ切り替える。未保存の変更があれば、拡張ホストが破棄してよいかを聞く */
  | { readonly type: "switchTarget"; readonly kind: string; readonly name: string }
  | OpenHandoffMessage;

/** 最初の中身を埋める `<script type="application/json">` の id */
export const DATA_ID = "ccnavi-phases-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ */
export function embedData(data: PhasesData): string {
  return embedJson(data);
}
