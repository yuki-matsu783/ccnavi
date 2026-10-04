/**
 * 実機の試験。Pyodide が MV3 の CSP で動くかと、PAT の期限の応答ヘッダを拡張の fetch で読めるかも見る。試験用の通信先（127.0.0.1）で組んだ拡張（dist-e2e/）を
 * Chromium（headless=new）に読み込み、模擬の GitHub を相手に次を確かめる。
 *
 * - 拡張のページの Web Worker で Pyodide が MV3 の CSP（'wasm-unsafe-eval' だけ）の下で起動し、ccnavi を import できる
 * - 設定画面で PAT とリポジトリを登録し、ボードが描ける
 * - 悪意のある Markdown を描いても、承認しても何も動かない
 * - ボードから承認と取り下げを書く（`createCommitOnBranch` の 1 コミット）
 * - 依頼済みのフェーズに MR のスレッドを出し（悪意のある本文でも何も動かず、隠れない）、
 *   ボードからレビュー済みのマーカーを書く
 * - service worker が PAT の期限のヘッダを CORS に公開されていなくても読み、ボードの帯とバッジで知らせる（模擬のホストで）
 * - PAT はボードに渡らない
 * - 通信先にセルフホストの GitLab（模擬。127.0.0.1:18788）を足したビルドで、GitLab のリポジトリを登録し、
 *   ボードを描き（スレッドの悪意のある本文でも何も動かない）、Commits API で承認を書き、「始める」で issue から
 *   親のブランチを作る。PAT は画面に渡らない
 *
 * `node scripts/test.js --e2e` で回す。
 */
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium, type BrowserContext, type Page } from "playwright-core";

import { fixture, requestedMark, reviewFamilyFiles } from "../fixtures/repo.js";
import { loadScene } from "../helpers/host-fixture.js";
import { loadGitLabScene } from "../helpers/gitlab-fixture.js";
import { LOGIN, MockGitHub, TOKEN } from "../helpers/mock-github.js";
import { MockGitLab } from "../helpers/mock-gitlab.js";
import { HERE } from "../helpers/python.js";

const DIST = path.join(HERE, "dist-e2e");
const PORT = 18787;
const GITLAB_PORT = 18788;
const lab = new MockGitLab(fixture(), "main", new Date());
let closeLab: () => Promise<void>;

let ctx: BrowserContext;
let id: string;
let closeServer: () => Promise<void>;
let profile: string;
const mock = new MockGitHub(fixture(), "main", new Date());
mock.apiBase = `http://127.0.0.1:${18787}`;
// 3 日後に切れる PAT（ヘッダの表記は GitHub と同じ「UTC」つき）
const EXPIRES = new Date(Date.now() + 3 * 86400000 - 3600000);
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
  closeLab = await lab.serve(GITLAB_PORT);
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
  await closeLab?.();
  fs.rmSync(profile, { recursive: true, force: true });
});

test("CX-T070 組んだ manifest の CSP は 'wasm-unsafe-eval' だけを足し、通信先は試験の 127.0.0.1（GitHub と、足したセルフホストの GitLab）だけ", () => {
  const m = JSON.parse(fs.readFileSync(path.join(DIST, "manifest.json"), "utf8"));
  assert.equal(
    m.content_security_policy.extension_pages,
    "script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; img-src 'self'; form-action 'none'; base-uri 'none'; connect-src 'self' http://127.0.0.1:18787 http://127.0.0.1:18788",
  );
  assert.deepEqual(m.host_permissions, ["http://127.0.0.1:18787/*", "http://127.0.0.1:18788/*"]);
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
  // 承認のボタンは承認待ちのある親子チケットだけ。レビュー済みとフォームは出さない。「始める」は
  // issue を押してから読む（ボードを開くたびには読まない）
  const actions = await page.locator("main button").evaluateAll((els) => els.map((e) => `${(e as HTMLElement).closest<HTMLElement>("[data-family]")?.dataset.family ?? "-"}:${(e as HTMLElement).dataset.action}`));
  assert.deepEqual(actions, ["i0001:approve", "i0002:approve", "-:issues"]);
  assert.equal(await page.locator("main form").count(), 0);
  // 承認の画面の本文は開いた形でボタンの上に見えている
  assert.ok(await page.locator('[data-family="i0001"] [data-testid=screen] pre').isVisible());
  const above = await page.evaluate(() => {
    const box = document.querySelector('[data-family="i0001"]') as HTMLElement;
    const screen = box.querySelector("[data-testid=screen]") as HTMLElement;
    const button = box.querySelector("button[data-action=approve]") as HTMLElement;
    return screen.getBoundingClientRect().top < button.getBoundingClientRect().top;
  });
  assert.ok(above);
  // 開いただけでは何も書かない
  assert.ok(mock.calls.every((c) => c.startsWith("GET ") || c === "POST /graphql"), mock.calls.join("\n"));
  assert.ok(!mock.calls.some((c) => c.includes("/issues")), mock.calls.join("\n"));
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
  // 開発者が子の提案を片付けた（子の提案があれば取り下げは出さない）
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

test("CX-T138 レビュー済み: スレッドの悪意のある本文を描いても何も動かず隠れない。解決したらボードからマーカーを 1 コミットで書く", async () => {
  mock.branch("i0004", "main");
  const at = mock.push("i0004", reviewFamilyFiles("i0004"), "作業とレビュー待ちの子");
  mock.push("i0004", { ".ccnavi/approved/phases/i0004/1.requested": requestedMark(at) }, "ccnavi: レビューを依頼した");
  mock.attachScene("i0004", loadScene("hostile"));
  let page = await openBoard();
  const box = page.locator('[data-family="i0004"] .review[data-phase="1"]');
  assert.equal(await box.locator(".thread").count(), 8);
  assert.equal(await box.locator("script, img, svg, iframe, form, style, details, summary, font").count(), 0);
  const text = (await box.textContent()) ?? "";
  for (const seen of ["畳んで隠した指摘", "〈HTML コメント: 隠したつもりの指摘〉", "hidden で隠した指摘", "白文字の指摘", "style で隠した指摘"]) {
    assert.ok(text.includes(seen), seen);
  }
  for (const seen of ["畳んで隠した指摘", "hidden で隠した指摘", "style で隠した指摘"]) {
    assert.ok(await box.getByText(seen).first().isVisible(), seen);
  }
  for (const a of await box.locator(".markdown a").all()) await a.click();
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned ?? null), null);
  assert.match((await box.locator(".problems").textContent()) ?? "", /未解決のスレッドが 8 件残っている/);
  assert.equal(await box.locator("button").count(), 0);
  await page.close();

  mock.attachScene("i0004", loadScene("resolved"));
  page = await openBoard();
  const said = await press(page, '[data-family="i0004"] button[data-action=review]');
  assert.match(said, /^written: レビュー済みを書いた/);
  const mark = JSON.parse(mock.files("i0004")[".ccnavi/approved/phases/i0004/1.reviewed"]);
  assert.deepEqual([mark.mr, mark.actor, mark.via], [42, LOGIN, "chrome"]);
  assert.equal(await page.locator('[data-family="i0004"] .review').count(), 0);
  assert.ok(!(await page.content()).includes(TOKEN));
  const copy = await page.evaluate(async () => {
    const send = (m: unknown) => (globalThis as unknown as { chrome: { runtime: { sendMessage(m: unknown): Promise<unknown> } } }).chrome.runtime.sendMessage(m);
    return JSON.stringify(await send({ kind: "host", host: "github.com", op: "reviewCopy", args: ["acme", "widgets", "i0004"] }));
  });
  assert.ok(copy.includes('"ok":true') && !copy.includes(TOKEN), copy);
  await page.close();
});

const GL_REPO = '[data-repo="gitlab.e2e/acme/widgets"]';

test("CX-T159 セルフホストの GitLab（足した通信先）: 登録してボードを描き、Commits API で承認を書く。スレッドの悪意のある本文でも何も動かず、PAT は画面に渡らない", async () => {
  const setup = await ctx.newPage();
  watch(setup);
  await setup.goto(`chrome-extension://${id}/options.html`);
  await setup.selectOption("#repo-host", "gitlab.e2e");
  await setup.fill("#repo-form [name=owner]", "acme");
  await setup.fill("#repo-form [name=repo]", "widgets");
  await setup.click("#repo-form button[type=submit]");
  await setup.waitForFunction(() => document.getElementById("repo-list")?.textContent?.includes("gitlab.e2e/acme/widgets"));
  await setup.selectOption("#token-host", "gitlab.e2e");
  await setup.fill("#token-value", TOKEN);
  await setup.click("#token-form button[type=submit]");
  await setup.waitForFunction(() => document.getElementById("token-list")?.textContent?.includes("gitlab.e2e（GitLab）: 登録済み"));
  await setup.close();

  lab.branch("i0004", "main");
  const at = lab.push("i0004", reviewFamilyFiles("i0004"), "作業とレビュー待ちの子");
  lab.push("i0004", { ".ccnavi/approved/phases/i0004/1.requested": requestedMark(at, 7, "gitlab", "201") }, "ccnavi: レビューを依頼した");
  lab.attachGitLabScene("i0004", loadGitLabScene("hostile"));
  const page = await openBoard();
  const repo = page.locator(`section${GL_REPO}`);
  const families = await repo.locator("[data-family]").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.family));
  assert.deepEqual(families, ["i0001", "i0002", "i0004"]);
  const review = repo.locator('[data-family="i0004"] .review[data-phase="1"]');
  assert.match((await review.locator("h4").textContent()) ?? "", /マージリクエスト !7/);
  assert.equal(await review.locator(".thread").count(), 8);
  assert.equal(await review.locator("script, img, svg, iframe, form, style, details, summary").count(), 0);
  for (const a of await review.locator(".markdown a").all()) await a.click();
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned ?? null), null);
  assert.ok((await review.textContent())?.includes("隠れた本文"));

  const before = lab.head("i0001");
  const said = await press(page, `${GL_REPO} [data-family="i0001"] button[data-action=approve]`);
  assert.match(said, /^written: 承認を書いた/);
  assert.equal(lab.glCommits.length, 1);
  assert.deepEqual([lab.glCommits[0].branch, lab.glCommits[0].parent, lab.glCommits[0].result], ["i0001", before, "written"]);
  assert.match(lab.files("i0001")[".ccnavi/approved/doing/i0001.md"], /^ccnavi_approved:/m);
  assert.ok(lab.calls.some((c) => c.endsWith("/personal_access_tokens/self")));
  assert.ok(!(await page.content()).includes(TOKEN));
  await page.close();
});

test("CX-T160 「始める」: ボードで issue を読み、押すと issue から決めた名前（i<番号>）の親のブランチを統合先の先頭に作る。閉じた識別子の issue は作らない", async () => {
  lab.issues.push({ number: 12, title: "新しい機能" }, { number: 5, title: "閉じた親子チケットと重なる" });
  const page = await openBoard();
  await page.click(`${GL_REPO} [data-testid=start] button[data-action=issues]`);
  await page.waitForSelector(`${GL_REPO} [data-testid=start] li[data-issue="12"]`);
  const said = await press(page, `${GL_REPO} [data-testid=start] li[data-issue="12"] button[data-action=start]`);
  assert.match(said, /^written: 親のブランチ i0012 を作った/);
  assert.deepEqual(lab.createdBranches, [{ name: "i0012", sha: lab.head("main") }]);
  await page.close();
  const again = await openBoard();
  await again.click(`${GL_REPO} [data-testid=start] button[data-action=issues]`);
  await again.waitForSelector(`${GL_REPO} [data-testid=start] li[data-issue="5"]`);
  const refused = await press(again, `${GL_REPO} [data-testid=start] li[data-issue="5"] button[data-action=start]`);
  assert.match(refused, /^refused: 始めなかった: i0005 は統合先 main の done\/ で閉じている/);
  assert.equal(lab.createdBranches.length, 1);
  assert.ok(!(await again.content()).includes(TOKEN));
  await again.close();
});

test("CX-T173 「要確認」: 描いた後に要確認が保存された親子チケットは、押しても書かずに「要確認のまま」と言う。外すのは確認を挟み、断れば残る", async () => {
  const key = "gitlab.e2e/acme/widgets";
  const page = await openBoard();
  const approve = `${GL_REPO} [data-family="i0002"] button[data-action=approve]`;
  assert.equal(await page.locator(approve).count(), 1);
  // ボタンを描いた後に（別のタブで）要確認が付いた: 押す前にも見て、書かない
  await page.evaluate(async (k) => {
    const c = (globalThis as unknown as { chrome: { storage: { local: { set(v: object): Promise<void> } } } }).chrome;
    await c.storage.local.set({ attention: { [k]: { i0002: "試験で付けた要確認" } } });
  }, key);
  const before = lab.glCommits.length;
  const said = await press(page, approve);
  assert.match(said, /^refused: i0002 は要確認のまま/);
  assert.equal(lab.glCommits.length, before);
  await page.close();

  // 開き直すとボタンは出ず、外すボタンだけ。確認を断れば残る
  const again = await ctx.newPage();
  watch(again);
  let answer = false;
  const asked: string[] = [];
  again.on("dialog", (d) => {
    asked.push(d.message());
    void (answer ? d.accept() : d.dismiss());
  });
  await again.goto(`chrome-extension://${id}/board.html`);
  await again.waitForFunction(() => document.body.dataset.state === "done", null, { timeout: 120_000 });
  const box = again.locator(`${GL_REPO} [data-family="i0002"]`);
  assert.deepEqual(await box.locator("button").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.action)), ["dismiss"]);
  assert.match((await box.textContent()) ?? "", /ほかの承認者には見えない/);
  await box.locator("button[data-action=dismiss]").click();
  await again.waitForTimeout(300);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /要確認を外す/);
  const kept = await again.evaluate(
    async () => (await (globalThis as unknown as { chrome: { storage: { local: { get(k: string): Promise<Record<string, unknown>> } } } }).chrome.storage.local.get("attention")).attention,
  );
  assert.equal((kept as Record<string, Record<string, string>>)[key]?.i0002, "試験で付けた要確認");
  // 確認を受ければ外れ、ボタンが戻る
  answer = true;
  await box.locator("button[data-action=dismiss]").click();
  await again.waitForSelector(approve);
  const gone = await again.evaluate(
    async () => (await (globalThis as unknown as { chrome: { storage: { local: { get(k: string): Promise<Record<string, unknown>> } } } }).chrome.storage.local.get("attention")).attention,
  );
  assert.equal((gone as Record<string, Record<string, string>>)[key]?.i0002, undefined);
  assert.equal(lab.glCommits.length, before);
  await again.close();
});

test("CX-T074 画面にも Worker にも CSP の違反とエラーが出ていない", () => {
  const csp = problems.filter((p) => /Content Security Policy|unsafe-eval/i.test(p));
  assert.deepEqual(csp, []);
  assert.deepEqual(problems, []);
});
