/**
 * テンプレート管理画面に出す言葉。判定は実行ファイルの答えが決めていて、ここは答えの値を読みやすい言葉に直すだけ。
 */
import { shownLayerOf } from "../../core/templatesmodel.js";
import type { PlanItem, FlowSummary, TemplateLayer, TemplateProblem } from "../../core/templates-view.js";
import { layerLabel as coreLayerLabel } from "../../core/sums.js";

/** 破棄した理由の言葉。知らない値はそのまま返す */
export function droppedLabel(reason: string): string {
  switch (reason) {
    case "invalid":
      return "読み込み不可";
    case "duplicate":
      return "共通設定と同じ内容";
    case "conflict":
      return "共通設定と内容が違う";
    default:
      return reason;
  }
}

/** ツリーの言葉。ワークスペースルートは空の文字なので、名前を付ける */
export function treeLabel(tree: string): string {
  return tree === "" ? "ワークスペース" : tree;
}

/** 設定の言葉。共通・ワークスペース（self）・プロジェクト（名前）。言い方は`core/sums.ts`と同じ */
export const layerLabel = coreLayerLabel;

/** 対象が見せる設定。実行ファイルが返していなければundefined */
export function layerFor(target: { readonly kind: string; readonly name: string }, layers: readonly TemplateLayer[]): TemplateLayer | undefined {
  const name = shownLayerOf(target);
  return layers.find((layer) => layer.name === name);
}

/** フローの要約の1行 */
export function flowText(flow: FlowSummary): string {
  return `${flow.name}（ノード${flow.nodes}・線${flow.connections}）`;
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

/** 本文の問題が、下書きのTODO（書きかけ）を指しているか */
export function isTodo(problem: TemplateProblem): boolean {
  return problem.detail.includes("TODO");
}
