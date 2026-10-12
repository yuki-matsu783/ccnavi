/**
 * テンプレート管理画面（React）を happy-dom で動かす。一覧の札と捨てた理由、足し算の枠、
 * 1 件の計画と本文、下書き、帯と錠、エラー・読み込み中、見た目、対象の切り替えを見る。
 * 見本は `tests/fixtures/templates-json.json` の `expect`（self の層）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import type { TemplateDetail } from "../../src/core/templates-view.js";
import { fixtureJson, openTemplates, page, openPage } from "../helpers/templates.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLDetailsElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

/** 要素の文字。前後の空白は除く */
function text(element: { textContent: string | null } | null | undefined): string {
  return (element?.textContent ?? "").trim();
}

/** 行の見出しを押して開く */
async function openRow(dom: DomPage, selector: string): Promise<void> {
  dom.click(dom.one(`${selector} .row-head`));
  await dom.settle();
}

test("CB-D324 一覧は開いている対象の層のテンプレートだけを出し、捨てた理由と使えない札を行に出す。足し算の枠は別に出す", async () => {
  const dom = await openTemplates();
  try {
    // 対象は self。一覧は self の層の 4 件（ends / feature / local / research）だけで、共通の層の行は出さない
    assert.deepEqual(
      dom.all("li.template").map((li) => `${li.getAttribute("data-layer")}/${li.getAttribute("data-name")}`),
      ["self/ends", "self/feature", "self/local", "self/research"],
    );
    assert.match(text(dom.one('li.template[data-name="feature"][data-layer="self"]')), /捨てた: 共通と同じ中身/);
    assert.match(text(dom.one('li.template[data-name="ends"][data-layer="self"]')), /使えない/);
    assert.match(text(dom.one('li.template[data-name="research"][data-layer="self"]')), /捨てた: 共通と食い違う/);
    assert.equal(dom.all('button[data-action="view-sum"], button[data-action="view-raw"]').length, 0);
    assert.equal(dom.all("details#sum").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D325 足し算の枠は閉じた枠で出て、選んだ足し算の索引（名前・由来・状態・捨てた理由）を表に出す。足し算は欄で切り替える", async () => {
  const dom = await openTemplates();
  try {
    const sum = dom.one<HTMLDetailsElement>("details#sum");
    assert.equal(sum.open, false);
    assert.equal(dom.one<HTMLSelectElement>("select#sum-select").value, "self");
    const rows = dom.all("details#sum tbody tr").map((tr) => `${tr.getAttribute("data-layer")}/${tr.getAttribute("data-name")}`);
    assert.deepEqual(rows, ["common/broken", "common/feature", "common/research", "self/ends", "self/feature", "self/local", "self/research"]);
    assert.match(text(dom.one('details#sum tr[data-layer="common"][data-name="broken"]')), /読めない/);
    assert.match(text(dom.one('details#sum tr[data-layer="self"][data-name="feature"]')), /使われない/);
    assert.match(text(dom.one('details#sum tr[data-layer="self"][data-name="research"]')), /共通と食い違う/);
    assert.match(text(dom.one('[data-count="sum"]')), /使われるのは 4 件（common \+ self）/);
    dom.change(dom.one<HTMLSelectElement>("select#sum-select"), "lib");
    await dom.settle();
    assert.deepEqual(
      dom.all("details#sum tbody tr").map((tr) => `${tr.getAttribute("data-layer")}/${tr.getAttribute("data-name")}`),
      ["common/broken", "common/feature", "common/research", "lib/lib"],
    );
    // 画面が覚えるものは無い（表示の切り替えを廃止した）
    assert.equal(dom.state(), undefined);
  } finally {
    await dom.close();
  }
});

test("CB-D326 見出しを押すと開き、計画の表が番号と種類を出す。本文は依頼を送り、返った本文の見えない文字を \\u で見せる", async () => {
  // 共通の層の行を開くので、対象は共通の設定（workspace）にする（一覧は開いている対象の層だけ）
  const dom = await openPage({ kind: "page", page: page({ target: { kind: "workspace", name: "" } }) });
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

test("CB-D329 外の変更の帯は changed で隠れた帯が現れ、更新は refresh を送る。錠は隠れた p が理由つきで現れる", async () => {
  const dom = await openTemplates();
  try {
    assert.ok(dom.one("#changed").classList.contains("hidden"));
    assert.ok(dom.one("#lock").classList.contains("hidden"));
    await dom.send({ type: "changed" });
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    dom.click(dom.one('#changed button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "refresh"), [{ type: "refresh", dirty: false }]);
    await dom.send({
      type: "lock",
      lock: { locked: true, reason: "作業中のチケットがあります（T1）", doing: ["T1"] },
      projectLocks: {},
    });
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
    assert.match(text(dom.one("#lock")), /取り込みと編集は止めています: 作業中のチケットがあります（T1）/);
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

test("CB-D348 足し算で捨てられた行の本文は、層か sha が違えば出さず、読めない理由と直接開く先を出す。一致すれば本文を出す", async () => {
  const dom = await openTemplates();
  try {
    await openRow(dom, 'li.template[data-name="feature"][data-layer="self"]');
    dom.click(dom.one('li.template[data-name="feature"][data-layer="self"] [data-action="show-text"]'));
    await dom.settle();
    // 共通と同じ中身の self の feature（捨てられた行）。実行ファイルは足し算で使われる共通の本文を返す
    const self = fixtureJson().templates.find((t) => t.name === "feature" && t.layer === "self");
    const common = fixtureJson().templates.find((t) => t.name === "feature" && t.layer === "common");
    assert.ok(self !== undefined && common !== undefined);
    assert.equal(self.sha, common.sha);
    // 層が違う本文は出さない（sha は同じでも）
    await dom.send({ type: "detail", name: "feature", layer: "self", template: { ...common, text: "共通の本文" }, error: "" });
    assert.equal(dom.all("pre.tpl-text").length, 0);
    assert.match(text(dom.one('[data-role="detail-mismatch"]')), /この行の本文は読めません/);
    assert.match(text(dom.one('[data-role="detail-mismatch"]')), /feature\.yml/);
    // 層が同じでも sha が違う本文は出さない
    await dom.send({ type: "detail", name: "feature", layer: "self", template: { ...self, sha: "f".repeat(64), text: "別の本文" }, error: "" });
    assert.equal(dom.all("pre.tpl-text").length, 0);
    assert.equal(dom.all('[data-role="detail-mismatch"]').length, 1);
    // 層と sha が一致する本文は出す
    await dom.send({ type: "detail", name: "feature", layer: "self", template: { ...self, text: "自分の本文" }, error: "" });
    assert.equal(dom.all('[data-role="detail-mismatch"]').length, 0);
    assert.equal(text(dom.one("pre.tpl-text")), "自分の本文");
  } finally {
    await dom.close();
  }
});
