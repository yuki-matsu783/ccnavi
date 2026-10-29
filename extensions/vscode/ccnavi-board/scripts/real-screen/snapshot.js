// 実画面で、左の一覧の画面を順に開き、PNG と画面の文（テキスト）を残す。
//
//   node scripts/real-screen/snapshot.js --folder <フォルダ> --out <出力ディレクトリ> [--width 1280] [--height 760] <画面名>...
//
// 画面名は左の ccnavi の一覧の表記（チケット管理・ルール管理など）。出力は <out>/<番号>-<画面名>.png と .txt。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { launch } = require("./lib/session.js");
const { WorkbenchPage } = require("./pages/WorkbenchPage.js");
const { BoardPage } = require("./pages/BoardPage.js");

const argv = process.argv.slice(2);
const opts = {};
const screens = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i].startsWith("--")) opts[argv[i].slice(2)] = argv[++i];
  else screens.push(argv[i]);
}

(async () => {
  if (!opts.folder || !opts.out || screens.length === 0) {
    console.error("使い方: node scripts/real-screen/snapshot.js --folder <フォルダ> --out <出力ディレクトリ> [--width N] [--height N] <画面名>...");
    process.exit(2);
  }
  fs.mkdirSync(opts.out, { recursive: true });
  const { browser, page } = await launch({ folder: opts.folder, width: +(opts.width || 1280), height: +(opts.height || 760) });
  try {
    const wb = new WorkbenchPage(page);
    await wb.openCcnaviSidebar();
    let n = 0;
    for (const name of screens) {
      const base = path.join(opts.out, `${String(++n).padStart(2, "0")}-${name}`);
      if (name === "チケット管理") {
        const board = await BoardPage.open(wb);
        fs.writeFileSync(`${base}.txt`, await board.text());
      } else {
        await wb.openScreen(name);
        const f = await wb.webview();
        fs.writeFileSync(`${base}.txt`, await f.evaluate(() => document.body.innerText));
      }
      await wb.screenshot(`${base}.png`);
    }
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
