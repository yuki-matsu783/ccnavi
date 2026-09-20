// 画面（React）を、画面ごとに 1 本へ束ねる。`pnpm run compile` と `pnpm test` が呼ぶ。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（core/render.ts）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
"use strict";

const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");

// 画面の一覧（名前 → 入口）。画面を足すときはここに 1 行足す。
//
// 名前は出来上がりの綴り（`out/webview/<名前>.js`）で、読む側（`src/webview-script.ts` の
// `webviewScript(name)`）は名前を取るので、画面が増えても読む側は直さなくてよい。
// 束ねるのは領域ごとのテスト（`test:rules` など）でも全部で、型を見るのも
// `tsconfig.webview.json`（`src/webview` 全部）で全部。束ねるものと型を見るものは揃えてある。
const SCREENS = {
  board: path.join("src", "webview", "board", "main.tsx"),
};

esbuild
  .build({
    // `{ in, out }` の形で渡すと、出口は `outdir` の下の `<out>.js` になる。
    // 分割（splitting）はしないので、画面どうしは何も共有せず 1 本ずつで完結する
    entryPoints: Object.entries(SCREENS).map(([name, entry]) => ({ in: path.join(here, entry), out: name })),
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
