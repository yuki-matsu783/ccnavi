/**
 * ルール管理とリスク管理が見せる「足し算」（共通の設定 + 1 つの設定）の読み取り用の表示を、ボードの JSON（`sums[]`）から取り出す。
 *
 * 足し算の結果は実行ファイルが判定と同じ関数で組んで出す。ここは 足し直さず、形を整えて渡すだけ
 * （拡張は判定も合成もしない）。ワークスペースの足し算は「共通の設定 + ワークスペースの設定」、プロジェクトの足し算は
 * 「共通の設定 + そのプロジェクトの設定」で、パスを持たないツール（Bash など）が当てる、全プロジェクトの設定を足した和ではない。
 *
 * ここは vscode にも DOM にも触れない。拡張ホストと画面の両方から読まれる。
 */
import type { BoardJson, SumFactorJson, SumJson, SumRuleJson } from "./model.js";

/** ルール管理が見せる足し算 1 件 */
export interface RulesSum {
  /** `self` / プロジェクトの名前 */
  readonly name: string;
  /** 欄に出す名前 */
  readonly label: string;
  readonly layers: readonly string[];
  /** そのレイヤーのルールファイル（ワークスペースルートからの相対のこともある。実行ファイルが出したまま） */
  readonly path: string;
  /** 実行ファイルがそのファイルを読めず、空として扱っている理由。空なら読めた */
  readonly unreadable: string;
  /** そのレイヤーのファイルが無い（「設定が無い」正常な状態） */
  readonly missing: boolean;
  readonly deny: readonly SumRuleJson[];
  readonly ask: readonly SumRuleJson[];
  readonly allow: readonly SumRuleJson[];
}

/** リスク管理が見せる足し算 1 件 */
export interface RiskSum {
  readonly name: string;
  readonly label: string;
  readonly layers: readonly string[];
  /** リスクレベルの境目の点（実際に使う値） */
  readonly levels: SumJson["risk"]["levels"];
  readonly factors: readonly SumFactorJson[];
  /** 内容が不正で空に戻したときの理由。空なら無い */
  readonly fallback: string;
  readonly problems: readonly string[];
}

/** 足し算を選ぶ欄の表記 */
export function sumLabel(name: string): string {
  return name === "self" ? "ワークスペースの足し算（共通の設定 + ワークスペースの設定）" : `プロジェクト ${name} の足し算（共通の設定 + ${name} の設定）`;
}

export function rulesSums(board: BoardJson | undefined): readonly RulesSum[] {
  return (board?.sums ?? []).map((sum) => ({
    name: sum.name,
    label: sumLabel(sum.name),
    layers: sum.layers,
    path: sum.rules.path,
    unreadable: sum.rules.unreadable,
    missing: sum.rules.missing,
    deny: sum.rules.deny,
    ask: sum.rules.ask,
    allow: sum.rules.allow,
  }));
}

export function riskSums(board: BoardJson | undefined): readonly RiskSum[] {
  return (board?.sums ?? []).map((sum) => ({
    name: sum.name,
    label: sumLabel(sum.name),
    layers: sum.layers,
    levels: sum.risk.levels,
    factors: sum.risk.factors,
    fallback: sum.risk.fallback,
    problems: sum.risk.problems,
  }));
}

/**
 * 最初に見せる足し算の名前。開いている対象が自身かプロジェクトならその足し算、共通の設定なら（共通だけの足し算は無いので）
 * ワークスペースの足し算。一覧に無ければ先頭。空なら空文字。
 */
export function defaultSum(sums: readonly { readonly name: string }[], target: { readonly kind: string; readonly name: string } | undefined): string {
  const want = target === undefined ? "self" : target.kind === "project" ? target.name : "self";
  return sums.find((s) => s.name === want)?.name ?? sums[0]?.name ?? "";
}
