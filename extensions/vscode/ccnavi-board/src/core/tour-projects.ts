/**
 * プロジェクト管理画面の案内の見本（`webview/tours/projects.yml` の `sample:`）を、画面が描く形にする。
 *
 * 見本は実行ファイルの JSON よりずっと小さい書き方で YAML に書き、ここで `ProjectsPage` と `MirrorData`（画面が描く形）にする。
 * 案内の間に描くだけで、検証・共通設定のずれ・取り込めるかは、見本に書いた値をそのまま使う（判定はしない）。
 * 見本の URL は `example.com` の例で、実在のリポジトリではない。
 */
import type { LintProblem, Severity } from "./lintmodel.js";
import { MIRROR_VERSION, type MirrorProject } from "./mirrormodel.js";
import type { MirrorData, ProjectRow, ProjectsPage, Stray } from "./projects-view.js";

export interface ProjectsSample {
  readonly page: ProjectsPage;
  /** 共通設定の同期の見本。書かなければ無し（画面は同期の欄もボタンも出さない） */
  readonly mirror: MirrorData | undefined;
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

function str(value: unknown): string {
  return typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
}

function num(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

const SEVERITIES: readonly Severity[] = ["error", "warn"];

function problemsOf(value: unknown, at: string): LintProblem[] {
  return list(value, at).map((item, index) => {
    const map = object(item, `${at}[${index}]`);
    const severity = SEVERITIES.find((s) => s === map.severity);
    if (severity === undefined) {
      throw new Error(`${at}[${index}].severity は error か warn`);
    }
    return { severity, where: str(map.where), detail: str(map.detail) };
  });
}

/** 見本の中身を読む。形が崩れていたら、どこかを名指しして落とす（テストが先に見つける） */
export function parseProjectsSample(raw: unknown, root = "/ws", generatedAt = "2026-10-12 10:30"): ProjectsSample {
  const sample = object(raw, "sample");
  const projectsRel = str(sample.projects_rel) || "projects";
  const rows: ProjectRow[] = list(sample.projects, "sample.projects").map((item, index) => {
    const map = object(item, `sample.projects[${index}]`);
    const name = str(map.name);
    if (name === "") {
      throw new Error(`sample.projects[${index}].name が必要です`);
    }
    const rulesExists = map.rules !== false;
    const origin = str(map.origin);
    return {
      name,
      root: `${root}/${projectsRel}/${name}`,
      rel: `${projectsRel}/${name}`,
      // `common` と `self` はレイヤーの名前として予約してあり、プロジェクトの設定として数えられない（ルールファイルの欄は空）
      rulesRel: name === "common" || name === "self" ? "" : `${projectsRel}/${name}/.ccnavi/config/project/rules.yml`,
      rulesExists,
      origin,
      originKey: origin,
      worktrees: list(map.worktrees, `sample.projects[${index}].worktrees`).map(str),
      tickets: num(map.tickets),
      doing: num(map.doing),
      problems: problemsOf(map.problems, `sample.projects[${index}].problems`),
    };
  });
  const strays: Stray[] = list(sample.strays, "sample.strays").map((item, index) => {
    const map = object(item, `sample.strays[${index}]`);
    return { path: str(map.path), reason: str(map.reason) };
  });
  const names = rows.map((row) => row.name);
  const page: ProjectsPage = {
    root,
    generatedAt,
    ticketsEnabled: sample.tickets_enabled !== false,
    projectsDir: `${root}/${projectsRel}`,
    projectsRel,
    ignored: true,
    lintError: "",
    dirProblems: [],
    rows,
    strays,
    workspaceWorktrees: list(sample.workspace_worktrees, "sample.workspace_worktrees").map(str),
    selfRulesRel: ".ccnavi/config/project/rules.yml",
    selfRulesExists: sample.self_rules !== false,
    existingNames: names,
  };
  if (sample.mirror === undefined || sample.mirror === null) {
    return { page, mirror: undefined };
  }
  const mirror = object(sample.mirror, "sample.mirror");
  const projects: MirrorProject[] = list(mirror.projects, "sample.mirror.projects").map((item, index) => {
    const map = object(item, `sample.mirror.projects[${index}]`);
    const name = str(map.name);
    if (!names.includes(name)) {
      throw new Error(`sample.mirror.projects[${index}].name「${name}」は sample.projects にない`);
    }
    return {
      name,
      state: str(map.state) || "synced",
      changes: list(map.changes, `sample.mirror.projects[${index}].changes`).map((change) => {
        const c = object(change, `sample.mirror.projects[${index}].changes[]`);
        return { rel: str(c.rel), action: str(c.action) || "update" };
      }),
      error: str(map.error),
      expect: str(map.expect),
    };
  });
  return {
    page,
    mirror: {
      status: { kind: "ok", list: { version: MIRROR_VERSION, ok: true, error: "", commonEmpty: false, projects, expect: str(mirror.expect) } },
      busy: false,
      checkedAt: str(mirror.checked_at),
    },
  };
}
