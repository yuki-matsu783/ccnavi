/**
 * 画面の入口の帳面。どの画面をどう開くかを1か所に集める。
 *
 * 画面どうしは互いをimportしない。プロジェクト管理画面から「チケット管理を開く」ような
 * 導線は、相手のパネルの関数を直に呼ばず、ここに登録された入口へ要求を出す。相手の
 * パネルがどう作られていて、いま開いているかどうかは、要求する側に見せない。
 *
 * 登録するのは`extension.ts`（組み立ての場）だけ。登録前に開こうとしたら、それは
 * 組み立ての誤りなので例外で止める（ユーザの操作では起こらない）。
 */

/** ルール管理画面が直すルールファイル。共通の設定、ワークスペースの設定、プロジェクト1つの設定 */
export type RulesTarget =
  | { readonly kind: "workspace" }
  | { readonly kind: "self" }
  | { readonly kind: "project"; readonly name: string };

/** リスク管理画面が直す配点のファイル。共通の設定、ワークスペースの設定、プロジェクト1つの設定 */
export type RiskTarget = RulesTarget;

/** フェーズ管理画面が直す定義のファイル。共通の設定、ワークスペースの設定、プロジェクト1つの設定（ルール管理・リスク管理と同じ3種） */
export type PhasesTarget = RulesTarget;

/** テンプレート管理画面が開く対象。共通の設定、ワークスペースの設定、プロジェクト1つの設定（ルール管理・リスク管理・フェーズ管理と同じ3種） */
export type TemplatesTarget = RulesTarget;

/**
 * ボードを開いたあとに進む場所（サイドパネルのユーザ待ち・進行中の親の行から）。行けるかはボードが、いまの答えで確かめてから進む。
 *
 * - `project` 開いたときの絞り込み（`""`はワークスペース（プロジェクト外）、`"*"`は全部）
 * - `approve` そのチケット1件の承認のオーバーレイ（カードの［承認］と同じ）
 * - `decide` そのフェーズのレビュー完了（カードの［レビュー完了］と同じ。リモートの指摘を取りに行く）
 * - `select` そのカードを選んで右のパネルに出し、焦点を当てる（絞り込みや畳んだ列で隠れていれば外す）
 * - `mergeCheck` 「MRの状況を確認」（リモートのMRを見に行く）
 */
export type BoardTarget =
  | { readonly kind: "project"; readonly project: string }
  | { readonly kind: "approve"; readonly ticket: string }
  | { readonly kind: "decide"; readonly parent: string; readonly phase: number }
  | { readonly kind: "select"; readonly ticket: string }
  | { readonly kind: "mergeCheck" };

/** 画面の入口（サイドパネルに並ぶ7つと、ボードから開くフロー編集・計画編集）。引数は開く側が解く（空の表記や未登録のプロジェクトの扱いは各パネルの持ち物） */
export interface Screens {
  /** ボード。`target`は開いたあとに進む場所。未指定なら開くだけ（絞り込みは前回のまま） */
  readonly board: (target?: BoardTarget) => Promise<void>;
  readonly rules: (target: RulesTarget) => Promise<void>;
  readonly risk: (target: RiskTarget) => Promise<void>;
  readonly phases: (target: PhasesTarget) => Promise<void>;
  readonly templates: (target: TemplatesTarget) => Promise<void>;
  /**
   * 取り込み待ち画面（`wip/handoff/`の下書きを取り込む）。`tree`は見るツリーのキー（`main:`はワークスペース、
   * `project:<名前>`、`worktree:<名前>`）。未指定はワークスペース。タブは1枚で、別のツリーを開けば中身を入れ替える
   */
  readonly handoff: (tree?: string) => Promise<void>;
  readonly projects: () => Promise<void>;
  /**
   * 子チケット1枚のフロー編集画面。ボードのカードの「フロー」からだけ開く。
   * タブは子ごとに1枚で、同じ子をもう1度開けば前面に出す
   */
  readonly flow: (ticket: string) => Promise<void>;
  /**
   * 親の提案1枚の計画編集タブ（計画のステップどうしの先行を図で引き直す）。対象は承認待ち（`todo/`）の
   * 計画を持つ親。タブは親ごとに1枚で、同じ親をもう1度開けば前面に出す
   */
  readonly workflow: (ticket: string) => Promise<void>;
}

let registered: Screens | undefined;

/** 組み立てのときに1度だけ呼ぶ。テストでは差し替えのために何度でも呼べる */
export function registerScreens(value: Screens): void {
  registered = value;
}

/** 登録を消す。テストのためだけにある */
export function forgetScreens(): void {
  registered = undefined;
}

/** 画面を開く。`screens().rules({ kind: "self" })`のように使う */
export function screens(): Screens {
  if (registered === undefined) {
    throw new Error("画面の入口が登録されていません（extension.tsのregisterScreensを通っていません）");
  }
  return registered;
}
