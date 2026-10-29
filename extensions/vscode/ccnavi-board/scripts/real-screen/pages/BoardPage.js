// チケット管理画面。操作は意味の名前（approve など）で呼び、セレクタと文言はここに閉じ込める。
// 画面の文言を直したら、この 1 ファイルだけ追従すればよい。
"use strict";

const { ApprovalDialog } = require("./ApprovalDialog.js");

const TEXT = {
  tourSkip: '[data-action="tour-skip"]',
  detail: "詳細",
  approve: "承認",
  reviewDone: "レビュー完了",
};

class BoardPage {
  /** @param {import("./WorkbenchPage.js").WorkbenchPage} workbench */
  constructor(workbench) {
    this.wb = workbench;
  }

  static async open(workbench) {
    await workbench.openScreen("チケット管理");
    const board = new BoardPage(workbench);
    await board.skipTour();
    return board;
  }

  async frame() {
    return this.wb.webview();
  }

  /** 初回の案内ツアー（見本のデータで始まる）を、あれば閉じる。 */
  async skipTour() {
    const f = await this.frame();
    const btn = f.locator(TEXT.tourSkip);
    if ((await btn.count()) > 0) {
      await btn.first().click();
      await this.wb.page.waitForTimeout(800);
    }
  }

  async text() {
    return (await this.frame()).evaluate(() => document.body.innerText);
  }

  /** 列（未着手・作業中・完了・取り消し）ごとのカード数。 */
  async columnCounts() {
    const t = await this.text();
    const out = {};
    for (const name of ["未着手", "作業中", "完了", "取り消し"]) {
      const m = t.match(new RegExp(`${name}\\n(\\d+)\\n`));
      out[name] = m ? Number(m[1]) : null;
    }
    return out;
  }

  /** 先頭のカードの「詳細」を開く。 */
  async expandFirstDetail() {
    await (await this.frame()).locator("summary", { hasText: TEXT.detail }).first().click();
    await this.wb.page.waitForTimeout(800);
  }

  /** 先頭の承認待ちカードの［承認］を押して、承認の確認（オーバーレイ）を開く。 */
  async openApproval() {
    const f = await this.frame();
    await f.getByRole("button", { name: TEXT.approve, exact: true }).first().click();
    await this.wb.page.waitForTimeout(2500);
    return new ApprovalDialog(this.wb);
  }

  /** 先頭のレビュー待ちの［レビュー完了］を押す。開いた画面の文を返す（指摘の有無で中身が変わる）。 */
  async clickReviewDone() {
    const f = await this.frame();
    await f.getByRole("button", { name: TEXT.reviewDone }).first().click();
    await this.wb.page.waitForTimeout(4000);
    return (await this.frame()).evaluate(() => document.body.innerText);
  }

  /** 開いているオーバーレイ・画面のボタンを名前で押し、待ったあとの画面の文を返す。 */
  async clickButton(name, { waitMs = 4000 } = {}) {
    const f = await this.frame();
    await f.getByRole("button", { name }).first().click();
    await this.wb.page.waitForTimeout(waitMs);
    return (await this.frame()).evaluate(() => document.body.innerText);
  }
}

module.exports = { BoardPage };
