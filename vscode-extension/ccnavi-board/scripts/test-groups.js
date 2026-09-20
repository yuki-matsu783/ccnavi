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

// 「環境が足りないので回せなかった」の終了コード。テストが落ちた（1）とは分ける。
// ターンの終わりの hook は、これを差し戻しに数えず人へ言う。
const NOT_READY = 3;

// 画面（React）は esbuild が束ね、テストは束ねたものを読む。その道は import では辿れないので、
// 画面とテストの結び付きだけは綴りの約束で決める。**表では持たない**（表は、画面を足したときに
// 黙って古くなる。このファイルがグループの表を持たないのと同じ理由）。
//
//   画面      `src/webview/<名前>/main.tsx` があるもの（`scripts/bundle-webview.js` と同じ見つけ方）
//   テスト    `test/helpers/<名前>.ts`（束ねたものを読む入口）と、同じ名前のグループ `test/<名前>/`
//
// これを辿るグループだけ、tsconfig.webview.json の型の検査と esbuild の束ねが要る。
const WEBVIEW_DIR = path.join(ROOT, "src", "webview");

/** 画面の一覧。`{ name, entry, helper }` を名前順で返す */
function screens() {
  return fs
    .readdirSync(WEBVIEW_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({
      name: entry.name,
      entry: path.join(WEBVIEW_DIR, entry.name, "main.tsx"),
      helper: path.join(TEST_DIR, "helpers", `${entry.name}.ts`),
    }))
    .filter((screen) => fs.existsSync(screen.entry))
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

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
  candidates.push(
    base,
    base + ".ts",
    base + ".tsx",
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  );
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

// `from "..."` と、`from` の無い副作用だけの `import "..."` の両方を拾う。引用符は
// どちらでもよい（このリポジトリは二重引用符で揃えているが、揃っていることを
// 見張るものが無いので、片方だけ拾うと黙って取りこぼす）。
const IMPORT = /(?:^|\s)(?:import|export)\b[^;]*?from\s*["']([^"']+)["']/g;
const SIDE_EFFECT_IMPORT = /(?:^|\s)import\s*["']([^"']+)["']/g;

/** 1 ファイルが読むもの（相対 import だけ）。 */
function importsOf(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const found = [];
  for (const pattern of [IMPORT, SIDE_EFFECT_IMPORT]) {
    for (const match of text.matchAll(pattern)) {
      const resolved = resolveImport(file, match[1]);
      if (resolved) found.push(resolved);
    }
  }
  return found;
}

/** ディレクトリの下の、条件に合うファイル全部（下の段も見る）。 */
function filesUnder(dir, accept) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort(byName)) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...filesUnder(full, accept));
    } else if (accept(entry.name)) {
      found.push(full);
    }
  }
  return found;
}

function byName(a, b) {
  return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
}

/** 起点から import で辿り着くファイル全部（起点自身も含む）。 */
function closureFrom(roots) {
  const seen = new Set();
  const stack = [...roots];
  while (stack.length > 0) {
    const file = stack.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    for (const next of importsOf(file)) stack.push(next);
  }
  return seen;
}

// 画面の束ねに入るファイル。触ったパス 1 つごとに取り直すと、画面の数だけ全体を歩き直す。
const screenFileCache = new Map();

/** 画面の束ねに入るファイル全部（入口から import で辿れるもの）。1 度取ったら覚えておく。 */
function screenFiles(screen) {
  const found = screenFileCache.get(screen.entry);
  if (found !== undefined) return found;
  const files = closureFrom([screen.entry]);
  screenFileCache.set(screen.entry, files);
  return files;
}

/** グループのテストが辿り着くファイル全部（テスト自身も含む）。 */
function closureOf(group) {
  // 下の段（`test/<グループ>/<何か>/x.test.ts`）も見る。tsc は `test/**/*.ts` を
  // コンパイルするので、ここで 1 段しか見ないと、下の段のテストが黙って回らない。
  return closureFrom(filesUnder(path.join(TEST_DIR, group), (name) => name.endsWith(".ts") || name.endsWith(".tsx")));
}

/** グループ名 -> そのグループが読むファイルの集合。 */
function closures() {
  const map = new Map();
  for (const group of groupNames()) map.set(group, closureOf(group));
  return map;
}

/**
 * 束ねた画面を読むグループ。
 *
 * `rel`（触ったファイル）を渡すと、**その画面の束ねに入るファイルか** を閉包で見て絞る。
 * 置き場の綴り（`src/webview/<名前>/` で始まるか）では決めない。画面をまたぐ import が
 * 1 本でも入ると、直したのに回らない側（回すものが減る側）に外れるため。
 *
 * どの画面の閉包にも入らないもの（`src/webview/vscode.ts` のような共通の部品）は全部に効くと見る。
 */
function webviewGroups(map, rel) {
  const all = screens();
  const absolute = rel === undefined ? undefined : path.join(ROOT, rel);
  const matched = absolute === undefined ? [] : all.filter((screen) => screenFiles(screen).has(absolute));
  const wanted = matched.length === 0 ? all : matched;
  const groups = groupsFor(wanted, map);
  // 絞った先にグループが 1 つも無い（グループも入口も無い置き方をされている）。どれが読むか
  // 決められないので、束ねを読むグループを全部返す。決められないときは多い側へ外す
  if (groups.length === 0 && wanted !== all) {
    return groupsFor(all, map);
  }
  return groups;
}

/** 画面の並びを、それを読むグループの並びにする。 */
function groupsFor(wanted, map) {
  const groups = new Set();
  for (const screen of wanted) {
    // その画面の束ねを読むテストを持つグループ
    for (const [group, files] of map) {
      if (files.has(screen.helper)) groups.add(group);
    }
    // 画面と同じ名前のグループは、テストの入口の綴りが約束と違っても必ず回す。
    // ここが無いと、画面を足して `test/helpers/<名前>.ts` を作り忘れたときに、
    // その画面のテストだけが黙って回らなくなる
    if (map.has(screen.name)) groups.add(screen.name);
  }
  return [...groups].sort();
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
  const inside = at >= 0 ? slashed.slice(at + marker.length) : slashed;
  if (at < 0 && path.isAbsolute(slashed)) return null;
  // `./src/x.ts` や `src/../src/x.ts` を `src/x.ts` に直す。直さないまま
  // `startsWith("src/webview/")` のような綴りの比較に渡すと、`./` が付いただけで
  // 別のファイルとして扱われ、回すものが減る側に外れる。
  const normalized = path.posix.normalize(inside);
  if (normalized.startsWith("../")) return null;
  if (at >= 0) return normalized;
  // 拡張のディレクトリから打たれた相対パス。実在するものだけ受ける。
  return fs.existsSync(path.join(ROOT, normalized)) ? normalized : null;
}

/**
 * 触ったファイル 1 つが呼ぶもの。
 *
 * 返すのは `{ groups, compile }`。`groups` が空で `compile` が真なら、テストは
 * 何も読まないが `tsc` は見るファイル（拡張ホスト側の panel など）。
 */
function callFor(rel, map) {
  if (rel === null) return { groups: [], compile: false, webview: false };

  // 読み物と絵。コンパイルもテストも要らない。
  if (rel.endsWith(".md") || rel.startsWith("media/")) return { groups: [], compile: false, webview: false };
  // 生成物と依存の置き場。触ったと数えない。
  if (rel.startsWith("out/") || rel.startsWith("node_modules/")) {
    return { groups: [], compile: false, webview: false };
  }

  const all = [...map.keys()];

  // 組み立ての土台。どのグループの結果も変わりうる。
  if (
    rel === "package.json" ||
    rel === "pnpm-lock.yaml" ||
    /^tsconfig[^/]*\.json$/.test(rel) ||
    rel.startsWith("scripts/")
  ) {
    return { groups: all, compile: true, webview: false };
  }

  // 固定データは import ではなく実行時に名前で開くので、辿れない。全部に効くと見る。
  if (rel.startsWith("test/fixtures/")) return { groups: all, compile: true, webview: false };

  const absolute = path.join(ROOT, rel);
  const groups = all.filter((group) => map.get(group).has(absolute));

  // 画面（React）は esbuild が束ね、テストは束ねたものを読む。その道は import では
  // 辿れないので、束ねたものを読むグループを足す。辿れたぶん（テストが画面のファイルを
  // 直に import している場合）は落とさずに和を取る。
  // 画面のファイルは `tsconfig.json` が exclude するので、`tsconfig.test.json` では型を見ない。
  // esbuild も型を見ない。回すグループが 0 本でも、画面の型の検査だけは必ず通す
  // （通さないと「型の検査だけ通しました」と出るのに何も見ていないターンができる）
  if (rel.startsWith("src/webview/")) {
    return { groups: [...new Set([...groups, ...webviewGroups(map, rel)])].sort(), compile: true, webview: true };
  }

  return { groups, compile: true, webview: false };
}

/** 触ったパスの一覧から、回すものを決める。 */
function planFor(paths) {
  const map = closures();
  const groups = new Set();
  let compile = false;
  let webview = false;
  for (const given of paths) {
    const call = callFor(relativeToExtension(given), map);
    if (call.compile) compile = true;
    if (call.webview) webview = true;
    for (const group of call.groups) groups.add(group);
  }
  return finish([...groups].sort(), compile, map, webview);
}

function planForGroups(names) {
  const map = closures();
  const known = new Set(map.keys());
  const unknown = names.filter((name) => !known.has(name));
  if (unknown.length > 0) {
    console.error(`知らないグループ: ${unknown.join(" ")}（ある: ${[...known].join(" ")}）`);
    process.exit(2);
  }
  return finish([...new Set(names)].sort(), true, map, false);
}

function finish(groups, compile, map, webview) {
  const needsWebview = webviewGroups(map);
  return {
    groups,
    compile: compile || groups.length > 0,
    // 触ったものが画面なら（`webview`）、回すグループが無くても型は見る
    webview: webview || groups.some((group) => needsWebview.includes(group)),
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
    // 下の段も見る。closureOf と同じ理由で、1 段しか見ないと黙って回らない。
    for (const full of filesUnder(dir, (name) => name.endsWith(".test.js"))) {
      if (domOnly && !full.endsWith(".dom.test.js")) continue;
      files.push(path.relative(ROOT, full));
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
    // 3 は「環境が足りない」で、テストが落ちたのではない。hook はこれを差し戻しに
    // 数えない（落ちてもいないテストを直せと言われることになるため）。
    return NOT_READY;
  }

  let code = run(process.execPath, [path.join("scripts", "clean-out.js")]);
  if (code !== 0) return code;

  code = run(process.execPath, [tsc, "-p", "tsconfig.test.json"]);
  if (code !== 0) return code;

  if (plan.webview) {
    code = run(process.execPath, [tsc, "-p", "tsconfig.webview.json"]);
    if (code !== 0) return code;
  }

  // 束ねるのは、型を見ないときでも必ず。`clean-out.js` が `out/webview` を消すので、
  // ここで作り直さないと、束ねたものを読む側（拡張の webview-script.ts、board の
  // テスト）が「画面が束ねられていない」で落ちる。esbuild は 0.1 秒ほど。
  code = run(process.execPath, [path.join("scripts", "bundle-webview.js")]);
  if (code !== 0) return code;

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
  // spec レポータにするのは、落ちたものが末尾にまとまるから。node は端末でないときは
  // 既定で TAP を出し、そこでは `not ok` が落ちたファイルの位置に出る。ターンの終わりの
  // hook がモデルへ渡せるのは末尾 40 行だけなので、TAP だと「落ちた」とだけ伝わって
  // 何が落ちたかが入らない。
  return run(process.execPath, ["--test", "--test-reporter=spec", ...files]);
}

process.exit(main(process.argv.slice(2)));
