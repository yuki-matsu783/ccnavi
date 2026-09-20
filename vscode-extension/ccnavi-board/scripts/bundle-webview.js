// 画面（React）を、画面ごとに 1 本へ束ねる。`pnpm run compile` と `pnpm test` が呼ぶ。
//
// 出来上がりは拡張が読んで `<script nonce>` に流し込む（各画面の入れ物を組む関数）。ファイルとして
// 読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
//
// **画面の一覧は表で持たない。** `src/webview/<名前>/main.tsx` があるものが画面で、出口は
// `out/webview/<名前>.js`。読む側（`src/webview-script.ts` の `webviewScript(name)`）は名前を取るので、
// 画面を足しても直すところは無い。表にすると、画面を足したときに黙って古くなる
// （`scripts/test-groups.js` が同じ見つけ方をする。片方だけ直す、が起きないように）。
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
