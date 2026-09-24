/** リスク管理画面（React）を happy-dom で動かす。描くものも、押したときの動きもここで見る。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRisk } from "../../src/core/risk-doc.js";
import type { RiskForm } from "../../src/core/risk-view.js";
import { openPage, openRisk, page, rowSelector } from "../helpers/risk.js";
import type { DomPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement, HTMLSelectElement } from "happy-dom" with { "resolution-mode": "import" };

/** 直前に送った保存の中身 */
function savedForm(dom: DomPage): RiskForm {
  const saves = dom.posted.filter((message) => message.type === "save");
  assert.ok(saves.length > 0, "保存を送っていない");
  return saves[saves.length - 1].form as RiskForm;
}

test("CB-D10 既定は畳み、行を押すと開いて state に id が入る。要約は加点条件に応じた文になる", async () => {
  const dom = await openRisk();
  try {
    assert.equal(dom.all(".factor.open").length, 0);
    assert.equal(dom.one(`${rowSelector("f1")} .sum .clip`).textContent, "変更した行数（追加＋削除）が 300 行を超えると加点行数が多い");
    assert.equal(dom.one(`${rowSelector("f2")} .sum .clip`).textContent, ".github/** に当てはまるファイルを 1 つ変更するごとに加点（上限 35 点）CI に触った");
    // script は「返した点を加点」で、points は測れなかったときの保険。judge は yes で加点
    assert.equal(dom.one(`${rowSelector("f3")} .sum .clip`).textContent, "スクリプト .ccnavi/common/scripts/risk.sh が返した点を加点（点を取れなかったときは 10 点）");
    assert.equal(dom.one(`${rowSelector("f4")} .sum .clip`).textContent, "問い「テストの無い変更を含むか」の答えが yes なら加点");
    dom.click(dom.one(`${rowSelector("f2")} .row-head`));
    await dom.settle();
    assert.ok(dom.one(rowSelector("f2")).classList.contains("open"));
    assert.deepEqual((dom.state() as { open: string[] }).open, ["ci"]);
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("f2")} input.f-max`).value, "35");
  } finally {
    await dom.close();
  }
});

test("CB-D11 加点条件を変えると値は持ち越さず、glob 以外では上限の欄が消え、行は開いたまま", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one(`${rowSelector("f2")} .row-head`));
    await dom.settle();
    dom.change(dom.one(`${rowSelector("f2")} select.f-kind`), "files_over");
    await dom.settle();
    assert.ok(dom.one(rowSelector("f2")).classList.contains("open"));
    assert.equal(dom.all(`${rowSelector("f2")} input.f-max`).length, 0);
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("f2")} input.f-value`).value, "");
    assert.equal(dom.one(`${rowSelector("f2")} .sum .clip`).textContent, "（基準 未設定）CI に触った");
    dom.type(dom.one(`${rowSelector("f2")} input.f-value`), "10");
    await dom.settle();
    assert.equal(dom.one(`${rowSelector("f2")} .sum .clip`).textContent, "変更したファイルが 10 件を超えると加点CI に触った");
    // 保存に渡る形にも出る。加点条件を変えても id と理由は持ち越す
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(savedForm(dom).factors[1], { origin: 1, id: "ci", points: "35", kind: "files_over", value: "10", max: "", message: "CI に触った" });
  } finally {
    await dom.close();
  }
});

test("CB-D12 絞り込みは一致した行だけを数え、開いている行は隠さない。保存中も開閉のボタンは押せる", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one(`${rowSelector("f1")} .row-head`));
    await dom.settle();
    dom.type(dom.one("#find"), "当てはまるファイル");
    await dom.settle();
    assert.equal(dom.one("#factor-count").textContent, "1 / 4（開いたまま 1）", "画面に出ている語で当たる");
    dom.type(dom.one("#find"), "github");
    await dom.settle();
    assert.ok(dom.one(rowSelector("f1")).classList.contains("hidden-by-find"));
    assert.ok(dom.one(rowSelector("f1")).classList.contains("open"));
    assert.equal(dom.one("#factor-count").textContent, "1 / 4（開いたまま 1）");
    // 保存の往復の間は欄を止めるが、行の開閉（twist）は止めない
    dom.type(dom.one(`${rowSelector("f1")} input.f-points`), "30");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.equal(dom.posted.filter((message) => message.type === "save").length, 1);
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).disabled);
    assert.ok(!dom.one<HTMLButtonElement>(`${rowSelector("f1")} .row-head .twist`).disabled);
    await dom.send({ type: "failed", message: "lint error" });
    assert.ok(!dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).disabled);
    assert.equal(dom.one("#status").textContent, "lint error");
  } finally {
    await dom.close();
  }
});

test("CB-D13 ファイルが無ければ欄も追加も押せず、「作る」だけ押せる", async () => {
  const dom = await openRisk({ exists: false });
  try {
    dom.click(dom.one(`${rowSelector("f1")} .row-head`));
    await dom.settle();
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-id`).disabled);
    assert.ok(dom.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    dom.click(dom.one('button[data-action="create"]'));
    await dom.settle();
    assert.deepEqual(
      dom.posted.filter((message) => message.type !== "ready"),
      [{ type: "create" }],
    );
  } finally {
    await dom.close();
  }
});

test("CB-T82 ファイルが無ければ組み込みだと言って作るボタンを出し、あれば出さない", async () => {
  const missing = await openRisk({ exists: false });
  try {
    assert.match(missing.one(".banner.missing").textContent, /\.ccnavi\/common\/risks\.yml が無い。実行ファイルは組み込みの配点で数えている/);
    assert.equal(missing.all('button[data-action="create"]').length, 1);
    assert.ok(missing.one<HTMLButtonElement>('button[data-action="open-risk"]').disabled);
  } finally {
    await missing.close();
  }
  const present = await openRisk();
  try {
    assert.equal(present.all('button[data-action="create"]').length, 0);
    assert.equal(present.all(".banner.missing").length, 0);
    assert.ok(!present.one<HTMLButtonElement>('button[data-action="open-risk"]').disabled);
  } finally {
    await present.close();
  }
});

test("CB-T84 保存できない理由と読み込みの苦情を出し、錠は届いたところで掛かる", async () => {
  const dom = await openRisk({ model: readRisk("version: 1\nfactors: nope\n").model });
  try {
    assert.ok(dom.one("#lock").classList.contains("hidden"));
    assert.match(dom.one(".problems").textContent, /factors が並びではない/);
    // 作業中のチケットが現れたら、保存は押せなくなる（編集の途中はそのまま）
    dom.type(dom.one("#find"), "");
    await dom.send({ type: "lock", lock: { locked: true, reason: "作業中のチケットがある（i0001-02）", doing: ["i0001-02"] } });
    assert.ok(!dom.one("#lock").classList.contains("hidden"));
    assert.equal(dom.one("#lock").textContent, "作業中のチケットがある（i0001-02）");
    assert.ok(dom.one<HTMLButtonElement>("#save").disabled);
  } finally {
    await dom.close();
  }
});

test("CB-T122 項目の一覧は 1 件 1 行で、控えてある id の行は開いて出す", async () => {
  const dom = await openRisk({}, { open: ["ci", "q"] });
  try {
    assert.equal(dom.all("#factors > li.factor").length, 4);
    assert.deepEqual(
      dom.all(".factor.open").map((row) => row.getAttribute("data-key")),
      ["f2", "f4"],
    );
    assert.equal(dom.all("#find").length, 1);
    // 畳むと控えからも消える
    dom.click(dom.one(`${rowSelector("f2")} .row-head`));
    await dom.settle();
    assert.deepEqual((dom.state() as { open: string[] }).open, ["q"]);
  } finally {
    await dom.close();
  }
});

test("CB-T126 項目の欄名は日本語で、値の欄は加点条件で名前が変わり、YAML のキー名は title に載せる", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one(`${rowSelector("f1")} .row-head`));
    await dom.settle();
    const caps = dom.all(`${rowSelector("f1")} .row-body > .field > .cap`);
    assert.deepEqual(
      caps.map((cap) => cap.textContent),
      ["id", "点", "加点条件", "基準", "理由"],
    );
    assert.deepEqual(
      caps.map((cap) => cap.getAttribute("title")),
      ["YAML のキー: id", "YAML のキー: points", "YAML のキー: lines_over / files_over / deleted_over / glob / script / judge", "YAML のキー: lines_over", "YAML のキー: message"],
    );
    // 境目の点はリスクレベルの名前を欄名にし、飾りのラベルは出さない
    assert.deepEqual(
      dom.all("#levels > .field > .cap").map((cap) => cap.textContent),
      ["MEDIUM", "HIGH", "CRITICAL"],
    );
    assert.equal(dom.one("#levels > .field > .cap").getAttribute("title"), "YAML のキー: levels.medium");
    // glob なら値の欄は glob で、上限が並ぶ
    dom.click(dom.one(`${rowSelector("f2")} .row-head`));
    await dom.settle();
    assert.deepEqual(
      dom.all(`${rowSelector("f2")} .row-body > .field > .cap`).map((cap) => cap.textContent),
      ["id", "点", "加点条件", "glob", "理由"],
    );
    assert.equal(dom.one(`${rowSelector("f2")} .inline > .cap`).textContent, "上限");
  } finally {
    await dom.close();
  }
});

test("CB-D53 項目を足すと開いて出し、並べ替えと削除が保存に渡る形に出る", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one('button[data-action="add"]'));
    await dom.settle();
    const added = dom.all("#factors > li.factor").pop();
    assert.ok(added !== undefined && added.classList.contains("open"), "足した項目は開いて出す");
    assert.equal(added.getAttribute("data-key"), "f5");
    dom.type(dom.one(`${rowSelector("f5")} input.f-id`), "new");
    await dom.settle();
    // 1 つ上げて、1 件目を消す
    dom.click(dom.all<HTMLButtonElement>(`${rowSelector("f5")} .buttons button`)[0]);
    await dom.settle();
    dom.click(dom.all<HTMLButtonElement>(`${rowSelector("f1")} .buttons button`)[2]);
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    assert.deepEqual(
      savedForm(dom).factors.map((factor) => factor.id),
      ["ci", "sh", "new", "q"],
    );
    assert.equal(savedForm(dom).factors[2].origin, null, "足した項目は元の位置を持たない");
  } finally {
    await dom.close();
  }
});

test("CB-D54 ファイルが外で変わったら帯を出し、押すと再読込を送る（打ちかけは残す）", async () => {
  const dom = await openRisk();
  try {
    assert.ok(dom.one("#changed").classList.contains("hidden"));
    dom.type(dom.one(`${rowSelector("f1")} input.f-points`), "30");
    await dom.settle();
    await dom.send({ type: "changed" });
    assert.ok(!dom.one("#changed").classList.contains("hidden"));
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).value, "30", "帯が出ても編集は消さない");
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    dom.click(dom.one('#changed button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload"), [{ type: "reload", dirty: true }]);
  } finally {
    await dom.close();
  }
});

test("CB-D55 組み上がったら ready を送り、届いた中身で編集を置き換える", async () => {
  const dom = await openRisk();
  try {
    assert.deepEqual(dom.posted, [{ type: "ready" }]);
    dom.type(dom.one(`${rowSelector("f1")} input.f-points`), "30");
    await dom.settle();
    assert.ok(!dom.one("#dirty").classList.contains("hidden"));
    // 再読込・保存が通ったときだけ届く。届いたらその中身で描き直す
    await dom.send({ type: "data", data: { kind: "page", page: page({ riskPath: "risks.yml" }) } });
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("f5")} input.f-points`).value, "25");
    assert.ok(dom.one("#dirty").classList.contains("hidden"));
    assert.equal(dom.one(".path").textContent, "risks.yml");
  } finally {
    await dom.close();
  }
});

test("CB-D56 読み直せなかったら理由を出し、配点は出さない", async () => {
  const dom = await openPage({ kind: "error", error: "配点のファイルを読めない: EACCES" });
  try {
    assert.match(dom.one(".empty").textContent, /リスク管理画面を読み直せなかった/);
    assert.equal(dom.one("pre.load-error").textContent, "配点のファイルを読めない: EACCES");
    assert.equal(dom.all("#factors").length, 0);
  } finally {
    await dom.close();
  }
});

test("CB-D57 見た目の切り替えは body のクラスだけを付け替え、編集の途中は消えない", async () => {
  const dom = await openRisk();
  try {
    dom.type(dom.one(`${rowSelector("f1")} input.f-points`), "30");
    await dom.settle();
    await dom.send({ type: "appearance", value: "claude-dark" });
    assert.equal(dom.document.body.className, "ccnavi-claude-dark");
    assert.equal(dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).value, "30");
    await dom.send({ type: "appearance", value: "vscode" });
    assert.equal(dom.document.body.className, "");
  } finally {
    await dom.close();
  }
});

test("CB-D58 加点条件の選択肢は 6 つで、キーの綴りと説明を並べて出す", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one(`${rowSelector("f1")} .row-head`));
    await dom.settle();
    const select = dom.one<HTMLSelectElement>(`${rowSelector("f1")} select.f-kind`);
    assert.deepEqual(
      Array.from(select.options).map((option) => option.value),
      ["lines_over", "files_over", "deleted_over", "glob", "script", "judge"],
    );
    assert.equal(select.options[0].textContent, "lines_over（変更した行数が基準を超えたら加点）");
    assert.equal(select.value, "lines_over");
  } finally {
    await dom.close();
  }
});

test("CB-D64 往復の間は、帯の再読込も止める。やめたと返れば欄は戻る", async () => {
  const dom = await openRisk();
  try {
    await dom.send({ type: "changed" });
    dom.type(dom.one(`${rowSelector("f1")} input.f-points`), "30");
    await dom.settle();
    dom.click(dom.one("#save"));
    await dom.settle();
    // 往復の間に帯の再読込を押せると、捨てたはずの編集が遅れて保存される
    for (const button of dom.all<HTMLButtonElement>('button[data-action="reload"]')) {
      assert.ok(button.disabled, "保存の往復の間はどの再読込も押せない");
    }
    await dom.send({ type: "failed", message: "lint error" });
    assert.ok(!dom.one<HTMLButtonElement>('#changed button[data-action="reload"]').disabled);
  } finally {
    await dom.close();
  }
});

test("CB-D65 再読込を押した時点で欄を止め、人がやめたら戻す", async () => {
  const dom = await openRisk();
  try {
    dom.click(dom.one('header button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload"), [{ type: "reload", dirty: false }]);
    // 止めていないと、読み直しを待つ間に打った内容が、届いた中身で黙って消える
    assert.ok(dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).disabled);
    await dom.send({ type: "cancelled" });
    assert.ok(!dom.one<HTMLInputElement>(`${rowSelector("f1")} input.f-points`).disabled);
    assert.equal(dom.one("#status").textContent, "");
  } finally {
    await dom.close();
  }
});

test("CB-D66 読み直せなかった画面からも、再読込を頼める", async () => {
  const dom = await openPage({ kind: "error", error: "EACCES" });
  try {
    dom.click(dom.one('button[data-action="reload"]'));
    await dom.settle();
    assert.deepEqual(dom.posted.filter((message) => message.type === "reload"), [{ type: "reload", dirty: false }]);
  } finally {
    await dom.close();
  }
});

test("CB-D100 拡張ホストが頼んだらリスク管理の案内を出し、閉じたら tourDone を返す。ファイルが無いときは作るボタンから始める", async () => {
  const dom = await openRisk();
  try {
    // 案内の入口は ? 1 文字で、ヘッダ（ツールバー）の最後の子。位置は Tour.css が 5 画面とも右上に揃える
    assert.equal(dom.one("header.toolbar > .tour-button:last-child").textContent, "?");
    await dom.send({ type: "tour" });
    await dom.settle();
    const titles: string[] = [];
    while (dom.all(".tour").length > 0) {
      titles.push(dom.one("#tour-title").textContent ?? "");
      dom.click(dom.one('[data-action="tour-next"]'));
      await dom.settle();
    }
    assert.deepEqual(titles, ["リスクレベルが上がる点数", "項目", "保存", "案内"]);
    assert.deepEqual(dom.posted.filter((message) => message.type === "tourDone"), [{ type: "tourDone" }]);
  } finally {
    await dom.close();
  }
  const missing = await openRisk({ exists: false });
  try {
    missing.click(missing.one('[data-action="tour"]'));
    await missing.settle();
    assert.equal(missing.one("#tour-title").textContent, "まずファイルを作る");
  } finally {
    await missing.close();
  }
});
