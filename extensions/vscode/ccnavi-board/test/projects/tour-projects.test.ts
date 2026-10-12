/**
 * プロジェクト管理の案内の見本（`tours/projects.yml` の `sample:`）の検査。見本はカードの欄を短く書き、
 * 画面は `tour-projects.ts` の小さな読み取りで `ProjectsPage` にする。実際の組み立て（`buildProjectsPage`）と同じ結果になることを見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { LintJson, LintProblem } from "../../src/core/lintmodel.js";
import type { BoardJson } from "../../src/core/model.js";
import { INTRO_ID } from "../../src/core/tour-def.js";
import {
  buildProjectsPage,
  checkName,
  checkRemote,
  duplicateOf,
  findStrayGitDirs,
  REASON_OUTSIDE,
  REASON_TOO_DEEP,
  remoteKeyOf,
  type DirEntry,
} from "../../src/core/projects.js";
import { parseProjectsSample, PROJECT_RULES_REL } from "../../src/core/tour-projects.js";
import { isTrackedProjectsDir, problemsOf } from "../../src/webview/projects/text.js";
import { fixture } from "../helpers/fixture.js";
import { loadTour } from "../helpers/tour.js";

const def = loadTour("projects");
const raw = def.sample as {
  root: string;
  projects_rel: string;
  rows: Array<{ name: string; origin: string; rules: string; claude_dir?: boolean; worktrees: string[]; tickets: number; doing: number; problems: Array<{ severity: "error" | "warn"; detail: string }> }>;
  strays: Array<{ path: string; reason: string }>;
  workspace_worktrees: string[];
  self_rules_exists: boolean;
  generated_at: string;
};
const sample = parseProjectsSample(def.sample);

/** 画面が `data-tour` で持つ指す先の印 */
const TARGETS = [
  "summary",
  "refresh",
  "banner",
  "fix-ignore",
  "clone",
  "clone-url",
  "clone-name",
  "clone-run",
  "clone-status",
  "list",
  "project",
  "project-flags",
  "project-rules",
  "project-worktrees",
  "project-check",
  "strays",
  "workspace",
  "workspace-rules",
  "tour",
];

const SCENE_KEYS = ["url", "name", "status", "failed", "cards", "strays", "banner"];

function cardsOf(scene: Record<string, string | number | boolean>): string[] {
  return String(scene.cards ?? "").split(",").map((item) => item.trim()).filter((item) => item !== "");
}

/** 見本と同じ内容になる、実行ファイルの出力（board と lint）を組む */
function realInput(): { board: BoardJson; lint: LintJson; origins: Record<string, string>; rulesRels: Record<string, string>; rulesExists: Record<string, boolean>; hasClaudeDir: Record<string, boolean> } {
  const base = fixture();
  const doing = base.tickets.find((t) => t.ticket === "i0001-02-02")!;
  const idle = base.tickets.find((t) => t.ticket === "i0001-01-01")!;
  const workspace = raw.root;
  const trees = [
    { name: "", root: workspace, project: "", kind: "main" as const },
    ...raw.rows.map((r) => ({ name: r.name, root: `${workspace}/${raw.projects_rel}/${r.name}`, project: r.name, kind: "project" as const })),
    ...raw.rows.flatMap((r) => r.worktrees.map((w) => ({ name: w, root: `${workspace}/.claude/worktrees/${w}`, project: r.name, kind: "worktree" as const }))),
    ...raw.workspace_worktrees.map((w) => ({ name: w, root: `${workspace}/.claude/worktrees/${w}`, project: "", kind: "worktree" as const })),
  ];
  const tickets = raw.rows.flatMap((r) => [
    ...Array.from({ length: r.doing }, () => ({ ...doing, project: r.name })),
    ...Array.from({ length: r.tickets - r.doing }, () => ({ ...idle, project: r.name })),
  ]);
  const problems: LintProblem[] = raw.rows.flatMap((r) => r.problems.map((p) => ({ ...p, where: `(${raw.projects_rel}/${r.name})` })));
  const lint: LintJson = {
    version: 1,
    root: workspace,
    rules: `${workspace}/.ccnavi/config/common/rules.yml`,
    mode: "enable",
    ticket_control: "enable",
    projects: raw.rows.map((r) => r.name),
    problems,
    errors: problems.filter((p) => p.severity === "error").length,
    warns: problems.filter((p) => p.severity === "warn").length,
  };
  const board: BoardJson = { ...base, root: workspace, generated_at: raw.generated_at, settings: { ...base.settings, projects: `${workspace}/${raw.projects_rel}` }, trees, tickets };
  return {
    board,
    lint,
    origins: Object.fromEntries(raw.rows.map((r) => [r.name, r.origin])),
    rulesRels: Object.fromEntries(raw.rows.filter((r) => r.rules !== "reserved").map((r) => [r.name, `${raw.projects_rel}/${r.name}/${PROJECT_RULES_REL}`])),
    rulesExists: Object.fromEntries(raw.rows.map((r) => [r.name, r.rules === "present"])),
    hasClaudeDir: Object.fromEntries(raw.rows.map((r) => [r.name, r.claude_dir === true])),
  };
}

test("CB-T540 プロジェクトの見本は、実際の組み立て（buildProjectsPage）と同じ ProjectsPage になる", () => {
  const input = realInput();
  const real = buildProjectsPage({
    board: input.board,
    lint: input.lint,
    lintError: "",
    origins: input.origins,
    strays: raw.strays,
    projectsRel: raw.projects_rel,
    ignored: true,
    rulesRels: input.rulesRels,
    rulesExists: input.rulesExists,
    hasClaudeDir: input.hasClaudeDir,
    selfRulesRel: PROJECT_RULES_REL,
    selfRulesExists: raw.self_rules_exists,
  });
  assert.deepEqual(sample.page, real);
  // 見せたい状態が揃っている。問題なし・warn・error、予約名、ワークツリー、作業中
  assert.deepEqual(sample.page.rows.map((r) => r.name), ["app", "lib", "common"]);
  assert.ok(sample.page.rows.some((r) => r.worktrees.length > 0));
  assert.ok(sample.page.rows.some((r) => r.doing > 0));
  assert.ok(sample.page.workspaceWorktrees.length > 0);
  assert.equal(sample.page.selfRulesExists, false);
  assert.ok(sample.page.rows.every((r) => r.originKey === remoteKeyOf(r.origin)), "origin の鍵が実際の読み取り（remoteKeyOf）と違う");
});

test("CB-T541 .gitignore の帯と索引に載っている苦情の見本は、実行ファイルの苦情と同じ置き場・同じ先頭の句で、画面の振り分けに合う", () => {
  for (const variant of [sample.ignore, sample.tracked]) {
    assert.equal(variant.ignored, false);
    assert.equal(variant.dirProblems.length, 1);
    assert.equal(variant.dirProblems[0].severity, "warn");
    assert.equal(variant.dirProblems[0].where, "(projects)");
    assert.deepEqual({ ...variant, ignored: true, dirProblems: [] }, sample.page, "帯の違い以外は同じ画面");
  }
  assert.ok(/無視されていない/.test(sample.ignore.dirProblems[0].detail));
  assert.ok(!isTrackedProjectsDir(sample.ignore.dirProblems[0], "projects"));
  assert.ok(isTrackedProjectsDir(sample.tracked.dirProblems[0], "projects"));
  assert.ok(!/無視されていない/.test(sample.tracked.dirProblems[0].detail), "索引の苦情に「無視されていない」が入ると帯の振り分けが変わる");
  assert.equal(sample.page.dirProblems.length, 0);
  assert.equal(sample.page.ignored, true);
});

test("CB-T542 認識されない git の見本は、実際の探し方（findStrayGitDirs）が同じ行を同じ理由で返す", () => {
  assert.equal(raw.strays[0].reason, REASON_TOO_DEEP);
  assert.equal(raw.strays[1].reason, REASON_OUTSIDE);
  // 見本の行から、その場所を歩く偽の木を組んで実際の探索に通す
  const tree: Record<string, DirEntry[]> = { "": [{ name: raw.projects_rel, isDir: true }] };
  const add = (rel: string, hasGit: boolean): void => {
    const parts = rel.split("/");
    for (let i = 0; i < parts.length; i += 1) {
      const parent = parts.slice(0, i).join("/");
      const here = parts.slice(0, i + 1).join("/");
      tree[parent] ??= [];
      if (!tree[parent].some((e) => e.name === parts[i])) {
        tree[parent].push({ name: parts[i], isDir: true });
      }
      tree[here] ??= [];
    }
    if (hasGit) {
      tree[rel].push({ name: ".git", isDir: true });
    }
  };
  for (const stray of raw.strays) {
    add(stray.path, true);
  }
  for (const r of raw.rows) {
    add(`${raw.projects_rel}/${r.name}`, true);
  }
  const found = findStrayGitDirs({
    projectsRel: raw.projects_rel,
    list: (rel) => tree[rel] ?? [],
    knownRels: new Set(raw.rows.map((r) => `${raw.projects_rel}/${r.name}`)),
  });
  assert.deepEqual(found, sample.page.strays);
});

test("CB-T543 カードの指摘の見本は、画面の間引き（problemsOf）を通して、問題なし・warn・error の 3 通りになる", () => {
  const byName = new Map(sample.page.rows.map((r) => [r.name, r]));
  assert.deepEqual(problemsOf(byName.get("app")!), []);
  const lib = problemsOf(byName.get("lib")!);
  assert.deepEqual(lib.map((p) => p.severity), ["warn"], ".claude/ の指摘は説明付きの 1 行にまとまる");
  assert.ok(lib[0].detail.startsWith(".claude/ があります"));
  assert.ok(byName.get("lib")!.problems[0].detail.startsWith(".claude/ を持つ"), "実行ファイルの文面の先頭の句と合う");
  const common = problemsOf(byName.get("common")!);
  assert.deepEqual(common.map((p) => p.severity), ["error"]);
  assert.equal(byName.get("common")!.rulesRel, "", "予約名は設定の対象外");
  assert.ok(common[0].detail.includes("`common`"));
});

test("CB-T544 見本の scene は、画面が読む鍵だけを使い、cards は見本のカード、banner は ignore か tracked だけを指す", () => {
  const names = new Set(sample.page.rows.map((r) => r.name));
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 段目`;
      const scene = step.scene;
      for (const key of Object.keys(scene)) {
        assert.ok(SCENE_KEYS.includes(key), `${at}: scene の鍵 ${key} を画面は読まない`);
      }
      for (const name of cardsOf(scene)) {
        assert.ok(names.has(name), `${at}: cards の ${name} が見本に無い`);
      }
      assert.equal(new Set(cardsOf(scene)).size, cardsOf(scene).length, `${at}: cards に重なりがある`);
      assert.ok(scene.banner === undefined || scene.banner === "ignore" || scene.banner === "tracked", `${at}: banner`);
      assert.ok(scene.strays === undefined || scene.strays === true, `${at}: strays`);
      assert.ok(scene.failed === undefined || scene.failed === true, `${at}: failed`);
      for (const key of ["url", "name", "status"]) {
        assert.ok(scene[key] === undefined || typeof scene[key] === "string", `${at}: ${key}`);
      }
      assert.ok(scene.failed !== true || String(scene.status ?? "") !== "", `${at}: failed に一言（status）が要る`);
    }
  }
});

test("CB-T545 失敗の一言の見本は、実際の clone の検査（checkRemote → checkName → duplicateOf）が返す文面と同じ", () => {
  const existing = sample.page.existingNames;
  const failures: string[] = [];
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const scene = step.scene;
      if (scene.failed !== true) {
        continue;
      }
      const at = `${course.id} の ${index + 1} 段目`;
      const url = String(scene.url ?? "");
      const name = String(scene.name ?? "");
      const remote = checkRemote(url);
      let said: string | undefined;
      if (!remote.ok) {
        said = remote.error;
      } else {
        const checked = checkName(name === "" ? remote.remote.name : name, existing);
        if (!checked.ok) {
          said = checked.error;
        } else {
          const twin = duplicateOf(sample.page.rows, remote.remote.key);
          if (twin !== undefined) {
            said = `同じリポジトリを ${twin.name} として既に clone しています（origin ${twin.origin}）`;
          } else {
            // 検査を通ったあとに、フォルダが空でないと断る（ファイルを見るので、ここでは文面の形だけを見る）
            said = `${sample.page.projectsRel}/${checked.name} が既にあり、空ではありません`;
          }
        }
      }
      assert.equal(scene.status, said, `${at}: 一言が実際の検査の文面と違う`);
      failures.push(said);
    }
  }
  // URL・資格情報・名前の文字・名前の衝突・同じリポジトリ・空でないフォルダの 6 通りを見せる
  assert.equal(new Set(failures).size, failures.length, "同じ失敗を重ねて見せている");
  assert.ok(failures.length >= 6);
  assert.ok(failures.some((f) => f.includes("資格情報")) && failures.some((f) => f.includes("ASCII")) && failures.some((f) => f.includes("既にあり、空ではありません")));
});

test("CB-T546 基本の案内は題と順序を保って最後に「?」を指し、ユースケースは「〜したいとき」の 4〜9 段で、指す先は画面にある印だけ", () => {
  assert.equal(def.courses[0].id, INTRO_ID);
  assert.deepEqual(
    def.courses[0].steps.map((step) => step.title),
    ["プロジェクトの置き場", "リポジトリを clone する", "プロジェクトの一覧", "検証の結果", "ワークスペース（プロジェクト外）", "更新", "案内"],
  );
  assert.deepEqual(
    def.courses[0].steps.map((step) => step.target),
    ["summary", "clone", "list", "project-check", "workspace", "refresh", "tour"],
  );
  const useCases = def.courses.slice(1);
  assert.ok(useCases.length >= 4 && useCases.length <= 8, `ユースケースが ${useCases.length} 本`);
  assert.equal(new Set(def.courses.map((c) => c.id)).size, def.courses.length);
  for (const course of useCases) {
    assert.match(course.title, /とき$/, `${course.id}: 題は利用者の目的（〜したいとき）で切る`);
    assert.ok(course.steps.length >= 4 && course.steps.length <= 9, `${course.id}: ${course.steps.length} 段`);
  }
  for (const course of def.courses) {
    for (const step of course.steps) {
      assert.ok(TARGETS.includes(step.target), `${course.id}: 指す先 ${step.target} は画面に無い印`);
    }
  }
});

test("CB-T547 段が指す先は、その段の scene で画面に出る（札・帯・一言・認識されない git・先頭のカードの欄）", () => {
  const rows = new Map(sample.page.rows.map((r) => [r.name, r]));
  for (const course of def.courses) {
    for (const [index, step] of course.steps.entries()) {
      const at = `${course.id} の ${index + 1} 段目（${step.target}）`;
      const scene = step.scene;
      const first = rows.get(cardsOf(scene)[0] ?? sample.page.rows[0].name)!;
      if (step.target === "project-flags") {
        assert.ok(problemsOf(first).length > 0 || first.doing > 0, `${at}: 先頭のカード ${first.name} に札が付かない`);
      }
      if (step.target === "banner") {
        assert.ok(scene.banner === "ignore" || scene.banner === "tracked", `${at}: 帯の scene が要る`);
      }
      if (step.target === "fix-ignore") {
        assert.equal(scene.banner, "ignore", at);
      }
      if (step.target === "clone-status") {
        assert.ok(String(scene.status ?? "") !== "", `${at}: 一言の scene が要る`);
      }
      if (step.target === "strays") {
        assert.equal(scene.strays, true, at);
      }
      if (step.target === "project-worktrees") {
        assert.ok(first.worktrees.length > 0, `${at}: 先頭のカードにワークツリーが無い`);
      }
      // 帯を出す段は、その帯が出る画面。ボタンは .gitignore の帯だけにある
      if (scene.banner === "tracked") {
        assert.notEqual(step.target, "fix-ignore", at);
      }
      // 空の clone の欄を指す段が、前の段の例を引き継ぐと見間違える。例を出す段は URL を書く
      if (scene.name !== undefined) {
        assert.ok(String(scene.url ?? "") !== "", `${at}: name だけの例`);
      }
    }
  }
});
