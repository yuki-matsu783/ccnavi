/**
 * テンプレート管理画面の案内の見本（`webview/tours/templates.yml` の `sample:`）を、画面が描く形にする。
 *
 * 見本は実行ファイルの JSON（`--templates --json`）より小さい書き方で YAML に書き、ここで `TemplatesPage`（画面が描く形）にする。
 * 案内の間に描くだけ。有効か・破棄した理由・取り込めるかは見本に書いた値をそのまま使う。
 */
import type { DraftEntry, FlowSummary, PlanItem, ProblemSeverity, Sum, SumRow, TemplateEntry, TemplateProblem, TemplatesPage } from "./templates-view.js";

export interface TemplatesSample {
  /** 見本の画面（対象はワークスペース、見せる層は self） */
  readonly page: TemplatesPage;
  /** テンプレートの名前 → 本文（「本文を見る」の見本） */
  readonly texts: Readonly<Record<string, string>>;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

function list(value: unknown, at: string): unknown[] {
  if (value === undefined || value === null) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error(`${at} がリストではない`);
  }
  return value;
}

/** 欄の文字。無いか null なら空 */
function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

const SEVERITIES: readonly ProblemSeverity[] = ["error", "warn", "info"];

function problemsOf(value: unknown, at: string): TemplateProblem[] {
  return list(value, at).map((item, index) => {
    const map = object(item, `${at}[${index}]`);
    const severity = SEVERITIES.find((s) => s === map.severity) ?? "warn";
    return { severity, detail: str(map.detail) };
  });
}

/** 計画の 1 ステップ。`index` は並びの添字、`number` は添字 + 1 */
function planOf(value: unknown, at: string): PlanItem[] {
  return list(value, at).map((item, index) => {
    const map = object(item, `${at}[${index}]`);
    return {
      index,
      number: index + 1,
      type: str(map.type),
      review: map.review === true,
      deferred: map.deferred === true,
      after: list(map.after, `${at}[${index}].after`).map((n) => num(n)),
    };
  });
}

function flowsOf(value: unknown, at: string): FlowSummary[] {
  return list(value, at).map((item, index) => {
    const map = object(item, `${at}[${index}]`);
    return { index, name: str(map.name), nodes: num(map.nodes), connections: num(map.connections), node_types: {} };
  });
}

function droppedOf(value: unknown, at: string): { reason: string; detail: string } | null {
  if (value === undefined || value === null) {
    return null;
  }
  const map = object(value, at);
  return { reason: str(map.reason), detail: str(map.detail) };
}

export function parseTemplatesSample(raw: unknown): TemplatesSample {
  const root = object(raw, "sample");
  const path = str(root.path);
  const texts: Record<string, string> = {};
  const templates: TemplateEntry[] = list(root.templates, "sample.templates").map((item, index) => {
    const at = `sample.templates[${index}]`;
    const map = object(item, at);
    const name = str(map.name);
    const dropped = droppedOf(map.dropped, `${at}.dropped`);
    const problems = problemsOf(map.problems, `${at}.problems`);
    const flows = flowsOf(map.flows, `${at}.flows`);
    const effective = dropped === null;
    texts[name] = str(map.text);
    return {
      name,
      layer: "self",
      path: `${path}${name}.yml`,
      sha: `sample-${name}`,
      valid: true,
      description: str(map.description),
      when: str(map.when),
      plan: planOf(map.plan, `${at}.plan`),
      feedback: planOf(map.feedback, `${at}.feedback`),
      has_flows: flows.length > 0,
      flows,
      effective,
      usable: effective && !problems.some((p) => p.severity === "error"),
      dropped,
      writable: true,
      problems,
    };
  });
  const drafts: DraftEntry[] = list(root.drafts, "sample.drafts").map((item, index) => {
    const at = `sample.drafts[${index}]`;
    const map = object(item, at);
    const name = str(map.name);
    const flows = flowsOf(map.flows, `${at}.flows`);
    return {
      name,
      tree: "",
      path: `${name}.yml`,
      valid: true,
      description: str(map.description),
      when: str(map.when),
      plan: planOf(map.plan, `${at}.plan`),
      feedback: planOf(map.feedback, `${at}.feedback`),
      has_flows: flows.length > 0,
      flows,
      problems: problemsOf(map.problems, `${at}.problems`),
    };
  });
  const sums: Sum[] = list(root.sums, "sample.sums").map((item, index) => {
    const at = `sample.sums[${index}]`;
    const map = object(item, at);
    const rows: SumRow[] = list(map.templates, `${at}.templates`).map((rowItem, rowIndex) => {
      const row = object(rowItem, `${at}.templates[${rowIndex}]`);
      const dropped = droppedOf(row.dropped, `${at}.templates[${rowIndex}].dropped`);
      return { name: str(row.name), layer: str(row.layer), effective: dropped === null, dropped };
    });
    return { name: str(map.name), layers: list(map.layers, `${at}.layers`).map((layer) => str(layer)), templates: rows };
  });
  const all = [...templates.flatMap((t) => t.problems), ...drafts.flatMap((d) => d.problems)];
  const page: TemplatesPage = {
    root: "",
    generatedAt: "",
    target: { kind: "self", name: "" },
    targets: [],
    data: {
      version: 1,
      root: "",
      layer: "self",
      keywords: [],
      templates: [],
      layers: [{ name: "self", path, exists: true, unreadable: "", writable: true, problems: [], templates }],
      sums,
      errors: all.filter((p) => p.severity === "error").length,
      warns: all.filter((p) => p.severity === "warn").length,
      infos: all.filter((p) => p.severity === "info").length,
      drafts,
    },
    // 案内の間は取り込み待ち画面を開かない（見本の上では何も始めない）
    handoff: { enabled: false, reason: "案内の間は開けません" },
    notices: [],
  };
  return { page, texts };
}
