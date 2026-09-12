import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildProjectsPage,
  checkName,
  checkRemote,
  cloneCommand,
  duplicateOf,
  fetchCommand,
  findStrayGitDirs,
  gitignoreHasProjects,
  gitignoreWithProjects,
  pullCommand,
  REASON_OUTSIDE,
  REASON_TOO_DEEP,
  remoteKeyOf,
  rewriteRulesForProject,
  type DirEntry,
} from "../src/core/projects.js";
import { parseLintJson } from "../src/core/lintmodel.js";
import { fixture } from "./fixture.js";

test("CB-T60 URL は https / ssh / scp 風の 3 形を通し、同じリポジトリは同じ鍵になる", () => {
  const https = checkRemote("https://GitLab.example.com/group/Repo.git");
  const ssh = checkRemote("ssh://git@gitlab.example.com:2222/group/repo");
  const scp = checkRemote("git@gitlab.example.com:group/repo.git");
  assert.ok(https.ok && ssh.ok && scp.ok);
  assert.equal(https.remote.key, "gitlab.example.com/group/repo");
  assert.equal(ssh.remote.key, https.remote.key);
  assert.equal(scp.remote.key, https.remote.key);
  assert.equal(https.remote.name, "Repo");
  assert.equal(scp.remote.name, "repo");
  assert.equal(remoteKeyOf("git@github.com:org/lib.git"), "github.com/org/lib");
});

test("CB-T61 ローカルパス・file・資格情報入りの URL は通さない", () => {
  for (const bad of ["", "   ", "/repo/x", "C:\\repo\\x", "file:///repo/x", "http://host/x", "git://host/x", "https://host", "a b"]) {
    const checked = checkRemote(bad);
    assert.equal(checked.ok, false, bad);
  }
  const creds = checkRemote("https://user:glpat-x@gitlab.example.com/g/p.git");
  assert.ok(!creds.ok);
  assert.match(creds.error, /資格情報/);
  // ssh の git@ はユーザ名であって資格情報ではない
  assert.ok(checkRemote("ssh://git@host.example/g/p").ok);
  assert.equal(remoteKeyOf("/local/path"), "");
});

test("CB-T62 名前は ASCII に絞り、既存のツリー名と大文字小文字違いでも衝突する", () => {
  assert.deepEqual(checkName(" lib ", []), { ok: true, name: "lib" });
  assert.ok(checkName("my.repo_1-x", []).ok);
  for (const bad of ["", ".hidden", "ライブラリ", "a/b", "a b", "-x"]) {
    assert.equal(checkName(bad, []).ok, false, bad);
  }
  const clash = checkName("Lib", ["", "lib", "i0001"]);
  assert.ok(!clash.ok);
  assert.match(clash.error, /lib/);
  const worktree = checkName("i0001", ["", "i0001"]);
  assert.ok(!worktree.ok);
});

test("CB-T63 clone / fetch / pull はターミナル向けの 1 行になる", () => {
  assert.equal(
    cloneCommand("C:\\ws", "C:\\ws\\projects", "git@host:g/p.git", "p"),
    "cd 'C:/ws' && git clone -- 'git@host:g/p.git' 'C:/ws/projects/p'",
  );
  assert.equal(fetchCommand("/ws/projects/lib"), "cd '/ws/projects/lib' && git fetch");
  assert.equal(pullCommand("/ws/projects/lib"), "cd '/ws/projects/lib' && git pull");
});

test("CB-T64 ccnavi が数えない .git を深さ 2 まで探し、置き場の中は 1 段深く見る", () => {
  const tree: Record<string, readonly DirEntry[]> = {
    "": [
      dir("projects"), dir("参考"), dir("tools"), dir("node_modules"), dir(".claude"), dir("src"), file("README.md"),
    ],
    projects: [dir("lib"), dir("group")],
    "projects/lib": [dir(".git"), dir("src")],
    "projects/group": [dir("deep")],
    "projects/group/deep": [dir(".git")],
    "参考": [dir(".git"), dir(".claude")],
    tools: [dir("vendor")],
    "tools/vendor": [dir("x")],
    "tools/vendor/x": [dir(".git")],
    node_modules: [dir("pkg")],
    "node_modules/pkg": [dir(".git")],
    ".claude": [dir("worktrees")],
    ".claude/worktrees": [dir("i0001")],
    ".claude/worktrees/i0001": [file(".git")],
    src: [dir("a")],
    "src/a": [file("a.py")],
  };
  const strays = findStrayGitDirs({
    projectsRel: "projects",
    list: (rel) => tree[rel] ?? [],
    knownRels: new Set(["projects/lib"]),
  });
  assert.deepEqual(strays, [
    { path: "projects/group/deep", reason: REASON_TOO_DEEP },
    { path: "参考", reason: REASON_OUTSIDE },
  ]);
});

test("CB-T65 .gitignore の置き場の行を見つけ、無ければ足す", () => {
  assert.equal(gitignoreHasProjects("/dist/\n/projects/\n", "projects"), true);
  assert.equal(gitignoreHasProjects("projects\n", "projects"), true);
  assert.equal(gitignoreHasProjects("/projects/lib/\n", "projects"), false);
  assert.equal(gitignoreHasProjects(undefined, "projects"), false);
  const added = gitignoreWithProjects("/dist/", "projects");
  assert.match(added, /^\/dist\/\n\n# .*\n\/projects\/\n$/);
  assert.equal(gitignoreWithProjects("/dist/\n/projects/\n", "projects"), "/dist/\n/projects/\n");
  assert.match(gitignoreWithProjects(undefined, "projects"), /^# .*\n\/projects\/\n$/);
});

test("CB-T66 写すときは出どころのコメントを足し、sh の綴りだけを {root} 付きにする", () => {
  const source = "deny:\n  - id: raw-git\n    message: |\n      'sh .claude/scripts/ccnavi-git.sh <サブコマンド>' を使う。\n      glob: '*/.claude/scripts/*' は変えない\n";
  const out = rewriteRulesForProject(source, ".claude/ccnavi/rules.yml", "lib", "2026-09-12");
  assert.match(out, /^# lib のルール。ワークスペースの \.claude\/ccnavi\/rules\.yml を 2026-09-12 に写した/);
  assert.match(out, /'sh \{root\}\/\.claude\/scripts\/ccnavi-git\.sh <サブコマンド>'/);
  assert.match(out, /glob: '\*\/\.claude\/scripts\/\*' は変えない/);
  // 置き換えは 1 種類だけで、既に {root} 付きの綴りには重ねない
  assert.ok(!out.includes("sh .claude/scripts/"));
  const twice = rewriteRulesForProject(out, "x", "lib", "d");
  assert.ok(!twice.includes("{root}/{root}"));
  assert.ok(!twice.includes("sh {root}/.claude/scripts/{root}"));
});

test("CB-T67 lint の JSON を読み、プロジェクトごとの苦情を引ける", () => {
  const parsed = parseLintJson(
    JSON.stringify({
      version: 1,
      root: "/ws",
      rules: "/ws/.claude/ccnavi/rules.yml",
      mode: "enable",
      ticket_control: "enable",
      projects: ["app", "lib"],
      problems: [
        { severity: "warn", where: "(projects)", detail: "projects/ がワークスペースの git で無視されていない" },
        { severity: "warn", where: "(projects/lib)", detail: "ルール config/rules.yml が無い" },
        { severity: "error", where: "(projects/app) no-message", detail: "文面が無い" },
        { severity: "warn", where: "(projects/application)", detail: "別のプロジェクト" },
      ],
      errors: 1,
      warns: 3,
    }),
  );
  assert.ok(parsed.ok);
  const page = buildProjectsPage({
    board: {
      ...fixture(),
      trees: [
        { name: "", root: "/ws", project: "", kind: "main" },
        { name: "app", root: "/ws/projects/app", project: "app", kind: "project" },
        { name: "lib", root: "/ws/projects/lib", project: "lib", kind: "project" },
        { name: "i0001", root: "/ws/.claude/worktrees/i0001", project: "", kind: "worktree" },
        { name: "i0002", root: "/ws/.claude/worktrees/i0002", project: "lib", kind: "worktree" },
      ],
      projects: ["app", "lib"],
      tickets: fixture().tickets.map((t) => (t.ticket === "i0001-02" ? { ...t, project: "lib" } : t)),
    },
    lint: parsed.value,
    lintError: "",
    origins: { app: "https://gitlab.example.com/g/app.git", lib: "" },
    strays: [],
    projectsRel: "projects",
    projectsDirExists: true,
    ignored: false,
    projectRules: "config/rules.yml",
    rulesExists: { app: true },
    hasClaudeDir: { lib: true },
  });
  assert.equal(page.rows.length, 2);
  const [app, lib] = page.rows;
  assert.equal(app.originKey, "gitlab.example.com/g/app");
  assert.equal(app.rulesRel, "projects/app/config/rules.yml");
  assert.equal(app.problems.length, 1);
  assert.equal(app.problems[0].severity, "error");
  assert.equal(lib.rulesExists, false);
  assert.equal(lib.hasClaudeDir, true);
  assert.deepEqual(lib.worktrees, ["i0002"]);
  assert.equal(lib.tickets, 1);
  assert.equal(lib.doing, 1);
  assert.deepEqual(lib.problems.map((p) => p.detail), ["ルール config/rules.yml が無い"]);
  assert.equal(page.dirProblems.length, 1);
  assert.deepEqual(page.workspaceWorktrees, ["i0001"]);
  assert.deepEqual(page.existingNames, ["app", "lib", "i0001", "i0002"]);
  assert.equal(duplicateOf(page.rows, "gitlab.example.com/g/app")?.name, "app");
  assert.equal(duplicateOf(page.rows, ""), undefined);
});

test("CB-T68 lint の JSON の版が違えば読まない", () => {
  const parsed = parseLintJson(JSON.stringify({ version: 2, problems: [] }));
  assert.ok(!parsed.ok);
  assert.match(parsed.error, /版が違う/);
  assert.ok(!parseLintJson("{").ok);
});

function dir(name: string): DirEntry {
  return { name, isDir: true };
}

function file(name: string): DirEntry {
  return { name, isDir: false };
}
