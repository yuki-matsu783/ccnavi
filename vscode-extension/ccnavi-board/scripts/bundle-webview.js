// 画面（React）を、画面ごとに 1 本へ束ねる。`pnpm run compile` と `pnpm test` が呼ぶ。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（core/render.ts）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");
const outdir = path.join(here, "out", "webview");

// 画面の一覧（名前 → 入口）。画面を足すときはここに 1 行足す。
//
// 名前は出来上がりの綴り（`out/webview/<名前>.js`）。読む側（`src/webview-script.ts` の
// `webviewScript`）に渡すのは**拡張子まで込みの `<名前>.js`** で、ここのキーそのものではない
// （`board-panel.ts` は `webviewScript("board.js")` と書く）。読む側は名前で引くので、
// 画面が増えても読む側は直さなくてよい。
const SCREENS = {
  board: path.join("src", "webview", "board", "main.tsx"),
};

if (Object.keys(SCREENS).length === 0) {
  console.error("画面が 1 つも無い（SCREENS が空。out/webview を空にして拡張が動かなくなる）");
  process.exit(1);
}

// 束ね直す前に置き場ごと消す。名前を変えたり画面をやめたりしたときに古い 1 本が残ると、
// 拡張はそれを読めてしまう（消す側と作り直す側を同じ場所に置いて、片方だけ走る形を作らない）。
fs.rmSync(outdir, { recursive: true, force: true, maxRetries: 3 });

esbuild
  .build({
    // `{ in, out }` の形で渡すと、出口は `outdir` の下の `<out>.js` になる。
    // 分割（splitting）はしない。chunk をファイルとして Webview に読ませることになり、
    // localResourceRoots を空のままにする方針と両立しないため。代わりに React 一式が
    // 画面ごとに重複する（1 画面あたり 200KB 強）
    entryPoints: Object.entries(SCREENS).map(([name, entry]) => ({ in: path.join(here, entry), out: name })),
    outdir,
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
