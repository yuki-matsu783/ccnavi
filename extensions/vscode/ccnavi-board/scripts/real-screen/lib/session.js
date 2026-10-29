// 実画面（code-server の VS Code）を Chromium で開く入口。ページオブジェクト（../pages/）はここで得た page を包む。
// 前提と準備の手順は docs/design/real-screen-check.md。ブラウザは playwright-core と、環境の Chromium
// （CCNAVI_CHROMIUM が最優先、無ければ /opt/pw-browsers の chromium-*）を使う。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { chromium } = require("playwright-core");

const DEFAULT_URL = "http://127.0.0.1:8080";

function findChromium() {
  if (process.env.CCNAVI_CHROMIUM) return process.env.CCNAVI_CHROMIUM;
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || "/opt/pw-browsers";
  const dirs = fs.existsSync(base) ? fs.readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)) : [];
  for (const d of dirs) {
    for (const sub of ["chrome-linux", "chrome-linux64"]) {
      const exe = path.join(base, d, sub, "chrome");
      if (fs.existsSync(exe)) return exe;
    }
  }
  throw new Error("Chromium が見つからない。CCNAVI_CHROMIUM に実行ファイルのパスを指定する");
}

/** code-server の `?folder=` にフォルダを渡して開き、ワークベンチが出るまで待つ。 */
async function launch({ folder, width = 1280, height = 760, url = process.env.CCNAVI_CODE_SERVER || DEFAULT_URL } = {}) {
  const browser = await chromium.launch({ executablePath: findChromium() });
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(`${url}/?folder=${encodeURIComponent(folder)}`);
  await page.waitForSelector(".monaco-workbench", { timeout: 60000 });
  await page.waitForTimeout(6000); // 拡張ホストが立ち上がるまで
  return { browser, page };
}

module.exports = { launch, findChromium };
