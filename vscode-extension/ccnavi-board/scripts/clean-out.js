// tsc の出力（out/src と out/test）と、バンドルした画面（out/webview）を消す。
// `pnpm test` と `pnpm run compile` の先頭で呼ぶ。
//
// tsc は out/ の古いファイルを消さない。テストの置き場を動かしたあとに古い out/test/ が残っていると、
// グロブが動かす前のテストまで拾って走らせる。out/webview も同じで、バンドルし直す前の画面が残っていると、
// 直したはずのものが直っていないのにテストが通る（テストはバンドルしたものを読む）。
// esbuild がバンドルした out/extension.js は消さない（compile のあとに test を回すと、ここで消えて
// vsix の入口が無くなる。out/webview は test がバンドルし直すので消してよい）。
// 消すのはこの 3 つだけで、引数は取らない。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const out = path.join(path.resolve(__dirname, ".."), "out");
for (const name of ["src", "test", "webview"]) {
  const target = path.join(out, name);
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true, maxRetries: 3 });
  }
}
