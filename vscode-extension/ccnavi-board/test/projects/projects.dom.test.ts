/** プロジェクト管理画面のスクリプトを happy-dom で動かす。メニューの開閉と各ボタンの送り先。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { ProjectRow, ProjectsPage } from "../../src/core/projects.js";
import { renderProjectsPage } from "../../src/core/projects-render.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLDetailsElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

function row(overrides: Partial<ProjectRow> = {}): ProjectRow {
  return { name: "lib", root: "/ws/projects/lib", rel: "projects/lib", rulesRel: "projects/lib/.ccnavi/config/rules.yml", rulesExists: true, hasClaudeDir: false, origin: "", originKey: "", worktrees: [], tickets: 0, doing: 0, problems: [], ...overrides };
}

function page(rows: readonly ProjectRow[], overrides: Partial<ProjectsPage> = {}): ProjectsPage {
  return { root: "/ws", generatedAt: "2026-09-13T00:00:00+0900", ticketsEnabled: true, projectsDir: "/ws/projects", projectsRel: "projects", ignored: false, lintError: "", dirProblems: [], rows, strays: [], workspaceWorktrees: [], existingNames: rows.map((r) => r.name), selfRulesRel: ".ccnavi/config/rules.yml", selfRulesExists: false, ...overrides };
}

test("CB-D30 メニューは 1 つだけ開き、項目を押すと名前付きで送って閉じる。Esc でも閉じる", async () => {
  const dom = await loadPage(renderProjectsPage(page([row(), row({ name: "app", rel: "projects/app", root: "/ws/projects/app" })]), { nonce: "n" }));
  try {
    const menus = dom.all<HTMLDetailsElement>('li.project[data-name="lib"] details.menu');
    assert.equal(menus.length, 2);
    dom.click(menus[0].querySelector("summary")!);
    await dom.settle();
    assert.ok(menus[0].open);
    dom.click(menus[1].querySelector("summary")!);
    await dom.settle();
    assert.ok(!menus[0].open, "別のメニューを開くと前のは閉じる");
    assert.ok(menus[1].open);
    dom.click(dom.one('li.project[data-name="lib"] button[data-action="pull"]'));
    assert.deepEqual(dom.posted, [{ type: "pull", name: "lib" }]);
    assert.ok(!menus[1].open, "項目を押すと閉じる");
    dom.click(menus[0].querySelector("summary")!);
    await dom.settle();
    dom.document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape" }));
    assert.ok(!menus[0].open);
  } finally {
    await dom.close();
  }
});

test("CB-D31 帯と行のボタンはそれぞれの型で送る。clone は欄の URL と名前を送り、名前は URL から埋まる", async () => {
  const dom = await loadPage(renderProjectsPage(page([row({ rulesExists: false })]), { nonce: "n" }));
  try {
    dom.click(dom.one('button[data-action="fix-ignore"]'));
    dom.click(dom.one('button[data-action="create-rules"][data-name="lib"]'));
    dom.click(dom.one('button[data-action="open-board"][data-name="lib"]'));
    dom.type(dom.one("#url"), "https://gitlab.example.com/g/tool.git");
    assert.equal(dom.one<HTMLInputElement>("#name").value, "tool");
    dom.click(dom.one('button[data-action="clone"]'));
    assert.deepEqual(dom.posted, [
      { type: "fixIgnore" },
      { type: "createRules", name: "lib" },
      { type: "openBoard", name: "lib" },
      { type: "clone", url: "https://gitlab.example.com/g/tool.git", name: "tool" },
    ]);
    await dom.send({ type: "cloned", message: "clone を送った" });
    assert.equal(dom.one<HTMLInputElement>("#url").value, "");
    assert.equal(dom.one("#status").textContent, "clone を送った");
  } finally {
    await dom.close();
  }
});
