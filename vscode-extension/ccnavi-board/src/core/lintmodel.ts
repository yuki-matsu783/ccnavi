/**
 * `ccnavi --lint --json`（lint の JSON）が出す形。実行ファイルと拡張の契約で、拡張はこれ以外を読まない。
 * 形の定義は ccnavi の README「lint の JSON」。
 *
 * 読み手はプロジェクト管理画面。プロジェクトごとの苦情（`projects/` が無視されていない、
 * `.claude/` を持つ）を拾って並べる。判定と同じ深刻度で届くので、
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
  /**
   * `--flow <パス>` を渡したときだけ在る。実行ファイルが読んだフローの中身（`data`。読めなければ null。
   * JSON にそのまま載らない値は `{"$ccnavi": ...}` の印）。無ければ、実行ファイルがフローを見たか分からない
   */
  readonly flow?: LintFlow;
}

export interface LintFlow {
  readonly path: string;
  readonly data: unknown;
  /** `SubagentStart` で担当に渡る手順の行（`flow.render`）。読めなければ null。古い実行ファイルなら無い */
  readonly rendered?: readonly string[] | null;
  /** フローで選べるサブエージェントとスキルの名前（`flow.catalog`）。古い実行ファイルなら無い */
  readonly candidates?: LintFlowCandidates;
}

/** 候補 1 件。`source` は `builtin`（組み込み）か `project`（ワークスペースの `.claude/` の下） */
export interface LintFlowCandidate {
  readonly name: string;
  readonly source: string;
}

export interface LintFlowCandidates {
  readonly agents: readonly LintFlowCandidate[];
  readonly skills: readonly LintFlowCandidate[];
}

function flowOf(raw: Record<string, unknown>): LintFlow {
  const rendered = Array.isArray(raw.rendered) ? raw.rendered.filter((l): l is string => typeof l === "string") : raw.rendered === null ? null : undefined;
  const cands = isRecord(raw.candidates) ? raw.candidates : undefined;
  const pick = (value: unknown): LintFlowCandidate[] =>
    list(value)
      .filter(isRecord)
      .map((c) => ({ name: str(c.name), source: str(c.source) }))
      .filter((c) => c.name !== "");
  return {
    path: str(raw.path),
    data: raw.data,
    ...(rendered === undefined ? {} : { rendered }),
    ...(cands === undefined ? {} : { candidates: { agents: pick(cands.agents), skills: pick(cands.skills) } }),
  };
}

export type ParsedLint = { readonly ok: true; readonly value: LintJson } | { readonly ok: false; readonly error: string };

export function parseLintJson(text: string): ParsedLint {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSON として読めません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSON の最上位がオブジェクトではありません" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== LINT_VERSION) {
    return {
      ok: false,
      error: `lint の JSON の版が違います（拡張は ${LINT_VERSION}、実行ファイルは ${String(raw.version)}）`,
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
      ...(isRecord(raw.flow) && "data" in raw.flow ? { flow: flowOf(raw.flow) } : {}),
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

/** `--lint --flow <パス>` の苦情の場所（実行ファイルの `lint.FLOW_WHERE`） */
export const FLOW_WHERE = "(flow)";

/**
 * 渡したフローについての苦情。`where` が `(flow)` のもの。フロー編集画面はこれだけを読む
 * （ほかの設定の苦情でフローの保存を止めない）。読めるか・形が正しいかの答えは実行ファイルが出し、
 * 拡張は並べるだけ
 */
export function problemsOfFlow(lint: LintJson): LintProblem[] {
  return lint.problems.filter((p) => p.where === FLOW_WHERE);
}

/**
 * 標準エラーが、実行ファイルの知らないオプションの苦情（argparse の `unrecognized arguments`）で、
 * そこに option が名指しされているか。`--version` を知らない古い実行ファイルを見分けるために使う
 * （`ccnavi.ts` の probeVersion）。ほかの新しいフラグを知っているかは、版の JSON の `flags` で見る
 */
export function unknownOption(stderr: string, option: string): boolean {
  return stderr.split(/\r?\n/).some((line) => line.includes("unrecognized arguments") && line.split(/\s+/).includes(option));
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
