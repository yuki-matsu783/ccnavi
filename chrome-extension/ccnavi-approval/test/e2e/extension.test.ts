/**
 * 実機の試験（ADR-0093 段階 1・確認事項 4）。試験用の通信先（127.0.0.1）で組んだ拡張（dist-e2e/）を
 * Chromium（headless=new）に読み込み、模擬の GitHub を相手に次を確かめる。
 *
 * - 拡張のページの Web Worker で Pyodide が MV3 の CSP（'wasm-unsafe-eval' だけ）の下で起き、ccnavi を import できる
 * - 設定画面で PAT とリポジトリを登録し、ボードが読み取り専用で描ける
 * - 悪意のある Markdown を描いても何も動かない
 *
 * `node scripts/test.js --e2e` で回す。
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium, type BrowserContext, type Page } from "playwright-core";

import { fixture } from "../fixtures/repo.js";
import { MockGitHub, TOKEN } from "../helpers/mock-github.js";
import { HERE } from "../helpers/python.js";

const DIST = path.join(HERE, "dist-e2e");
const PORT = 18787;

let ctx: BrowserContext;
let id: string;
let closeServer: () => Promise<void>;
let profile: string;
const mock = new MockGitHub(fixture(), "main", new Date());
const problems: string[] = [];

function watch(page: Page): void {
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
}

before(async () => {
  closeServer = await mock.serve(PORT);
  profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-chrome-profile-"));
  ctx = await chromium.launchPersistentContext(profile, {
    channel: "chromium",
    headless: true,
    args: [`--disable-extensions-except=${DIST}`, `--load-extension=${DIST}`],
  });
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  id = new URL(sw.url()).host;
});

after(async () => {
  await ctx?.close();
  await closeServer?.();
  fs.rmSync(profile, { recursive: true, force: true });
});

test("CX-T070 組んだ manifest の CSP は 'wasm-unsafe-eval' だけを足し、通信先は試験の 127.0.0.1 だけ", () => {
  const m = JSON.parse(fs.readFileSync(path.join(DIST, "manifest.json"), "utf8"));
  assert.equal(
    m.content_security_policy.extension_pages,
    "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; connect-src 'self' http://127.0.0.1:18787",
  );
  assert.deepEqual(m.host_permissions, ["http://127.0.0.1:18787/*"]);
});

test("CX-T071 設定画面で PAT とリポジトリを登録する。PAT は画面に読み返さない", async () => {
  const page = await ctx.newPage();
  watch(page);
  await page.goto(`chrome-extension://${id}/options.html`);
  await page.fill("#repo-form [name=owner]", "acme");
  await page.fill("#repo-form [name=repo]", "widgets");
  await page.click("#repo-form button[type=submit]");
  await page.waitForSelector("#repo-list li");
  assert.match((await page.textContent("#repo-list")) ?? "", /github\.com\/acme\/widgets（統合先 デフォルトブランチ・直近 3 日）/);
  await page.fill("#token-value", TOKEN);
  await page.click("#token-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("token-list")?.textContent?.includes("登録済み"));
  assert.equal(await page.inputValue("#token-value"), "");
  assert.ok(!((await page.content()) ?? "").includes(TOKEN));
  await page.close();
});

test("CX-T072 ボード: Worker の Pyodide が CSP の下で起き、承認待ちを読み取り専用で描く", async () => {
  const page = await ctx.newPage();
  watch(page);
  const t0 = Date.now();
  await page.goto(`chrome-extension://${id}/board.html`);
  await page.waitForFunction(() => document.body.dataset.state !== "loading", null, { timeout: 120_000 });
  const took = Date.now() - t0;
  assert.equal(await page.getAttribute("body", "data-state"), "done", (await page.textContent("#status")) ?? "");
  const status = page.locator("#status");
  const boot = Number(await status.getAttribute("data-boot-ms"));
  const imp = Number(await status.getAttribute("data-import-ms"));
  assert.equal(await status.getAttribute("data-violations"), "0");
  console.log(`# Pyodide 起動 ${boot} ms・ccnavi の読み込み ${imp} ms・ボードが描けるまで ${took} ms`);

  assert.match((await page.textContent("[data-testid=integration]")) ?? "", /^統合先: main（ホストのデフォルトブランチ）/);
  const families = await page.locator("[data-family]").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.family));
  assert.deepEqual(families, ["i0001", "i0002"]);
  assert.equal(await page.locator('[data-family="i0001"] [data-ticket="i0001"].entry').count(), 1);
  assert.match((await page.textContent('[data-family="i0001"] .rejected')) ?? "", /先行 i0003-01 が閉じていない/);
  assert.match((await page.textContent('[data-family="i0001"] .closure')) ?? "", /i0003/);
  // 見た目を目で確かめるとき: CCNAVI_E2E_SHOT=<png のパス>
  if (process.env.CCNAVI_E2E_SHOT) await page.screenshot({ path: process.env.CCNAVI_E2E_SHOT, fullPage: true });
  // 承認・取り下げ・レビュー済み・「始める」は段階 1 では出さない
  assert.equal(await page.locator("main button, main form").count(), 0);
  assert.ok(mock.calls.every((c) => c.startsWith("GET ") || c === "POST /graphql"), mock.calls.join("\n"));
  await page.close();
});

test("CX-T073 悪意のある Markdown を描いても何も動かない（script・onerror・javascript:・svg・data:）", async () => {
  const page = await ctx.newPage();
  watch(page);
  await page.goto(`chrome-extension://${id}/board.html`);
  await page.waitForFunction(() => document.body.dataset.state === "done", null, { timeout: 120_000 });
  const box = page.locator('[data-ticket="i0002"] .markdown');
  assert.equal(await box.locator("script, img, svg, iframe, form, style").count(), 0);
  assert.equal(await box.locator("h1").textContent(), "悪意のある本文");
  const hrefs = await box.locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  assert.deepEqual(hrefs.filter((h) => h !== null).sort(), ["https://example.com/ok", "mailto:a@example.com"]);
  // href を外したリンクと onclick があった段落を押しても何も起きない
  for (const a of await box.locator("a:not([href])").all()) await a.click();
  await box.locator("p", { hasText: "style と onclick" }).click();
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned ?? null), null);
  const attrs = await box.locator("*").evaluateAll((els) => els.flatMap((e) => Array.from(e.attributes).map((a) => a.name)));
  assert.ok(!attrs.some((n) => /^on/i.test(n) || n === "style"), attrs.join(","));
  await page.close();
});

test("CX-T074 画面にも Worker にも CSP の違反とエラーが出ていない", () => {
  const csp = problems.filter((p) => /Content Security Policy|unsafe-eval/i.test(p));
  assert.deepEqual(csp, []);
  assert.deepEqual(problems, []);
});
