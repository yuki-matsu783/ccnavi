// 画面（React）と、その CSS を、画面ごとに 1 本ずつへ束ねる。`pnpm run compile` と、画面を読む
// テストを回すとき（`scripts/test-groups.js`）に呼ばれる。
//
// 出来上がりは拡張が読んで `<script nonce>` と `<style nonce>` に流し込む（各画面の入れ物を組む関数）。
// ファイルとして読ませないので、Webview の localResourceRoots は空のままでよく、CSP も nonce だけで済む。
// tsc は型を見るだけ（tsconfig.webview.json は noEmit）で、JS を出すのは esbuild のほう。
//
// **画面の一覧は表で持たない。** `src/webview/<名前>/main.tsx` があるものが画面で、出口は
// `out/webview/<名前>.js`。CSS は同じ置き場の `style.css` が入口で、出口は `out/webview/<名前>.css`。
// 表にすると、画面を足したときに黙って古くなる
// （`scripts/test-groups.js` が同じ見つけ方をする。片方だけ直す、が起きないように）。
//
// 読む側（`src/webview-asset.ts` の `webviewScript` / `webviewStyle`）に渡すのは**拡張子まで込みの
// `<名前>.js`・`<名前>.css`** で、ここの名前そのものではない（`board-panel.ts` は
// `webviewScript("board.js")` と書く）。読む側は名前で引くので、画面が増えても直すところは無い。
//
// CSS は小さくしない（JS は小さくする）。`<style nonce>` に入るぶんは数 KB で、画面のスクリプト
// （1 本 200KB 強）に比べれば誤差になる。そのぶん、開発者ツールで読める形のまま出て、esbuild が
// 付ける `/* src/webview/board/Card.css */` の行で、どの部品の CSS かがその場で分かる。
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
const OUT_DIR = path.join(here, "out", "webview");

/** 画面の一覧。`{ name, entry, style }` を名前順で返す */
function screens() {
  return fs
    .readdirSync(WEBVIEW_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({
      name: entry.name,
      entry: path.join(WEBVIEW_DIR, entry.name, "main.tsx"),
      style: path.join(WEBVIEW_DIR, entry.name, "style.css"),
    }))
    .filter((screen) => fs.existsSync(screen.entry))
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

const found = screens();
if (found.length === 0) {
  console.error(`画面が 1 つも見つかりません: ${WEBVIEW_DIR}/<名前>/main.tsx`);
  process.exit(1);
}

// CSS の入口が無い画面は、見た目だけが抜けた画面になる。束ねる前に名指しで止める
// （出口が出来ないだけだと、拡張が読むところまで行ってから「束ねられていない」と言われる）。
const styleless = found.filter((screen) => !fs.existsSync(screen.style));
if (styleless.length > 0) {
  for (const screen of styleless) {
    console.error(`画面の CSS の入口がありません: ${screen.style}（部品の CSS を @import する 1 本を置く）`);
  }
  process.exit(1);
}

const common = {
  outdir: OUT_DIR,
  bundle: true,
  sourcemap: false,
  logLevel: "info",
};

esbuild
  .build({
    ...common,
    // `{ in, out }` の形で渡すと、出口は `outdir` の下の `<out>.js` になる。
    // 分割（splitting）はしない。chunk をファイルとして Webview に読ませることになり、
    // localResourceRoots を空のままにする方針と両立しないため。代わりに React 一式が
    // 画面ごとに重複する（1 画面あたり 200KB 強）
    entryPoints: found.map((screen) => ({ in: screen.entry, out: screen.name })),
    platform: "browser",
    // Webview は VS Code に入っている Chromium。ES2022 で足りる
    target: "es2022",
    format: "iife",
    jsx: "automatic",
    minify: true,
    // React の開発用の検査（開発者向けの警告と遅い経路）を落とす
    define: { "process.env.NODE_ENV": '"production"' },
  })
  .then(() =>
    esbuild.build({
      ...common,
      // CSS は別に束ねる。JS の入口から import させないのは、小さくするかどうかを別に決めるため
      // （esbuild の minify は JS と CSS の両方に掛かる）と、画面のスクリプトが CSS を持ち回らない
      // ようにするため（CSS を挿すのは入れ物を組む側で、画面は nonce を知らない）。
      entryPoints: found.map((screen) => ({ in: screen.style, out: screen.name })),
      // `@import` の外の綴り（url() など）は無い。あれば esbuild が名指しで落とす
      minify: false,
    }),
  )
  .catch(() => process.exit(1));
