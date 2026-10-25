/**
 * `scripts/test-groups.js`（触ったファイルから回すグループを決める）のうち、共通の部品
 * （`extensions/shared/`）の扱いを確かめる。shared の CSS は各画面の style.css が
 * `@ccnavi/shared/...` で `@import` するため、直したら読む画面のグループが回らないといけない。
 * どの画面も読まない shared のファイル（Chrome 拡張だけが読む `browser-theme.css`）は、`shared` グループだけを回す
 * （`appearance.css` との一致を確かめる CB-T393 がそこにあるため）。
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as path from "node:path";

const ROOT = path.join(__dirname, "..", "..", "..");
const SHARED = path.join(ROOT, "..", "..", "shared", "styles");

/** `--plan --for` の出力の 1 行を、グループの配列・compile・webview にする */
function plan(...paths: string[]): { groups: string[]; compile: boolean; webview: boolean } {
  const line = execFileSync(process.execPath, [path.join(ROOT, "scripts", "test-groups.js"), "--plan", "--for", ...paths], { encoding: "utf8" }).trim();
  const match = /^groups=(\S*) compile=(yes|no) webview=(yes|no)$/.exec(line);
  assert.ok(match !== null, line);
  return { groups: match[1] === "" ? [] : match[1].split(","), compile: match[2] === "yes", webview: match[3] === "yes" };
}

test("CB-T394 shared の CSS を直すと、@ccnavi/shared を @import する画面のグループとコンパイル・画面の型の検査が回る。どの画面も読まないもの（browser-theme.css）は shared グループだけ", () => {
  const page = plan(path.join(SHARED, "page.css"));
  assert.deepEqual(page.groups, ["board", "flow", "handoff", "phases", "projects", "risk", "rules", "shared", "templates", "workflow"]);
  assert.ok(page.compile && page.webview);
  // 区切りがバックスラッシュで来る形（Windows）も同じ
  assert.deepEqual(plan(path.join(SHARED, "page.css").replace(/\//g, "\\")), page);
  // どの画面も読まない browser-theme.css は、全グループではなく shared グループ（CB-T393 の置き場）だけ。shared グループは画面のバンドルを読むので、型の検査とバンドルは付く
  assert.deepEqual(plan(path.join(SHARED, "browser-theme.css")), { groups: ["shared"], compile: true, webview: true });
  // 拡張の外のどのファイルでもない（Chrome 拡張だけのファイル）は何も回さない
  assert.deepEqual(plan(path.join(ROOT, "..", "..", "chrome", "ccnavi-approval", "x.css")), { groups: [], compile: false, webview: false });
});

test("CB-T395 パスの祖先に extensions/shared/ が含まれていても、VS Code 拡張のファイルは拡張のファイルとして扱う", () => {
  const inside = "extensions/vscode/ccnavi-board/src/core/hooks.ts";
  const plain = plan(`/home/u/clone/${inside}`);
  assert.deepEqual(plain.groups, ["rules", "shared"]);
  // 祖先に shared があっても（クローンの置き場の名前など）同じ
  assert.deepEqual(plan(`/home/u/extensions/shared/clone/${inside}`), plain);
  // ワークツリーの置き場の下でも同じ
  assert.deepEqual(plan(`/home/u/extensions/shared/clone/.claude/worktrees/w/${inside}`), plain);
  // 拡張の目印が無いときだけ shared。目印が複数あるときは最後の出現を採る
  assert.deepEqual(plan(path.join(SHARED, "browser-theme.css")), { groups: ["shared"], compile: true, webview: true });
  assert.deepEqual(plan(`/home/u/extensions/vscode/ccnavi-board/.claude/worktrees/w/${inside}`), plain);
});

test("CB-T1219 開発用の画面確認ツール（scripts/render-board.js と scripts/lib/）を直すと devtools グループだけが回り、他の scripts/ は全部が回る", () => {
  assert.deepEqual(plan("scripts/render-board.js"), { groups: ["devtools"], compile: true, webview: false });
  assert.deepEqual(plan("scripts/lib/vscode-stub.js"), { groups: ["devtools"], compile: true, webview: false });
  assert.ok(plan("scripts/test-groups.js").groups.length > 1);
  assert.ok(plan("scripts/test-groups.js").groups.includes("devtools"));
});
