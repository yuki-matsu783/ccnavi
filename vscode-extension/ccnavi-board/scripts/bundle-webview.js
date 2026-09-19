// 画面（React）を 1 本に束ねる。`pnpm run compile` と `pnpm test` が呼ぶ。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（core/render.ts）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
"use strict";

const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");

esbuild
  .build({
    entryPoints: [path.join(here, "src", "webview", "board", "main.tsx")],
    outfile: path.join(here, "out", "webview", "board.js"),
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
