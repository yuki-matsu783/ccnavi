/**
 * テンプレート管理画面の編集欄（React）を happy-dom で動かす。編集の開始と答え、直したときの読ませる依頼（editCheck）、
 * 計画の表の操作（ステップを足す・消す・動かす）、保存（editSave）と保存の答え、未保存のまま閉じる確認、始められなかったときの理由、
 * 新しいテンプレートを作る入口を見る。読めるか・計画の図の中身は実行ファイルの答え（見本 `tests/fixtures/templates-check-json.json`）のまま。
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseTemplateCheck } from "../../src/core/templatesmodel.js";
import type { EditOpened, TemplateCheck } from "../../src/core/templates-view.js";
import { loadPage, type DomPage } from "../helpers/dom.js";
import { page, templatesCheckFixtureText, templatesHtml } from "../helpers/templates.js";
import type { HTMLButtonElement, HTMLInputElement, HTMLSelectElement, HTMLTextAreaElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直してから読ませるまでの待ちを越える（`Edit.tsx` の CHECK_DELAY_MS は 300） */
const WAIT_CHECK_MS = 450;

function fixtureCheck(): TemplateCheck {
  const parsed = parseTemplateCheck(templatesCheckFixtureText());
  assert.ok(parsed.ok, parsed.ok ? "" : parsed.error);
  return parsed.value;
}

function openedOf(check: TemplateCheck, extra: Partial<EditOpened> = {}): EditOpened {
  return { name: "feature", layer: "self", created: false, sha: "sha-opened", data: check.data, check, lock: { locked: false, reason: "", doing: [] }, ...extra };
}

/** 見本の画面（図の大きさを測れる細工つき）。self の層の feature の行を開いた状態 */
async function openList(): Promise<DomPage> {
  const dom = await loadPage(templatesHtml({ kind: "page", page: page() }), undefined, { measure: true });
  await dom.settle();
  return dom;
}

/** 編集を始めて、拡張ホストの答え（editOpened）を届ける。送った依頼を返す */
async function startEdit(dom: DomPage, opened: EditOpened = openedOf(fixtureCheck())): Promise<{ readonly session: number }> {
  const row = 'li.template[data-name="feature"][data-layer="self"]';
  if (dom.all(`${row}.open`).length === 0) {
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
  }
  const before = dom.posted.filter((m) => m.type === "editOpen").length;
  dom.click(dom.one(`${row} button[data-action="edit-template"]`));
  await dom.settle();
  const asked = dom.posted.filter((m) => m.type === "editOpen");
  assert.equal(asked.length, before + 1);
  const last = asked[asked.length - 1];
  assert.equal(last.name, "feature");
  assert.equal(last.layer, "self");
  const session = last.session as number;
  await dom.send({ type: "editOpened", session, opened, error: "" });
  await dom.settle();
  return { session };
}

/** 直したあとの読ませる依頼を待つ。最後に届いた依頼を返す */
async function checkAsked(dom: DomPage): Promise<Record<string, unknown>> {
  await new Promise((resolve) => setTimeout(resolve, WAIT_CHECK_MS));
  await dom.settle();
  const asked = dom.posted.filter((m) => m.type === "editCheck");
  assert.ok(asked.length > 0, "editCheck を送っていない");
  return asked[asked.length - 1];
}

const text = (element: { textContent: string | null } | null | undefined): string => (element?.textContent ?? "").trim();

test("CB-D382 編集を押すと editOpen を送り、答えが届くまで読み込み中を出す。答えが届くと一覧の代わりに編集欄を出し、説明・使う場面・計画の表・フローの欄が見本どおりに入る", async () => {
  const dom = await openList();
  try {
    dom.click(dom.one('li.template[data-name="feature"][data-layer="self"] button[data-action="edit-template"]'));
    await dom.settle();
    assert.match(text(dom.one("#edit-loading")), /feature を読み込み中/);
    const session = dom.posted.find((m) => m.type === "editOpen")?.session as number;
    await dom.send({ type: "editOpened", session, opened: openedOf(fixtureCheck()), error: "" });
    await dom.settle();
    assert.equal(dom.all("li.template").length, 0, "編集の間は一覧を出さない");
    assert.equal(dom.one<HTMLTextAreaElement>(".f-description").value, "機能を設計から実装まで進める");
    assert.equal(dom.one<HTMLInputElement>(".f-when").value, "新しい機能を足すとき");
    assert.deepEqual(
      dom.all('section.tpl-edit-plan[data-part="plan"] tbody tr').map((tr) => text(tr.querySelector("td.mono"))),
      ["1", "2"],
    );
    assert.deepEqual(
      dom.all('section.tpl-edit-plan[data-part="feedback"] tbody tr').map((tr) => text(tr.querySelector("td.mono"))),
      ["3"],
      "フィードバック計画の番号は全体計画の続き",
    );
    assert.equal(dom.all('section.tpl-edit-plan[data-part="plan"] .react-flow__node').length, 4, "start と end と 2 ステップ");
    assert.equal(dom.all('section[data-role="flows"] li.row').length, 2);
    assert.match(text(dom.one('section[data-role="flows"] li[data-index="2"]')), /フロー: 実装の手順/);
    assert.equal(dom.all('[data-role="dirty"]').length, 0);
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, true, "直す前は保存を押せない");
    // 編集の間は対象の切り替えと更新を押せない
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="reload"]').disabled, true);
  } finally {
    await dom.close();
  }
});

test("CB-D383 説明を直すと「未保存」が出て、少し待つと直した中身を editCheck で読ませる。答えが届くまでは古い図に線を引けない", async () => {
  const dom = await openList();
  try {
    const { session } = await startEdit(dom);
    dom.type(dom.one(".f-description"), "新しい説明");
    await dom.settle();
    assert.equal(dom.all('[data-role="dirty"]').length, 1);
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, false);
    const asked = await checkAsked(dom);
    assert.equal(asked.session, session);
    assert.equal((asked.data as { description: string }).description, "新しい説明");
    assert.equal(dom.all('section.plan-graph[data-part="plan"][data-editable="0"]').length, 1, "読ませている間は線を引けない");
    // 答えが届くと図は引ける形に戻る
    const check = fixtureCheck();
    await dom.send({ type: "editChecked", session, seq: asked.seq, check: { ...check, problems: [{ severity: "warn", detail: "検査の指摘です" }] }, error: "" });
    await dom.settle();
    assert.match(text(dom.one(".tpl-problems")), /\[warn\] 検査の指摘です/);
    assert.equal(dom.all('section.plan-graph[data-part="plan"][data-editable="1"]').length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D384 計画の表でステップを足す・動かす・消す。足したステップには前の末尾のステップが先行し、全体計画の最後の 1 ステップは消せない。番号は全体計画の続きで振る", async () => {
  const dom = await openList();
  try {
    await startEdit(dom);
    const rows = (part: string) => dom.all(`section.tpl-edit-plan[data-part="${part}"] tbody tr`);
    // 足す: 全体計画に docs を末尾に足すと 3 ステップになり、先行ステップは 2
    dom.change(dom.one<HTMLSelectElement>('section.tpl-edit-plan[data-part="plan"] select.f-add-type'), "docs");
    await dom.settle();
    dom.click(dom.one('section.tpl-edit-plan[data-part="plan"] button[data-action="add-item"]'));
    await dom.settle();
    assert.equal(rows("plan").length, 3);
    assert.equal(text(rows("plan")[2].querySelectorAll("td")[3]), "2");
    assert.equal(rows("feedback")[0].querySelector("td.mono")?.textContent, "4", "フィードバック計画の番号が続きになる");
    // 動かす: 1 は 2 の先行なので前にも後ろにも動かせない。足した 3 は 2 の後続なので前へ動かせない
    assert.equal(rows("plan")[0].querySelector<HTMLButtonElement>('button[data-action="move-up"]')?.disabled, true);
    assert.equal(rows("plan")[2].querySelector<HTMLButtonElement>('button[data-action="move-up"]')?.disabled, true);
    // 消す: 3 番を消すと 2 ステップに戻る
    dom.click(rows("plan")[2].querySelector<HTMLButtonElement>('button[data-action="remove-item"]') as HTMLButtonElement);
    await dom.settle();
    assert.equal(rows("plan").length, 2);
    // 2 番を消すと 1 ステップだけになり、最後の 1 ステップは消せない
    dom.click(rows("plan")[1].querySelector<HTMLButtonElement>('button[data-action="remove-item"]') as HTMLButtonElement);
    await dom.settle();
    assert.equal(rows("plan").length, 1);
    assert.equal(rows("plan")[0].querySelector<HTMLButtonElement>('button[data-action="remove-item"]')?.disabled, true);
    // 消した 2 番のフローはステップと一緒になくなる
    assert.equal(dom.all('section[data-role="flows"] li.row').length, 1);
    assert.match(text(dom.one('section[data-role="flows"] li[data-index="1"]')), /フローなし/);
    // フィードバック計画のステップは全部消せて、「作る」入口に戻る
    dom.click(rows("feedback")[0].querySelector<HTMLButtonElement>('button[data-action="remove-item"]') as HTMLButtonElement);
    await dom.settle();
    assert.equal(rows("feedback").length, 0);
    assert.equal(dom.all('section.tpl-edit-plan[data-part="feedback"] button[data-action="add-item"]').length, 1);
    // 元に戻す
    dom.click(dom.one('button[data-action="undo-edit"]'));
    await dom.settle();
    assert.equal(rows("feedback").length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D385 保存は editSave に開いたときのハッシュと書き出す中身を載せて送る。断られたら理由を出して編集を続けられ、書けたら未保存が消える", async () => {
  const dom = await openList();
  try {
    const { session } = await startEdit(dom);
    dom.type(dom.one(".f-description"), "保存する説明");
    await dom.settle();
    dom.click(dom.one('button[data-action="save-edit"]'));
    await dom.settle();
    const saves = dom.posted.filter((m) => m.type === "editSave");
    assert.equal(saves.length, 1);
    assert.equal(saves[0].name, "feature");
    assert.equal(saves[0].layer, "self");
    assert.equal(saves[0].expect, "sha-opened");
    assert.equal((saves[0].data as { description: string }).description, "保存する説明");
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, true, "保存の答えを待つ間は押せない");
    // 断られた
    await dom.send({ type: "editSaved", session, sha: "", error: "保存しません: 外で変わっています" });
    await dom.settle();
    assert.match(text(dom.one('[data-role="edit-status"]')), /外で変わっています/);
    assert.equal(dom.all('[data-role="dirty"]').length, 1, "断られたら未保存のまま");
    // もう一度押して、書けた
    dom.click(dom.one('button[data-action="save-edit"]'));
    await dom.settle();
    await dom.send({ type: "editSaved", session, sha: "sha-new", error: "" });
    await dom.settle();
    assert.match(text(dom.one('[data-role="edit-status"]')), /保存しました/);
    assert.equal(dom.all('[data-role="dirty"]').length, 0);
    // 次の保存の期待は、書いたファイルのハッシュ
    dom.type(dom.one(".f-when"), "別の場面");
    await dom.settle();
    dom.click(dom.one('button[data-action="save-edit"]'));
    await dom.settle();
    assert.equal(dom.posted.filter((m) => m.type === "editSave").at(-1)?.expect, "sha-new");
  } finally {
    await dom.close();
  }
});

test("CB-D386 未保存のまま閉じようとすると破棄してよいかを聞く。続ければ編集に戻り、破棄すれば一覧に戻る。直していなければそのまま閉じる", async () => {
  const dom = await openList();
  try {
    await startEdit(dom);
    dom.click(dom.one('button[data-action="close-edit"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="edit"]').length, 0, "直していないのでそのまま閉じる");
    assert.equal(dom.posted.filter((m) => m.type === "editClose").length, 1, "閉じたことを拡張ホストに知らせる");
    assert.equal(dom.all("li.template").length, 4);

    await startEdit(dom);
    dom.type(dom.one(".f-description"), "直した");
    await dom.settle();
    dom.click(dom.one('button[data-action="close-edit"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="confirm-close"]').length, 1);
    dom.click(dom.one('button[data-action="keep-edit"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="confirm-close"]').length, 0);
    assert.equal(dom.all('[data-role="edit"]').length, 1);
    dom.click(dom.one('button[data-action="close-edit"]'));
    await dom.settle();
    dom.click(dom.one('button[data-action="discard-edit"]'));
    await dom.settle();
    assert.equal(dom.all('[data-role="edit"]').length, 0);
    assert.equal(dom.all("li.template").length, 4);
  } finally {
    await dom.close();
  }
});

test("CB-D387 編集を始められなかったときは、理由を一覧の上に出す。読み取り専用の層では編集と作るの入口を出さない", async () => {
  const dom = await openList();
  try {
    dom.click(dom.one('li.template[data-name="feature"][data-layer="self"] button[data-action="edit-template"]'));
    await dom.settle();
    const session = dom.posted.find((m) => m.type === "editOpen")?.session as number;
    await dom.send({ type: "editOpened", session, opened: null, error: "実行ファイルが古い版です" });
    await dom.settle();
    assert.match(text(dom.one('[data-role="edit-error"]')), /実行ファイルが古い版です/);
    assert.equal(dom.all('[data-role="edit"]').length, 0);
    assert.equal(dom.all("li.template").length, 4);
    // 読み直すと、前の失敗の理由は消える
    await dom.send({ type: "data", data: { kind: "page", page: page() } });
    await dom.settle();
    assert.equal(dom.all('[data-role="edit-error"]').length, 0);
  } finally {
    await dom.close();
  }
  const fixture = page();
  const readOnly = {
    ...fixture,
    data: { ...fixture.data, layers: fixture.data.layers.map((l) => (l.name === "self" ? { ...l, writable: false } : l)) },
  };
  const ro = await loadPage(templatesHtml({ kind: "page", page: readOnly }), undefined, { measure: true });
  try {
    await ro.settle();
    assert.equal(ro.all('button[data-action="edit-template"]').length, 0);
    assert.equal(ro.all('[data-role="new-template"]').length, 0);
  } finally {
    await ro.close();
  }
});

test("CB-D388 新しいテンプレートは名前を入れて作る。使えない名前・同じ名前の行があるときは押せず、答えが届くと新規として編集欄が開き、まだ保存していないことを言う", async () => {
  const dom = await openList();
  try {
    const make = dom.one<HTMLButtonElement>('button[data-action="new-template"]');
    assert.equal(make.disabled, true, "名前が空のうちは押せない");
    dom.type(dom.one(".f-new-name"), "bad name");
    await dom.settle();
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="new-template"]').disabled, true);
    dom.type(dom.one(".f-new-name"), "feature");
    await dom.settle();
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="new-template"]').disabled, true, "同じ名前が一覧にある");
    assert.match(text(dom.one('[data-role="name-taken"]')), /既にあります/);
    dom.type(dom.one(".f-new-name"), "my-flow");
    await dom.settle();
    dom.click(dom.one('button[data-action="new-template"]'));
    await dom.settle();
    const asked = dom.posted.find((m) => m.type === "editNew");
    assert.equal(asked?.name, "my-flow");
    const check = fixtureCheck();
    const skeleton = { name: "my-flow", description: "", plan: ["implement"] };
    await dom.send({ type: "editOpened", session: asked?.session, opened: { name: "my-flow", layer: "self", created: true, sha: "", data: skeleton, check: { ...check, name: "my-flow" }, lock: { locked: false, reason: "", doing: [] } }, error: "" });
    await dom.settle();
    assert.match(text(dom.one('[data-role="edit"] h2')), /my-flow/);
    assert.match(text(dom.one('[data-role="edit"] h2')), /新規/);
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, false, "新規は直さなくても保存できる");
    dom.click(dom.one('button[data-action="save-edit"]'));
    await dom.settle();
    assert.equal(dom.posted.find((m) => m.type === "editSave")?.expect, "", "新規はまだファイルが無い");
  } finally {
    await dom.close();
  }
});

test("CB-D1224 作った直後は error の苦情を隠し（warn は出す）、触ったあとは出る", async () => {
  const dom = await openList();
  try {
    dom.type(dom.one(".f-new-name"), "my-flow2");
    await dom.settle();
    dom.click(dom.one('button[data-action="new-template"]'));
    await dom.settle();
    const asked = dom.posted.find((m) => m.type === "editNew");
    const check = fixtureCheck();
    const problems = [{ severity: "error", detail: "description がありません" }, { severity: "warn", detail: "when がありません" }];
    const skeleton = { name: "my-flow2", description: "", plan: ["implement"] };
    await dom.send({ type: "editOpened", session: asked?.session, opened: { name: "my-flow2", layer: "self", created: true, sha: "", data: skeleton, check: { ...check, name: "my-flow2", problems }, lock: { locked: false, reason: "", doing: [] } }, error: "" });
    await dom.settle();
    assert.equal(dom.all(".tpl-problems .sev-error").length, 0, "未入力の間は error を出さない");
    assert.equal(dom.all(".tpl-problems .sev-warn").length, 1);
    dom.type(dom.one(".f-description"), "x");
    dom.type(dom.one(".f-description"), "");
    await dom.settle();
    assert.equal(dom.all(".tpl-problems .sev-error").length, 1, "触ったあとは空に戻しても出す");
  } finally {
    await dom.close();
  }
});

test("CB-D389 ステップのフローは最初から図を開いていて、部品箱でノードを足すと未保存になり、答えを読み直す。「フローを足す」は雛形を置き、「フローを外す」で消える", async () => {
  const dom = await openList();
  try {
    await startEdit(dom);
    const item = (index: number) => `section[data-role="flows"] li[data-index="${index}"]`;
    // 2 番にはフローがある。最初から図と右の欄が出ている。畳むと消え、開き直すと戻る
    assert.equal(dom.all(`${item(2)} .tpl-flow-edit`).length, 1);
    dom.click(dom.one(`${item(2)} button[data-action="toggle-flow"]`));
    await dom.settle();
    assert.equal(dom.all(`${item(2)} .tpl-flow-edit`).length, 0);
    dom.click(dom.one(`${item(2)} button[data-action="toggle-flow"]`));
    await dom.settle();
    assert.equal(dom.all(`${item(2)} .tpl-flow-edit`).length, 1);
    assert.equal(dom.all(`${item(2)} .react-flow__node`).length >= 2, true, "見本のフローのノードが図に出る");
    assert.equal(dom.all('[data-role="dirty"]').length, 0);
    const nodes = dom.all(`${item(2)} .react-flow__node`).length;
    dom.click(dom.one(`${item(2)} button[data-action="add-node"][data-type="prompt"]`));
    await dom.settle();
    assert.equal(dom.all(`${item(2)} .react-flow__node`).length, nodes + 1);
    assert.equal(dom.all('[data-role="dirty"]').length, 1);
    const asked = await checkAsked(dom);
    const flows = (asked.data as { flows: Record<string, { nodes: unknown[] }> }).flows;
    assert.equal(flows["2"].nodes.length, nodes + 1);
    // 1 番にはフローが無い。足すと雛形が置かれて図が開く
    assert.match(text(dom.one(item(1))), /フローなし/);
    dom.click(dom.one(`${item(1)} button[data-action="add-flow"]`));
    await dom.settle();
    assert.equal(dom.all(`${item(1)} .tpl-flow-edit`).length, 1);
    assert.match(text(dom.one(item(1))), /フロー: feature-1/);
    // 外すと消える
    dom.click(dom.one(`${item(1)} button[data-action="remove-flow"]`));
    await dom.settle();
    assert.match(text(dom.one(item(1))), /フローなし/);
    assert.equal(dom.all(`${item(1)} .tpl-flow-edit`).length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D390 作業中のチケットがある間は編集はできるが保存できない。錠は開いたときの答えと、あとから届く editLock で替わり、錠が解けると保存できる", async () => {
  const dom = await openList();
  try {
    const locked = { locked: true, reason: "作業中のチケットがあります（i0001-1）。いま保存すると、進行中の作業の判定が途中で変わるため、作業が終わってから保存してください。", doing: ["i0001-1"] };
    const { session } = await startEdit(dom, openedOf(fixtureCheck(), { lock: locked }));
    assert.match(text(dom.one('[data-role="edit-lock"]')), /作業中のチケットがあります/);
    dom.type(dom.one(".f-description"), "錠の間も直せる");
    await dom.settle();
    assert.equal(dom.all('[data-role="dirty"]').length, 1);
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, true, "錠が掛かっている間は保存を押せない");
    await dom.send({ type: "editLock", session, lock: { locked: false, reason: "", doing: [] } });
    await dom.settle();
    assert.equal(dom.all('[data-role="edit-lock"]').length, 0);
    assert.equal(dom.one<HTMLButtonElement>('button[data-action="save-edit"]').disabled, false);
    // 別の編集の番号の錠は受けない
    await dom.send({ type: "editLock", session: session + 99, lock: locked });
    await dom.settle();
    assert.equal(dom.all('[data-role="edit-lock"]').length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D739 一覧の行は最初から開き、図の中身を求める（preview）。答えが届くと計画の図と子のフローの図を読むだけで出し、「図を編集」で編集欄に進める", async () => {
  const dom = await openList();
  try {
    const row = 'li.template[data-name="feature"][data-layer="self"]';
    assert.equal(dom.all(`${row}.open`).length, 1, "最初から開いている");
    const asked = dom.posted.filter((m) => m.type === "preview" && m.name === "feature");
    assert.equal(asked.length, 1, "1 行につき 1 度だけ求める");
    assert.equal(asked[0].layer, "self");
    // 答えが届くまでは表で出す（読み込み中の表示）
    assert.equal(dom.all(`${row} table.tpl-plan`).length > 0, true);
    await dom.send({ type: "preview", name: "feature", layer: "self", check: fixtureCheck(), error: "" });
    await dom.settle();
    assert.equal(dom.all(`${row} [data-role="diagrams"]`).length, 1);
    assert.equal(dom.all(`${row} .plan-graph`).length >= 1, true, "計画の図が出る");
    assert.equal(dom.all(`${row} .plan-graph[data-editable="1"]`).length, 0, "一覧の図は読むだけ");
    assert.equal(dom.all(`${row} [data-role="flow-diagram"] .react-flow__node`).length >= 2, true, "子のフローの図が出る");
    assert.equal(dom.all(`${row} table.tpl-plan`).length, 0, "図が出たら表は出さない");
    // 畳むと消え、押し直すと戻る。求め直さない
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    assert.equal(dom.all(`${row}.open`).length, 0);
    dom.click(dom.one(`${row} .row-head`));
    await dom.settle();
    assert.equal(dom.all(`${row} [data-role="diagrams"]`).length, 1);
    assert.equal(dom.posted.filter((m) => m.type === "preview" && m.name === "feature").length, 1);
    await startEdit(dom);
    assert.equal(dom.all('section[data-role="edit"]').length, 1);
  } finally {
    await dom.close();
  }
});

test("CB-D740 図の中身を読めなかったときは、理由を出す", async () => {
  const dom = await openList();
  try {
    const row = 'li.template[data-name="feature"][data-layer="self"]';
    await dom.send({ type: "preview", name: "feature", layer: "self", check: null, error: "検査できません" });
    await dom.settle();
    assert.match(text(dom.one(`${row} [data-role="preview-error"]`)), /検査できません/);
    assert.equal(dom.all(`${row} table.tpl-plan`).length, 0);
    assert.equal(dom.posted.filter((m) => m.type === "preview" && m.name === "feature").length, 1, "失敗しても頼み直さない");
  } finally {
    await dom.close();
  }
});
