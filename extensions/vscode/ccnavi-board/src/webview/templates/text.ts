/**
 * テンプレート管理画面に出す言葉。判定は実行ファイルの答えが決めていて、ここは答えの値を読みやすい言葉に直すだけ。
 * 拡張が有効かどうかや捨てた理由を自分で判定することはしない。
 */
import type { PlanItem, ProblemSeverity, FlowSummary, TemplateEntry, TemplateLayer, TemplateProblem } from "../../core/templates-view.js";

/** 深刻度の言葉。表示は `[error]` の形にする（`Problems.tsx`） */
export function severityLabel(severity: ProblemSeverity): string {
  return severity;
}

/** 捨てた理由の言葉。知らない値はそのまま返す */
export function droppedLabel(reason: string): string {
  switch (reason) {
    case "invalid":
      return "読めない";
    case "duplicate":
      return "共通と同じ中身";
    case "conflict":
      return "共通と食い違う";
    default:
      return reason;
  }
}

/** ツリーの言葉。ワークスペースルートは空の文字なので、名前を付ける */
export function treeLabel(tree: string): string {
  return tree === "" ? "ワークスペース" : tree;
}

/** 層の言葉。共通・ワークスペース（self）・プロジェクトの名前 */
export function layerLabel(layer: string): string {
  if (layer === "common") {
    return "共通";
  }
  if (layer === "self") {
    return "ワークスペース";
  }
  return `プロジェクト ${layer}`;
}

/** 対象に見える層。共通の設定は共通だけ、プロジェクトは共通と自分、ワークスペースは共通と自分（self） */
export function layersFor(target: { readonly kind: string; readonly name: string }, layers: readonly TemplateLayer[]): TemplateLayer[] {
  return layers.filter((layer) => {
    if (target.kind === "workspace") {
      return layer.name === "common";
    }
    if (target.kind === "project") {
      return layer.name === "common" || layer.name === target.name;
    }
    return layer.name === "common" || layer.name === "self";
  });
}

/** 対象に見えるテンプレート。共通の設定は共通の層のものだけ、ほかは足し算の結果すべて */
export function templatesFor(target: { readonly kind: string; readonly name: string }, templates: readonly TemplateEntry[]): TemplateEntry[] {
  return templates.filter((entry) => target.kind !== "workspace" || entry.layer === "common");
}

/** フローの要約の 1 行 */
export function flowText(flow: FlowSummary): string {
  return `${flow.name}（ノード ${flow.nodes}・接続 ${flow.connections}）`;
}

/** 待つ項の番号の並び。空なら「なし」 */
export function afterText(after: readonly number[]): string {
  return after.length === 0 ? "なし" : after.join(", ");
}

/**
 * 待つ項を、計画の中の添字（`index`）から提案に写したときの番号（`number`）に直す。
 * 見つからない添字は、そのまま添字を返す。
 */
export function afterNumbers(item: PlanItem, items: readonly PlanItem[]): number[] {
  return item.after.map((at) => items.find((other) => other.index === at)?.number ?? at);
}

/** 本文の問題が、下書きの TODO（書きかけ）を指しているか */
export function isTodo(problem: TemplateProblem): boolean {
  return problem.detail.includes("TODO");
}
