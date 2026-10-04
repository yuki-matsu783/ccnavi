/**
 * フロー編集画面に出す言葉。ノードのバッジと、1 行の要約。
 *
 * バッジは**種類の性質**を言うだけで、良し悪しは言わない。止まる・戻るを決めるのは実行ファイルと、
 * サブエージェントに渡る道具（AskUserQuestion はどのサブエージェントにも渡らない。入れ子の上限では
 * Agent ツールが渡らない。付録 C）。
 */
import { branchItems, dataText, nodeType, type FlowNode } from "../../core/flow-doc.js";

export interface Badge {
  readonly kind: string;
  readonly text: string;
  readonly title: string;
}

/** 種類に付くバッジ。無ければ undefined */
export function badgeOf(type: string): Badge | undefined {
  if (type === "askUserQuestion") {
    return {
      kind: "ask",
      text: "メインに戻る（ユーザに聞く）",
      title: "サブエージェントはユーザに質問できません（AskUserQuestion は渡されません）。このノードで手を止め、問いと選択肢を添えてメインに返します。メインがユーザに聞き、その答えを渡して同じサブエージェントを再開させます",
    };
  }
  if (type === "subAgent" || type === "subAgentFlow") {
    return {
      kind: "nest",
      text: "入れ子（上限なら戻る）",
      title: "Agent ツールがあれば、入れ子のサブエージェントとして起動します。入れ子の上限（既定はメインの下 3 段、クラウドの環境では 1 段）に達して Agent ツールが無ければ、このノードで止まってメインに返します",
    };
  }
  return undefined;
}

function line(text: string, limit = 60): string {
  const shown = text.split(/\s+/).filter((part) => part !== "").join(" ");
  return shown.length > limit ? `${shown.slice(0, limit)}…` : shown;
}

/** ノードの中身の 1 行。種類を知らないノードは空 */
export function summaryOf(node: FlowNode): string {
  switch (nodeType(node)) {
    case "prompt":
    case "codex":
      return line(dataText(node, "prompt"));
    case "subAgent":
      return line(dataText(node, "description") || dataText(node, "prompt"));
    case "askUserQuestion":
      return line(dataText(node, "questionText"));
    case "ifElse":
    case "switch":
    case "branch": {
      const target = dataText(node, "evaluationTarget");
      return line(target !== "" ? target : branchItems(node).map((b) => String(b.label ?? "")).join(" / "));
    }
    case "skill":
      return line(dataText(node, "name"));
    case "mcp":
      return line([dataText(node, "serverId"), dataText(node, "toolName")].filter((p) => p !== "").join(" / "));
    default:
      return line(dataText(node, "label"));
  }
}
