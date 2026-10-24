/**
 * テンプレート管理画面が読む実行ファイルの答えの読み取り（`--templates --json`・`--templates --show <名前> --json`）。
 * 形の契約は `templates-view.ts`、見本は `tests/fixtures/templates-json.json`。
 *
 * 読むだけ（有効か・破棄した理由は実行ファイルが決めて答える）。読み方は `planordermodel.ts` と
 * `lintmodel.ts` に揃える。答えの版が違えば解釈せず、版の違いを伝える。VS Code の API も DOM も node も読まない。
 */
import { readPlans } from "./approvemodel.js";
import type {
  Dropped,
  DraftEntry,
  FlowSummary,
  PlanItem,
  PhaseTypeInfo,
  ProblemSeverity,
  Sum,
  SumRow,
  TemplateDetail,
  TemplateCheck,
  TemplateEntry,
  TemplateLayer,
  TemplateProblem,
  TemplatesJson,
} from "./templates-view.js";

/** 答えの版。欄を足すだけなら上がらない（`planordermodel.ts` の PLAN_ORDER_VERSION と同じ決まり） */
export const TEMPLATES_VERSION = 1;

export type TemplatesParse = { readonly ok: true; readonly value: TemplatesJson } | { readonly ok: false; readonly error: string };
export type TemplateShowParse = { readonly ok: true; readonly value: TemplateDetail } | { readonly ok: false; readonly error: string };

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
    return { ok: false, error: str(raw.error) || "テンプレートを読み込めませんでした" };
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

export type TemplateCheckParse = { readonly ok: true; readonly value: TemplateCheck } | { readonly ok: false; readonly error: string };

/** `--templates --check-file <パス> --json` の答えを読む。失敗の答え（`{ok:false,error}`）は error をそのまま返す */
export function parseTemplateCheck(text: string): TemplateCheckParse {
  const parsed = parseRecord(text);
  if (!parsed.ok) {
    return parsed;
  }
  const raw = parsed.value;
  if (raw.ok === false) {
    return { ok: false, error: str(raw.error) || "テンプレートを検査できませんでした" };
  }
  if (raw.version !== TEMPLATES_VERSION) {
    return { ok: false, error: versionMismatch(raw.version) };
  }
  for (const key of ["problems", "plans", "types"] as const) {
    if (!Array.isArray(raw[key])) {
      return { ok: false, error: `テンプレートの検査の JSON の ${key} 欄が配列ではありません` };
    }
  }
  const valid = bool(raw.valid);
  // 読めたと言うのに中身が無い答えは、見比べる相手が無いので受けない
  if (valid && !isRecord(raw.data)) {
    return { ok: false, error: "テンプレートの検査の JSON の data 欄がありません。ccnavi が古い可能性があります。実行ファイルを更新してください" };
  }
  return {
    ok: true,
    value: {
      version: TEMPLATES_VERSION,
      layer: str(raw.layer),
      name: str(raw.name),
      sha: str(raw.sha),
      valid,
      problems: readProblems(raw.problems),
      data: valid ? raw.data : null,
      plans: valid ? readPlans(raw.plans) : [],
      types: list(raw.types)
        .filter(isRecord)
        .map((t): PhaseTypeInfo => ({ id: str(t.id), title: str(t.title), kind: str(t.kind), review: str(t.review) })),
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

/** 本文を読むときの層。`project` の対象は層の名前（その層）、それ以外は共通との統合結果の self */
export function templatesLayerOf(target: { readonly kind: string; readonly name: string }): string {
  return target.kind === "project" ? target.name : "self";
}

/** 対象が見せる層の名前。共通の設定は common、ワークスペースは self、プロジェクトはその名前（編集できるのはこの層のテンプレートだけ） */
export function shownLayerOf(target: { readonly kind: string; readonly name: string }): string {
  if (target.kind === "workspace") {
    return "common";
  }
  return target.kind === "project" ? target.name : "self";
}

/** テンプレートの名前に使える形（実行ファイルの `NAME_RE` と上限と同じ。ファイル名になるので、読ませる前にここでも断る） */
export function validTemplateName(name: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) && name.length <= 64;
}

/**
 * 新しいテンプレートの名前が、共通の層か `layer` の層の既存の名前と重なるか。大文字小文字は区別しない（別の機械で 2 本が 1 本に重なる）。
 * 重なる既存の 1 件を返す。無ければ undefined
 */
export function templateNameTaken(layers: readonly TemplateLayer[], layer: string, name: string): { readonly layer: string; readonly name: string } | undefined {
  const folded = name.toLowerCase();
  for (const l of layers) {
    if (l.name !== "common" && l.name !== layer) {
      continue;
    }
    const hit = l.templates.find((t) => t.name.toLowerCase() === folded);
    if (hit !== undefined) {
      return { layer: l.name, name: hit.name };
    }
  }
  return undefined;
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
    valid: bool(raw.valid),
    description: str(raw.description),
    when: str(raw.when),
    plan: readPlanItems(raw.plan),
    feedback: readPlanItems(raw.feedback),
    has_flows: bool(raw.has_flows),
    flows: readFlows(raw.flows),
    problems: readProblems(raw.problems),
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

/** 破棄した理由。無いか null なら null */
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
    return { ok: false, error: `JSON として解釈できません: ${(error as Error).message}` };
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
