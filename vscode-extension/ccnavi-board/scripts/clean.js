// 生成物を名指しで消す。`pnpm run clean` で呼ぶ。
//
// 作業ツリーを `git worktree remove` する前に走らせる。pnpm の node_modules は
// `.pnpm/` の下が深く（Windows の 260 文字を超える）、symlink も含むので、
// git の削除が途中で止まって抜け殻が残ることがある。先にここで消しておく。
//
// 消すのは、このディレクトリ直下の決まった 2 つだけ。引数は取らない。
// `rm -rf` の代わりに任意のパスを消す道具にはしない（ccnavi の recursive-delete の趣旨）。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const here = path.resolve(__dirname, "..");
const TARGETS = ["node_modules", "out"];

for (const name of TARGETS) {
  const target = path.join(here, name);
  if (!fs.existsSync(target)) {
    continue;
  }
  fs.rmSync(target, { recursive: true, force: true, maxRetries: 3 });
  console.log(`removed ${path.relative(process.cwd(), target) || target}`);
}
