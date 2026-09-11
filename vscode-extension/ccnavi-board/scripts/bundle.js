// 拡張の本体を 1 本に束ねる。`pnpm run compile` が tsc のあとに呼ぶ。
//
// 実行時の依存（yaml）を vsix に入れるため。package.sh は vsce を --no-dependencies で
// 走らせて node_modules を見に行かないので、依存はここで本体に取り込んでおく。
// テストは tsc が out/src と out/test に出したものを使い、この束は使わない。
"use strict";

const path = require("node:path");
const esbuild = require("esbuild");

const here = path.resolve(__dirname, "..");

esbuild
  .build({
    entryPoints: [path.join(here, "src", "extension.ts")],
    outfile: path.join(here, "out", "extension.js"),
    bundle: true,
    platform: "node",
    target: "node20",
    format: "cjs",
    external: ["vscode"],
    sourcemap: false,
    logLevel: "info",
  })
  .catch(() => process.exit(1));
