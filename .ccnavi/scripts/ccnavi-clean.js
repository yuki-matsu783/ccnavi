// ccnavi-clean の本体。ccnavi-clean.sh から呼ぶ。単体では打たない。
//
//   node ccnavi-clean.js <作業ツリーの絶対パス> [--dry-run]
//
// 名前の検査と未コミットの変更の確認は sh が済ませている。ここがするのは、決まった
// 名前の生成物を探して消すことだけ。
//
// Node で消すのは vscode-extension/ccnavi-board/scripts/clean.js と同じ理由。pnpm の
// node_modules は深く（Windows の 260 文字を超える）、junction も含む。fs.rmSync は
// 長いパスを扱え、symlink と junction はたどらずにリンクそのものだけを消す。
"use strict";

const fs = require("node:fs");
const path = require("node:path");

// どこにあっても生成物と言える名前。
const ALWAYS = new Set(["node_modules", ".venv", "__pycache__", ".pytest_cache"]);
// ありふれた名前なので、隣に package.json があるときだけ生成物と見る。
const BESIDE_PACKAGE_JSON = new Set(["out"]);
// 中へ降りない。git の管理領域。
const SKIP = new Set([".git"]);

// 作業ツリーの置き場の直下か。sh が組み立てた値だが、ここでも確かめる。
// 取り違えたときに消す範囲が広がる向きの誤りなので、二重にしておく。
function isWorktree(top) {
  const parent = path.dirname(top);
  return path.basename(parent) === "worktrees" && path.basename(path.dirname(parent)) === ".claude";
}

// リンクか。Windows の junction も lstat ではリンクに見える。
function isLink(p) {
  try {
    return fs.lstatSync(p).isSymbolicLink();
  } catch {
    return true; // 確かめられないものは、たどらない側に倒す
  }
}

function findTargets(top) {
  const found = [];
  const stack = [top];
  while (stack.length > 0) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue; // 読めないディレクトリは探さない。消すものを増やす向きには倒さない
    }
    const hasPackageJson = entries.some((e) => e.isFile() && e.name === "package.json");
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (ALWAYS.has(e.name) || (hasPackageJson && BESIDE_PACKAGE_JSON.has(e.name))) {
        // リンクなら rmSync はリンクだけを消す。先は残る。
        if (e.isDirectory() || e.isSymbolicLink()) {
          found.push(full);
        }
        continue;
      }
      if (e.isDirectory() && !SKIP.has(e.name) && !isLink(full)) {
        stack.push(full);
      }
    }
  }
  return found.sort();
}

function main(argv) {
  const top = argv[0];
  const dryRun = argv.slice(1).includes("--dry-run");
  if (!top || !path.isAbsolute(top) || !isWorktree(top)) {
    console.error(`ccnavi-clean: .claude/worktrees/ の直下の絶対パスを渡してください（${top || "空"}）。`);
    return 2;
  }

  const targets = findTargets(top);
  if (targets.length === 0) {
    console.log(`消すものはありません（${top}）`);
    return 0;
  }

  const failed = [];
  for (const target of targets) {
    const shown = path.relative(top, target).split(path.sep).join("/");
    if (dryRun) {
      console.log(`would remove ${shown}`);
      continue;
    }
    try {
      fs.rmSync(target, { recursive: true, force: true, maxRetries: 3 });
      console.log(`removed ${shown}`);
    } catch (err) {
      failed.push(shown);
      console.error(`消せなかった: ${shown} (${err.code || err.message})`);
    }
  }

  if (failed.length > 0) {
    console.error(
      "ccnavi-clean: 消し残しがあります。Windows では、読み込まれている DLL（uv の .venv の .pyd）は" +
        "どの作業ツリーからも消せません。テストが終わるのを待って打ち直すか、ディレクトリごと mv で" +
        " .claude/worktrees/ の外へ出してください（HANDOVER.md の「作業ツリーが消せない」）。",
    );
    return 1;
  }
  return 0;
}

process.exitCode = main(process.argv.slice(2));
