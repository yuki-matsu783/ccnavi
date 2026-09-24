/** フェーズ管理画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases, TEMPLATE_PHASES_TEXT } from "../../src/core/phases-doc.js";
import type { PhasesForm } from "../../src/core/phases-view.js";
import { openPage, openPhases, page, rowSelector } from "../helpers/phases.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement, HTMLOptionElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直前に送った保存の中身 */
function savedForm(dom: DomPage): PhasesForm {
  const saves = dom.posted.filter((message) => message.type === "save");
  assert.ok(saves.length > 0, "保存を送っていない");
  return saves[saves.length - 1].form as PhasesForm;
}

/** 関係の欄の選択肢を押す（mousedown。押すたびに 1 件ずつ付け外しする） */
function pick(dom: DomPage, field: string, id: string): void {
  const option = dom.one(`${field} select.id-select option[value="${id}"]`);
  const view = option.ownerDocument.defaultView;
  assert.ok(view !== null);
  option.dispatchEvent(new view.MouseEvent("mousedown", { bubbles: true, cancelable: true }));
}

test("CB-D20 既定は畳み、行を押すと開いて state に id が入る。ほかの種類との関係・補足は値がある種類だけ開く", async () => {
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
    // design は題で、acceptance は when（「設計と並行してよく」）で当たる
    assert.equal(dom.one("#phase-count").textContent, "2 / 5（開いたまま 1）", "開いている research は一致しないので数えず、開いたままの数として添える");
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
      ["id", "題", "区分", "レビュー", "範囲", "成果物", "並行できる種類", "一緒に必要な種類", "先に済ませる種類", "案内するエージェント", "使う場面"],
    );
    assert.deepEqual(
      caps.map((cap) => cap.getAttribute("title")),
      ["id", "title", "kind", "review", "scope", "deliverables", "overlap", "requires", "after", "agent", "when"].map((key) => `YAML のキー: ${key}`),
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

test("CB-D67 ほかの種類との関係・補足は、最後の値を消しても畳まれない（打っている欄が消えない）", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    // 雛形の research は when だけを持つので開いている
    assert.ok(dom.one(`${rowSelector("p1")} details.more`).hasAttribute("open"));
    dom.type(dom.one(`${rowSelector("p1")} input.f-when`), "");
    await dom.settle();
    assert.ok(dom.one(`${rowSelector("p1")} details.more`).hasAttribute("open"), "値を消した拍子に、打っている欄ごと畳まない");
    assert.match(dom.one(`${rowSelector("p1")} details.more > summary`).textContent, /ほかの種類との関係・補足（未設定）/);
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

test("CB-D84 未保存の変更の有無は変わったときだけ拡張ホストへ伝え、切り替え中は「読み込み中」を出して中身が届けば描き直す", async () => {
  const dom = await openPhases();
  try {
    assert.deepEqual(dom.posted, [{ type: "ready" }], "開いた時点の「変更なし」は送らない");
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる");
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる2");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "dirty"), [{ type: "dirty", dirty: true }], "打ち続けても 1 度だけ");
    await dom.send({ type: "data", data: { kind: "loading", text: "web のフェーズを読み込み中..." } });
    assert.equal(dom.one("#ccnavi-loading").textContent, "web のフェーズを読み込み中...");
    assert.equal(dom.all(rowSelector("p1")).length, 0);
    assert.deepEqual(
      dom.posted.filter((message) => message.type === "dirty").map((message) => message.dirty),
      [true, false],
      "編集を捨てたことも伝える",
    );
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    assert.equal(dom.all("#ccnavi-loading").length, 0);
    // 行の鍵は画面の中で数え続けるので、描き直した行は別の鍵になる
    assert.ok(dom.all(".phase").length > 0);
  } finally {
    await dom.close();
  }
});

test("CB-D85 関係の欄はほかの種類の id を複数選択で選べ、自分の id は候補に出ない。並びはファイルの順に揃う", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p4")} .row-head`));
    await dom.settle();
    const values = (field: string, only?: "checked"): string[] =>
      dom
        .all<HTMLOptionElement>(`${rowSelector("p4")} ${field} select.id-select option`)
        .filter((option) => only === undefined || option.selected)
        .map((option) => option.value);
    // 雛形の implement。自分（implement）は候補に出ない
    assert.deepEqual(values(".f-requires"), ["research", "design", "acceptance", "implement-feedback"]);
    assert.deepEqual(values(".f-requires", "checked"), ["acceptance"]);
    // after の候補は work の種類だけ。feedback の種類は待つ先にできない
    assert.deepEqual(values(".f-after"), ["research", "design", "acceptance"]);
    assert.deepEqual(values(".f-after", "checked"), ["acceptance"]);
    // 後から付けても、並びはファイルの順に揃う（YAML に余計な差分を出さない）
    pick(dom, `${rowSelector("p4")} .f-after`, "design");
    await dom.settle();
    assert.deepEqual(values(".f-after", "checked"), ["design", "acceptance"]);
    pick(dom, `${rowSelector("p4")} .f-requires`, "research");
    await dom.settle();
    // after に挙げた id は overlap で選べない（両方に挙げると検証が止める）
    assert.ok(dom.one<HTMLOptionElement>(`${rowSelector("p4")} .f-overlap option[value="design"]`).disabled);
    assert.ok(!dom.one<HTMLOptionElement>(`${rowSelector("p4")} .f-overlap option[value="implement-feedback"]`).disabled);
    // 共通層ではほかの層を指せないので、id を打つ欄は出さない
    assert.equal(dom.all(`${rowSelector("p4")} input.id-extra`).length, 0);
    dom.click(dom.one("#save"));
    await dom.settle();
    const saved = savedForm(dom).phases[3];
    assert.deepEqual(saved.after, ["design", "acceptance"]);
    assert.deepEqual(saved.requires, ["research", "acceptance"]);
  } finally {
    await dom.close();
  }
});

test("CB-D94 関係の欄は矢印で印だけを動かし、Space で付け外しする。change で届いた選択はそのまま受ける", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p4")} .row-head`));
    await dom.settle();
    const select = `${rowSelector("p4")} .f-requires select.id-select`;
    const selected = (): string[] =>
      dom
        .all<HTMLOptionElement>(`${select} option`)
        .filter((option) => option.selected)
        .map((option) => option.value);
    const active = (): string | null => dom.one(`${select} option.active`).getAttribute("value");
    assert.deepEqual(selected(), ["acceptance"]);
    assert.equal(active(), "research");
    // 矢印は印を動かすだけで、選択を 1 件に縮めない
    dom.key("ArrowDown", dom.one(select));
    await dom.settle();
    dom.key("ArrowDown", dom.one(select));
    await dom.settle();
    assert.equal(active(), "acceptance");
    assert.deepEqual(selected(), ["acceptance"]);
    dom.key("ArrowUp", dom.one(select));
    await dom.settle();
    dom.key(" ", dom.one(select));
    await dom.settle();
    assert.deepEqual(selected(), ["design", "acceptance"]);
    dom.key("End", dom.one(select));
    await dom.settle();
    assert.equal(active(), "implement-feedback");
    assert.equal(dom.one(select).getAttribute("aria-activedescendant"), dom.one(`${select} option.active`).id);
    // 止めきれずに change が届いたときは、届いた選択を並びの順で受ける
    for (const option of dom.all<HTMLOptionElement>(`${select} option`)) {
      option.selected = option.value === "implement-feedback" || option.value === "research";
    }
    dom.change(dom.one(select));
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedForm(dom).phases[3].requires, ["research", "implement-feedback"]);
  } finally {
    await dom.close();
  }
});

test("CB-D87 層の画面では、候補に無い id を打って足せる。自分の id と空は足さず、無い id と自分自身は印を付けて出す", async () => {
  const base = readPhases(TEMPLATE_PHASES_TEXT).model;
  const phases = base.form.phases.map((p) => (p.id === "acceptance" ? { ...p, overlap: [" design ", "", "acceptance"] } : p));
  const dom = await openPhases({ layer: true, model: { ...base, form: { ...base.form, phases } } });
  try {
    dom.click(dom.one(`${rowSelector("p3")} .row-head`));
    await dom.settle();
    // 前後の空白は落として読み、空は出さない。自分自身は外せるように印を付けて出す
    const checked = dom.all<HTMLOptionElement>(`${rowSelector("p3")} .f-overlap option`).filter((option) => option.selected);
    assert.deepEqual(checked.map((option) => option.value), ["design", "acceptance"]);
    assert.ok(dom.one(`${rowSelector("p3")} .f-overlap .id-option.foreign`).textContent?.includes("acceptance"));
    dom.type(dom.one(`${rowSelector("p3")} .f-requires input.id-extra`), "外の種類, acceptance");
    await dom.settle();
    dom.key("Enter", dom.one(`${rowSelector("p3")} .f-requires input.id-extra`));
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("p3")} .f-requires input.id-extra`).value, "");
    assert.ok(dom.one(`${rowSelector("p3")} .f-requires .id-option.foreign`).textContent?.includes("外の種類"));
    pick(dom, `${rowSelector("p3")} .f-overlap`, "acceptance");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    const saved = savedForm(dom).phases[2];
    assert.deepEqual(saved.overlap, ["design"]);
    assert.deepEqual(saved.requires, ["外の種類"]);
  } finally {
    await dom.close();
  }
});

test("CB-D88 feedback の種類は先に済ませる種類を持てないと言い、欄を出さない", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p5")} .row-head`));
    await dom.settle();
    assert.equal(dom.all(`${rowSelector("p5")} .f-after .id-option`).length, 0);
    assert.match(dom.one(`${rowSelector("p5")} .f-after`).textContent ?? "", /feedback の種類は持てない/);
  } finally {
    await dom.close();
  }
});

test("CB-D86 図を見ているときに種類を足すと、一覧へ移って足した行が見える", async () => {
  const dom = await openPhases({}, { view: "graph" });
  try {
    assert.ok(dom.one("#phases").classList.contains("hidden"));
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    assert.ok(!dom.one("#phases").classList.contains("hidden"), "一覧へ移っていない");
    const rows = dom.all(".phase");
    const added = rows[rows.length - 1];
    assert.ok(added.classList.contains("open"));
    assert.ok(!added.classList.contains("hidden-by-find"));
    assert.equal(dom.document.activeElement, added.querySelector("input.f-id"));
  } finally {
    await dom.close();
  }
});

test("CB-D90 拡張ホストが頼んだら吹き出しの案内を出し、最後まで進めると閉じて tourDone を返す。案内の前の様子（図・絞り込み・開いた行）に戻り、途中の切り替えは控えに書かない", async () => {
  const dom = await openPhases({}, { view: "graph" });
  try {
    assert.ok(dom.one("#phases").classList.contains("hidden"), "図で始まっていない");
    assert.equal(dom.all(".tour").length, 0, "頼まれるまでは出さない");
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "フェーズの種類");
    assert.ok(!dom.one("#phases").classList.contains("hidden"), "1 段目で一覧に切り替わっていない");
    const titles = [dom.one("#tour-title").textContent];
    for (let i = 0; i < 6; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
      titles.push(dom.one("#tour-title").textContent);
      if (i === 0) {
        // 関係の段で、関係を持つ行（design）と、その関係の欄が開いている
        assert.ok(dom.one(`${rowSelector("p2")} details.more`).hasAttribute("open"));
      }
    }
    assert.deepEqual(titles, ["フェーズの種類", "ほかの種類との関係", "全体計画の待ち方", "図", "保存", "ヘルプ", "案内"]);
    // 途中の一覧と図の切り替えは控えに書かない（途中でタブを閉じても、次は元の図で開く）
    assert.equal((dom.state() as { view?: string }).view, "graph");
    // 最後の段は「完了」だけ（同じ働きのボタンを 2 つ並べない）
    assert.equal(dom.one('[data-action="tour-next"]').textContent, "完了");
    assert.equal(dom.all('[data-action="tour-skip"]').length, 0);
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
    assert.ok(dom.one("#phases").classList.contains("hidden"), "案内の前の図に戻っていない");
    // 案内が開いた見本の行も閉じる
    assert.equal(dom.all(".phase.open").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D93 案内の間は Tab が吹き出しのボタンの中だけを巡り、焦点は「次へ」から始まる。閉じたら絞り込みも戻る", async () => {
  const dom = await openPhases();
  try {
    dom.type(dom.one("#find"), "設計");
    await dom.settle();
    await dom.send({ type: "tour" });
    await dom.settle();
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
    // 2 段目：スキップ・戻る・次へ
    assert.equal(dom.document.activeElement, dom.one('[data-action="tour-next"]'));
    dom.key("Tab");
    await dom.settle();
    assert.equal(dom.document.activeElement, dom.one('[data-action="tour-skip"]'), "Tab が吹き出しの外へ出た");
    dom.document.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    await dom.settle();
    assert.equal(dom.document.activeElement, dom.one('[data-action="tour-next"]'));
    // 見本の行を出すために外した絞り込みは、閉じたら戻る
    assert.equal(dom.one<HTMLInputElement>("#find").value, "");
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.one<HTMLInputElement>("#find").value, "設計");
  } finally {
    await dom.close();
  }
});

test("CB-D91 案内は Esc かスキップでやめられ、やめても tourDone を返す。読み込み中に頼まれたら中身が出てから始める", async () => {
  const dom = await openPage({ kind: "loading", text: "フェーズの種類を読み込み中..." });
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0, "指す先が無い読み込み中には出さない");
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.all(".tour").length, 1);
    dom.key("Escape");
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D92 細かい説明はヘルプを押したときだけ出す。ヘッダ右上の ? で案内をもう一度見られ、開いていたヘルプは閉じる", async () => {
  const dom = await openPhases();
  try {
    assert.equal(dom.all("#help").length, 0, "ヘルプを押すまで説明は出さない");
    // 案内の入口は ? 1 文字で、ヘッダ（ツールバー）の最後の子。位置は Tour.css が 5 画面とも右上に揃える
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    dom.click(dom.one('[data-action="help"]'));
    await dom.settle();
    assert.match(dom.one("#help").textContent ?? "", /判定が使う待ち方は、層を合わせたうえで親チケットの承認のときに決まる/);
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    assert.equal(dom.all("#help").length, 0, "案内を始めたらヘルプは閉じる");
    assert.equal(dom.one("#tour-title").textContent, "フェーズの種類");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    // ? から始めた案内も、やめたら tourDone を返す
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
  } finally {
    await dom.close();
  }
});
