import { test } from "node:test";
import assert from "node:assert/strict";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRulesPage } from "../../src/core/rules-render.js";
import type { ProjectRow, ProjectsPage } from "../../src/core/projects.js";
import { renderProjectsPage } from "../../src/core/projects-render.js";

function row(overrides: Partial<ProjectRow> = {}): ProjectRow {
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
    samplesPath: ".ccnavi/common/rule-samples.yml",
    lock: { locked: false, reason: "", doing: [] },
  };
  const html = renderRulesPage({ ...base, notices: ["実行ファイルはこのファイルを読めない: <理由>"] }, { nonce: "n" });
  assert.match(html, /<div class="banner warn">実行ファイルはこのファイルを読めない: &lt;理由&gt;<\/div>/);
  // 「外で変わった」の帯（hidden 付き）は常にあるので、注意の帯だけを見る
  assert.doesNotMatch(renderRulesPage(base, { nonce: "n" }), /<div class="banner warn">/);
});

test("CB-T113 カードは層の置き場を出す。自身の層は本体の枠に出す", () => {
  const html = renderProjectsPage(
    page([
      row({ name: "app", rel: "projects/app", rulesRel: "projects/app/.ccnavi/config/rules.yml" }),
      row({ rulesExists: false }),
      row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false }),
    ]),
    { nonce: "n" },
  );
  // カードは </li> で切る。切らないと最後のカードにページ末尾のスクリプト（create-rules の綴りを含む）が付く
  const cards = html.split('<li class="project').slice(1).map((c) => c.slice(0, c.indexOf("</li>")));
  const [app, lib, reserved] = cards;
  assert.match(app, /あり<\/span> <span class="mono small">projects\/app\/\.ccnavi\/config\/rules\.yml/);
  assert.match(lib, /なし<\/span> <span class="mono small dim">projects\/lib\/\.ccnavi\/config\/rules\.yml<\/span>/);
  assert.match(lib, /data-action="create-rules" data-name="lib"/);
  assert.match(lib, /data-action="open-rules" data-name="lib" disabled /);
  // 予約名のプロジェクトは層が無いので、置く先も作るボタンも出さない
  assert.match(reserved, /層として数えられていない/);
  assert.doesNotMatch(reserved, /create-rules/);

  const workspace = html.slice(html.indexOf('<section class="workspace">'));
  assert.match(workspace, /自身の層のルール<\/span> <span class="dim">なし<\/span> <span class="mono small">\.ccnavi\/config\/rules\.yml<\/span>/);
  assert.match(workspace, /data-action="create-self-rules"/);
  assert.match(workspace, /data-action="open-self-rules" disabled /);
  const exists = renderProjectsPage(page([], { selfRulesExists: true }), { nonce: "n" });
  assert.doesNotMatch(exists, /data-action="create-self-rules"/);
  assert.match(exists, /data-action="open-self-rules" title=/);
});

test("CB-T123 プロジェクト管理は同じ事象の注意を 1 か所にだけ出し、行末のボタンは 2 つのメニューにまとめる", () => {
  const html = renderProjectsPage(
    page(
      [row({ hasClaudeDir: true, problems: [{ severity: "warn", where: "(projects/lib)", detail: ".claude/ を持つ。Claude Code がそこのスキルを読み、cd 1 回で別のルートに見える" }, { severity: "warn", where: "(projects/lib)", detail: ".claude/settings.json を読めない: 壊れている" }, { severity: "error", where: "(projects/lib) x", detail: "文面が無い" }] })],
      { ignored: false, dirProblems: [{ severity: "warn", where: "(projects)", detail: "projects/ がワークスペースの git で無視されていない" }, { severity: "warn", where: "(projects)", detail: "別の指摘" }] },
    ),
    { nonce: "n" },
  );
  // .gitignore の帯（直すボタン付き）があるので、lint の「無視されていない」は重ねない。別の指摘は出る
  assert.match(html, /data-action="fix-ignore"/);
  assert.doesNotMatch(html, /無視されていない/);
  assert.match(html, /warn: 別の指摘/);
  // .claude/ の説明があるので、lint の同じ指摘（実物の文面「.claude/ を持つ。…」）は重ねない。
  // ".claude/settings.json を読めない" のような別の warn と error は出る
  assert.doesNotMatch(html, /\.claude\/ を持つ/);
  assert.match(html, /warn: \.claude\/settings\.json を読めない: 壊れている/);
  assert.match(html, /\.claude\/ がある。Claude Code は[^<]*プロジェクトの設定は \.ccnavi\/config\/ に置く/);
  assert.match(html, /error: 文面が無い/);
  // 行末は「開く ▾」と「git ▾」の 2 つ。中のボタンの data-action は前のまま
  const card = html.slice(html.indexOf('<li class="project'), html.indexOf("    </li>"));
  assert.equal((card.match(/<details class="menu">/g) ?? []).length, 2);
  assert.match(card, /<summary class="action">開く ▾<\/summary>/);
  assert.match(card, /<summary class="action">git ▾<\/summary>/);
  for (const action of ["open-rules", "open-phases", "open-board", "fetch", "pull"]) {
    assert.match(card, new RegExp(`data-action="${action}" data-name="lib"`), action);
  }
  // 置き場の案内は層のルールの置き場から逆算する。ディレクトリを挟まない形や層でない行は既定
  const flat = renderProjectsPage(page([row({ hasClaudeDir: true, rulesRel: "projects/lib/rules.yml" }), row({ name: "app", rel: "projects/app", hasClaudeDir: true, rulesRel: "projects/app/conf/ccnavi/rules.yml" }), row({ name: "Self", rel: "projects/Self", hasClaudeDir: true, rulesRel: "" })]), { nonce: "n" });
  const dirs = [...flat.matchAll(/プロジェクトの設定は ([^ ]+)\/ に置く/g)].map((m) => m[1]);
  assert.deepEqual(dirs, [".ccnavi/config", "conf/ccnavi", ".ccnavi/config"]);
  // メニューの項目は HC で枠が出る書き方（contrastBorder の変数）。押せない項目は点線
  assert.match(html, /\.menu > \.menu-items > button\.action \{ justify-content: flex-start; border-color: var\(--vscode-contrastBorder, transparent\);/);
  assert.match(html, /\.menu > \.menu-items > button\.action:disabled \{ border-style: dashed; \}/);
  // .gitignore が済んでいれば lint の指摘はそのまま出る
  const fine = renderProjectsPage(page([row()], { dirProblems: [{ severity: "warn", where: "(projects)", detail: "projects/ がワークスペースの git で無視されていない" }] }), { nonce: "n" });
  assert.match(fine, /warn: projects\/ がワークスペースの git で無視されていない/);
});
