/**
 * 画面の入口の帳面。どの画面をどう開くかを 1 か所に集める。
 *
 * 画面どうしは互いを import しない。プロジェクト管理画面から「チケット管理を開く」ような
 * 導線は、相手のパネルの関数を直に呼ばず、ここに登録された入口へ要求を出す。相手の
 * パネルがどう作られていて、いま開いているかどうかは、要求する側に見せない。
 *
 * 登録するのは `extension.ts`（組み立ての場）だけ。登録前に開こうとしたら、それは
 * 組み立ての誤りなので例外で止める（利用者の操作では起こらない）。
 */

/** ルール設定画面が直すルールファイル。共通の設定、ワークスペースの設定、プロジェクト 1 つの設定 */
export type RulesTarget =
  | { readonly kind: "workspace" }
  | { readonly kind: "self" }
  | { readonly kind: "project"; readonly name: string };

/** フェーズ管理画面が直す種類のファイル。共通の設定、ワークスペースの設定、プロジェクト 1 つの設定 */
export type PhasesTarget =
  | { readonly kind: "common" }
  | { readonly kind: "self" }
  | { readonly kind: "project"; readonly name: string };

/** 5 つの画面の入口。引数は開く側が解く（空の綴りや未登録のプロジェクトの扱いは各パネルの持ち物） */
export interface Screens {
  /** ボード。`project` は開いたときの絞り込み（`""` はワークスペース（プロジェクト外）、`"*"` は全部、未指定は前回のまま） */
  readonly board: (project?: string) => Promise<void>;
  readonly rules: (target: RulesTarget) => Promise<void>;
  readonly risk: () => Promise<void>;
  readonly phases: (target: PhasesTarget) => Promise<void>;
  readonly projects: () => Promise<void>;
}

let registered: Screens | undefined;

/** 組み立てのときに 1 度だけ呼ぶ。テストでは差し替えのために何度でも呼べる */
export function registerScreens(value: Screens): void {
  registered = value;
}

/** 登録を消す。テストのためだけにある */
export function forgetScreens(): void {
  registered = undefined;
}

/** 画面を開く。`screens().rules({ kind: "self" })` のように使う */
export function screens(): Screens {
  if (registered === undefined) {
    throw new Error("画面の入口が登録されていません（extension.ts の registerScreens を通っていません）");
  }
  return registered;
}
