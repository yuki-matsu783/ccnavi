/**
 * `ccnavi --lint --json`（lint の JSON）が出す形。実行ファイルと拡張の契約で、拡張はこれ以外を読まない。
 * 形の定義は ccnavi の README「lint の JSON」。
 *
 * 読み手はプロジェクト管理画面。プロジェクトごとの苦情（`projects/` が無視されていない、
 * `config/rules.yml` が無い、`.claude/` を持つ）を拾って並べる。判定と同じ深刻度で届くので、
 * 拡張は自分で `.gitignore` やルールの中身を解釈し直さない。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const LINT_VERSION = 1;

export type Severity = "error" | "warn";

export interface LintProblem {
  readonly severity: Severity;
  /** 人向けの文面で `error:` の後ろに出る場所。`(projects/lib) rule-id` など。ファイル全体への苦情なら空 */
  readonly where: string;
  readonly detail: string;
}

export interface LintJson {
  readonly version: number;
  readonly root: string;
  readonly rules: string;
  readonly mode: string;
  readonly ticket_control: string;
  readonly projects: readonly string[];
  readonly problems: readonly LintProblem[];
  readonly errors: number;
  readonly warns: number;
}

export type ParsedLint = { readonly ok: true; readonly value: LintJson } | { readonly ok: false; readonly error: string };

export function parseLintJson(text: string): ParsedLint {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めない: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではない" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== LINT_VERSION) {
    return {
      ok: false,
      error: `lint の JSON の版が違う（拡張は ${LINT_VERSION}、実行ファイルは ${String(raw.version)}）`,
    };
  }
  const problems = list(raw.problems).filter(isRecord).map(problem);
  const errors = problems.filter((p) => p.severity === "error").length;
  return {
    ok: true,
    value: {
      version,
      root: str(raw.root),
      rules: str(raw.rules),
      mode: str(raw.mode),
      ticket_control: str(raw.ticket_control),
      projects: list(raw.projects).map(str),
      problems,
      errors: typeof raw.errors === "number" ? raw.errors : errors,
      warns: typeof raw.warns === "number" ? raw.warns : problems.length - errors,
    },
  };
}

function problem(raw: Record<string, unknown>): LintProblem {
  return {
    severity: raw.severity === "error" ? "error" : "warn",
    where: str(raw.where),
    detail: str(raw.detail),
  };
}

/** そのプロジェクトについての苦情。`where` が `(projects/<名前>)` で始まるもの */
export function problemsOfProject(lint: LintJson, name: string): LintProblem[] {
  const prefix = `(projects/${name})`;
  return lint.problems.filter((p) => p.where === prefix || p.where.startsWith(`${prefix} `));
}

/** 置き場そのものについての苦情。`where` が `(projects)` */
export function problemsOfProjectsDir(lint: LintJson): LintProblem[] {
  return lint.problems.filter((p) => p.where === "(projects)");
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
