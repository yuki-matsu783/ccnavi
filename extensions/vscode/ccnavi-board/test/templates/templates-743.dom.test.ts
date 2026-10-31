/**
 * テンプレート管理画面の新しい名前の検査・子のフロー編集の案内文・CSS（#743）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { parseTemplateCheck, validNewTemplateName, validTemplateName } from "../../src/core/templatesmodel.js";
import type { EditOpened } from "../../src/core/templates-view.js";
import { loadPage, type DomPage } from "../helpers/dom.js";
import { page, templatesCheckFixtureText, templatesHtml } from "../helpers/templates.js";
import type { HTMLButtonElement } from "happy-dom" with { "resolution-mode": "import" };

test("CB-T1236 新しい名前は、Windows の予約名（拡張子付きも）と末尾の . を断る。読み込み側の検査は変えない", () => {
  for (const ok of ["feature", "a", "console", "com10", "lpt0", "v1.2_x", "con-x", "A".repeat(64)]) {
    assert.ok(validNewTemplateName(ok), ok);
  }
  for (const bad of ["con", "CON", "Prn", "aux", "nul", "com1", "COM9", "lpt1", "lpt9", "con.x", "nul.tar.gz", "a.", "a..", "", "-x", "a b"]) {
    assert.ok(!validNewTemplateName(bad), bad);
  }
  // 置いてあるファイルの読み込み側（`validTemplateName`）は断らない
  assert.ok(validTemplateName("con"));
  assert.ok(validTemplateName("a."));
});

test("CB-T1237 CSS: 計画の編集欄の表は、操作の列を右端に固定する", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "..", "..", "src", "webview", "templates", "Edit.css"), "utf8");
  assert.match(css, /\.tpl-edit-items td\.tpl-edit-ops[^{]*\{[^}]*position: sticky; right: 0/);
});

test("CB-T1242 CSS: 計画の編集欄の表の見出しは、狭い幅でも折れない", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "..", "..", "src", "webview", "templates", "Edit.css"), "utf8");
  assert.match(css, /\.tpl-edit-items th[^{]*\{[^}]*white-space: nowrap/);
});

async function openList(): Promise<DomPage> {
  const dom = await loadPage(templatesHtml({ kind: "page", page: page() }), undefined, { measure: true });
  await dom.settle();
  return dom;
}

test("CB-D1244 新しい名前が con や a. のときは作るを押せず、理由を title に出す", async () => {
  const dom = await openList();
  try {
    for (const bad of ["con", "NUL.txt", "a."]) {
      dom.type(dom.one(".f-new-name"), bad);
      await dom.settle();
      const make = dom.one<HTMLButtonElement>('button[data-action="new-template"]');
      assert.equal(make.disabled, true, bad);
      assert.match(make.title, /Windows の予約名/);
    }
    dom.type(dom.one(".f-new-name"), "my-flow");
    await dom.settle();
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="new-template"]').disabled, false);
  } finally {
    await dom.close();
  }
});

test("CB-D1245 子のフロー編集の右の欄は、グループ化・Ctrl+C/V/D・Ctrl+Z の案内を出さない", async () => {
  const dom = await openList();
  try {
    const parsed = parseTemplateCheck(templatesCheckFixtureText());
    assert.ok(parsed.ok);
    const check = parsed.value;
    const opened: EditOpened = { name: "feature", layer: "self", created: false, sha: "sha", data: check.data, check, lock: { locked: false, reason: "", doing: [] } };
    const row = 'li.template[data-name="feature"][data-layer="self"]';
    if (dom.all(`${row}.open`).length === 0) {
      dom.click(dom.one(`${row} .row-head`));
      await dom.settle();
    }
    dom.click(dom.one(`${row} button[data-action="edit-template"]`));
    await dom.settle();
    const asked = dom.posted.find((m) => m.type === "editOpen");
    await dom.send({ type: "editOpened", session: asked?.session, opened, error: "" });
    await dom.settle();
    const hint = dom.all(".tpl-flow-edit aside.inspector .hint").map((h) => h.textContent ?? "").join("\n");
    assert.match(hint, /ノードを押すと/);
    assert.doesNotMatch(hint, /グループ化|Ctrl\+/);
  } finally {
    await dom.close();
  }
});
