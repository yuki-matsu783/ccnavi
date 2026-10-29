// VS Code の枠（左の一覧・下のターミナル・通知）。拡張の画面は BoardPage など別のページオブジェクトが受け持つ。
"use strict";

class WorkbenchPage {
  constructor(page) {
    this.page = page;
  }

  /** 右のチャット（code-server の機能）を閉じ、左の ccnavi の一覧を開く。 */
  async openCcnaviSidebar() {
    await this.page.keyboard.press("Control+Alt+b");
    await this.page.locator('.activitybar a[aria-label*="ccnavi"]').first().click();
    await this.page.waitForTimeout(2500);
  }

  /** 左の一覧の項目名（例: チケット管理）。 */
  async sidebarItems() {
    return this.page.locator(".pane-body .monaco-list-row").allInnerTexts();
  }

  /** 左の一覧から画面を開く。開いた画面は、呼び出し側が対応するページオブジェクトで包む。 */
  async openScreen(name) {
    await this.page.locator(".pane-body .monaco-list-row", { hasText: name }).first().click();
    await this.page.waitForTimeout(5000); // webview が中身を受け取るまで
  }

  /** 画面（拡張の webview）のフレーム。中身があるものを後ろから探す。 */
  async webview() {
    for (const f of [...this.page.frames()].reverse()) {
      const ok = await f
        .evaluate(() => !!document.querySelector("#root") && document.body.innerText.length > 5)
        .catch(() => false);
      if (ok) return f;
    }
    throw new Error("拡張の画面（webview）が見つからない");
  }

  async terminalText() {
    return this.page.locator(".terminal-wrapper").first().innerText().catch(() => "");
  }

  async notificationText() {
    return this.page.locator(".notifications-toasts").innerText().catch(() => "");
  }

  async screenshot(file) {
    await this.page.screenshot({ path: file });
  }
}

module.exports = { WorkbenchPage };
