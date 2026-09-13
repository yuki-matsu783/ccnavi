import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../src/core/rules-doc.js";
import { renderRulesPage } from "../src/core/rules-render.js";
import type { ProjectRow, ProjectsPage } from "../src/core/projects.js";
import { renderProjectsPage } from "../src/core/projects-render.js";

function row(overrides: Partial<ProjectRow> = {}): ProjectRow {
  return {
    name: "lib",
    root: "/ws/projects/lib",
    rel: "projects/lib",
    rulesRel: "projects/lib/.ccnavi/config/rules.yml",
    rulesExists: true,
    oldRulesRel: "projects/lib/config/rules.yml",
    oldRulesExists: false,
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

function page(rows: readonly ProjectRow[], overrides: Partial<ProjectsPage> = {}): ProjectsPage {
  return {
    root: "/ws",
    generatedAt: "2026-09-13T00:00:00+0900",
    ticketsEnabled: true,
    projectsDir: "/ws/projects",
    projectsRel: "projects",
    projectsDirExists: true,
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

test("CB-T112 ルール設定画面は注意を上部に実体参照で出し、無ければ出さない", () => {
  const base = {
    root: "/ws",
    rulesPath: "projects/lib/.ccnavi/config/rules.yml",
    mode: "enable",
    model: readRules("deny: []\n").model,
    hooks: [],
    hookFiles: { settings: true, settingsLocal: false },
    samplesPath: ".claude/ccnavi/rule-samples.yml",
    lock: { locked: false, reason: "", doing: [] },
  };
  const html = renderRulesPage({ ...base, notices: ["旧の置き場 <projects/lib/config/rules.yml> が残っている"] }, { nonce: "n" });
  assert.match(html, /<div class="banner warn">旧の置き場 &lt;projects\/lib\/config\/rules\.yml&gt; が残っている<\/div>/);
  // 「外で変わった」の帯（hidden 付き）は常にあるので、注意の帯だけを見る
  assert.doesNotMatch(renderRulesPage(base, { nonce: "n" }), /<div class="banner warn">/);
});

test("CB-T113 カードは層の置き場を出し、旧の置き場が残っていれば読まれていないと言う。自身の層は本体の枠に出す", () => {
  const html = renderProjectsPage(
    page([
      row({ name: "app", rel: "projects/app", rulesRel: "projects/app/.ccnavi/config/rules.yml" }),
      row({ rulesExists: false, oldRulesExists: true }),
      row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false, oldRulesRel: "projects/Self/config/rules.yml" }),
    ]),
    { nonce: "n" },
  );
  // カードは </li> で切る。切らないと最後のカードにページ末尾のスクリプト（create-rules の綴りを含む）が付く
  const cards = html.split('<li class="project').slice(1).map((c) => c.slice(0, c.indexOf("</li>")));
  const [app, lib, reserved] = cards;
  assert.match(app, /あり<\/span> <span class="mono small">projects\/app\/\.ccnavi\/config\/rules\.yml/);
  assert.doesNotMatch(app, /旧の置き場/);
  assert.match(lib, /data-action="create-rules" data-name="lib"/);
  assert.match(lib, /旧の置き場 <span class="mono">projects\/lib\/config\/rules\.yml<\/span> は判定に読まれていません。中身を <span class="mono">projects\/lib\/\.ccnavi\/config\/rules\.yml<\/span> へ移し/);
  assert.match(lib, /data-action="open-rules" data-name="lib" disabled /);
  // 予約名のプロジェクトは層が無いので、置く先も作るボタンも出さない
  assert.match(reserved, /層として数えられていません/);
  assert.doesNotMatch(reserved, /create-rules/);

  const workspace = html.slice(html.indexOf('<section class="workspace">'));
  assert.match(workspace, /自身の層のルール<\/span> <span class="dim">なし<\/span> <span class="mono small">\.ccnavi\/config\/rules\.yml<\/span>/);
  assert.match(workspace, /data-action="create-self-rules"/);
  assert.match(workspace, /data-action="open-self-rules" disabled /);
  const exists = renderProjectsPage(page([], { selfRulesExists: true }), { nonce: "n" });
  assert.doesNotMatch(exists, /data-action="create-self-rules"/);
  assert.match(exists, /data-action="open-self-rules" title=/);
  // 古い実行ファイル（自身の層を出さない）なら枠に何も足さない
  assert.doesNotMatch(renderProjectsPage(page([], { selfRulesRel: "" }), { nonce: "n" }), /自身の層のルール/);
});
