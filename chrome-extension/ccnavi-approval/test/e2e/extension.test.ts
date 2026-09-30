/**
 * 実機の試験（ADR-0093 段階 1・3、確認事項 4・5）。試験用の通信先（127.0.0.1）で組んだ拡張（dist-e2e/）を
 * Chromium（headless=new）に読み込み、模擬の GitHub を相手に次を確かめる。
 *
 * - 拡張のページの Web Worker で Pyodide が MV3 の CSP（'wasm-unsafe-eval' だけ）の下で起き、ccnavi を import できる
 * - 設定画面で PAT とリポジトリを登録し、ボードが描ける
 * - 悪意のある Markdown を描いても、承認しても何も動かない
 * - ボードから承認と取り下げを書く（`createCommitOnBranch` の 1 コミット）
 * - service worker が PAT の期限のヘッダを CORS に公開されていなくても読み、ボードの帯とバッジで知らせる（確認事項 5。模擬のホストで）
 * - PAT はボードに渡らない
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
// 3 日後に切れる PAT（ヘッダの綴りは GitHub と同じ「UTC」つき）
const EXPIRES = new Date(Date.now() + 3 * 86400000 + 3600000);
mock.expiration = `${EXPIRES.toISOString().slice(0, 19).replace("T", " ")} UTC`;
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

test("CX-T072 ボード: Worker の Pyodide が CSP の下で起き、承認待ちを描く。開いただけでは何も書かない", async () => {
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
  // 段階 3: 承認のボタンは承認待ちのある家族だけ。レビュー済み・「始める」とフォームは出さない
  const actions = await page.locator("main button").evaluateAll((els) => els.map((e) => `${(e as HTMLElement).closest<HTMLElement>("[data-family]")?.dataset.family}:${(e as HTMLElement).dataset.action}`));
  assert.deepEqual(actions, ["i0001:approve", "i0002:approve"]);
  assert.equal(await page.locator("main form").count(), 0);
  // 開いただけでは何も書かない
  assert.ok(mock.calls.every((c) => c.startsWith("GET ") || c === "POST /graphql"), mock.calls.join("\n"));
  assert.equal(mock.commitCalls.length, 0);
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

async function openBoard(): Promise<Page> {
  const page = await ctx.newPage();
  watch(page);
  page.on("dialog", (d) => void (d.type() === "prompt" ? d.accept("押し間違い") : d.accept()));
  await page.goto(`chrome-extension://${id}/board.html`);
  await page.waitForFunction(() => document.body.dataset.state === "done", null, { timeout: 120_000 });
  return page;
}

async function press(page: Page, selector: string): Promise<string> {
  await page.click(selector);
  await page.waitForFunction(() => document.getElementById("result")?.dataset.kind !== undefined && document.body.dataset.state === "done", null, { timeout: 120_000 });
  return `${await page.getAttribute("#result", "data-kind")}: ${await page.textContent("#result")}`;
}

test("CX-T075 ボードで承認すると、親のブランチへ 1 コミットで書き、書いた後の中身を確かめて描き直す", async () => {
  const page = await openBoard();
  const before = mock.head("i0001");
  const said = await press(page, '[data-family="i0001"] button[data-action=approve]');
  assert.match(said, /^written: 承認を書いた/);
  assert.equal(mock.commitCalls.length, 1);
  const call = mock.commitCalls[0];
  assert.deepEqual([call.branch, call.expected, call.result], ["i0001", before, "written"]);
  assert.match(call.headline, /^ccnavi: i0001 を承認（Chrome 拡張 /);
  assert.match(mock.files("i0001")[".ccnavi/approved/doing/i0001.md"], /^ccnavi_approved:/m);
  // 描き直したボード: i0001 は承認待ちから消え、作業中の承認済みチケットに出る
  assert.equal(await page.locator('[data-family="i0001"] .entry').count(), 0);
  assert.equal(await page.locator('[data-family="i0001"] .approved-item[data-ticket="i0001"]').count(), 1);
  await page.close();
});

test("CX-T076 着手前で子の無い承認は、ボードから取り下げられる（承認コミットの親の提案に戻す）", async () => {
  const original = fixture().i0001.files["wip/proposals/todo/i0001.md"];
  // 開発者が子の提案を片付けた（子の提案があれば取り下げは出さない。8.8）
  mock.push("i0001", { "wip/proposals/todo/i0001-01.md": null }, "子の提案を片付ける");
  const page = await openBoard();
  const said = await press(page, '[data-family="i0001"] .approved-item[data-ticket="i0001"] button[data-action=withdraw]');
  assert.match(said, /^written: 取り下げを書いた/);
  const files = mock.files("i0001");
  assert.equal(files["wip/proposals/todo/i0001.md"], original);
  assert.ok(!(".ccnavi/approved/doing/i0001.md" in files));
  const last = JSON.parse(files[".ccnavi/approved/events/i0001.ndjson"].trim().split("\n").pop() as string);
  assert.deepEqual([last.kind, last.via, last.reason, last.actor], ["withdrawn", "chrome", "押し間違い", "alice"]);
  // 取り下げた提案はまた承認待ちに出る
  assert.equal(await page.locator('[data-family="i0001"] .entry[data-ticket="i0001"]').count(), 1);
  await page.close();
});

test("CX-T077 悪意のある Markdown の提案を承認しても何も動かない", async () => {
  const page = await openBoard();
  const said = await press(page, '[data-family="i0002"] button[data-action=approve]');
  assert.match(said, /^written: 承認を書いた/);
  assert.equal(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned ?? null), null);
  assert.match(mock.files("i0002")[".ccnavi/approved/doing/i0002.md"], /悪意のある本文/);
  const box = page.locator('[data-family="i0002"]');
  assert.equal(await box.locator("script, img, svg, iframe, form, style").count(), 0);
  await page.close();
});

test("CX-T078 PAT の期限: service worker が応答ヘッダ（CORS に公開していない）から読み、切れる 7 日前からボードの帯とバッジで知らせる", async () => {
  const page = await openBoard();
  const banner = page.locator('#banner [data-level="soon"]');
  assert.equal(await banner.count(), 1);
  assert.match((await banner.textContent()) ?? "", /あと 3 日で切れる/);
  const [sw] = ctx.serviceWorkers();
  const badge = await sw.evaluate(() => (globalThis as unknown as { chrome: { action: { getBadgeText(d: object): Promise<string> } } }).chrome.action.getBadgeText({}));
  assert.equal(badge, "3d");
  await page.close();
});

test("CX-T079 PAT はボードに渡らない（画面にも、service worker の答えにも無い）", async () => {
  const page = await openBoard();
  assert.ok(!(await page.content()).includes(TOKEN));
  const answers = await page.evaluate(async () => {
    const send = (m: unknown) => (globalThis as unknown as { chrome: { runtime: { sendMessage(m: unknown): Promise<unknown> } } }).chrome.runtime.sendMessage(m);
    return JSON.stringify([
      await send({ kind: "token.get", host: "github.com" }),
      await send({ kind: "token.status", host: "github.com" }),
      await send({ kind: "host", host: "github.com", op: "viewer", args: ["acme", "widgets"] }),
    ]);
  });
  assert.ok(!answers.includes(TOKEN), answers);
  await page.close();
});

test("CX-T074 画面にも Worker にも CSP の違反とエラーが出ていない", () => {
  const csp = problems.filter((p) => /Content Security Policy|unsafe-eval/i.test(p));
  assert.deepEqual(csp, []);
  assert.deepEqual(problems, []);
});
