/**
 * `scripts/test-groups.js`（触ったファイルから回すグループを決める）のうち、共通の部品
 * （`extensions/shared/`）の扱いを確かめる。shared の CSS は各画面の style.css が
 * `@ccnavi/shared/...` で `@import` するため、直したら読む画面のグループが回らないといけない。
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

test("CB-T392 shared の CSS を直すと、@ccnavi/shared を @import する画面のグループとコンパイル・画面の型の検査が回る", () => {
  const page = plan(path.join(SHARED, "page.css"));
  assert.deepEqual(page.groups, ["board", "flow", "phases", "projects", "risk", "rules", "shared", "workflow"]);
  assert.ok(page.compile && page.webview);
  // 区切りがバックスラッシュで来る形（Windows）も同じ
  assert.deepEqual(plan(path.join(SHARED, "page.css").replace(/\//g, "\\")), page);
  // 拡張の外のどのファイルでもない（Chrome 拡張だけのファイル）は何も回さない
  assert.deepEqual(plan(path.join(ROOT, "..", "..", "chrome", "ccnavi-approval", "x.css")), { groups: [], compile: false, webview: false });
});
