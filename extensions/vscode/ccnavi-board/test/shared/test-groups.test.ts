/**
 * `scripts/test-groups.js`（触ったファイルから回すグループを決める）のうち、共通の部品
 * （`extensions/shared/`）の扱いを確かめる。sharedのCSSは各画面のstyle.cssが
 * `@ccnavi/shared/...`で`@import`するため、直したら読む画面のグループが回らないといけない。
 * どの画面も読まないsharedのファイル（Chrome拡張だけが読む`browser-theme.css`）は、`shared`グループだけを回す
 * （`appearance.css`との一致を確かめるCB-T393 がそこにあるため）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const ROOT = path.join(__dirname, "..", "..", "..");
const SHARED = path.join(ROOT, "..", "..", "shared", "styles");

/** `--plan --for`の出力の1行を、グループのリスト・compile・webviewにする */
function plan(...paths: string[]): Plan {
  return planAt(ROOT, ...paths);
}

type Plan = { groups: string[]; compile: boolean; webview: boolean };

/** `root`のコピーの runner を回す（コピーを壊して試すとき用） */
function planAt(root: string, ...paths: string[]): Plan {
  const line = execFileSync(process.execPath, [path.join(root, "scripts", "test-groups.js"), "--plan", "--for", ...paths], { encoding: "utf8", cwd: root }).trim();
  const match = /^groups=(\S*) compile=(yes|no) webview=(yes|no)$/.exec(line);
  assert.ok(match !== null, line);
  return { groups: match[1] === "" ? [] : match[1].split(","), compile: match[2] === "yes", webview: match[3] === "yes" };
}

test("CB-T394 sharedのCSSを直すと、@ccnavi/sharedを @importする画面のグループとコンパイル・画面の型の検査が回る。どの画面も読まないもの（browser-theme.css）はsharedグループだけ", () => {
  const page = plan(path.join(SHARED, "page.css"));
  assert.deepEqual(page.groups, ["board", "devtools", "flow", "handoff", "phases", "projects", "risk", "rules", "shared", "templates", "workflow"]);
  assert.ok(page.compile && page.webview);
  // 区切りがバックスラッシュで来る形（Windows）も同じ
  assert.deepEqual(plan(path.join(SHARED, "page.css").replace(/\//g, "\\")), page);
  // どの画面も読まないbrowser-theme.cssは、全グループではなくsharedグループ（CB-T393 の置き場）だけ。sharedグループは画面のバンドルを読むので、型の検査とバンドルは付く
  assert.deepEqual(plan(path.join(SHARED, "browser-theme.css")), { groups: ["shared"], compile: true, webview: true });
  // 拡張の外のどのファイルでもない（Chrome拡張だけのファイル）は何も回さない
  assert.deepEqual(plan(path.join(ROOT, "..", "..", "chrome", "ccnavi-approval", "x.css")), { groups: [], compile: false, webview: false });
});

test("CB-T395 パスの祖先にextensions/shared/ が含まれていても、VS Code拡張のファイルは拡張のファイルとして扱う", () => {
  const inside = "extensions/vscode/ccnavi-board/src/core/hooks.ts";
  const plain = plan(`/home/u/clone/${inside}`);
  assert.deepEqual(plain.groups, ["rules", "shared"]);
  // 祖先にsharedがあっても（クローンの置き場の名前など）同じ
  assert.deepEqual(plan(`/home/u/extensions/shared/clone/${inside}`), plain);
  // ワークツリーの置き場の下でも同じ
  assert.deepEqual(plan(`/home/u/extensions/shared/clone/.claude/worktrees/w/${inside}`), plain);
  // 拡張の目印が無いときだけshared。目印が複数あるときは最後の出現を採る
  assert.deepEqual(plan(path.join(SHARED, "browser-theme.css")), { groups: ["shared"], compile: true, webview: true });
  assert.deepEqual(plan(`/home/u/extensions/vscode/ccnavi-board/.claude/worktrees/w/${inside}`), plain);
});

test("CB-T1219 開発用の画面確認ツール（scripts/render-board.jsとscripts/lib/）を直すとdevtoolsグループだけが回り、他のscripts/ は全グループで回る", () => {
  assert.deepEqual(plan("scripts/render-board.js"), { groups: ["devtools"], compile: true, webview: true });
  assert.deepEqual(plan("scripts/lib/vscode-stub.js"), { groups: ["devtools"], compile: true, webview: true });
  assert.ok(plan("scripts/test-groups.js").groups.length > 1);
  assert.ok(plan("scripts/test-groups.js").groups.includes("devtools"));
});

// ここから下は、触ったファイルから回すグループを決める計画（--plan）の観点。

const NOT_GROUPS = new Set(["helpers", "fixtures"]);
/** `--plan`が読むもの。コピーで試すときはこれだけあればよい（node_modules も out も読まない） */
const PLAN_INPUTS = ["scripts", "src", "test", "package.json", "tsconfig.test.json"];

function groupsOnDisk(): string[] {
  const base = path.join(ROOT, "test");
  return fs
    .readdirSync(base, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !NOT_GROUPS.has(entry.name))
    .map((entry) => entry.name)
    .sort();
}

function sorted(plan0: Plan): Plan {
  return { ...plan0, groups: [...plan0.groups].sort() };
}

/** `--plan`が読むものだけをコピーした拡張のルートで`body`を回す。中で好きに壊してよい */
function withCopiedBoard(body: (root: string) => void): void {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-plan-"));
  try {
    const root = path.join(temp, "board");
    fs.mkdirSync(root);
    for (const name of PLAN_INPUTS) fs.cpSync(path.join(ROOT, name), path.join(root, name), { recursive: true });
    body(root);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

test("CB-T1266 読むグループだけが挙がる: 部品は読むグループを、テストファイルは自分のグループだけを、画面は同じ画面のグループとバンドルを回す", () => {
  // リスク管理画面の文書。risk のテストが直接読む。import の辿り方は表で持たないので「読むものが挙がり、読まないものは挙がらない」だけを見る
  const doc = plan("src/core/risk-doc.ts");
  assert.ok(doc.groups.includes("risk"));
  assert.ok(!doc.groups.includes("rules") && !doc.groups.includes("board"));
  assert.ok(doc.compile);
  assert.deepEqual(plan("test/risk/risk-doc.test.ts").groups, ["risk"]);
  // 画面（React）は esbuild がバンドルし、テストはバンドルしたものを読む。画面ごとの置き場を runner が知っている
  const board = plan("src/webview/board/App.tsx");
  assert.ok(board.groups.includes("board") && !board.groups.includes("projects"));
  assert.ok(board.webview);
  const projects = plan("src/webview/projects/App.tsx");
  assert.ok(projects.groups.includes("projects") && !projects.groups.includes("board"));
  assert.ok(projects.webview);
  // どの画面にも属さない部品は全画面に影響する
  const part = plan("src/webview/vscode.ts");
  assert.ok(part.groups.includes("board") && part.groups.includes("projects"));
  assert.ok(part.webview);
});

test("CB-T1267 全グループが回るもの（ビルドの土台・固定データ・共通の道具）と、何も回らないもの（文書・拡張ホスト・拡張の外）", () => {
  const all = groupsOnDisk();
  assert.ok(all.length > 1);
  for (const file of ["package.json", "tsconfig.test.json", "scripts/bundle-webview.js", "test/fixtures/board.json"]) {
    assert.deepEqual(sorted(plan(file)).groups, all, file);
  }
  // runner がディスク上のグループを全部知っている
  assert.deepEqual(sorted(plan("package.json")).groups, all);
  // 画面を描くグループはどれも読む部品。devtools は happy-dom を使わず、bugreport も dom.ts を import しない
  assert.deepEqual(sorted(plan("test/helpers/dom.ts")).groups, all.filter((name) => name !== "devtools" && name !== "bugreport"));
  // 拡張ホスト側の入口はテストが読まない。型だけ見る
  assert.deepEqual(plan("src/extension.ts"), { groups: [], compile: true, webview: false });
  assert.deepEqual(plan("README.md"), { groups: [], compile: false, webview: false });
  assert.deepEqual(plan(path.join(ROOT, "..", "..", "..", "go", "main.go")), { groups: [], compile: false, webview: false });
});

test("CB-T1268 パスの綴りが変わっても同じ結論: ./ 付き・絶対パス・ワークツリーの下・複数指定", () => {
  for (const given of ["src/webview/board/App.tsx", "./src/webview/board/App.tsx"]) {
    const got = plan(given);
    assert.ok(got.groups.includes("board") && got.webview, given);
  }
  assert.deepEqual(plan("package.json"), plan("./package.json"));
  const expected = plan("src/core/risk-doc.ts");
  assert.deepEqual(plan(path.join(ROOT, "src", "core", "risk-doc.ts")), expected);
  assert.deepEqual(plan("/tmp/ws/.claude/worktrees/w1/extensions/vscode/ccnavi-board/src/core/risk-doc.ts"), expected);
  const several = plan("src/core/risk-doc.ts", "test/rules/hooks.test.ts", "README.md");
  assert.ok(several.groups.includes("risk") && several.groups.includes("rules"));
});

test("CB-T1269 画面の名前の約束: src/webview/<名前>/main.tsx ごとに test/<名前>/ と test/helpers/<名前>.ts がある", () => {
  const webview = path.join(ROOT, "src", "webview");
  const names = fs.readdirSync(webview).filter((name) => fs.existsSync(path.join(webview, name, "main.tsx")));
  assert.ok(names.length > 0, "画面が 1 つも見つからない");
  for (const name of names) {
    assert.ok(groupsOnDisk().includes(name), `${name}: test/${name}/ が無い`);
    assert.ok(fs.existsSync(path.join(ROOT, "test", "helpers", `${name}.ts`)), `${name}: test/helpers/${name}.ts が無い`);
  }
});

test("CB-T1270 画面をまたぐ import があっても、import 先の画面のグループが回る（閉包で見る）", () => {
  withCopiedBoard((root) => {
    // 行頭から始まるように改行を先に置く。行の途中だと runner の正規表現に当たらず、穴があっても通ってしまう
    fs.appendFileSync(path.join(root, "src", "webview", "projects", "App.tsx"), '\nimport "../board/state.js";\n');
    const got = planAt(root, "src/webview/board/state.ts");
    assert.ok(got.groups.includes("board"));
    assert.ok(got.groups.includes("projects"), "projects のバンドルにも入るので、projects も回る");
  });
});

test("CB-T1271 画面を足してテストの helper を作り忘れても、同じ名前のグループは回る", () => {
  withCopiedBoard((root) => {
    fs.mkdirSync(path.join(root, "src", "webview", "fakescreen"));
    fs.mkdirSync(path.join(root, "test", "fakescreen"));
    fs.writeFileSync(path.join(root, "src", "webview", "fakescreen", "main.tsx"), "export const x = 1;\n");
    fs.writeFileSync(path.join(root, "test", "fakescreen", "fake.test.ts"), 'import { test } from "node:test";\ntest("f", () => {});\n');
    const got = planAt(root, "src/webview/fakescreen/main.tsx");
    assert.ok(got.groups.includes("fakescreen"));
    assert.ok(got.webview);
  });
});

test("CB-T1272 画面の約束を満たさない置き方の webview ファイルでも、型の検査とバンドルは必ず入り、バンドルを読むグループは全部回る", () => {
  withCopiedBoard((root) => {
    fs.mkdirSync(path.join(root, "src", "webview", "parts"));
    fs.writeFileSync(path.join(root, "src", "webview", "parts", "main.tsx"), "export const x = 1;\n");
    const got = planAt(root, "src/webview/parts/main.tsx");
    assert.ok(got.webview && got.compile);
    for (const group of ["board", "projects"]) assert.ok(got.groups.includes(group), group);
  });
});
