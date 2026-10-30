/**
 * ワークスペースの設定とプロジェクトの設定の定義。ファイルが無いときの見せ方と、無いファイルへの書き戻し、共通の設定の phases.yml を読めないときの error の帯、
 * 共通の設定から足される定義（読み取り専用の欄）と、それとの重なり。
 * 画面の側は React なので happy-dom で動かして見る。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readPhases } from "../../src/core/phases-doc.js";
import type { PhasesPage } from "../../src/core/phases-view.js";
import { openPhases, rowSelector } from "../helpers/phases.js";
import { commonPhases } from "../../src/core/sums.js";
import type { SumJson } from "../../src/core/model.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const MISSING: Partial<PhasesPage> = { exists: false, model: { version: null, form: { phases: [] }, problems: [] } };

test("CB-T114 定義のファイルがないのは正常で、不備の帯も作るボタンも出さず、欄を触れるようにして最初の保存で作らせる", async () => {
  const dom = await openPhases({ ...MISSING, notices: ["読み込めない <理由>"] });
  try {
    assert.equal(dom.all(".banner.missing").length, 0, "ファイルがないことを不備として出さない");
    assert.equal(dom.all('button[data-action="create"]').length, 0, "雛形は置かない");
    assert.equal(dom.all('button[data-action="open-self"]').length, 0, "共通向けの「自身のレイヤーを開く」はありません");
    // 文面はそのまま出る（React が文字として入れるので、実体参照に変わらない）
    assert.equal(dom.all(".banner.warn:not(#changed)").length, 1);
    assert.equal(dom.one(".banner.warn:not(#changed)").textContent, "読み込めない <理由>");
    // 無くても定義を足して保存できる
    assert.ok(!dom.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.match(dom.one("#phases .empty").textContent, /定義を追加して保存すると、ファイルが作られます/);
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    assert.ok(!dom.one<HTMLInputElement>(".phase input.f-id").disabled);
  } finally {
    await dom.close();
  }
});

test("CB-T239 共通の設定の phases.yml を読み込めないときは error の帯を出し、画面は開いたまま触れる。画面から削除する・直す手段は出さない", async () => {
  const dom = await openPhases({ errors: ["共通の設定の phases.yml を読み込めません（.ccnavi/config/common/phases.yml）。共通の定義は空として扱われています: 壊れている"] });
  try {
    assert.match(dom.one(".banner.error").textContent, /共通の設定の phases.yml を読み込めません/);
    assert.equal(dom.all(".banner.error").length, 1);
    // 画面は開いている。欄は触れる（保存を止めるのは作業中のチケットだけ）
    assert.ok(!dom.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    assert.equal(dom.all('button[data-action="create"], button[data-action="open-self"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-T116 ないファイル（空の本文）に定義を追加して書き戻すと、version と定義を持つ読めるファイルになる", () => {
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
        agent: "",
        when: "",
      },
    ],
  });
  assert.match(text, /^version: 1$/m);
  assert.match(text, /^phases:\n {2}notes:\n/m);
  assert.ok(!text.includes("{}"));
  // 作るときは先頭に説明のコメントを足す。足しても読み直して苦情が出ない
  const again = readPhases(`# 説明\n${text}`);
  assert.deepEqual(again.model.problems, []);
  assert.deepEqual(again.model.form.phases.map((p) => [p.id, p.title, p.inherit]), [["notes", "メモ", true]]);
});

const COMMON_PLAN = { id: "plan", title: "調査", kind: "work", review: "mr", scope: ["wip/plan/*"] };
const COMMON_IMPL = { id: "implement", title: "実装とテスト", kind: "work", review: "mr", scope: ["src/*", "tests/*"] };

test("CB-T527 共通の設定の定義は、統合結果（sums[].phases.types）のうち出どころが common のものだけを取る。統合結果がなければ空", () => {
  const sum = {
    name: "self",
    layers: ["common", "self"],
    phases: {
      path: ".ccnavi/config/project/phases.yml",
      unreadable: "",
      types: [
        { ...COMMON_IMPL, source: "common" },
        { id: "docs", source: "self", kind: "work", title: "文書", review: "mr", scope: ["docs/*"] },
      ],
    },
  } as unknown as SumJson;
  assert.deepEqual(commonPhases(sum), [COMMON_IMPL]);
  assert.deepEqual(commonPhases(undefined), []);
});

test("CB-T528 共通の設定の定義は一覧の下に読み取り専用で並び、出どころが共通だと分かる。なければ欄を出さない", async () => {
  const dom = await openPhases({ common: [COMMON_IMPL] });
  try {
    const items = dom.all("#common-phases li.common-phase");
    assert.equal(items.length, 1);
    assert.equal(items[0].getAttribute("data-source"), "common");
    assert.match(items[0].textContent ?? "", /実装とテスト/);
    assert.equal(dom.one("#common-count").textContent, "1");
    // 編集の欄は持たない
    assert.equal(dom.all("#common-phases input, #common-phases select, #common-phases button").length, 0);
    // 見本にも implement があるので、同じ id の一文が出る（中身の比べは保存の --lint に任せ、保存は止めない）
    assert.match(dom.one("#common-same-id").textContent, /共通の設定にも同じ id があります（implement）/);
  } finally {
    await dom.close();
  }
  const none = await openPhases();
  try {
    assert.equal(none.all("#common-phases").length, 0);
    assert.equal(none.all("#common-same-id").length, 0);
  } finally {
    await none.close();
  }
});

test("CB-T529 共通の設定の別の id と表示名が重なる定義は、id の重なりと同じく保存を止めて下部に error の文を出す。直せば保存できる", async () => {
  // 共通の plan と、見本の research（p1）の表示名「調査」が重なる
  const dom = await openPhases({ common: [COMMON_PLAN] });
  try {
    assert.match(dom.one("#status").textContent, /共通の設定のフェーズ定義とタイトル（title）が重なっています（research）/);
    assert.ok(dom.one(".foot").className.includes("error"));
    dom.click(dom.one(`${rowSelector("p1")} .row-head`));
    await dom.settle();
    dom.type(dom.one(`${rowSelector("p2")} input.f-title`), "設計する");
    await dom.settle();
    // 別の行を直して未保存にしても、重なりが残る間は保存できない
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled);
    dom.type(dom.one(`${rowSelector("p1")} input.f-title`), "調べる");
    await dom.settle();
    assert.ok(!dom.one<HTMLButtonElement>("#save").disabled);
    assert.doesNotMatch(dom.one("#status").textContent ?? "", /重なっています/);
  } finally {
    await dom.close();
  }
});

test("CB-T530 id が空の行（打っている途中）は、共通の設定の定義と表示名が重なっても重なりの印を付けない。id を打てば止める", async () => {
  const dom = await openPhases({ common: [{ id: "plan", title: "計画", kind: "work", review: "mr", scope: ["wip/plan/*"] }] });
  try {
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    // 足した行に、共通の plan と同じ表示名を打つ（id はまだ空）
    const titles = dom.all(".phase input.f-title");
    dom.type(titles[titles.length - 1], "計画");
    await dom.settle();
    assert.equal(dom.all(".phase input.f-id.duplicate").length, 0);
    assert.doesNotMatch(dom.one("#status").textContent ?? "", /共通の設定のフェーズ定義とタイトル（title）が重なっています/);
    // id を打てば重なりとして止める
    const ids = dom.all(".phase input.f-id");
    dom.type(ids[ids.length - 1], "planning");
    await dom.settle();
    assert.equal(dom.all(".phase input.f-id.duplicate").length, 1);
    assert.match(dom.one("#status").textContent ?? "", /共通の設定のフェーズ定義とタイトル（title）が重なっています（planning）/);
  } finally {
    await dom.close();
  }
});
