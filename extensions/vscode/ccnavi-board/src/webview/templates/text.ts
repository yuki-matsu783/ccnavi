/**
 * テンプレート管理画面に出す言葉。判定は実行ファイルの答えが決めていて、ここは答えの値を読みやすい言葉に直すだけ。
 */
import { shownLayerOf } from "../../core/templatesmodel.js";
import type { PlanItem, ProblemSeverity, FlowSummary, TemplateLayer, TemplateProblem } from "../../core/templates-view.js";
import { layerLabel as coreLayerLabel } from "../../core/sums.js";

/** 深刻度の言葉。表示は `[error]` の形にする（`Problems.tsx`） */
export function severityLabel(severity: ProblemSeverity): string {
  return severity;
}

/** 破棄した理由の言葉。知らない値はそのまま返す */
export function droppedLabel(reason: string): string {
  switch (reason) {
    case "invalid":
      return "読み込めません";
    case "duplicate":
      return "共通と同じ中身";
    case "conflict":
      return "共通と食い違います";
    default:
      return reason;
  }
}

/** ツリーの言葉。ワークスペースルートは空の文字なので、名前を付ける */
export function treeLabel(tree: string): string {
  return tree === "" ? "ワークスペース" : tree;
}

/** 層の言葉。共通・ワークスペース（self）・プロジェクト（名前）。言い方は `core/sums.ts` と同じ */
export const layerLabel = coreLayerLabel;

/** 対象が見せる層。実行ファイルが返していなければ undefined */
export function layerFor(target: { readonly kind: string; readonly name: string }, layers: readonly TemplateLayer[]): TemplateLayer | undefined {
  const name = shownLayerOf(target);
  return layers.find((layer) => layer.name === name);
}

/** フローの要約の 1 行 */
export function flowText(flow: FlowSummary): string {
  return `${flow.name}（ノード ${flow.nodes}・接続 ${flow.connections}）`;
}

/** 先行ステップの番号の並び。空なら「なし」 */
export function afterText(after: readonly number[]): string {
  return after.length === 0 ? "なし" : after.join(", ");
}

/**
 * 先行ステップを、計画の中の添字（`index`）から提案にコピーしたときの番号（`number`）に直す。
 * 見つからない添字は、そのまま添字を返す。
 */
export function afterNumbers(item: PlanItem, items: readonly PlanItem[]): number[] {
  return item.after.map((at) => items.find((other) => other.index === at)?.number ?? at);
}

/** 本文の問題が、下書きの TODO（書きかけ）を指しているか */
export function isTodo(problem: TemplateProblem): boolean {
  return problem.detail.includes("TODO");
}
