// 画面（React）を、画面ごとに 1 本へ束ねる。`pnpm run compile` と、画面を読むテストを回すとき
// （`scripts/test-groups.js`）に呼ばれる。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（各画面の入れ物を組む関数）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
//
// **画面の一覧は表で持たない。** `src/webview/<名前>/main.tsx` があるものが画面で、出口は
// `out/webview/<名前>.js`。表にすると、画面を足したときに黙って古くなる
// （`scripts/test-groups.js` が同じ見つけ方をする。片方だけ直す、が起きないように）。
//
// 読む側（`src/webview-script.ts` の `webviewScript`）に渡すのは**拡張子まで込みの `<名前>.js`**
// で、ここの名前そのものではない（`board-panel.ts` は `webviewScript("board.js")` と書く）。
// 読む側は名前で引くので、画面が増えても直すところは無い。
//
// 古い束ねを消すのはここではなく scripts/clean-out.js。あちらが out/webview ごと消してから
// ここが作り直す順で、束ねる前に消す形にはしない（esbuild が落ちたときに、動いていた画面まで
// 消えたまま残るため）。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");
const WEBVIEW_DIR = path.join(here, "src", "webview");

/** 画面の一覧。`{ name, entry }` を名前順で返す */
function screens() {
  return fs
    .readdirSync(WEBVIEW_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({ name: entry.name, entry: path.join(WEBVIEW_DIR, entry.name, "main.tsx") }))
    .filter((screen) => fs.existsSync(screen.entry))
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

const found = screens();
if (found.length === 0) {
  console.error(`画面が 1 つも見つかりません: ${WEBVIEW_DIR}/<名前>/main.tsx`);
  process.exit(1);
}

esbuild
  .build({
    // `{ in, out }` の形で渡すと、出口は `outdir` の下の `<out>.js` になる。
    // 分割（splitting）はしない。chunk をファイルとして Webview に読ませることになり、
    // localResourceRoots を空のままにする方針と両立しないため。代わりに React 一式が
    // 画面ごとに重複する（1 画面あたり 200KB 強）
    entryPoints: found.map((screen) => ({ in: screen.entry, out: screen.name })),
    outdir: path.join(here, "out", "webview"),
    bundle: true,
    platform: "browser",
    // Webview は VS Code に入っている Chromium。ES2022 で足りる
    target: "es2022",
    format: "iife",
    jsx: "automatic",
    minify: true,
    // React の開発用の検査（開発者向けの警告と遅い経路）を落とす
    define: { "process.env.NODE_ENV": '"production"' },
    sourcemap: false,
    logLevel: "info",
  })
  .catch(() => process.exit(1));
