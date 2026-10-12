/**
 * プロジェクト管理画面の案内の見本（`webview/tours/projects.yml` の `sample:`）を、画面が描く形にする。
 *
 * 見本はカードの欄をまとめた短い書き方で持ち、ここで `ProjectsPage`（画面が描く形）にする。案内の間に描くだけで、
 * 実際の一覧にも clone の欄にも使わない。`projects.ts` の `buildProjectsPage` は実行ファイルの出力を要るので画面から読めない
 * （`commands.ts` 経由で `node:path` も読む）。同じ組み立てをここで小さく持ち、そろっていることはテストが見る。
 */
import type { LintProblem, Severity } from "./lintmodel.js";
import type { ProjectRow, ProjectsPage, Stray } from "./projects-view.js";

/** 見本の `rules:` の値。ルールファイルの有無、または予約名で設定の対象外 */
export const RULES_STATES = ["present", "absent", "reserved"] as const;
export type RulesState = (typeof RULES_STATES)[number];

/** プロジェクトの設定のルールファイル（プロジェクトのルートからの相対）。実行ファイルの既定の置き場 */
export const PROJECT_RULES_REL = ".ccnavi/config/project/rules.yml";

export interface ProjectsSample {
  /** 帯が出ない画面（`.gitignore` に置き場がある） */
  readonly page: ProjectsPage;
  /** `.gitignore` に置き場が無い画面（scene の `banner: ignore`） */
  readonly ignore: ProjectsPage;
  /** 置き場が索引に載っている画面（scene の `banner: tracked`） */
  readonly tracked: ProjectsPage;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${at} がオブジェクトではない`);
  }
  return value as Record<string, unknown>;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function num(value: unknown, at: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error(`${at} は 0 以上の整数にする`);
  }
  return value;
}

function strings(value: unknown, at: string): string[] {
  if (value === undefined) {
    return [];
  }
  if (!Array.isArray(value)) {
    throw new Error(`${at} はリストにする`);
  }
  return value.map((item) => str(item));
}

function problemOf(raw: unknown, where: string, at: string): LintProblem {
  const map = object(raw, at);
  const severity = str(map.severity);
  if (severity !== "error" && severity !== "warn") {
    throw new Error(`${at}.severity は error か warn にする`);
  }
  return { severity: severity as Severity, where, detail: str(map.detail) };
}

function rowOf(raw: unknown, index: number, workspace: string, projectsRel: string): ProjectRow {
  const at = `sample.rows[${index}]`;
  const map = object(raw, at);
  const name = str(map.name);
  const rules = str(map.rules) as RulesState;
  if (name === "" || !RULES_STATES.includes(rules)) {
    throw new Error(`${at} は name と rules（${RULES_STATES.join(" / ")}）が要る`);
  }
  const rel = `${projectsRel}/${name}`;
  const problems = Array.isArray(map.problems) ? map.problems : [];
  return {
    name,
    root: `${workspace}/${rel}`,
    rel,
    rulesRel: rules === "reserved" ? "" : `${rel}/${PROJECT_RULES_REL}`,
    rulesExists: rules === "present",
    hasClaudeDir: map.claude_dir === true,
    origin: str(map.origin),
    originKey: originKey(str(map.origin)),
    worktrees: strings(map.worktrees, `${at}.worktrees`),
    tickets: num(map.tickets, `${at}.tickets`),
    doing: num(map.doing, `${at}.doing`),
    problems: problems.map((item, i) => problemOf(item, `(${rel})`, `${at}.problems[${i}]`)),
  };
}

/** 同じリポジトリかを比べる鍵（`projects.ts` の `checkRemote` の `key` と同じ。https / ssh / scp 風の 3 形） */
function originKey(url: string): string {
  const full = /^(?:https|ssh):\/\/(?:[^/@]*@)?([^/:]*)(?::\d+)?\/(.+)$/i.exec(url);
  const scp = /^(?:[^@/:\\]+@)?([^@/:\\]{2,}):([^\\]+)$/.exec(url);
  const found = full ?? scp;
  if (found === null) {
    return "";
  }
  const path = found[2].replace(/^\/+/, "").replace(/\/+$/, "").replace(/\.git$/i, "");
  return path === "" ? "" : `${found[1].toLowerCase()}/${path.toLowerCase()}`;
}

export function parseProjectsSample(raw: unknown): ProjectsSample {
  const root = object(raw, "sample");
  const projectsRel = str(root.projects_rel);
  if (projectsRel === "") {
    throw new Error("sample.projects_rel が要る");
  }
  const workspace = str(root.root);
  const rows = (Array.isArray(root.rows) ? root.rows : []).map((item, index) => rowOf(item, index, workspace, projectsRel));
  const strays: Stray[] = (Array.isArray(root.strays) ? root.strays : []).map((item, index) => {
    const map = object(item, `sample.strays[${index}]`);
    return { path: str(map.path), reason: str(map.reason) };
  });
  const workspaceWorktrees = strings(root.workspace_worktrees, "sample.workspace_worktrees");
  const page: ProjectsPage = {
    root: workspace,
    generatedAt: str(root.generated_at),
    ticketsEnabled: true,
    projectsDir: `${workspace}/${projectsRel}`,
    projectsRel,
    ignored: true,
    lintError: "",
    dirProblems: [],
    rows,
    strays,
    workspaceWorktrees,
    selfRulesRel: PROJECT_RULES_REL,
    selfRulesExists: root.self_rules_exists === true,
    existingNames: [...rows.map((r) => r.name), ...rows.flatMap((r) => r.worktrees), ...workspaceWorktrees],
  };
  const dir = (detail: string): LintProblem => ({ severity: "warn", where: "(projects)", detail });
  return {
    page,
    ignore: { ...page, ignored: false, dirProblems: [dir(str(root.ignore_problem))] },
    tracked: { ...page, ignored: false, dirProblems: [dir(str(root.tracked_problem))] },
  };
}
