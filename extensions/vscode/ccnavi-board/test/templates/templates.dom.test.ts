/**
 * テンプレート管理画面（React）を happy-dom で動かす。一覧の札と捨てた理由、足し算と層ごとの切り替え、
 * 1 件の計画と本文、下書き、帯と錠、エラー・読み込み中、見た目、対象の切り替えを見る。
 * 見本は `tests/fixtures/templates-json.json` の `expect`（self の層）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { TemplateDetail } from "../../src/core/templates-view.js";
import { fixtureJson, openTemplates, page, openPage } from "../helpers/templates.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は除く */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

/** 行の見出しを押して開く */
async function openRow(dom: DomPage, selector: string): Promise<void> {
  dom.click(dom.one(`${selector} .row-head`));
  await dom.settle();
}

test("CB-D324 足し算の一覧は data.templates の件数だけ行を出し、捨てた理由と使えない札を行に出す", async () => {
  const dom = await openTemplates();
  try {
    assert.equal(dom.all("li.template").length, fixtureJson().templates.length);
    assert.match(text(dom.one('li.template[data-name="broken"][data-layer="common"]')), /捨てた: 読めない/);
    assert.match(text(dom.one('li.template[data-name="feature"][data-layer="self"]')), /捨てた: 共通と同じ中身/);
    assert.match(text(dom.one('li.template[data-name="ends"][data-layer="self"]')), /使えない/);
    // 同じ名前の行は層で引き分ける
    assert.match(text(dom.one('li.template[data-name="feature"][data-layer="common"]')), /使える/);
  } finally {
    await dom.close();
  }
});

test("CB-D325 「層ごと」は層の箱を共通と自分の 2 つ出し、自分の層の苦情を [warn] の文字つきで出す。表示は覚える", async () => {
  const dom = await openTemplates();
  try {
    dom.click(dom.one('button[data-action="view-raw"]'));
    await dom.settle();
    assert.deepEqual(
      dom.all("div.tpl-layer").map((box) => box.getAttribute("data-layer")),
      ["common", "self"],
    );
    const warn = dom.one('div.tpl-layer[data-layer="self"] .tpl-problems li.sev-warn');
    assert.match(text(warn), /\[warn\]/);
    assert.match(text(warn), /notes\.txt/);
    assert.deepEqual(dom.state(), { view: "raw" });
  } finally {
    await dom.close();
  }
});

test("CB-D326 見出しを押すと開き、計画の表が番号と種類を出す。本文は依頼を送り、返った本文の見えない文字を \\u で見せる", async () => {
  const dom = await openTemplates();
  try {
    await openRow(dom, 'li.template[data-name="feature"][data-layer="common"]');
    // 全体計画（先頭の表）は 2 行、フィードバック計画（次の表）は 1 行
    const [plan] = dom.all("li.template[data-name='feature'][data-layer='common'] table.tpl-plan");
    assert.equal(text(plan.querySelector("caption")), "全体計画");
    const rows = Array.from(plan.querySelectorAll("tbody tr"));
    assert.equal(rows.length, 2);
    assert.equal(text(rows[0].querySelector("td")), "1");
    assert.match(text(rows[0]), /design/);
    // 待つ項は提案に写した番号で出す（index ではなく number）
    assert.equal(text(rows[1].querySelectorAll("td")[3]), "1");
    dom.click(dom.one('li.template[data-name="feature"][data-layer="common"] [data-action="show-text"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "showTemplate"), [{ type: "showTemplate", name: "feature", layer: "common" }]);
    const detail: TemplateDetail = { ...fixtureJson().templates[1], text: "a‮b\nc" };
    await dom.send({ type: "detail", name: "feature", layer: "common", template: detail, error: "" });
    const pre = dom.one("pre.tpl-text");
    assert.match(text(pre), /a\\u202eb/);
    assert.ok(!(pre.textContent ?? "").includes("‮"));
    assert.ok((pre.textContent ?? "").includes("\n"));
  } finally {
    await dom.close();
  }
});

test("CB-D327 苦情の件数と深刻度の文字（[error] [warn] [info]）が出る。要約の件数は見本の数と合う", async () => {
  const dom = await openTemplates();
  try {
    const summary = text(dom.one('[data-tour="summary"]'));
    assert.match(summary, /error 3/);
    assert.match(summary, /warn 2/);
    assert.match(summary, /info 1/);
    await openRow(dom, 'li.template[data-name="ends"][data-layer="self"]');
    assert.match(text(dom.one('li.template[data-name="ends"][data-layer="self"] .sev-label')), /\[error\]/);
    await openRow(dom, 'li.template[data-name="feature"][data-layer="self"]');
    assert.match(text(dom.one('li.template[data-name="feature"][data-layer="self"] .sev-label')), /\[info\]/);
    await openRow(dom, 'li.template[data-name="local"][data-layer="self"]');
    assert.match(text(dom.one('li.template[data-name="local"][data-layer="self"] .sev-label')), /\[warn\]/);
  } finally {
    await dom.close();
  }
});

test("CB-D328 下書きは 3 件出て、取り込めない・TODO・同名ありを札で出す。効力が無いことを一覧の前に言う", async () => {
  const dom = await openTemplates();
  try {
    assert.equal(dom.all("li.draft").length, 3);
    assert.match(text(dom.one('li.draft[data-name="broken"]')), /取り込めない/);
    assert.match(text(dom.one('li.draft[data-name="login"]')), /TODO が残っている/);
    assert.match(text(dom.one('li.draft[data-name="research"]')), /同名あり: common/);
    assert.match(text(dom.one('[data-tour="drafts"] .hint')), /効力のない下書き/);
  } finally {
    await dom.close();
  }
});

test("CB-D329 変更の帯は changed で出し、更新は refresh を送る。錠は理由を帯に出す", async () => {
  const dom = await openTemplates();
  try {
    assert.equal(dom.all('[data-banner="changed"]').length, 0);
    await dom.send({ type: "changed" });
    assert.equal(dom.all('[data-banner="changed"]').length, 1);
    dom.click(dom.one('button[data-action="refresh"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "refresh"), [{ type: "refresh", dirty: false }]);
    await dom.send({
      type: "lock",
      lock: { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] },
      projectLocks: {},
    });
    assert.match(text(dom.one('[data-banner="lock"]')), /作業中のチケットがあります（T1）/);
  } finally {
    await dom.close();
  }
});

test("CB-D330 読み込みのエラーは文面を出し、読み込み中は読み込みの文を出す。見た目は body のクラスだけを変える", async () => {
  const dom = await openTemplates();
  try {
    await dom.send({ type: "data", data: { kind: "error", error: "実行ファイルが返らない", target: { kind: "self", name: "" }, targets: page().targets } });
    assert.equal(text(dom.one("pre.load-error")), "実行ファイルが返らない");
    assert.ok(dom.all("select#target").length === 1);
    await dom.send({ type: "data", data: { kind: "loading", text: "読み込み中です" } });
    assert.equal(text(dom.one("#ccnavi-loading")), "読み込み中です");
    await dom.send({ type: "appearance", value: "claude-dark" });
    assert.ok(dom.document.body.classList.contains("ccnavi-claude-dark"));
    await dom.send({ type: "appearance", value: "vscode" });
    assert.ok(!dom.document.body.classList.contains("ccnavi-claude-dark"));
  } finally {
    await dom.close();
  }
});

test("CB-D331 対象を切り替えると switchTarget を送る。共通の設定を開いたときは共通の層のテンプレートだけを出す", async () => {
  const dom = await openTemplates();
  try {
    dom.change(dom.one<HTMLSelectElement>("select#target"), "workspace:");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "switchTarget"), [{ type: "switchTarget", kind: "workspace", name: "" }]);
  } finally {
    await dom.close();
  }
  const common = await openPage({ kind: "page", page: page({ target: { kind: "workspace", name: "" } }) });
  try {
    assert.deepEqual(
      common.all("li.template").map((li) => li.getAttribute("data-layer")),
      ["common", "common", "common"],
    );
  } finally {
    await common.close();
  }
});
