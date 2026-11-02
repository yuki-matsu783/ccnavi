/**
 * `ccnavi --lint --json`（lintのJSON）が出す形。実行ファイルと拡張の契約で、拡張はこれ以外を読まない。
 * 形の定義はccnaviのdocs/11_requirements/json-shapes.md「lintのJSON」。
 *
 * 読み手はプロジェクト管理画面。プロジェクトごとの苦情（`projects/`が無視されていない、
 * プロジェクトの中にワークツリーがある）を拾って並べる。判定と同じ深刻度で届くので、
 * 拡張は自分で`.gitignore`やルールの中身を解釈し直さない。
 */

/** 拡張が読める版。実行ファイルが違う版を出したら、解釈せずに版の違いを伝える */
export const LINT_VERSION = 1;

export type Severity = "error" | "warn";

export interface LintProblem {
  readonly severity: Severity;
  /** ユーザ向けの文面で`error:`の後ろに出る場所。`(projects/lib) rule-id`など。ファイル全体への苦情なら空 */
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
   * `--flow <パス>`を渡したときだけ在る。実行ファイルが読んだフローの中身（`data`。読めなければnull。
   * JSONにそのまま載らない値は`{"$ccnavi": ...}`のマーカー）。無ければ、実行ファイルがフローを見たか分からない
   */
  readonly flow?: LintFlow;
}

export interface LintFlow {
  readonly path: string;
  readonly data: unknown;
  /** `SubagentStart`で担当に渡る手順の行（`flow_render.render`）。読めなければnull */
  readonly rendered: readonly string[] | null;
  /** フローで選べるサブエージェントとスキルの名前（`flow.catalog`） */
  readonly candidates: LintFlowCandidates;
}

/** 候補1件。`source`は`builtin`（組み込み）か`project`（ワークスペースの`.claude/`の下） */
export interface LintFlowCandidate {
  readonly name: string;
  readonly source: string;
}

export interface LintFlowCandidates {
  readonly agents: readonly LintFlowCandidate[];
  readonly skills: readonly LintFlowCandidate[];
}

function flowOf(raw: Record<string, unknown>): LintFlow {
  const rendered = Array.isArray(raw.rendered) ? raw.rendered.filter((l): l is string => typeof l === "string") : null;
  const cands = isRecord(raw.candidates) ? raw.candidates : {};
  const pick = (value: unknown): LintFlowCandidate[] =>
    list(value)
      .filter(isRecord)
      .map((c) => ({ name: str(c.name), source: str(c.source) }))
      .filter((c) => c.name !== "");
  return {
    path: str(raw.path),
    data: raw.data,
    rendered,
    candidates: { agents: pick(cands.agents), skills: pick(cands.skills) },
  };
}

export type ParsedLint = { readonly ok: true; readonly value: LintJson } | { readonly ok: false; readonly error: string };

export function parseLintJson(text: string): ParsedLint {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    return { ok: false, error: `JSONとして解釈できません: ${(error as Error).message}` };
  }
  if (!isRecord(raw)) {
    return { ok: false, error: "JSONの最上位がオブジェクトではありません" };
  }
  const version = typeof raw.version === "number" ? raw.version : NaN;
  if (version !== LINT_VERSION) {
    return {
      ok: false,
      error: `lintのJSONの版が違います（拡張は${LINT_VERSION}、実行ファイルは${String(raw.version)}）`,
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

/**
 * 行の並びを先頭`max`件までにし、超えた分は「ほかN件」の1行にまとめる。帯・保存拒否・承認ダイアログで
 * 同じ件数の上限を使う。同じ文（行全体の文字列一致）は先に1つへ畳む。
 */
export const MAX_WARN_LINES = 5;

/** `--lint`の報告を切り詰めたときの「ほかN件」に続ける案内。報告の元が`--lint`のときだけ渡す */
export const LINT_MORE_HINT = "。`ccnavi --lint`で全部見られます";

/**
 * `hint`は「ほかN件」の後ろに続ける案内（なければ空文字）。全部を見る手段は呼ぶ側の出どころで違う
 * （`--lint`の報告なら`LINT_MORE_HINT`、承認画面の理由には案内なし）。
 */
export function truncateLines(lines: readonly string[], hint: string, max: number = MAX_WARN_LINES): string[] {
  const all = [...new Set(lines)];
  if (all.length <= max) {
    return all;
  }
  return [...all.slice(0, max), `ほか${all.length - max}件${hint}`];
}

/** 報告から`prefix`で始まる行を取り出し、一時ファイルのパスを対象のパスに直す。切り詰めは直したあとに行う */
function linesOf(report: string, prefix: string, tmp: string, shown: string): string[] {
  return report
    .split(/\r?\n/)
    .filter((line) => line.startsWith(prefix))
    .map((line) => line.split(tmp).join(shown));
}

/**
 * `--lint`の報告（文面）からwarnの行だけを取り出す。行は`warn: <場所>: <中身>`。
 * 保存が通ったあとに画面の帯へ出す。一時ファイルのパスは対象のファイルのパスに直す。
 * 帯が長くならないよう、先頭`MAX_WARN_LINES`件までにし、残りは「ほかN件」の1行にまとめる。
 * 保存したファイルのwarnだけに絞る印は、報告の行に無い（場所はルールidや`(risk)`など
 * まちまち）ので、絞らない。
 */
export function warnLinesOf(report: string, tmp: string, shown: string): string[] {
  return truncateLines(linesOf(report, "warn: ", tmp, shown), LINT_MORE_HINT);
}

/**
 * `--lint`の報告からerrorの行だけを取り出す（保存の拒否に出す）。行頭は日本語（`エラー: `）にする。
 * 対象のファイル（`shown`）に触れる行を先頭に出し、先頭`MAX_WARN_LINES`件までにする。
 * 無関係なチケット由来の指摘が何十件あっても、保存を止めた本当の原因が埋もれない。
 */
export function errorLinesOf(report: string, tmp: string, shown: string): string[] {
  const all = linesOf(report, "error: ", tmp, shown).map((line) => `エラー: ${line.slice("error: ".length)}`);
  const own = all.filter((line) => line.includes(shown));
  return truncateLines([...own, ...all.filter((line) => !line.includes(shown))], LINT_MORE_HINT);
}

/** そのプロジェクトについての苦情。`where`が`(projects/<名前>)`で始まるもの */
export function problemsOfProject(lint: LintJson, name: string): LintProblem[] {
  const prefix = `(projects/${name})`;
  return lint.problems.filter((p) => p.where === prefix || p.where.startsWith(`${prefix} `));
}

/** `--lint --flow <パス>`の苦情の場所（実行ファイルの`lint.FLOW_WHERE`） */
export const FLOW_WHERE = "(flow)";

/**
 * 渡したフローについての苦情。`where`が`(flow)`のもの。フロー編集画面はこれだけを読む
 * （ほかの設定の苦情でフローの保存を止めない）。読めるか・形が正しいかの答えは実行ファイルが出し、
 * 拡張は並べるだけ
 */
export function problemsOfFlow(lint: LintJson): LintProblem[] {
  return lint.problems.filter((p) => p.where === FLOW_WHERE);
}

/** 置き場そのものについての苦情。`where`が`(projects)` */
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
