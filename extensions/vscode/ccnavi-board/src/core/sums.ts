/**
 * ルール管理とリスク管理が見せる「統合結果」（共通の設定 + 1 つの設定）の読み取り用の表示と、フェーズ管理が見せる共通の設定の定義を、
 * ボードの JSON（`sums[]`）から取り出す。
 *
 * 統合結果は実行ファイルが判定と同じ関数で組んで出す。ここは形を整えて渡すだけ。ワークスペースの統合結果は「共通の設定 + ワークスペースの設定」、プロジェクトの統合結果は
 * 「共通の設定 + そのプロジェクトの設定」で、パスを持たないツール（Bash など）が適用する、全プロジェクトの設定を足した和ではない。
 *
 * ここは vscode にも DOM にも触れない。拡張ホストと画面の両方から読まれる。
 */
import type { BoardJson, SumFactorJson, SumJson, SumRuleJson } from "./model.js";
import type { CommonPhase } from "./phases-view.js";

/** ルール管理が見せる統合結果 1 件 */
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

/** リスク管理が見せる統合結果 1 件 */
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

/** 統合結果を選ぶ欄の表記 */
export function sumLabel(name: string): string {
  return name === "self" ? "統合結果（共通 + ワークスペース）" : `統合結果（共通 + プロジェクト ${name}）`;
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
 * 最初に見せる統合結果の名前。開いている対象が自身かプロジェクトならその統合結果、共通の設定なら（共通だけの統合結果は無いので）
 * ワークスペースの統合結果。一覧に無ければ先頭。空なら空文字。
 */
export function defaultSum(sums: readonly { readonly name: string }[], target: { readonly kind: string; readonly name: string } | undefined): string {
  const want = target === undefined ? "self" : target.kind === "project" ? target.name : "self";
  return sums.find((s) => s.name === want)?.name ?? sums[0]?.name ?? "";
}

/** 出どころが共通の設定であることを言う `source` の値 */
const COMMON_SOURCE = "common";

/**
 * フェーズ管理が見せる、共通の設定の定義（読み取り専用）。統合結果（`sums[].phases.types`）のうち出どころが共通のもの。
 * 統合結果が無い（古い実行ファイル・その設定が対象でない）なら空。
 */
export function commonPhases(sum: SumJson | undefined): readonly CommonPhase[] {
  return (sum?.phases.types ?? [])
    .filter((t) => t.source === COMMON_SOURCE)
    .map((t) => ({ id: t.id, title: t.title, kind: t.kind, review: t.review, scope: t.scope }));
}
