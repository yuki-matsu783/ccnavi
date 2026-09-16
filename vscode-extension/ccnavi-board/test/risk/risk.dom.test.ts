/** リスク管理画面のスクリプトを happy-dom で動かす。 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readRisk } from "../../src/core/risk-doc.js";
import { renderRiskPage, type RiskPage } from "../../src/core/risk-render.js";
import { loadPage } from "../helpers/dom.js";
import type { HTMLButtonElement, HTMLInputElement } from "happy-dom" with { "resolution-mode": "import" };

const RISK = `version: 1
factors:
  - id: big-diff
    points: 25
    lines_over: 300
    message: "行数が多い"
  - id: ci
    points: 35
    glob: ".github/**"
    max: 35
    message: "CI に触った"
  - id: sh
    points: 10
    script: ".ccnavi/common/scripts/risk.sh"
  - id: q
    points: 20
    judge: "テストの無い変更を含むか"
`;

function html(overrides: Partial<RiskPage> = {}): string {
  return renderRiskPage(
    { root: "/ws", riskPath: ".ccnavi/common/risks.yml", exists: true, ticketControl: "enable", model: readRisk(RISK).model, lock: { locked: false, reason: "", doing: [] }, ...overrides },
    { nonce: "n" },
  );
}

test("CB-D10 既定は畳み、行を押すと開いて state に id が入る。要約は当て方に応じた文になる", async () => {
  const page = await loadPage(html());
  try {
    assert.equal(page.all(".factor.open").length, 0);
    assert.equal(page.one('.factor[data-key="f1"] .sum .clip').textContent, "差分が 300 行を超えたら加点行数が多い");
    assert.equal(page.one('.factor[data-key="f2"] .sum .clip').textContent, ".github/** にヒットしたファイルが 1 つあるごとに加点（上限 35 点）CI に触った");
    // script は「出した点を加点」で、points は測れなかったときの保険。judge は yes で加点
    assert.equal(page.one('.factor[data-key="f3"] .sum .clip').textContent, "スクリプト .ccnavi/common/scripts/risk.sh が出した点を加点（測れなければ 10 点）");
    assert.equal(page.one('.factor[data-key="f4"] .sum .clip').textContent, "問い「テストの無い変更を含むか」に yes だったら加点");
    page.click(page.one('.factor[data-key="f2"] .row-head'));
    assert.ok(page.one('.factor[data-key="f2"]').classList.contains("open"));
    assert.deepEqual((page.state() as { open: string[] }).open, ["ci"]);
    assert.equal(page.one<HTMLInputElement>('.factor[data-key="f2"] input.f-max').value, "35");
  } finally {
    await page.close();
  }
});

test("CB-D11 当て方を変えると値は持ち越さず、glob 以外では上限の欄が消え、行は開いたまま", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.factor[data-key="f2"] .row-head'));
    page.change(page.one('.factor[data-key="f2"] select.f-kind'), "files_over");
    const row = page.one('.factor[data-key="f2"]');
    assert.ok(row.classList.contains("open"));
    assert.equal(page.all('.factor[data-key="f2"] input.f-max').length, 0);
    assert.equal(page.one<HTMLInputElement>('.factor[data-key="f2"] input.f-value').value, "");
    assert.equal(page.one('.factor[data-key="f2"] .sum .clip').textContent, "（しきい値 未設定）CI に触った");
    page.type(page.one('.factor[data-key="f2"] input.f-value'), "10");
    assert.equal(page.one('.factor[data-key="f2"] .sum .clip').textContent, "変えたファイルが 10 件を超えたら加点CI に触った");
  } finally {
    await page.close();
  }
});

test("CB-D12 絞り込みは一致した行だけを数え、開いている行は隠さない。保存中も開閉のボタンは押せる", async () => {
  const page = await loadPage(html());
  try {
    page.click(page.one('.factor[data-key="f1"] .row-head'));
    page.type(page.one("#find"), "ヒットしたファイル");
    assert.equal(page.one("#factor-count").textContent, "1 / 4（開いたまま 1）", "画面に出ている語で当たる");
    page.type(page.one("#find"), "github");
    assert.ok(page.one('.factor[data-key="f1"]').classList.contains("hidden-by-find"));
    assert.ok(page.one('.factor[data-key="f1"]').classList.contains("open"));
    assert.equal(page.one("#factor-count").textContent, "1 / 4（開いたまま 1）");
    // 保存の往復の間は欄を止めるが、行の開閉（twist）は止めない
    page.type(page.one('.factor[data-key="f1"] input.f-points'), "30");
    page.click(page.one("#save"));
    assert.equal(page.posted.filter((m) => m.type === "save").length, 1);
    assert.ok(page.one<HTMLInputElement>('.factor[data-key="f1"] input.f-points').disabled);
    assert.ok(!page.one<HTMLButtonElement>('.factor[data-key="f1"] .row-head .twist').disabled);
    await page.send({ type: "failed", message: "lint error" });
    assert.ok(!page.one<HTMLInputElement>('.factor[data-key="f1"] input.f-points').disabled);
    assert.equal(page.one("#status").textContent, "lint error");
  } finally {
    await page.close();
  }
});

test("CB-D13 ファイルが無ければ欄も追加も押せず、「作る」だけ押せる", async () => {
  const page = await loadPage(html({ exists: false }));
  try {
    page.click(page.one('.factor[data-key="f1"] .row-head'));
    assert.ok(page.one<HTMLInputElement>('.factor[data-key="f1"] input.f-id').disabled);
    assert.ok(page.one<HTMLButtonElement>('button[data-action="add"]').disabled);
    page.click(page.one('button[data-action="create"]'));
    assert.deepEqual(page.posted, [{ type: "create" }]);
  } finally {
    await page.close();
  }
});

test("CB-D44 開いたまま disable になったら帯を出し、enable に戻せば隠す", async () => {
  const page = await loadPage(html());
  try {
    const banner = page.one("#ticket-off");
    assert.ok(banner.classList.contains("hidden"), "enable では隠れている");
    await page.send({ type: "ticketControl", value: "disable" });
    assert.ok(!banner.classList.contains("hidden"));
    assert.match(banner.textContent, /配点は子チケットを閉じるときにしか使われない/);
    // 帯を出すだけで、編集中の内容は残る（HTML を張り替えない）
    assert.deepEqual(page.posted, []);
    await page.send({ type: "ticketControl", value: "enable" });
    assert.ok(banner.classList.contains("hidden"));
  } finally {
    await page.close();
  }
});
