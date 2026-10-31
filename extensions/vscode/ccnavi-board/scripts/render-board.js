// VS Code なしで Chromium にボードの画面を描き、PNG とテキストに残す開発用ツール。
// ccnavi の配布物には含めない（package.sh の対象外）。
//
//   node scripts/render-board.js (--repo <ccnavi 配布済みのリポジトリ> | --fixture <BoardJson ファイル>) --out <出力ディレクトリ>
//        [--appearance vscode|claude-light|claude-dark] [--width 1400]
//
// 前提: `pnpm install && pnpm run compile`（このスクリプトからはビルドしない）。
// ブラウザは playwright-core と PLAYWRIGHT_BROWSERS_PATH の Chromium（Chrome 拡張の試験と同じやり方。
// `playwright install` はしない。版が違うときは CCNAVI_CHROMIUM に実行ファイルのパスを指定する）。
//
// 出力（<out>/）: board.png（fullPage）・board.txt（body の見えているテキスト）・board.html（入れ物の HTML）・
// board.log（ページのコンソール・ページエラー・CSP 違反・画面が拡張ホストへ送ったメッセージ。アプリのログではない）。
//
// 終了コード: 0 描けた / 1 描けなかった・失敗の条件に当たった / 2 使い方の誤り / 3 環境が揃っていない
// （`scripts/test-groups.js` の NOT_READY と同じ意味）。
//
// `--repo` の中身は読むだけ。`--out` が `--repo` の下を指すときは 2 で止める（リポジトリを汚さないため）。
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { installVsCodeStub } = require("./lib/vscode-stub.js");

const ROOT = path.resolve(__dirname, "..");

const EXIT = { OK: 0, FAILED: 1, USAGE: 2, NOT_READY: 3 };

/** 環境が揃っていないときに、出力の末尾へ足す。直し方を書いた文書と、その索引の引き方 */
const NOT_READY_HINT =
  "案内: 前提と直し方は extensions/vscode/ccnavi-board/docs/design/development.md の「前提と依存」（ccnavi --docs --keyword render-board でも引ける）";

const DEFAULT_APPEARANCE = "claude-light";
const DEFAULT_WIDTH = 1400;
const MIN_WIDTH = 320;
const MAX_WIDTH = 3000;
const VIEWPORT_HEIGHT = 900;
/** 描画の完了（読み込み中の表示が消えること）を待つ上限 */
const RENDER_TIMEOUT_MS = 15000;

const USAGE = [
  "使い方: node scripts/render-board.js (--repo <リポジトリ> | --fixture <BoardJson ファイル>) --out <出力ディレクトリ>",
  "                                     [--appearance vscode|claude-light|claude-dark] [--width 1400]",
  "  --repo        ccnavi を配布済みのリポジトリ。ccnavi --explain --json で中身を得る（読むだけ）",
  "  --fixture     BoardJson のファイル（test/fixtures/board.json など）。--repo とどちらか一方",
  "  --out         出力ディレクトリ（無ければ作る。リポジトリの下は不可）",
  `  --appearance  見た目。既定は ${DEFAULT_APPEARANCE}（vscode は VS Code のテーマ変数を使うので、Chromium だけでは色が欠ける）`,
  `  --width       ページの幅（${MIN_WIDTH}〜${MAX_WIDTH} の整数。既定 ${DEFAULT_WIDTH}。高さは全体が入るまで伸ばす）`,
].join("\n");

/** 終了コードを持つ失敗。main が受けて理由を 1 行出す */
class ToolError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

/**
 * 引数を解く（形だけ。`--appearance` の値は out/ の表で `validateAppearance` が確かめる）。
 * 誤りは `{ ok: false, error }`（終了コード 2）。
 */
function parseArgs(argv) {
  const options = { repo: undefined, fixture: undefined, out: undefined, appearance: DEFAULT_APPEARANCE, width: DEFAULT_WIDTH };
  const valued = { "--repo": "repo", "--fixture": "fixture", "--out": "out", "--appearance": "appearance", "--width": "width" };
  const seen = new Set();
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const key = valued[flag];
    if (key === undefined) {
      return { ok: false, error: `知らない引数: ${flag}` };
    }
    if (seen.has(flag)) {
      return { ok: false, error: `${flag} が 2 度ある` };
    }
    seen.add(flag);
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--") || value === "") {
      return { ok: false, error: `${flag} に値が要る` };
    }
    options[key] = value;
    i += 1;
  }
  if ((options.repo === undefined) === (options.fixture === undefined)) {
    return { ok: false, error: "--repo と --fixture はどちらか一方だけ指す" };
  }
  if (options.out === undefined) {
    return { ok: false, error: "--out が要る" };
  }
  if (seen.has("--width")) {
    if (!/^[0-9]+$/.test(options.width)) {
      return { ok: false, error: `--width は整数（${MIN_WIDTH}〜${MAX_WIDTH}）: ${options.width}` };
    }
    const width = Number(options.width);
    if (width < MIN_WIDTH || width > MAX_WIDTH) {
      return { ok: false, error: `--width は ${MIN_WIDTH}〜${MAX_WIDTH}: ${options.width}` };
    }
    options.width = width;
  }
  return { ok: true, options };
}

/** `--appearance` の値を、拡張が持つ表（`APPEARANCES`）で確かめる。表を二重に持たない */
function validateAppearance(value, appearances) {
  if (appearances.includes(value)) {
    return { ok: true };
  }
  return { ok: false, error: `--appearance は ${appearances.join("|")} のどれか: ${value}` };
}

/** 存在する一番深い祖先を実際のパスにそろえ、残りはそのまま付ける（出力先はまだ無いことがある） */
function realPathOf(target) {
  const absolute = path.resolve(target);
  const rest = [];
  let current = absolute;
  for (;;) {
    try {
      return path.join(fs.realpathSync(current), ...rest);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) {
        return absolute;
      }
      rest.unshift(path.basename(current));
      current = parent;
    }
  }
}

/**
 * `child` が `parent` と同じか、その下か。両方を実際のパスに直してから、`fold`（`foldPath`。大文字小文字と NFC/NFD を
 * 区別しない）で比べる。区切りは `\` と `/` の両方を `/` に揃える。
 */
function isInside(child, parent, fold) {
  const norm = (p) => fold(realPathOf(p).replace(/\\/g, "/")).replace(/\/+$/, "");
  const c = norm(child);
  const p = norm(parent);
  return c === p || c.startsWith(`${p}/`);
}

/** コンソールの `error` のうち、数えないもの（favicon の取得失敗） */
function isIgnorableConsole(entry) {
  return /favicon/i.test(entry.text) || /favicon/i.test(entry.url || "");
}

/**
 * ページの記録から、失敗の条件に当たるものを取り出す（ページエラー・コンソールの error・CSP 違反）。
 * 記録は `{ kind: "console" | "pageerror" | "csp" | "posted", type?, text, url? }`。
 */
function failuresOf(entries) {
  return entries.filter((entry) => {
    if (entry.kind === "pageerror" || entry.kind === "csp") {
      return true;
    }
    return entry.kind === "console" && entry.type === "error" && !isIgnorableConsole(entry);
  });
}

/** board.log の 1 行 */
function logLine(entry) {
  const text = entry.text.replace(/\r?\n/g, "\\n");
  if (entry.kind === "console") {
    return `[console.${entry.type}] ${text}`;
  }
  return `[${entry.kind}] ${text}`;
}

/** 失敗のあるなしを終了コードにする */
function codeForFailures(failures) {
  return failures.length === 0 ? EXIT.OK : EXIT.FAILED;
}

/** out/ のバンドルを読む。無ければ「環境が揃っていない」（3） */
function loadOut() {
  try {
    const out = path.join(ROOT, "out", "src");
    const assets = require(path.join(out, "webview-asset.js"));
    return {
      loadBoard: require(path.join(out, "ccnavi.js")).loadBoard,
      parseBoardJson: require(path.join(out, "core", "model.js")).parseBoardJson,
      buildBoard: require(path.join(out, "core", "board.js")).buildBoard,
      renderBoardPage: require(path.join(out, "core", "render.js")).renderBoardPage,
      APPEARANCES: require(path.join(out, "core", "appearance.js")).APPEARANCES,
      foldPath: require(path.join(out, "core", "handoffmodel.js")).foldPath,
      // バンドルが無いと投げる（「pnpm run compile を実行してください」）。ここで読んで確かめる
      script: assets.webviewScript("board"),
      style: assets.webviewStyle("board"),
    };
  } catch (error) {
    if (error && error.code === "MODULE_NOT_FOUND") {
      throw new ToolError(EXIT.NOT_READY, "out/ がありません。pnpm run compile を実行してください");
    }
    throw new ToolError(EXIT.NOT_READY, error instanceof Error ? error.message : String(error));
  }
}

/**
 * playwright-core の Chromium。無ければ 3。
 * playwright-core の版と入っているブラウザの版が違うときは、`CCNAVI_CHROMIUM` に実行ファイルのパスを指定する。
 */
function loadChromium() {
  let chromium;
  try {
    ({ chromium } = require("playwright-core"));
  } catch {
    throw new ToolError(EXIT.NOT_READY, "playwright-core がありません。pnpm install を実行してください");
  }
  const executable = process.env.CCNAVI_CHROMIUM || chromium.executablePath();
  if (!fs.existsSync(executable)) {
    throw new ToolError(EXIT.NOT_READY, `Chromium がありません: ${executable}（PLAYWRIGHT_BROWSERS_PATH を見る。playwright install はしない。版が違うなら CCNAVI_CHROMIUM に実行ファイルのパスを指定する）`);
  }
  return { chromium, executable };
}

/** 描くボード（BoardJson）を得る */
async function boardJsonOf(options, out) {
  if (options.fixture !== undefined) {
    let text;
    try {
      text = fs.readFileSync(options.fixture, "utf8");
    } catch (error) {
      throw new ToolError(EXIT.USAGE, `--fixture を読めません: ${error.message}`);
    }
    const parsed = out.parseBoardJson(text);
    if (!parsed.ok) {
      throw new ToolError(EXIT.FAILED, `--fixture を解釈できません: ${parsed.error}`);
    }
    return parsed.board;
  }
  const loaded = await out.loadBoard(path.resolve(options.repo), "");
  if (!loaded.ok) {
    throw new ToolError(EXIT.FAILED, loaded.error);
  }
  return loaded.board;
}

const OUTPUT_NAMES = ["board.png", "board.txt", "board.html", "board.log"];

async function render(options, out, browserType) {
  // 前回の出力を先に消す。失敗したのに古い PNG が残ると、成功と取り違えるため
  fs.mkdirSync(options.out, { recursive: true });
  for (const name of OUTPUT_NAMES) {
    fs.rmSync(path.join(options.out, name), { force: true });
  }
  const board = await boardJsonOf(options, out);
  const data = { kind: "board", board: out.buildBoard(board) };
  const html = out.renderBoardPage(data, {
    nonce: crypto.randomBytes(16).toString("base64"),
    script: out.script,
    style: out.style,
    appearance: options.appearance,
  });

  const htmlPath = path.join(options.out, "board.html");
  fs.writeFileSync(htmlPath, html);

  const entries = [];
  let browser;
  try {
    browser = await browserType.chromium.launch({ executablePath: browserType.executable });
  } catch (error) {
    throw new ToolError(EXIT.NOT_READY, `Chromium を起動できません: ${String(error.message).split("\n")[0]}`);
  }
  let timedOut = false;
  try {
    const page = await browser.newPage({ viewport: { width: options.width, height: VIEWPORT_HEIGHT } });
    page.on("console", (message) => {
      entries.push({ kind: "console", type: message.type(), text: message.text(), url: message.location().url });
    });
    page.on("pageerror", (error) => entries.push({ kind: "pageerror", text: error.message }));
    await page.addInitScript({ content: `(${installVsCodeStub.toString()})(window);` });
    await page.goto(pathToFileURL(htmlPath).href);
    try {
      await page.waitForFunction(() => document.querySelector("#root") !== null && document.querySelector("#ccnavi-loading") === null, null, {
        timeout: RENDER_TIMEOUT_MS,
      });
    } catch {
      timedOut = true;
    }
    for (const text of await page.evaluate(() => window.__csp || [])) {
      entries.push({ kind: "csp", text });
    }
    for (const message of await page.evaluate(() => window.__posted || [])) {
      entries.push({ kind: "posted", text: JSON.stringify(message) });
    }
    await page.screenshot({ path: path.join(options.out, "board.png"), fullPage: true });
    fs.writeFileSync(path.join(options.out, "board.txt"), `${await page.evaluate(() => document.body.innerText)}\n`);
  } finally {
    await browser.close();
    // 描画の途中で投げても、集めた記録は残す（調べたいのはそのとき）
    fs.writeFileSync(path.join(options.out, "board.log"), entries.map(logLine).join("\n") + (entries.length > 0 ? "\n" : ""));
  }

  if (timedOut) {
    throw new ToolError(EXIT.FAILED, `描画が ${RENDER_TIMEOUT_MS / 1000} 秒で終わりませんでした（読み込み中のまま）。${path.join(options.out, "board.log")} を見る`);
  }
  const failures = failuresOf(entries);
  if (codeForFailures(failures) !== EXIT.OK) {
    throw new ToolError(EXIT.FAILED, `${failures.length} 件の失敗: ${logLine(failures[0])}（${path.join(options.out, "board.log")}）`);
  }
}

async function main(argv) {
  try {
    const parsed = parseArgs(argv);
    if (!parsed.ok) {
      throw new ToolError(EXIT.USAGE, `${parsed.error}\n${USAGE}`);
    }
    const options = parsed.options;
    const out = loadOut();
    const checked = validateAppearance(options.appearance, out.APPEARANCES);
    if (!checked.ok) {
      throw new ToolError(EXIT.USAGE, `${checked.error}\n${USAGE}`);
    }
    if (fs.existsSync(options.out) && !fs.statSync(options.out).isDirectory()) {
      throw new ToolError(EXIT.USAGE, `--out がディレクトリではありません: ${options.out}`);
    }
    if (options.repo !== undefined) {
      if (!fs.existsSync(options.repo) || !fs.statSync(options.repo).isDirectory()) {
        throw new ToolError(EXIT.USAGE, `--repo がありません: ${options.repo}`);
      }
      if (isInside(options.out, options.repo, out.foldPath)) {
        throw new ToolError(EXIT.USAGE, `--out が --repo の下です（リポジトリを汚さないため、外のパスを指定する）: ${options.out}`);
      }
    }
    const browserType = loadChromium();
    await render(options, out, browserType);
    console.log(`描きました: ${path.join(options.out, "board.png")}`);
    return EXIT.OK;
  } catch (error) {
    if (error instanceof ToolError) {
      console.error(`render-board: ${error.message}`);
      if (error.code === EXIT.NOT_READY) {
        console.error(NOT_READY_HINT);
      }
      return error.code;
    }
    console.error(`render-board: ${error && error.stack ? error.stack : String(error)}`);
    return EXIT.FAILED;
  }
}

module.exports = { EXIT, ToolError, parseArgs, validateAppearance, isInside, isIgnorableConsole, failuresOf, logLine, codeForFailures };

if (require.main === module) {
  main(process.argv.slice(2)).then((code) => {
    // process.exit で切ると、パイプへの出力が（Windows で）残りを落とすことがある
    process.exitCode = code;
  });
}
