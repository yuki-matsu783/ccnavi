/** フェーズ管理画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../../src/core/phases-doc.js";
import type { PhasesForm } from "../../src/core/phases-view.js";
import { openPage, openPhases, page, rowSelector, SAMPLE_PHASES_TEXT } from "../helpers/phases.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直前に送った保存の中身 */
function savedForm(dom: DomPage): PhasesForm {
  const saves = dom.posted.filter((message) => message.type === "save");
  assert.ok(saves.length > 0, "保存を送っていない");
  return saves[saves.length - 1].form as PhasesForm;
}

test("CB-D20 既定は畳み、行を押すと開いて state に id が入る。補足は値がある定義だけ開く", async () => {
  const dom = await openPhases();
  try {
    assert.equal(dom.all(".phase").length, 5);
    assert.equal(dom.all(".phase.open").length, 0);
    dom.click(dom.one(`${rowSelector("p2")} .row-head`));
    await dom.settle();
    assert.ok(dom.one(rowSelector("p2")).classList.contains("open"));
    assert.deepEqual((dom.state() as { open: string[] }).open, ["design"]);
    // 見本の design は when を持つので開く。implement は agent も when も無いので閉じる
    assert.ok(dom.one(`${rowSelector("p2")} details.more`).hasAttribute("open"));
    assert.ok(!dom.one(`${rowSelector("p4")} details.more`).hasAttribute("open"));
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
    assert.match(dom.one("#status").textContent ?? "", /id が重なっています（research）/);
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

test("CB-D22 絞り込みは title と scope にも当たり、開いている行は隠さず、一致した行だけを数える。足した定義は開いて焦点が id に来る", async () => {
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

test("CB-T125 件の欄名は日本語で、YAML のキー名は欄名の title に載せる", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    const caps = dom.all(`${rowSelector("p1")} .row-body .field > .cap`);
    assert.deepEqual(
      caps.map((cap) => cap.textContent),
      ["id", "タイトル", "区分", "レビュー", "範囲", "成果物", "案内するエージェント", "使う場面"],
    );
    assert.deepEqual(
      caps.map((cap) => cap.getAttribute("title")),
      ["id", "title", "kind", "review", "scope", "deliverables", "agent", "when"].map((key) => `YAML のキー: ${key}`),
    );
  } finally {
    await dom.close();
  }
});

test("CB-D59 リストの欄は , で区切って打て、打っている途中の区切りは消えない", async () => {
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

test("CB-D60 定義の並べ替えと削除が保存に渡る形に出る", async () => {
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
    await dom.send({ type: "data", data: { kind: "page", page: page({ phasesPath: ".ccnavi/config/phases.yml" }) } });
    assert.ok(dom.one("#changed").classList.contains("hidden"));
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.one(".path").textContent, ".ccnavi/config/phases.yml");
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("p6")} input.f-title`).value, "調査");
  } finally {
    await dom.close();
  }
});

test("CB-D62 読み直せなかったら理由を出し、定義は出さない", async () => {
  const dom = await openPage({ kind: "error", error: "定義のファイルを読めない: EACCES" });
  try {
    assert.match(dom.one(".empty").textContent, /フェーズ管理画面を読み込めませんでした/);
    assert.equal(dom.one("pre.load-error").textContent, "定義のファイルを読めない: EACCES");
    assert.equal(dom.all("#phases").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D63 定義が無いファイルは、保存する前に足すと言う。苦情と錠はそのまま出す", async () => {
  const dom = await openPhases({
    model: readPhases("version: 1\nphases: nope\n").model,
    lock: { locked: true, reason: "作業中のチケットがある（i0001-02-02）", doing: ["i0001-02-02"] },
  });
  try {
    assert.match(dom.one("#phases .empty").textContent, /定義がありません。定義が 1 つも無いファイルは実行ファイルが読めない/);
    assert.match(dom.one(".problems").textContent, /phases がマップ（キーと値の組の集まり）ではありません/);
    assert.equal(dom.one("#lock").textContent, "作業中のチケットがある（i0001-02-02）");
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
  } finally {
    await dom.close();
  }
});

test("CB-D67 補足は、最後の値を消しても畳まれない（打っている欄が消えない）", async () => {
  const dom = await openPhases();
  try {
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    // 見本の research は when だけを持つので開いている
    assert.ok(dom.one(`${rowSelector("p1")} details.more`).hasAttribute("open"));
    dom.type(dom.one(`${rowSelector("p1")} input.f-when`), "");
    await dom.settle();
    assert.ok(dom.one(`${rowSelector("p1")} details.more`).hasAttribute("open"), "値を消した拍子に、打っている欄ごと畳まない");
    assert.match(dom.one(`${rowSelector("p1")} details.more > summary`).textContent, /^補足（未設定）/);
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
    // レイヤーの置き場を実行ファイルに聞く間、欄は止まっている
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
    await dom.send({ type: "data", data: { kind: "loading", text: "web のフェーズを読み込み中…" } });
    assert.equal(dom.one("#ccnavi-loading").textContent, "web のフェーズを読み込み中…");
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

test("CB-D85 関係の欄（overlap・requires・after）と、その id を選ぶ複数選択は、work の定義にも feedback の定義にも出ない。保存に順序の欄は載らない", async () => {
  const dom = await openPhases();
  try {
    for (const key of ["p1", "p2", "p3", "p4", "p5"]) {
      dom.click(dom.one(`${rowSelector(key)} .row-head`));
      await dom.settle();
      assert.equal(dom.all(`${rowSelector(key)} .f-overlap, ${rowSelector(key)} .f-requires, ${rowSelector(key)} .f-after, ${rowSelector(key)} select.id-select`).length, 0, key);
      assert.doesNotMatch(dom.one(`${rowSelector(key)} details.more > summary`).textContent ?? "", /並行できる|一緒に必要|先に済ませる/, key);
    }
    dom.type(dom.one(`${rowSelector("p4")} input.f-when`), "受入テスト作成を先に置く");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    const saved = savedForm(dom);
    assert.deepEqual(Object.keys(saved), ["phases"]);
    for (const phase of saved.phases) {
      assert.ok(!("after" in phase) && !("overlap" in phase) && !("requires" in phase), JSON.stringify(phase));
    }
    assert.equal(saved.phases[3].when, "受入テスト作成を先に置く");
  } finally {
    await dom.close();
  }
});

test("CB-D87 古い順序の欄が残ったファイルは、読まない欄として定義ごとに名指しし、順序は計画の after で決めると言う。保存しても欄は残ると言う", async () => {
  const dom = await openPhases({ model: readPhases(SAMPLE_PHASES_TEXT.replace("version: 1\n", "version: 1\norder: dag\n").replace('    scope: ["src/*", "tests/*"]\n', '    scope: ["src/*", "tests/*"]\n    requires: [acceptance]\n    after: [acceptance]\n')).model });
  try {
    const note = dom.one("#unread").textContent ?? "";
    assert.match(note, /order（ファイルの頭）/);
    assert.match(note, /implement の requires・after/);
    assert.match(note, /順序は親チケットの計画の項の after で決めます/);
    assert.match(note, /保存しても欄はそのまま残ります/);
    // 読まない欄は一覧の欄としては出さない
    dom.click(dom.one(`${rowSelector("p4")} .row-head`));
    await dom.settle();
    assert.equal(dom.all(`${rowSelector("p4")} .f-requires, ${rowSelector("p4")} .f-after`).length, 0);
  } finally {
    await dom.close();
  }
  // 古い欄が無ければ出さない
  const plain = await openPhases();
  try {
    assert.equal(plain.all("#unread").length, 0);
  } finally {
    await plain.close();
  }
});

test("CB-D86 図・一覧と図の切り替え・全体計画の待ち方の選択は出ない。state に前の図の表示や点の位置が残っていても一覧で開き、定義を足せば足した行が見える", async () => {
  const dom = await openPhases({}, { view: "graph", spots: { design: { x: 40, y: 80 } } });
  try {
    assert.equal(dom.all("#phase-graph, .react-flow, .graph, .graph-legend, .graph-note").length, 0, "図が残っている");
    assert.equal(dom.all('[data-action="show-graph"], [data-action="show-list"], .tabs').length, 0, "一覧と図の切り替えが残っている");
    assert.equal(dom.all('#f-order, [data-yaml-key="order"], label.order').length, 0, "待ち方の選択が残っている");
    assert.ok(!dom.one("#phases").classList.contains("hidden"), "一覧が隠れている");
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    const rows = dom.all(".phase");
    const added = rows[rows.length - 1];
    assert.ok(added.classList.contains("open"));
    assert.equal(dom.document.activeElement, added.querySelector("input.f-id"));
  } finally {
    await dom.close();
  }
});

test("CB-D90 拡張ホストが頼んだら吹き出しの案内を出し、最後まで進めると閉じて tourDone を返す。案内の前の様子（開いた行）に戻る。図・関係の欄・待ち方の段は無い", async () => {
  const dom = await openPhases();
  try {
    assert.equal(dom.all(".tour").length, 0, "頼まれるまでは出さない");
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "フェーズ定義");
    // 1 段目で、順序は親チケットの計画で決めることを言う
    assert.match(dom.one(".tour-bubble").textContent ?? "", /順序（どのフェーズがどれを待つか）は、親チケットの計画の項の after で決めます/);
    const titles = [dom.one("#tour-title").textContent];
    for (let i = 0; i < 4; i += 1) {
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
      titles.push(dom.one("#tour-title").textContent);
      if (i === 0) {
        // 補足の段で、値を持つ行（research）と、その補足が開いている
        assert.ok(dom.one(`${rowSelector("p1")} details.more`).hasAttribute("open"));
        assert.ok(dom.one(rowSelector("p1")).classList.contains("open"));
      }
    }
    assert.deepEqual(titles, ["フェーズ定義", "補足", "保存", "ヘルプ", "案内"]);
    // 最後の段は「完了」。やめる × はどの段でも右上に出す
    assert.equal(dom.one('[data-action="tour-next"]').textContent, "完了");
    assert.equal(dom.one('.tour-bubble > [data-action="tour-skip"]').textContent?.trim(), "×");
    dom.click(dom.one('[data-action="tour-next"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
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
    // 2 段目：×・戻る・次へ
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

test("CB-D91 案内は Esc か × でやめられ、やめても tourDone を返す。読み込み中に頼まれたら中身が出てから始める", async () => {
  const dom = await openPage({ kind: "loading", text: "フェーズ定義を読み込み中…" });
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

test("CB-D147 案内は → で次の段へ、← で前の段へ動く。端の段ではどちらも何もしない", async () => {
  const dom = await openPhases();
  try {
    await dom.send({ type: "tour" });
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "フェーズ定義");
    dom.key("ArrowLeft");
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "フェーズ定義");
    dom.key("ArrowRight");
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "補足");
    dom.key("ArrowLeft");
    await dom.settle();
    assert.equal(dom.one("#tour-title").textContent, "フェーズ定義");
    for (let i = 0; i < 8; i += 1) {
      dom.key("ArrowRight");
      await dom.settle();
    }
    // 最後の段で → を押しても閉じない
    assert.equal(dom.one("#tour-title").textContent, "案内");
    assert.equal(dom.all(".tour").length, 1);
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 0);
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
    assert.match(dom.one("#help").textContent ?? "", /順序（どのフェーズがどれを待つか）はこのファイルには書きません。親チケットの計画の項に after で先行を書きます/);
    dom.click(dom.one('[data-action="tour"]'));
    await dom.settle();
    assert.equal(dom.all("#help").length, 0, "案内を始めたらヘルプは閉じる");
    assert.equal(dom.one("#tour-title").textContent, "フェーズ定義");
    dom.click(dom.one('[data-action="tour-skip"]'));
    await dom.settle();
    assert.equal(dom.all(".tour").length, 0);
    // ? から始めた案内も、やめたら tourDone を返す
    assert.equal(dom.posted.filter((message) => message.type === "tourDone").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D151 設定の切り替えの欄は、選んだ対象を種類と名前で送る。対象が 1 つだけでも出す", async () => {
  const targets = [
    { kind: "self", name: "", label: "ワークスペース" },
    { kind: "project", name: "app:x", label: "プロジェクト app:x" },
    { kind: "project", name: "lib", label: "プロジェクト lib" },
  ];
  const dom = await openPhases({ target: { kind: "self", name: "" }, targets });
  try {
    const select = dom.one<HTMLSelectElement>("select#target");
    assert.deepEqual(
      [...select.options].map((o) => o.textContent),
      ["ワークスペース", "プロジェクト app:x", "プロジェクト lib"],
    );
    assert.equal(select.value, "self:");
    dom.change(select, "project:app:x");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "switchTarget"), [{ type: "switchTarget", kind: "project", name: "app:x" }]);
  } finally {
    await dom.close();
  }

  const single = await openPhases({ target: { kind: "self", name: "" }, targets: targets.slice(0, 1) });
  try {
    assert.equal(single.all("select#target option").length, 1);
  } finally {
    await single.close();
  }
});

test("CB-D153 読み込みに失敗した画面にも設定の切り替えの欄を出し、ワークスペースの設定へ戻れる", async () => {
  const dom = await openPage({
    kind: "error",
    error: "ファイルを読めません",
    target: { kind: "project", name: "app" },
    targets: [
      { kind: "self", name: "", label: "ワークスペース" },
      { kind: "project", name: "app", label: "プロジェクト app" },
    ],
  });
  try {
    const select = dom.one<HTMLSelectElement>("select#target");
    assert.equal(select.value, "project:app");
    dom.change(select, "self:");
    await dom.settle();
    assert.deepEqual(dom.posted.filter((m) => m.type === "switchTarget"), [{ type: "switchTarget", kind: "self", name: "" }]);
  } finally {
    await dom.close();
  }
});

