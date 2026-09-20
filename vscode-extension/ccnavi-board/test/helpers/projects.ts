/**
 * プロジェクト管理画面（React）を happy-dom で動かす。`test/helpers/board.ts` と同じ役割。
 *
 * 画面は束ねた 1 本（`out/webview/projects.js`）で、拡張はそれを `<script nonce>` に流し込む。
 * ここでも同じ 1 本を流し込むので、テストが見るのは配るものと同じ画面になる。
 * 束ねるのは `pnpm test` の中の `scripts/bundle-webview.js`。この入口の綴り（`test/helpers/<画面の名前>.ts`）が
 * 約束で、`scripts/test-groups.js` はそれを辿って「この画面を読むグループ」を決める。
 *
 * React は押した直後には描き直さない。操作のあとは `await page.settle()` を挟んでから見る。
 */
import type { LintProblem } from "../../src/core/lintmodel.js";
import type { ProjectRow, ProjectsData, ProjectsPage } from "../../src/core/projects-view.js";
import { renderProjectsPage, type RenderOptions } from "../../src/core/projects-render.js";
import { screenScript, screenStyle } from "./bundle.js";
import { loadPage, type DomPage } from "./dom.js";

export const NONCE = "TEST-NONCE-123";

/** 画面の HTML。CSS や nonce のように、文字列のまま見たいものはこれを見る */
export function projectsHtml(data: ProjectsData, options: Partial<RenderOptions> = {}): string {
  return renderProjectsPage(data, { nonce: NONCE, script: screenScript("projects"), style: screenStyle("projects"), ...options });
}

/** 見本の 1 行。差し替えたいところだけ渡す */
export function row(overrides: Partial<ProjectRow> = {}): ProjectRow {
  return {
    name: "lib",
    root: "/ws/projects/lib",
    rel: "projects/lib",
    rulesRel: "projects/lib/.ccnavi/config/rules.yml",
    rulesExists: true,
    hasClaudeDir: false,
    origin: "",
    originKey: "",
    worktrees: [],
    tickets: 0,
    doing: 0,
    problems: [],
    ...overrides,
  };
}

/** 見本の中身。差し替えたいところだけ渡す */
export function page(rows: readonly ProjectRow[], overrides: Partial<ProjectsPage> = {}): ProjectsPage {
  return {
    root: "/ws",
    generatedAt: "2026-09-13T00:00:00+0900",
    ticketsEnabled: true,
    projectsDir: "/ws/projects",
    projectsRel: "projects",
    ignored: true,
    lintError: "",
    dirProblems: [],
    rows,
    strays: [],
    workspaceWorktrees: [],
    existingNames: rows.map((r) => r.name),
    selfRulesRel: ".ccnavi/config/rules.yml",
    selfRulesExists: false,
    ...overrides,
  };
}

/** HTML を happy-dom に読ませ、React がマウントし終わるまで待つ */
export async function openPage(data: ProjectsData, initialState?: unknown): Promise<DomPage> {
  const dom = await loadPage(projectsHtml(data), initialState);
  await dom.settle();
  return dom;
}

/** 見本の一覧を開く。差し替えたいところだけ渡す */
export async function openProjects(
  rows: readonly ProjectRow[] = [row()],
  overrides: Partial<ProjectsPage> = {},
  initialState?: unknown,
): Promise<DomPage> {
  return openPage({ kind: "page", page: page(rows, overrides) }, initialState);
}

/** カード 1 枚の中の要素。`li.project[data-name=…]` の下だけを見る */
export function cardSelector(name: string): string {
  return `li.project[data-name="${name}"]`;
}

/** 苦情の見本 */
export function problem(severity: LintProblem["severity"], detail: string, where = "(projects/lib)"): LintProblem {
  return { severity, where, detail };
}
