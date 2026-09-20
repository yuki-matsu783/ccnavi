// 画面（React）を、画面ごとに 1 本へ束ねる。`pnpm run compile` と `pnpm test` が呼ぶ。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（各画面の入れ物を組む関数）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
//
// **画面を足すときは SCREENS に 1 行足す。** 読む側（`src/webview-script.ts` の `webviewScript(name)`）は
// 名前を取るので直すところは無い。出口は `out/webview/<名前>.js` で、`scripts/clean-out.js` は
// `out/webview` をまとめて消すのでそのままでよい。
"use strict";

const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");

/** 名前 → 入口。名前は `webviewScript("<名前>.js")` と `out/webview/<名前>.js` の綴りになる */
const SCREENS = [
  { name: "board", entry: ["src", "webview", "board", "main.tsx"] },
  { name: "projects", entry: ["src", "webview", "projects", "main.tsx"] },
];

esbuild
  .build({
    entryPoints: SCREENS.map((screen) => ({ in: path.join(here, ...screen.entry), out: screen.name })),
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
