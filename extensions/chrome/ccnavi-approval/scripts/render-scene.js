// 実リポジトリの状態（ブランチ・コミット・提案ファイル）を模擬のホストに載せ、拡張を読み込んだ Chromium にボードを描かせて、
// PNG・テキスト・DOM の要約に残す開発用ツール。ccnavi の配布物には含めない。
//
//   node scripts/render-scene.js --repo <ccnavi を配布済みのリポジトリ> --out <出力ディレクトリ> [--host github|gitlab] [--width 1400]
//
// 前提（このスクリプトからはビルドしない）: `node scripts/test.js --e2e` と同じ組み立て（dist-e2e/ と out/）が済んでいること。
//   node scripts/test.js --e2e   （または dist-e2e/ と out/ だけ要るなら、その中の build と tsc）
// ブラウザは playwright-core と PLAYWRIGHT_BROWSERS_PATH の Chromium（`playwright install` はしない）。版が違うときは
// CCNAVI_CHROMIUM に実行ファイルのパスを指定する。
//
// 通信先は 127.0.0.1:18787（GitHub）・18788（GitLab）で固定（拡張の host_permissions と一致が要る）。同時に 2 本は回せない。
// PAT は模擬の固定のダミー。実際の GitHub / GitLab へは繋がない。
//
// 出力（<out>/）: board.png（fullPage）・board.txt（body の見えているテキスト）・board.dom.json（親子のチケットごとのバッジと操作）・
// board.log（ページと service worker のコンソール・ページエラー。リクエストは記録しない）。
//
// 終了コード: 0 描けた / 1 描けなかった・失敗の条件に当たった / 2 使い方の誤り / 3 環境が揃っていない。
//
// `--repo` の中身は読むだけ。`--out` が `--repo` の下を指すときは 2 で止める（リポジトリを汚さないため）。
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(HERE, "dist-e2e");
const PORTS = { github: 18787, gitlab: 18788 };
const HOST_ID = { github: "github.com", gitlab: "gitlab.e2e" };
/** 完了の待ち（Pyodide の起動を含む。e2e の待ちに合わせる） */
const RENDER_TIMEOUT_MS = 120_000;
const VIEWPORT_HEIGHT = 900;
const OUTPUT_NAMES = ["board.png", "board.txt", "board.dom.json", "board.log"];

async function load(rel) {
  const file = path.join(HERE, "out", "test", "helpers", rel);
  if (!fs.existsSync(file)) {
    throw Object.assign(new Error(`out/ がありません（${rel}）。node scripts/test.js --e2e で組み立てる`), { notReady: true });
  }
  return import(pathToFileURL(file).href);
}

function portFree(port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once("error", () => resolve(false));
    probe.listen(port, "127.0.0.1", () => probe.close(() => resolve(true)));
  });
}

/**
 * service worker のコンソールと例外を、DevTools の WebSocket で直接拾う（playwright-core 1.56 の context には service worker の
 * console が来ないため）。`Runtime.enable` は、つなぐ前の console も送り直す。記録は `push` へ渡す。つないだ数と止める関数を返す。
 */
async function watchServiceWorkers(profile, push) {
  const portFile = path.join(profile, "DevToolsActivePort");
  const sockets = new Map();
  let stopped = false;
  const attach = (target) => {
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    sockets.set(target.id, ws);
    ws.addEventListener("open", () => ws.send(JSON.stringify({ id: 1, method: "Runtime.enable" })));
    ws.addEventListener("message", (event) => {
      const m = JSON.parse(String(event.data));
      if (m.method === "Runtime.consoleAPICalled") {
        const text = m.params.args.map((a) => (a.value !== undefined ? String(a.value) : a.description ?? a.type)).join(" ");
        push({ kind: "worker-console", type: m.params.type, text });
      } else if (m.method === "Runtime.exceptionThrown") {
        const d = m.params.exceptionDetails;
        push({ kind: "worker-error", text: d.exception?.description ?? d.text });
      }
    });
  };
  const poll = async () => {
    if (stopped || !fs.existsSync(portFile)) return;
    const port = fs.readFileSync(portFile, "utf8").split("\n")[0].trim();
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      for (const t of targets) if (t.type === "service_worker" && !sockets.has(t.id)) attach(t);
    } catch {
      // ブラウザが閉じつつある
    }
  };
  const timer = setInterval(poll, 200);
  await poll();
  return {
    attached: () => sockets.size,
    stop: () => {
      stopped = true;
      clearInterval(timer);
      for (const ws of sockets.values()) ws.close();
    },
  };
}

/** 環境が揃っていないときに、出力の末尾へ足す。直し方の書いた文書（frontmatter が無く、索引には載らない） */
const NOT_READY_HINT =
  "案内: 前提と直し方は extensions/chrome/ccnavi-approval/VERIFY.md の「4.0 ツールで描いて確かめる」と README.md を見る";

async function main(argv) {
  let lib;
  try {
    lib = await load("render-scene-lib.js");
  } catch (error) {
    console.error(`render-scene: ${error.message}`);
    console.error(NOT_READY_HINT);
    return 3;
  }
  const { EXIT, ToolError, USAGE, parseArgs, isInside, failuresOf, logLine, redact } = lib;
  try {
    const parsed = parseArgs(argv);
    if (!parsed.ok) throw new ToolError(EXIT.USAGE, `${parsed.error}\n${USAGE}`);
    const options = parsed.options;
    if (!fs.existsSync(options.repo) || !fs.statSync(options.repo).isDirectory()) throw new ToolError(EXIT.USAGE, `--repo がありません: ${options.repo}`);
    if (fs.existsSync(options.out) && !fs.statSync(options.out).isDirectory()) throw new ToolError(EXIT.USAGE, `--out がディレクトリではありません: ${options.out}`);
    if (isInside(options.out, options.repo)) throw new ToolError(EXIT.USAGE, `--out が --repo の下です（リポジトリを汚さないため、外のパスを指定する）: ${options.out}`);

    // 前回の出力を先に消す。環境や入力で途中で止まっても、古い PNG が残って成功と取り違えられないように
    fs.mkdirSync(options.out, { recursive: true });
    for (const name of OUTPUT_NAMES) fs.rmSync(path.join(options.out, name), { force: true });

    if (!fs.existsSync(path.join(DIST, "manifest.json"))) {
      throw new ToolError(EXIT.NOT_READY, "dist-e2e/ がありません。node scripts/test.js --e2e で組み立てる（このスクリプトからはビルドしない）");
    }
    let converted;
    let mocks;
    let summary;
    let playwright;
    try {
      converted = await load("repo-to-fixture.js");
      mocks = { github: (await load("mock-github.js")).MockGitHub, gitlab: (await load("mock-gitlab.js")).MockGitLab, token: (await load("mock-github.js")).TOKEN };
      summary = await import(pathToFileURL(path.join(HERE, "out", "src", "core", "dom-summary.js")).href);
      playwright = await import("playwright-core");
    } catch (error) {
      throw new ToolError(EXIT.NOT_READY, error.notReady ? error.message : `部品を読めません: ${error.message}`);
    }
    const { chromium } = playwright;
    const executable = process.env.CCNAVI_CHROMIUM || chromium.executablePath();
    if (!fs.existsSync(executable)) {
      throw new ToolError(EXIT.NOT_READY, `Chromium がありません: ${executable}（PLAYWRIGHT_BROWSERS_PATH を見る。playwright install はしない。版が違うなら CCNAVI_CHROMIUM に実行ファイルのパスを指定する）`);
    }
    const port = PORTS[options.host];
    if (!(await portFree(port))) throw new ToolError(EXIT.NOT_READY, `127.0.0.1:${port} が使用中です（別の render-scene や e2e が動いていないか）`);

    let fixture;
    try {
      fixture = converted.repoToFixture(path.resolve(options.repo));
    } catch (error) {
      throw new ToolError(EXIT.FAILED, `--repo を模擬に載せられません: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`);
    }
    const skipped = Object.entries(fixture.skipped).map(([why, paths]) => `${why} ${paths.length} 件`);
    if (skipped.length > 0) console.log(`載せなかったもの: ${skipped.join("、")}`);

    const mock = new mocks[options.host](fixture.branches, fixture.defaultBranch, fixture.now);
    mock.apiBase = options.host === "github" ? `http://127.0.0.1:${port}` : `http://127.0.0.1:${port}/api/v4`;
    const closeServer = await mock.serve(port);
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-render-scene-"));
    const entries = [];
    const secrets = [mocks.token];
    let ctx;
    let workers = { attached: () => 0, stop: () => {} };
    let timedOut = false;
    let leaked = "";
    try {
      try {
        ctx = await chromium.launchPersistentContext(profile, {
          ...(process.env.CCNAVI_CHROMIUM ? { executablePath: executable } : { channel: "chromium" }),
          headless: true,
          viewport: { width: options.width, height: VIEWPORT_HEIGHT },
          args: [`--disable-extensions-except=${DIST}`, `--load-extension=${DIST}`, "--remote-debugging-port=0"],
        });
      } catch (error) {
        throw new ToolError(EXIT.NOT_READY, `Chromium を起動できません: ${String(error.message).split("\n")[0]}`);
      }
      workers = await watchServiceWorkers(profile, (entry) => entries.push(entry));
      const watch = (page) => {
        page.on("console", (m) => entries.push({ kind: "console", type: m.type(), text: m.text(), url: m.location().url }));
        page.on("pageerror", (e) => entries.push({ kind: "pageerror", text: e.message }));
      };
      let [sw] = ctx.serviceWorkers();
      if (!sw) sw = await ctx.waitForEvent("serviceworker");
      const id = new URL(sw.url()).host;

      // 設定画面で PAT とリポジトリを登録する（e2e の CX-T071 / CX-T159 と同じ手順）
      const setup = await ctx.newPage();
      watch(setup);
      await setup.goto(`chrome-extension://${id}/options.html`);
      if (options.host === "gitlab") await setup.selectOption("#repo-host", HOST_ID.gitlab);
      await setup.fill("#repo-form [name=owner]", "acme");
      await setup.fill("#repo-form [name=repo]", "widgets");
      await setup.click("#repo-form button[type=submit]");
      await setup.waitForSelector("#repo-list li");
      if (options.host === "gitlab") await setup.selectOption("#token-host", HOST_ID.gitlab);
      await setup.fill("#token-value", mocks.token);
      await setup.click("#token-form button[type=submit]");
      await setup.waitForFunction(() => document.getElementById("token-list")?.textContent?.includes("登録済み"));
      await setup.close();

      const page = await ctx.newPage();
      watch(page);
      await page.goto(`chrome-extension://${id}/board.html`);
      try {
        await page.waitForFunction(() => document.body.dataset.state !== "loading", null, { timeout: RENDER_TIMEOUT_MS });
      } catch {
        timedOut = true;
      }
      await page.screenshot({ path: path.join(options.out, "board.png"), fullPage: true });
      const text = `${await page.evaluate(() => document.body.innerText)}\n`;
      const dom = `${JSON.stringify(await page.evaluate(`(${summary.summarizeBoard.toString()})(document)`), null, 2)}\n`;
      fs.writeFileSync(path.join(options.out, "board.txt"), text);
      fs.writeFileSync(path.join(options.out, "board.dom.json"), dom);
      // PAT が画面の文字や要約に出ていないか（出ていたら失敗。PNG は文字を読めないので対象外）
      leaked = [text, dom].some((t) => secrets.some((s) => t.includes(s))) ? "PAT が board.txt か board.dom.json に出ている" : "";
      // service worker のエラーを拾えていないのに成功にしない（監視が黙って無効になる穴を塞ぐ）
      if (workers.attached() === 0) entries.push({ kind: "worker-error", text: "service worker を監視できなかった（DevTools に接続できていない）" });
      const summarized = JSON.parse(dom);
      // 画面に通知（読み込みの失敗など）が出ていたら 1 行で知らせる。終了コードは変えない（失敗の画面を描くのもこのツールの用途）
      const notices = [...summarized.repoNotices.map((n) => n.text), ...summarized.families.flatMap((f) => f.notices.map((t) => `${f.name}: ${t}`))];
      if (notices.length > 0) console.log(`画面に通知があります（${notices.length} 件）: ${redact(notices[0], secrets).slice(0, 120)}`);
      const state = summarized.state;
      if (!timedOut && state !== "done") entries.push({ kind: "pageerror", text: `画面の状態が ${state}（done ではない）。${redact(summarized.status, secrets)}` });
    } finally {
      workers.stop();
      await ctx?.close();
      await closeServer();
      fs.rmSync(profile, { recursive: true, force: true });
      // 描画の途中で投げても、集めた記録は残す（調べたいのはそのとき）
      fs.writeFileSync(path.join(options.out, "board.log"), entries.map((e) => logLine(e, secrets)).join("\n") + (entries.length > 0 ? "\n" : ""));
    }

    const log = path.join(options.out, "board.log");
    if (timedOut) throw new ToolError(EXIT.FAILED, `描画が ${RENDER_TIMEOUT_MS / 1000} 秒で終わりませんでした（読み込み中のまま）。${log} を見る`);
    if (leaked) throw new ToolError(EXIT.FAILED, leaked);
    const failures = failuresOf(entries);
    if (failures.length > 0) throw new ToolError(EXIT.FAILED, `${failures.length} 件の失敗: ${logLine(failures[0], secrets)}（${log}）`);
    console.log(`描きました: ${path.join(options.out, "board.png")}`);
    return EXIT.OK;
  } catch (error) {
    if (error instanceof ToolError) {
      console.error(`render-scene: ${error.message}`);
      if (error.code === EXIT.NOT_READY) {
        console.error(NOT_READY_HINT);
      }
      return error.code;
    }
    console.error(`render-scene: ${redact(error && error.stack ? error.stack : String(error), [])}`);
    return 1;
  }
}

process.exitCode = await main(process.argv.slice(2));
