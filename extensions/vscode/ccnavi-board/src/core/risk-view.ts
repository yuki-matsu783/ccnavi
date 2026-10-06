/**
 * リスク管理画面の、拡張ホストと Webview の間の契約。
 *
 * 画面は React で組み、拡張ホストは HTML を組み立てない。更新のたびに画面を作り直さず、画面の中身にも型検査を効かせるため。拡張ホストが渡すのは
 * 「いま何を見せるか」（`RiskData`）だけで、境目の点の欄も項目の行も画面が作る。画面が返すのは
 * ユーザが押した操作（`RiskMessage`）だけで、点も数えず、ファイルも書かない。点を数えるのは実行ファイルだけにして、答えを 2 か所に持たない。
 *
 * **配点の形（`KINDS`・`FactorForm` など）もここに置く。** 読み書き（`risk-doc.ts`）の側に
 * 置いたままだと、画面がそこから `yaml` を辿ることになり、バンドルしたものに YAML の解析器が丸ごと入る。
 * 同じ理由で、ここには VS Code の API も DOM も node も入れない。
 *
 * 編集する 1 本は、共通の設定・ワークスペースの設定・プロジェクトの設定のどれか（画面上部の「設定」の欄で切り替える。
 * タブは 1 枚）。それとは別に、読み取り専用の足し算（`RiskPage.sums`）を持つ。
 *
 * この画面は `retainContextWhenHidden: true`（編集の途中を持つ）。渡し方は `retainedHost` で、
 * 入れ物は 1 度しか入らない（入れ直すと打ちかけの編集が消える）。**中身（`data`）が届くのは、画面の編集を捨ててよいとき
 * だけ**（ユーザが「更新」を押した、保存や作成が通って中身が入れ替わった）。ファイルが外で
 * 変わっただけのときは `changed` の帯を出し、捨てるかどうかはユーザが決める。
 */
import type { AppearanceMessage } from "./appearance.js";
import type { Lock } from "./lock.js";
import { embedJson, type DataMessage } from "./screen-host.js";
import type { RiskSum } from "./sums.js";
import type { TargetOption } from "./targets.js";

// ---- 配点の形（画面と読み書きで分け合う）

/** 加点条件。1 件につき 1 つ。ccnavi の risk.KINDS と同じ順 */
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
  /** ファイルが在るか。無ければ「エディタで開く」が押せない */
  readonly exists: boolean;
  /**
   * 組み込みの配点を読み取り専用で見せているか。共通の設定にもワークスペースの設定にも配点のファイルが無いとき
   * （そのとき効いているのは組み込みの配点）、共通かワークスペースの設定を開くとこうなる。欄は止まり、
   * 「組み込みの配点でファイルを作る」だけができる。省くと偽。偽でファイルが無いときは、空として出して欄は触れ、
   * 検証を通った最初の保存でファイルを作る
   */
  readonly builtin?: boolean;
  readonly model: RiskModel;
  readonly lock: Lock;
  /** 上部に出す注意（実行ファイルがこの設定を読めていない、など） */
  readonly notices?: readonly string[];
  /** 開いている対象と、切り替えられる対象。無ければ切り替えの欄を出さない */
  readonly target?: { readonly kind: string; readonly name: string };
  readonly targets?: readonly TargetOption[];
  /**
   * 読み取り専用の足し算（共通の設定 + ワークスペース、共通の設定 + 各プロジェクト）。実行ファイルが出した結果で、
   * 拡張は合成しない。保存済みの配点の和で、編集中の内容は含まない。ボードを読めなかったときは無い
   */
  readonly sums?: readonly RiskSum[];
}

// ---- やり取り

/**
 * 画面に見せる中身。読み直せなかったときは配点の代わりに文面を渡す（`kind: "error"`）。
 * ボード・プロジェクト管理と同じ形で、画面はどちらでも 1 枚を描く。
 */
export type RiskData =
  | { readonly kind: "page"; readonly page: RiskPage }
  /** `targets` は読めなかった画面から別の対象へ戻るための欄（共通・ワークスペースと、いま開いていた対象）。ボードを読めていないので、ほかのプロジェクトは載せない */
  | { readonly kind: "error"; readonly error: string; readonly target?: { readonly kind: string; readonly name: string }; readonly targets?: readonly TargetOption[] }
  /** 開いているタブの対象を切り替えて、新しい対象を読んでいる間（タブは 1 枚）。`text` は画面に出す一言 */
  | { readonly kind: "loading"; readonly text: string };

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
  /** 頼んだ往復が起きなかった（ユーザが「破棄して読み直す？」をやめた）。画面は欄を戻す */
  | { readonly type: "cancelled" }
  /** 初回の吹き出しの案内を出す。画面は指す先が出てから始める（`src/tour.ts`） */
  | { readonly type: "tour" }
  | AppearanceMessage;

/** 画面 → 拡張ホスト。受け側（risk-panel の `asMessage`）が形を確かめてから使う */
export type RiskMessage =
  /** 画面が組み上がった。拡張ホストはここで中身を渡し直す */
  | { readonly type: "ready" }
  | { readonly type: "reload"; readonly dirty: boolean }
  /** 未保存の変更の有無が変わった。別の対象へ切り替えるときに聞くかを拡張ホストが決める */
  | { readonly type: "dirty"; readonly dirty: boolean }
  | { readonly type: "openFile" }
  | { readonly type: "create" }
  | { readonly type: "save"; readonly form: RiskForm }
  /** 吹き出しの案内を閉じた（最後まで見ても、途中でやめても）。拡張ホストは次から初回の案内を頼まない */
  | { readonly type: "tourDone" }
  /** 開いたまま別の設定へ切り替える。未保存の変更があれば、拡張ホストが破棄してよいかを聞く */
  | { readonly type: "switchTarget"; readonly kind: string; readonly name: string };

/** 最初の中身を埋める `<script type="application/json">` の id。画面はこれを読んで最初の 1 枚を描く */
export const DATA_ID = "ccnavi-risk-data";

/** 最初の中身を HTML に埋める形にする。埋め方は `screen-host.ts` が持つ（React の画面で同じ） */
export function embedData(data: RiskData): string {
  return embedJson(data);
}
