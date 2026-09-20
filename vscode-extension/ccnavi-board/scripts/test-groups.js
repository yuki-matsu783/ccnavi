// テストを、触ったファイルに関わるグループだけ回す。
//
// `test/<グループ>/` がグループで、`pnpm test` は全部を回す。ここが要るのは、
// 1 つのグループだけ回したいときに `tsc` を 1 回で済ませるため。package.json に
// グループごとの行を並べていたときは、2 グループ回すとコンパイルも 2 回走った。
// コンパイルは約 6 秒、テストの実行は全部で約 5 秒なので、2 回目のコンパイルは
// テスト全部を回すより高い。
//
// どのグループがどのファイルを読むかは表で持たない。テストの import を辿って
// そのつど数える。表は、ファイルを増やしたときに黙って古くなる。
//
//   node scripts/test-groups.js --all                    全部
//   node scripts/test-groups.js rules shared             名指し
//   node scripts/test-groups.js --for src/core/hooks.ts  触ったファイルから決める
//   node scripts/test-groups.js --plan --for <パス>...    何を回すかだけ出す（走らせない）
//   node scripts/test-groups.js --dom --all              画面（*.dom.test.js）だけ
//
// `--plan` は node_modules が無くても動く。読むのはソースの綴りだけで、
// コンパイルも実行もしないため。ターンの終わりの hook（.claude/hooks/test-ext.sh）は
// これを使って「回すものが無いターン」を見分ける。
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const TEST_DIR = path.join(ROOT, "test");

// test/ の下にあってグループではないもの。helpers はどのグループも読む部品、
// fixtures は実行時に名前で開く固定データ。
const NOT_GROUPS = new Set(["helpers", "fixtures"]);

// 画面（React）の束ねたものを読むテストの入口。これを辿るグループだけ、
// tsconfig.webview.json の型の検査と esbuild の束ねが要る。画面を足して別の入口から
// 読ませるなら、その入口もここに挙げる。
const BUNDLE_ENTRIES = [path.join(TEST_DIR, "helpers", "board.ts")];

/** test/ の下のディレクトリ名。増やしても直すところは無い。 */
function groupNames() {
  return fs
    .readdirSync(TEST_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !NOT_GROUPS.has(e.name))
    .map((e) => e.name)
    .sort();
}

/**
 * import の綴りを、このリポジトリの中のファイルに解く。
 *
 * テストは `../../src/core/hooks.js` と書く（module: Node16 なので出力側の綴り）。
 * 解く先は `.ts` か `.tsx`。`node:fs` や `happy-dom` のような外のものは null。
 */
function resolveImport(from, spec) {
  if (!spec.startsWith(".")) return null;
  const base = path.resolve(path.dirname(from), spec);
  const candidates = [];
  if (base.endsWith(".js")) {
    candidates.push(base.slice(0, -3) + ".ts", base.slice(0, -3) + ".tsx");
  }
  candidates.push(base, base + ".ts", base + ".tsx", path.join(base, "index.ts"));
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

const IMPORT = /(?:^|\s)(?:import|export)[^;]*?from\s*"([^"]+)"/g;

/** 1 ファイルが読むもの（相対 import だけ）。 */
function importsOf(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const found = [];
  for (const match of text.matchAll(IMPORT)) {
    const resolved = resolveImport(file, match[1]);
    if (resolved) found.push(resolved);
  }
  return found;
}

/** グループのテストが辿り着くファイル全部（テスト自身も含む）。 */
function closureOf(group) {
  const dir = path.join(TEST_DIR, group);
  const seen = new Set();
  const stack = fs
    .readdirSync(dir)
    .filter((name) => name.endsWith(".ts"))
    .map((name) => path.join(dir, name));
  while (stack.length > 0) {
    const file = stack.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    for (const next of importsOf(file)) stack.push(next);
  }
  return seen;
}

/** グループ名 -> そのグループが読むファイルの集合。 */
function closures() {
  const map = new Map();
  for (const group of groupNames()) map.set(group, closureOf(group));
  return map;
}

/** 束ねた画面を読むグループ（BUNDLE_ENTRIES を辿るもの）。 */
function webviewGroups(map) {
  return [...map.entries()]
    .filter(([, files]) => BUNDLE_ENTRIES.some((entry) => files.has(entry)))
    .map(([group]) => group);
}

/**
 * 触ったパスを、このリポジトリのルートからの相対（`/` 区切り）にする。
 *
 * hook が渡すのはワークスペースルートからの相対でも絶対でもよく、Windows では
 * 区切りがバックスラッシュで来る。拡張の外のパスは null。
 */
function relativeToExtension(given) {
  const slashed = given.replace(/\\/g, "/");
  const marker = "vscode-extension/ccnavi-board/";
  const at = slashed.lastIndexOf(marker);
  if (at >= 0) return slashed.slice(at + marker.length);
  if (path.isAbsolute(slashed)) return null;
  // 拡張のディレクトリから打たれた相対パス。実在するものだけ受ける。
  return fs.existsSync(path.join(ROOT, slashed)) ? slashed : null;
}

/**
 * 触ったファイル 1 つが呼ぶもの。
 *
 * 返すのは `{ groups, compile }`。`groups` が空で `compile` が真なら、テストは
 * 何も読まないが `tsc` は見るファイル（拡張ホスト側の panel など）。
 */
function callFor(rel, map) {
  if (rel === null) return { groups: [], compile: false };

  // 読み物と絵。コンパイルもテストも要らない。
  if (rel.endsWith(".md") || rel.startsWith("media/")) return { groups: [], compile: false };
  // 生成物と依存の置き場。触ったと数えない。
  if (rel.startsWith("out/") || rel.startsWith("node_modules/")) {
    return { groups: [], compile: false };
  }

  const all = [...map.keys()];

  // 組み立ての土台。どのグループの結果も変わりうる。
  if (
    rel === "package.json" ||
    rel === "pnpm-lock.yaml" ||
    /^tsconfig[^/]*\.json$/.test(rel) ||
    rel.startsWith("scripts/")
  ) {
    return { groups: all, compile: true };
  }

  // 固定データは import ではなく実行時に名前で開くので、辿れない。全部に効くと見る。
  if (rel.startsWith("test/fixtures/")) return { groups: all, compile: true };

  // 画面（React）は esbuild が束ね、テストは束ねたものを読む。import では辿れないので、
  // 束ねたものを読むグループに効くと見る。
  if (rel.startsWith("src/webview/")) return { groups: webviewGroups(map), compile: true };

  const absolute = path.join(ROOT, rel);
  const groups = all.filter((group) => map.get(group).has(absolute));
  return { groups, compile: true };
}

/** 触ったパスの一覧から、回すものを決める。 */
function planFor(paths) {
  const map = closures();
  const groups = new Set();
  let compile = false;
  for (const given of paths) {
    const call = callFor(relativeToExtension(given), map);
    if (call.compile) compile = true;
    for (const group of call.groups) groups.add(group);
  }
  return finish([...groups].sort(), compile, map);
}

function planForGroups(names) {
  const map = closures();
  const known = new Set(map.keys());
  const unknown = names.filter((name) => !known.has(name));
  if (unknown.length > 0) {
    console.error(`知らないグループ: ${unknown.join(" ")}（ある: ${[...known].join(" ")}）`);
    process.exit(2);
  }
  return finish([...new Set(names)].sort(), true, map);
}

function finish(groups, compile, map) {
  const needsWebview = webviewGroups(map);
  return {
    groups,
    compile: compile || groups.length > 0,
    webview: groups.some((group) => needsWebview.includes(group)),
  };
}

function run(command, args) {
  const result = spawnSync(command, args, { cwd: ROOT, stdio: "inherit" });
  if (result.error) {
    console.error(`起動できませんでした: ${command} ${args.join(" ")}`);
    console.error(String(result.error.message));
    return 1;
  }
  return result.status === null ? 1 : result.status;
}

/** ローカルに入れた実行ファイル。pnpm を通さずに呼ぶので、hook からも同じ綴りで動く。 */
function localBin(relative) {
  return path.join(ROOT, "node_modules", relative);
}

function testFiles(groups, domOnly) {
  const files = [];
  for (const group of groups) {
    const dir = path.join(ROOT, "out", "test", group);
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir).sort()) {
      if (!name.endsWith(".test.js")) continue;
      if (domOnly && !name.endsWith(".dom.test.js")) continue;
      files.push(path.join("out", "test", group, name));
    }
  }
  return files;
}

function main(argv) {
  const domOnly = argv.includes("--dom");
  const planOnly = argv.includes("--plan");
  const rest = argv.filter((arg) => arg !== "--dom" && arg !== "--plan");

  let plan;
  if (rest.includes("--all")) {
    plan = planForGroups(groupNames());
  } else if (rest[0] === "--for") {
    plan = planFor(rest.slice(1));
  } else if (rest.length > 0) {
    plan = planForGroups(rest);
  } else {
    console.error("引数が要ります: --all / <グループ>... / --for <パス>...");
    return 2;
  }

  if (planOnly) {
    // 1 行で出す。hook とテストがこの行を読む。
    console.log(
      `groups=${plan.groups.join(",")} compile=${plan.compile ? "yes" : "no"} ` +
        `webview=${plan.webview ? "yes" : "no"}`,
    );
    return 0;
  }

  if (!plan.compile) {
    console.log("ccnavi-board: 触ったのは読み物だけなので、回すものはありません");
    return 0;
  }

  const tsc = localBin(path.join("typescript", "bin", "tsc"));
  if (!fs.existsSync(tsc)) {
    console.error("ccnavi-board: node_modules が無いのでテストを回せません。");
    console.error("vscode-extension/ccnavi-board で 'pnpm install --frozen-lockfile' を通してください。");
    return 1;
  }

  let code = run(process.execPath, [path.join("scripts", "clean-out.js")]);
  if (code !== 0) return code;

  code = run(process.execPath, [tsc, "-p", "tsconfig.test.json"]);
  if (code !== 0) return code;

  if (plan.webview) {
    code = run(process.execPath, [tsc, "-p", "tsconfig.webview.json"]);
    if (code !== 0) return code;
    code = run(process.execPath, [path.join("scripts", "bundle-webview.js")]);
    if (code !== 0) return code;
  }

  if (plan.groups.length === 0) {
    console.log("ccnavi-board: 型の検査だけ通しました（テストが読まないファイルの変更）");
    return 0;
  }

  const files = testFiles(plan.groups, domOnly);
  if (files.length === 0) {
    console.log(`ccnavi-board: ${plan.groups.join(" ")} に回すテストがありません`);
    return 0;
  }
  console.log(`ccnavi-board: ${plan.groups.join(" ")}（${files.length} ファイル）`);
  return run(process.execPath, ["--test", ...files]);
}

process.exit(main(process.argv.slice(2)));
