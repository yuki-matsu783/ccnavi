// tsc の出力（out/）だけを消す。`pnpm test` と `pnpm run compile` の先頭で呼ぶ。
//
// tsc は out/ の古いファイルを消さない。テストの置き場を動かしたあとに古い out/test/ が残っていると、
// グロブが動かす前のテストまで拾って走らせる。消すのはこのディレクトリ直下の out/ だけで、引数は取らない。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const target = path.join(path.resolve(__dirname, ".."), "out");
if (fs.existsSync(target)) {
  fs.rmSync(target, { recursive: true, force: true, maxRetries: 3 });
}
