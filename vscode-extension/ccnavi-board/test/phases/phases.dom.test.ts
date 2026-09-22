/** フェーズ管理画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../../src/core/phases-doc.js";
import type { PhasesForm } from "../../src/core/phases-view.js";
import { openPage, openPhases, page, rowSelector } from "../helpers/phases.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直前に送った保存の中身 */
function savedForm(dom: DomPage): PhasesForm {
  const saves = dom.posted.filter((message) => message.type === "save");
  assert.ok(saves.length > 0, "保存を送っていない");
  return saves[saves.length - 1].form as PhasesForm;
}

test("CB-D20 既定は畳み、行を押すと開いて state に id が入る。関係と案内は値がある種類だけ開く", async () => {
  const dom = await openPhases();
  try {
    assert.equal(dom.all(".phase").length, 5);
    assert.equal(dom.all(".phase.open").length, 0);
    dom.click(dom.one(`${rowSelector("p2")} .row-head`));
    await dom.settle();
    assert.ok(dom.one(rowSelector("p2")).classList.contains("open"));
    assert.deepEqual((dom.state() as { open: string[] }).open, ["design"]);
    // 雛形の design は when を持つので開く。implement-feedback は関係も案内も無いので閉じる
    assert.ok(dom.one(`${rowSelector("p2")} details.more`).hasAttribute("open"));
    assert.ok(!dom.one(`${rowSelector("p5")} details.more`).hasAttribute("open"));
  } finally {
    await dom.close();
  }
});

test("CB-D21 範囲を inherit に変えると glob の欄が消え、行は開いたまま。id が重なると保存できない", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p2")} .row-head`));
    await dom.settle();
    dom.change(dom.one(`${rowSelector("p2")} select.f-scope`), "inherit");
    await dom.settle();
    assert.ok(dom.one(rowSelector("p2")).classList.contains("open"));
    assert.equal(dom.all(`${rowSelector("p2")} input.f-scope-globs`).length, 0);
    assert.equal(dom.one(`${rowSelector("p2")} .sum .mono`).textContent, "inherit");
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    dom.type(dom.one(`${rowSelector("p2")} input.f-id`), "research");
    await dom.settle();
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled);
    assert.match(dom.one("#status").textContent ?? "", /id が重なっている（research）/);
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("p2")} input.f-id`).classList.contains("duplicate"));
    // 直せば保存できるようになり、文面も消える
    dom.type(dom.one(`${rowSelector("p2")} input.f-id`), "design2");
    await dom.settle();
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    assert.equal(dom.one("#status").textContent, "");
  } finally {
    await dom.close();
  }
});

test("CB-D22 絞り込みは title と scope にも当たり、開いている行は隠さず、一致した行だけを数える。足した種類は開いて焦点が id に来る", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one("#find"), "設計");
    await dom.settle();
    assert.equal(dom.one("#phase-count").textContent, "1 / 5（開いたまま 1）", "開いている research は一致しないので数えず、開いたままの数として添える");
    assert.ok(dom.one(rowSelector("p1")).classList.contains("hidden-by-find"));
    assert.ok(dom.one(rowSelector("p1")).classList.contains("open"));
    assert.ok(!dom.one(rowSelector("p2")).classList.contains("hidden-by-find"));
    dom.type(dom.one("#find"), "");
    await dom.settle();
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    const rows = dom.all(".phase");
    const added = rows[rows.length - 1];
    assert.ok(added.classList.contains("open"));
    assert.equal(dom.document.activeElement, added.querySelector("input.f-id"));
    assert.equal(added.querySelector(".sum .mono")?.textContent, "inherit");
  } finally {
    await dom.close();
  }
});

test("CB-D23 保存の往復の間は欄を止めるが、行の開閉のボタンは止めない。失敗が返れば欄は戻る", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.equal(dom.posted.filter((message) => message.type === "save").length, 1);
    assert.equal(savedForm(dom).phases[0].title, "調べる");
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("p1")} input.f-title`).disabled);
    assert.ok(!dom.one<HTMLButtonElement>(`${rowSelector("p1")} .row-head .twist`).disabled);
    await dom.send({ type: "failed", message: "lint error" });
    assert.ok(!dom.one<HTMLInputElement>(`${rowSelector("p1")} input.f-title`).disabled);
    assert.equal(dom.one("#status").textContent, "lint error");
  } finally {
    await dom.close();
  }
});

test("CB-T125 種類の欄名は日本語で、YAML のキー名は欄名の title に載せる", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    const caps = dom.all(`${rowSelector("p1")} .row-body .field > .cap`);
    assert.deepEqual(
      caps.map((cap) => cap.textContent),
      ["id", "題", "区分", "レビュー", "範囲", "成果物", "並行できる種類", "一緒に要る種類", "エージェント", "置く目安"],
    );
    assert.deepEqual(
      caps.map((cap) => cap.getAttribute("title")),
      ["id", "title", "kind", "review", "scope", "deliverables", "overlap", "requires", "agent", "when"].map((key) => `YAML のキー: ${key}`),
    );
  } finally {
    await dom.close();
  }
});

test("CB-D59 並びの欄は , で区切って打て、打っている途中の区切りは消えない", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p2")} .row-head`));
    await dom.settle();
    const scope = dom.one<HTMLInputElement>(`${rowSelector("p2")} input.f-scope-globs`);
    assert.equal(scope.value, "wip/design/*, docs/*");
    dom.type(scope, "wip/design/*, docs/*, ");
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("p2")} input.f-scope-globs`).value, "wip/design/*, docs/*, ", "区切りの直後が打てる");
    dom.type(dom.one(`${rowSelector("p2")} input.f-scope-globs`), "wip/design/*, docs/*, README.md");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedForm(dom).phases[1].scope, ["wip/design/*", "docs/*", "README.md"]);
  } finally {
    await dom.close();
  }
});

test("CB-D60 種類の並べ替えと削除が保存に渡る形に出る", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.all<HTMLButtonElement>(`${rowSelector("p3")} .buttons button`)[0]);
    await dom.settle();
    dom.click(dom.all<HTMLButtonElement>(`${rowSelector("p5")} .buttons button`)[2]);
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(
      savedForm(dom).phases.map((phase) => phase.id),
      ["research", "acceptance", "design", "implement"],
    );
  } finally {
    await dom.close();
  }
});

test("CB-D61 ファイルが外で変わったら帯を出し、届いた中身で編集を置き換える", async () => {
  const dom = await openPhases();
  try {
    assert.deepEqual(dom.posted, [{ type: "ready" }]);
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる");
    await dom.settle();
    await dom.send({ type: "changed" });
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("p1")} input.f-title`).value, "調べる", "帯が出ても編集は消さない");
    // 再読込が通ったら、その中身で描き直す
    await dom.send({ type: "data", data: { kind: "page", page: page({ phasesPath: ".ccnavi/common/phases.yml" }) } });
    assert.ok(dom.one("#changed").classList.contains("hidden"));
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.one(".path").textContent, ".ccnavi/common/phases.yml");
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("p6")} input.f-title`).value, "調査");
  } finally {
    await dom.close();
  }
});

test("CB-D62 読み直せなかったら理由を出し、種類は出さない", async () => {
  const dom = await openPage({ kind: "error", error: "種類のファイルを読めない: EACCES" });
  try {
    assert.match(dom.one(".empty").textContent, /フェーズ管理画面を読み直せなかった/);
    assert.equal(dom.one("pre.load-error").textContent, "種類のファイルを読めない: EACCES");
    assert.equal(dom.all("#phases").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D63 種類が無いファイルは、保存する前に足すと言う。苦情と錠はそのまま出す", async () => {
  const dom = await openPhases({
    model: readPhases("version: 1\nphases: nope\n").model,
    lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] },
  });
  try {
    assert.match(dom.one("#phases .empty").textContent, /種類が無い。種類が 1 つも無いファイルは実行ファイルが読めない/);
    assert.match(dom.one(".problems").textContent, /phases が対応表ではない/);
    assert.equal(dom.one("#lock").textContent, "作業中のチケットがある（i0001-02）");
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
  } finally {
    await dom.close();
  }
});

test("CB-D67 関係と案内は、最後の値を消しても畳まれない（打っている欄が消えない）", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p2")} .row-head`));
    await dom.settle();
    // 雛形の design は when を持つので開いている
    assert.ok(dom.one(`${rowSelector("p2")} details.more`).hasAttribute("open"));
    dom.type(dom.one(`${rowSelector("p2")} input.f-when`), "");
    await dom.settle();
    assert.ok(dom.one(`${rowSelector("p2")} details.more`).hasAttribute("open"), "値を消した拍子に、打っている欄ごと畳まない");
    assert.match(dom.one(`${rowSelector("p2")} details.more > summary`).textContent, /関係と案内（未設定）/);
  } finally {
    await dom.close();
  }
});

test("CB-D68 往復の間は帯の再読込も止め、再読込を押した時点で欄を止める", async () => {
  const dom = await openPhases();
  try {
    await dom.send({ type: "changed" });
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    for (const button of dom.all<HTMLButtonElement>('button[data-action="reload"]')) {
      assert.ok(button.disabled, "保存の往復の間はどの再読込も押せない");
    }
    await dom.send({ type: "failed", message: "lint error" });
    dom.click(dom.one('#changed button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload"), [{ type: "reload", dirty: true }]);
    // 層の置き場を実行ファイルに聞く間、欄は止まっている
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("p1")} input.f-title`).disabled);
    await dom.send({ type: "cancelled" });
    assert.ok(!dom.one<HTMLInputElement>(`${rowSelector("p1")} input.f-title`).disabled);
  } finally {
    await dom.close();
  }
});
