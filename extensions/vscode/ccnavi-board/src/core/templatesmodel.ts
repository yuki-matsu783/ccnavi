/**
 * テンプレート管理画面が読む実行ファイルの答えの読み取り（`--templates --json`・`--templates --show <名前> --json`・
 * `--templates --import … --json`）。形の契約は `templates-view.ts`、見本は `tests/fixtures/templates-json.json`。
 *
 * 読むだけで判定はしない（有効か・捨てた理由・取り込めるかは実行ファイルが決めて答える）。読み方は `planordermodel.ts` と
 * `lintmodel.ts` に揃える。答えの版が違えば解釈せず、版の違いを伝える。VS Code の API も DOM も node も読まない。
 */
import type {
  Dropped,
  DraftEntry,
  FlowSummary,
  ImportAnswer,
  ImportDraftRef,
  PlanItem,
  ProblemSeverity,
  Sum,
  SumRow,
  TemplateDetail,
  TemplateEntry,
  TemplateLayer,
  TemplateProblem,
  TemplatesJson,
} from "./templates-view.js";

/** 答えの版。欄を足すだけなら上がらない（`planordermodel.ts` の PLAN_ORDER_VERSION と同じ決まり） */
export const TEMPLATES_VERSION = 1;

export type TemplatesParse = { readonly ok: true; readonly value: TemplatesJson } | { readonly ok: false; readonly error: string };
export type TemplateShowParse = { readonly ok: true; readonly value: TemplateDetail } | { readonly ok: false; readonly error: string };
export type ImportParse = { readonly ok: true; readonly value: ImportAnswer } | { readonly ok: false; readonly error: string };

/** `--templates --json` の答えを読む。版が違う・欄が足りないときは文面を返す */
export function parseTemplates(text: string): TemplatesParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== TEMPLATES_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  // 版は同じでも、下書きの欄が無いのは古い実行ファイル。専用の文で言う
  if (raw.drafts === undefined) {
    return { ok: false, error: "drafts 欄がありません。ccnavi が古い可能性があります。実行ファイルを更新してください" };
  }
  for (const key of ["templates", "layers", "sums", "drafts"] as const) {
    if (!Array.isArray(raw[key])) {
      return { ok: false, error: `テンプレートの JSON の ${key} 欄が配列ではありません` };
    }
  }
  for (const key of ["errors", "warns", "infos"] as const) {
    if (typeof raw[key] !== "number") {
      return { ok: false, error: `テンプレートの JSON の ${key} 欄が数ではありません` };
    }
  }
  return {
    ok: true,
    value: {
      version: TEMPLATES_VERSION,
      root: str(raw.root),
      layer: str(raw.layer),
      keywords: strings(raw.keywords),
      templates: list(raw.templates).filter(isRecord).map(readEntry),
      layers: list(raw.layers).filter(isRecord).map(readLayer),
      sums: list(raw.sums).filter(isRecord).map(readSum),
      errors: num(raw.errors),
      warns: num(raw.warns),
      infos: num(raw.infos),
      drafts: list(raw.drafts).filter(isRecord).map(readDraft),
    },
  };
}

/** `--templates --show <名前> --json` の答えを読む。失敗の答え（`{ok:false,error}`）は error をそのまま返す */
export function parseTemplateShow(text: string): TemplateShowParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.ok === false) {
    return { ok: false, error: str(raw.error) || "テンプレートを読めませんでした" };
  }
  if (raw.version !== TEMPLATES_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  const template = raw.template;
  if (!isRecord(template)) {
    return { ok: false, error: "テンプレートの答えに template 欄がありません" };
  }
  const body = template.text;
  if (typeof body !== "string") {
    return { ok: false, error: "テンプレートの本文（text）が文字列ではありません" };
  }
  return { ok: true, value: { ...readEntry(template), text: body } };
}

/**
 * `--templates --import … --json` の答えを読む。`draft` 欄を持つ答えは（下見でエラーを返したものも含めて）
 * `ok` が偽でも中身を返す。`draft` 欄の無い失敗の答え（`{version, ok:false, error}`）は error を返す。
 */
export function parseImport(text: string): ImportParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.version !== TEMPLATES_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  if (!("draft" in raw)) {
    return { ok: false, error: str(raw.error) || "取り込みの答えに draft 欄がありません" };
  }
  return {
    ok: true,
    value: {
      ok: raw.ok === true,
      draft: isRecord(raw.draft) ? readDraftRef(raw.draft) : null,
      to: str(raw.to),
      layer: str(raw.layer),
      target: str(raw.target),
      exists: bool(raw.exists),
      replace: bool(raw.replace),
      errors: strings(raw.errors),
      infos: strings(raw.infos),
      problems: readProblems(raw.problems),
      diff: strings(raw.diff),
      new_text: str(raw.new_text),
      written: bool(raw.written),
      removed: bool(raw.removed),
      kept: str(raw.kept),
    },
  };
}

/**
 * 読み違えを誘う文字を `\uXXXX` に直して見せる。制御文字（Cc）と書式文字（Cf）・行と段落の区切り（Zl・Zp）が対象で、
 * 改行とタブは残す。BMP の外の文字は `\UXXXXXXXX`。実行ファイルの `plan_templates.reveal` と同じ形。
 */
export function revealControls(text: string): string {
  let out = "";
  for (const ch of text) {
    out += ch === "\n" || ch === "\t" || !HIDDEN.test(ch) ? ch : escapeChar(ch);
  }
  return out;
}

/** 本文を読むときの層。`project` の対象は層の名前（その層）、それ以外は共通との足し算の self */
export function templatesLayerOf(target: { readonly kind: string; readonly name: string }): string {
  return target.kind === "project" ? target.name : "self";
}

/** 書き方が読み違いを誘う文字（`revealControls` が直す対象）。改行とタブは含めない */
const HIDDEN = /^[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]$/u;

function escapeChar(ch: string): string {
  const code = ch.codePointAt(0) ?? 0;
  return code <= 0xffff ? `\\u${code.toString(16).padStart(4, "0")}` : `\\U${code.toString(16).padStart(8, "0")}`;
}

// ---- 入れ子の読み取り

function readEntry(raw: Record<string, unknown>): TemplateEntry {
  return {
    name: str(raw.name),
    layer: str(raw.layer),
    path: str(raw.path),
    sha: str(raw.sha),
    valid: bool(raw.valid),
    description: str(raw.description),
    when: str(raw.when),
    plan: readPlanItems(raw.plan),
    feedback: readPlanItems(raw.feedback),
    has_flows: bool(raw.has_flows),
    flows: readFlows(raw.flows),
    effective: bool(raw.effective),
    usable: bool(raw.usable),
    dropped: readDropped(raw.dropped),
    writable: bool(raw.writable),
    problems: readProblems(raw.problems),
  };
}

function readDraft(raw: Record<string, unknown>): DraftEntry {
  return {
    name: str(raw.name),
    tree: str(raw.tree),
    path: str(raw.path),
    sha: str(raw.sha),
    valid: bool(raw.valid),
    description: str(raw.description),
    when: str(raw.when),
    plan: readPlanItems(raw.plan),
    feedback: readPlanItems(raw.feedback),
    has_flows: bool(raw.has_flows),
    flows: readFlows(raw.flows),
    problems: readProblems(raw.problems),
    importable: bool(raw.importable),
    checked_with: str(raw.checked_with),
    exists_in: strings(raw.exists_in),
    also_in: strings(raw.also_in),
  };
}

function readLayer(raw: Record<string, unknown>): TemplateLayer {
  return {
    name: str(raw.name),
    path: str(raw.path),
    exists: bool(raw.exists),
    unreadable: str(raw.unreadable),
    writable: bool(raw.writable),
    problems: readProblems(raw.problems),
    templates: list(raw.templates).filter(isRecord).map(readEntry),
  };
}

function readSum(raw: Record<string, unknown>): Sum {
  return {
    name: str(raw.name),
    layers: strings(raw.layers),
    templates: list(raw.templates).filter(isRecord).map(readSumRow),
  };
}

function readSumRow(raw: Record<string, unknown>): SumRow {
  return {
    name: str(raw.name),
    layer: str(raw.layer),
    effective: bool(raw.effective),
    dropped: readDropped(raw.dropped),
  };
}

function readDraftRef(raw: Record<string, unknown>): ImportDraftRef {
  return { name: str(raw.name), tree: str(raw.tree), path: str(raw.path), sha: str(raw.sha) };
}

function readProblems(value: unknown): TemplateProblem[] {
  return list(value)
    .filter(isRecord)
    .map((p) => ({ severity: severityOf(p.severity), detail: str(p.detail) }));
}

function severityOf(value: unknown): ProblemSeverity {
  return value === "error" || value === "info" || value === "warn" ? value : "warn";
}

function readPlanItems(value: unknown): PlanItem[] {
  return list(value)
    .filter(isRecord)
    .map((p) => ({
      index: num(p.index),
      number: num(p.number),
      type: str(p.type),
      review: str(p.review),
      after: numbers(p.after),
    }));
}

function readFlows(value: unknown): FlowSummary[] {
  return list(value)
    .filter(isRecord)
    .map((f) => ({
      index: num(f.index),
      name: str(f.name),
      nodes: num(f.nodes),
      connections: num(f.connections),
      node_types: counts(f.node_types),
    }));
}

/** 捨てた理由。無いか null なら null */
function readDropped(value: unknown): Dropped | null {
  return isRecord(value) ? { reason: str(value.reason), detail: str(value.detail) } : null;
}

// ---- 共通の部品

/** JSON として読み、最上位がオブジェクトのものを返す。読めなければ文面 */
function parseRecord(text: string): { readonly ok: true; readonly value: Record<string, unknown> } | { readonly ok: false; readonly error: string } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  return { ok: true, value: raw };
}

function versionMismatch(version: unknown): string {
  return `テンプレートの JSON の形が違います（拡張は ${TEMPLATES_VERSION}、実行ファイルは ${String(version)}）`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return list(value).filter((v): v is string => typeof v === "string");
}

function numbers(value: unknown): number[] {
  return list(value).filter((v): v is number => typeof v === "number");
}

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function bool(value: unknown): boolean {
  return value === true;
}

function counts(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (isRecord(value)) {
    for (const [key, count] of Object.entries(value)) {
      if (typeof count === "number") {
        out[key] = count;
      }
    }
  }
  return out;
}
