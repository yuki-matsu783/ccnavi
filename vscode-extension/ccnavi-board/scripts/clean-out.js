// tsc の出力（out/src と out/test）だけを消す。`pnpm test` と `pnpm run compile` の先頭で呼ぶ。
//
// tsc は out/ の古いファイルを消さない。テストの置き場を動かしたあとに古い out/test/ が残っていると、
// グロブが動かす前のテストまで拾って走らせる。esbuild が束ねた out/extension.js は消さない
// （package.sh は compile のあとに test を通すので、ここで消すと vsix の入口が無くなる）。
// 消すのはこの 2 つだけで、引数は取らない。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const out = path.join(path.resolve(__dirname, ".."), "out");
for (const name of ["src", "test"]) {
  const target = path.join(out, name);
  if (fs.existsSync(target)) {
    fs.rmSync(target, { recursive: true, force: true, maxRetries: 3 });
  }
}
