import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../src/core/phases-doc.js";
import { renderPhasesPage, type PhasesPage } from "../src/core/phases-render.js";
import type { ProjectRow, ProjectsPage } from "../src/core/projects.js";
import { renderProjectsPage } from "../src/core/projects-render.js";

function phasesPage(overrides: Partial<PhasesPage> = {}): PhasesPage {
  return {
    root: "/ws",
    phasesPath: ".ccnavi/config/phases.yml",
    exists: false,
    ticketControl: "enable",
    model: { version: null, form: { phases: [] }, problems: [] },
    lock: { locked: false, reason: "", doing: [] },
    ...overrides,
  };
}

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

function projectsPage(rows: readonly ProjectRow[], overrides: Partial<ProjectsPage> = {}): ProjectsPage {
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
    selfRulesExists: true,
    ...overrides,
  };
}

function embedded(html: string): { exists: boolean; editable: boolean } {
  const found = /<script nonce="n" type="application\/json" id="page">(.*?)<\/script>/s.exec(html);
  assert.ok(found !== null);
  return JSON.parse(found[1]);
}

test("CB-T114 層の種類のファイルが無いときは雛形を置かず、欄を触れるようにして最初の保存で作らせる。注意は実体参照で出す", () => {
  const layer = renderPhasesPage(phasesPage({ layer: true, notices: ["読めない <理由>"] }), { nonce: "n" });
  assert.ok(layer.includes('class="banner missing"'));
  assert.ok(layer.includes("最初の保存でファイルが作られる"));
  assert.ok(!layer.includes('data-action="create"'));
  assert.ok(!layer.includes("雛形でファイルを作る</button>"));
  assert.deepEqual(embedded(layer), { ...embedded(layer), exists: false, editable: true });
  assert.ok(layer.includes('<div class="banner warn">読めない &lt;理由&gt;</div>'));

  // 共通層は今までどおり雛形を作るまで触れない。注意が無ければ帯を足さない（「外で変わった」の帯は hidden で常にある）
  const common = renderPhasesPage(phasesPage({ phasesPath: ".claude/ccnavi/phases.yml" }), { nonce: "n" });
  assert.ok(common.includes('data-action="create">雛形でファイルを作る</button>'));
  assert.equal(embedded(common).editable, false);
  assert.ok(!common.includes('<div class="banner warn">'));
});

test("CB-T115 プロジェクト管理画面はカードと本体の枠からフェーズ管理を開ける。層の無いプロジェクトでは押せない", () => {
  const html = renderProjectsPage(
    projectsPage([row(), row({ name: "Self", rel: "projects/Self", rulesRel: "", rulesExists: false })]),
    { nonce: "n" },
  );
  const cards = html.split('<li class="project').slice(1).map((c) => c.slice(0, c.indexOf("</li>")));
  const [lib, reserved] = cards;
  assert.match(lib, /data-action="open-phases" data-name="lib" title=/);
  assert.match(reserved, /data-action="open-phases" data-name="Self" disabled /);
  const workspace = html.slice(html.indexOf('<section class="workspace">'), html.indexOf("<footer"));
  assert.match(workspace, /自身の層のフェーズの種類<\/span> <button type="button" class="action small" data-action="open-self-phases"/);
  assert.match(html, /action === "open-self-phases"\) \{ vscode\.postMessage\(\{ type: "openSelfPhases" \}\)/);
  // 古い実行ファイル（層を出さない）なら本体の枠に層の入口を出さない
  assert.doesNotMatch(renderProjectsPage(projectsPage([], { selfRulesRel: "" }), { nonce: "n" }), /data-action="open-self-phases"/);
});

test("CB-T116 無いファイル（空の本文）に種類を足して書き戻すと、version と種類を持つ読めるファイルになる", () => {
  const text = readPhases("").apply({
    phases: [
      {
        origin: null,
        id: "notes",
        title: "メモ",
        kind: "work",
        review: "none",
        inherit: true,
        scope: [],
        deliverables: [],
        overlap: [],
        requires: [],
        agent: "",
        when: "",
      },
    ],
  });
  assert.match(text, /^version: 1$/m);
  assert.match(text, /^phases:\n {2}notes:\n/m);
  assert.ok(!text.includes("{}"));
  // 層に作るときは先頭に説明のコメントを足す。足しても読み直して苦情が出ない
  const again = readPhases(`# 説明\n${text}`);
  assert.deepEqual(again.model.problems, []);
  assert.deepEqual(again.model.form.phases.map((p) => [p.id, p.title, p.inherit]), [["notes", "メモ", true]]);
});
