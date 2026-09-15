/**
 * ルール設定画面のスクリプトを happy-dom で動かす。行の開閉・絞り込み・札・判定の展開など、
 * HTML の文字列を見るだけでは分からない振る舞いを確かめる。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHooks } from "../../src/core/hooks.js";
import { readRules } from "../../src/core/rules-doc.js";
import { renderRulesPage } from "../../src/core/rules-render.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const RULES = `version: 3
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: "push は人が行う"
  - id: no-rm
    match: Bash
    glob: "*rm -rf*"
    message: "消さない"
    additionalContext: "代わりに ccnavi-git.sh rm"
ask:
  - id: deps
    match: Write|Edit
    regex: "pyproject\\\\.toml"
    additionalContextOnce: "依存が変わる"
allow: []
`;

function html(): string {
  return renderRulesPage(
    {
      root: "/ws",
      rulesPath: ".ccnavi/common/rules.yml",
      mode: "enable",
      model: readRules(RULES).model,
      hooks: parseHooks(JSON.stringify({ hooks: {} }), "settings"),
      hookFiles: { settings: true, settingsLocal: false },
      samplesPath: ".ccnavi/common/rule-samples.yml",
      lock: { locked: false, reason: "", doing: [] },
    },
    { nonce: "n" },
  );
}

test("CB-D01 既定は全部畳む。行の見出しを押すと開き、開いた行の id が state に入る。もう一度押すと畳む", async () => {
  const page = await loadPage(html());
  try {
    assert.equal(page.all(".rule").length, 3);
    assert.equal(page.all(".rule.open").length, 0);
    const head = page.one('.rule[data-id="no-rm"] .row-head');
    page.click(head);
    assert.ok(page.one('.rule[data-id="no-rm"]').classList.contains("open"));
    assert.deepEqual((page.state() as { open: string[] }).open, ["no-rm"]);
    // 開いた行の欄には値が入っている
    assert.equal(page.one<HTMLInputElement>('.rule[data-id="no-rm"] input.f-id').value, "no-rm");
    page.click(head);
    assert.ok(!page.one('.rule[data-id="no-rm"]').classList.contains("open"));
    assert.deepEqual((page.state() as { open: string[] }).open, []);
  } finally {
    await page.close();
  }
});

test("CB-D02 state に控えた id の行は、読み直したあとも開いている", async () => {
  const page = await loadPage(html(), { open: ["deps"], tab: "rules" });
  try {
    assert.ok(page.one('.rule[data-id="deps"]').classList.contains("open"));
    assert.equal(page.all(".rule.open").length, 1);
  } finally {
    await page.close();
  }
});

test("CB-D03 コンテキストの欄は値があるルールだけ最初から開き、利用者が閉じれば描き直しても閉じたまま", async () => {
  const page = await loadPage(html());
  try {
    assert.ok(!page.one('.rule[data-id="git-push"] details.more').hasAttribute("open"));
    assert.ok(page.one('.rule[data-id="no-rm"] details.more').hasAttribute("open"));
    assert.ok(page.one('.rule[data-id="deps"] details.more').hasAttribute("open"));
    // 閉じてから、形式を変えて描き直す
    const more = page.one('.rule[data-id="no-rm"] details.more');
    more.removeAttribute("open");
    more.dispatchEvent(new page.window.Event("toggle"));
    page.click(page.one('.rule[data-id="no-rm"] .row-head'));
    page.type(page.one('.rule[data-id="no-rm"] select.f-kind'), "regex");
    assert.ok(!page.one('.rule[data-id="no-rm"] details.more').hasAttribute("open"));
    assert.ok(page.one('.rule[data-id="no-rm"]').classList.contains("open"), "描き直しても開いたまま");
  } finally {
    await page.close();
  }
});

test("CB-D04 絞り込みは一致しない行を隠し、開いている行は隠さず、件数は一致した数、畳んだタイプの矢印は開いた向き", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.rule[data-id="deps"] .row-head'));
    page.click(page.one('button[data-action="fold-section"][data-section="deny"]'));
    assert.ok(page.one('.rule-section[data-section="deny"]').classList.contains("folded"));
    page.type(page.one("#find"), "rm -rf");
    const hidden = page.all(".rule").map((r) => `${r.getAttribute("data-id")}:${r.classList.contains("hidden-by-find")}`);
    assert.deepEqual(hidden, ["git-push:true", "no-rm:false", "deps:true"]);
    // 開いている deps は hidden-by-find でも表示は消えない（CSS の :not(.open)）
    assert.ok(page.one("#tab-rules").classList.contains("finding"));
    assert.equal(page.one('[data-count="deny"]').textContent, "1 / 2");
    assert.equal(page.one('[data-count="ask"]').textContent, "0 / 1");
    assert.equal(page.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▾");
    // 空に戻すと元どおり
    page.type(page.one("#find"), "");
    assert.equal(page.all(".rule.hidden-by-find").length, 0);
    assert.equal(page.one('[data-count="deny"]').textContent, "2");
    assert.equal(page.one('.rule-section[data-section="deny"] h2 > .twist').textContent, "▸");
  } finally {
    await page.close();
  }
});

test("CB-D05 ツールの札で選ぶと、欄と行の要約が同じ操作の中で新しい値になる", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.rule[data-id="no-rm"] .row-head'));
    const input = page.one<HTMLInputElement>('.rule[data-id="no-rm"] input.f-match');
    input.dispatchEvent(new page.window.Event("focus"));
    assert.ok(page.one('.rule[data-id="no-rm"] .picker').classList.contains("open"));
    const box = page.one<HTMLInputElement>('.rule[data-id="no-rm"] .picker input[value="Write"]');
    box.checked = true;
    box.dispatchEvent(new page.window.Event("input", { bubbles: true }));
    box.dispatchEvent(new page.window.Event("change", { bubbles: true }));
    assert.equal(input.value, "Bash|Write");
    assert.equal(page.one('.rule[data-id="no-rm"] .sum .mono').textContent, "Bash|Write");
    assert.ok(!page.one<HTMLButtonElement>("#save").disabled, "編集したので保存できる");
  } finally {
    await page.close();
  }
});

test("CB-D06 判定で当たった行はその場で開くが state には入らず、次の判定で畳まれる", async () => {
  const page = await loadPage(html());
  try {
    const judged = (id: string) => ({
      type: "judged",
      result: { known: true, verdict: "deny", code: "", tool: "Bash", subject: "x", rules: [{ section: "deny", id, kind: "glob", written: "*", pattern: ".*", source: "file" }], response: "" },
      hooks: [],
    });
    await page.send(judged("git-push"));
    assert.ok(page.one('.rule[data-id="git-push"]').classList.contains("open"));
    assert.ok(page.one('.rule[data-id="git-push"]').classList.contains("hit"));
    assert.deepEqual(((page.state() as { open?: string[] }) ?? {}).open ?? [], []);
    assert.equal((page.state() as { tab: string }).tab, "judge");
    await page.send(judged("no-rm"));
    assert.ok(!page.one('.rule[data-id="git-push"]').classList.contains("open"));
    assert.ok(page.one('.rule[data-id="no-rm"]').classList.contains("open"));
  } finally {
    await page.close();
  }
});

test("CB-D07 足したルールは開いて焦点が id に来る。タイプを移すと移った先でも開いたまま。保存は今の並びを送る", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('button[data-action="add"][data-section="allow"]'));
    const added = page.one('[data-list="allow"] .rule');
    assert.ok(added.classList.contains("open"));
    assert.equal(page.document.activeElement, page.one('[data-list="allow"] .rule input.f-id'));
    page.type(page.one('[data-list="allow"] .rule input.f-id'), "new-one");
    page.type(page.one('[data-list="allow"] .rule select.f-section'), "ask");
    assert.equal(page.all('[data-list="allow"] .rule').length, 0);
    const moved = page.one('.rule[data-id="new-one"]');
    assert.ok(moved.closest('[data-list="ask"]') !== null);
    assert.ok(moved.classList.contains("open"));
    page.click(page.one("#save"));
    const save = page.posted.find((m) => m.type === "save") as unknown as { sections: { ask: { id: string }[] } };
    assert.deepEqual(save.sections.ask.map((r) => r.id), ["deps", "new-one"]);
  } finally {
    await page.close();
  }
});
