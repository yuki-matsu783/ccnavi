/**
 * リスク管理画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない（ADR-0064）。拡張ホストが渡すのは
 * 「いま何を見せるか」（`RiskData`）だけで、境目の点の欄も項目の行も画面が作る。画面が返すのは
 * 人が押した操作（`RiskMessage`）だけで、点も数えず、ファイルも書かない（ADR-0035）。
 *
 * **配点の形（`KINDS`・`FactorForm` など）もここに置く。** 読み書き（`risk-doc.ts`）の側に
 * 置いたままだと、画面がそこから `yaml` を辿ることになり、束ねたものに YAML の解析器が丸ごと入る。
 * 同じ理由で、ここには VS Code の API も DOM も node も入れない。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（ADR-0062）。**中身（`data`）が届くのは、画面の編集を捨ててよいとき
 * だけ**（人が「更新」を押した、保存や作成が通って中身が入れ替わった）。ファイルが外で
 * 変わっただけのときは `changed` の帯を出し、捨てるかどうかは人が決める。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";

// ---- 配点の形（画面と読み書きで分け合う）

/** 加点条件。1 件につき 1 つ。ccnavi の risk.KINDS と同じ並び */
export const KINDS = ["lines_over", "files_over", "deleted_over", "glob", "script", "judge"] as const;
export type FactorKind = (typeof KINDS)[number];

/** リスクレベルの名前は固定。境目の点だけ動かす。LOW は境目の点を持たない */
export const LEVEL_NAMES = ["medium", "high", "critical"] as const;
export type LevelName = (typeof LEVEL_NAMES)[number];

/** 組み込みの配点（risk.builtin と同じ値）。ファイルが無いときに画面が見せ、作るときに書き出す */
export const BUILTIN_LEVELS: Readonly<Record<LevelName, number>> = { medium: 20, high: 40, critical: 70 };

/** 画面で編集する項目 1 件。`origin` は読み込んだときの位置で、新しい項目は null */
export interface FactorForm {
  readonly origin: number | null;
  readonly id: string;
  /** 加点。整数のはずだが欄の文字のまま持つ。整数でなければそのまま書いて lint が言う */
  readonly points: string;
  readonly kind: FactorKind;
  /** 加点条件の値。lines_over 等なら基準、glob ならパターン、script ならパス、judge なら問い */
  readonly value: string;
  /** glob の上限。空なら青天井（欄を書かない） */
  readonly max: string;
  readonly message: string;
}

export interface RiskForm {
  /** 境目の点。空ならそのリスクレベルは組み込みの値（欄を書かない） */
  readonly levels: Readonly<Record<LevelName, string>>;
  readonly factors: readonly FactorForm[];
}

export interface RiskModel {
  readonly version: number | null;
  readonly form: RiskForm;
  /** 読み込み時の苦情。形が読めなかった場所。あっても他は出す */
  readonly problems: readonly string[];
}

/** 加点条件の説明。select のラベルと、値の欄の placeholder */
export const KIND_LABELS: Readonly<Record<FactorKind, { readonly label: string; readonly placeholder: string }>> = {
  lines_over: { label: "変更した行数が基準を超えたら加点", placeholder: "300（追加と削除の合計がこれを超えたら加点）" },
  files_over: { label: "変更したファイル数が基準を超えたら加点", placeholder: "10（変更したファイルの数がこれを超えたら加点）" },
  deleted_over: { label: "削除したファイル数が基準を超えたら加点", placeholder: "3（削除したファイルの数がこれを超えたら加点）" },
  glob: { label: "glob に当てはまるファイルを 1 つ変更するごとに加点", placeholder: ".github/**（ワークツリーのルートからの相対。当てはまるファイル 1 つごとに points を加点し、max が上限）" },
  script: { label: "スクリプトが返した点を加点", placeholder: ".ccnavi/common/scripts/xxx.sh（.ccnavi/common/scripts/ の下だけ。スクリプトが返した点を加点し、失敗や読めない出力なら points を加点）" },
  judge: { label: "サブエージェントの答えが yes なら加点", placeholder: "テストの無い振る舞いの変更を含むか（差分を読んで yes/no で答えられる質問。yes で加点）" },
};

// ---- 画面に見せる形

export interface RiskPage {
  readonly root: string;
  /** 配点のファイル（ワークスペースルートからの相対で見せる） */
  readonly riskPath: string;
  /** ファイルが在るか。無ければ組み込みの配点を見せ、「作る」だけができる */
  readonly exists: boolean;
  readonly model: RiskModel;
  readonly lock: Lock;
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときは配点の代わりに文面を渡す（`kind: "error"`）。
 * ボード・プロジェクト管理と同じ形で、画面はどちらでも 1 枚を描く。
 */
export type RiskData =
  | { readonly kind: "page"; readonly page: RiskPage }
  | { readonly kind: "error"; readonly error: string };

/**
 * 拡張ホスト → 画面。中身を包む形は `screen-host.ts` が決める（渡すのはそこ）。
 *
 * `failed` は操作の結果をその場で言う一言、`lock` は保存してよいかの取り直し、`changed` は
 * ファイルが外で変わったという帯。どれも画面の編集には触らない。
 */
export type ToRisk =
  | DataMessage<RiskData>
  | { readonly type: "failed"; readonly message: string }
  | { readonly type: "lock"; readonly lock: Lock }
  | { readonly type: "changed" }
  /** 頼んだ往復が起きなかった（人が「破棄して読み直す？」をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** 初回の吹き出しの案内を出す。画面は指す先が出てから始める（`src/tour.ts`） */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（risk-panel の `asMessage`）が形を確かめてから使う */
export type RiskMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "create" }
  | { readonly type: "save"; readonly form: RiskForm }
  /** 吹き出しの案内を閉じた（最後まで見ても、途中でやめても）。拡張ホストは次から初回の案内を頼まない */
  | { readonly type: "tourDone" };

/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-risk-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: RiskData): string {
  return embedJson(data);
}
