// 実画面でチケット管理を開き、先頭の承認待ちを承認する。承認の前後を撮り、画面の文を標準出力へ出す。
//
//   node scripts/real-screen/approve.js --folder <検証用コピー> --out <出力ディレクトリ> [--tag a] [--no-confirm]
//
// --no-confirm は、確認のオーバーレイを開いて撮るだけで承認しない。code-server を先に起動しておく
// （手順は docs/design/real-screen-check.md）。検証用コピーは origin を外すか、偽のホストを向けてあること。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { launch } = require("./lib/session.js");
const { WorkbenchPage } = require("./pages/WorkbenchPage.js");
const { BoardPage } = require("./pages/BoardPage.js");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i < 0 ? fallback : process.argv[i + 1];
}

(async () => {
  const folder = arg("folder");
  const out = arg("out");
  if (!folder || !out) {
    console.error("使い方: node scripts/real-screen/approve.js --folder <フォルダ> --out <出力ディレクトリ> [--tag a] [--no-confirm]");
    process.exit(2);
  }
  const tag = arg("tag", "approve");
  fs.mkdirSync(out, { recursive: true });
  const shot = (wb, name) => wb.screenshot(path.join(out, `${tag}-${name}.png`));

  const { browser, page } = await launch({ folder });
  try {
    const wb = new WorkbenchPage(page);
    await wb.openCcnaviSidebar();
    const board = await BoardPage.open(wb);
    await shot(wb, "01-board");
    console.log("列ごとの件数:", JSON.stringify(await board.columnCounts()));
    const dialog = await board.openApproval();
    await shot(wb, "02-dialog");
    console.log("--- 承認の確認 ---\n" + (await dialog.body()));
    if (process.argv.includes("--no-confirm")) return;
    console.log("--- 承認の結果 ---\n" + (await dialog.confirm()));
    await shot(wb, "03-done");
    console.log("--- ターミナル ---\n" + (await wb.terminalText()));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
