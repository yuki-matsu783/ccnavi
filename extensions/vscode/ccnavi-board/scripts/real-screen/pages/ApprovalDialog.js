// 承認の確認オーバーレイと、承認したあとの結果の枠。
"use strict";

class ApprovalDialog {
  /** @param {import("./WorkbenchPage.js").WorkbenchPage} workbench */
  constructor(workbench) {
    this.wb = workbench;
  }

  /** オーバーレイの本文（承認する内容の一覧）。ボタンより後ろの文だけ。 */
  async body() {
    const f = await this.wb.webview();
    const t = await f.evaluate(() => document.body.innerText);
    const at = t.indexOf("承認待ちのチケット");
    return at < 0 ? t : t.slice(at);
  }

  /** ［この N 件を承認する］を押し、結果の枠が出るまで待って、その文を返す。 */
  async confirm({ waitMs = 10000 } = {}) {
    const f = await this.wb.webview();
    await f.getByRole("button", { name: /を承認する/ }).click();
    await this.wb.page.waitForTimeout(waitMs);
    const after = await this.wb.webview();
    const t = await after.evaluate(() => document.body.innerText);
    const at = t.indexOf("件を承認しました");
    return at < 0 ? t : t.slice(at);
  }

  async cancel() {
    const f = await this.wb.webview();
    await f.getByRole("button", { name: "キャンセル" }).click();
  }
}

module.exports = { ApprovalDialog };
