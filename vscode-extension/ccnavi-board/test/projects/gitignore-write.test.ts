import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { addProjectsToGitignore } from "../../src/core/gitignore-write.js";

function scratch(): string {
  return fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-gitignore-write-")));
}

test("CB-T289 .gitignore が無い（ENOENT）ときは置き場の行だけで新しく作る", () => {
  const root = scratch();
  const file = path.join(root, ".gitignore");
  assert.deepEqual(addProjectsToGitignore(file, "projects"), { ok: true });
  assert.match(fs.readFileSync(file, "utf8"), /^# .*\n\/projects\/\n$/);
});

test("CB-T290 既にある .gitignore には置き場の行を足し、元の行は残す", () => {
  const root = scratch();
  const file = path.join(root, ".gitignore");
  fs.writeFileSync(file, "/dist/\n", "utf8");
  assert.deepEqual(addProjectsToGitignore(file, "projects"), { ok: true });
  assert.match(fs.readFileSync(file, "utf8"), /^\/dist\/\n\n# .*\n\/projects\/\n$/);
});

test("CB-T291 無い以外で読めない（ディレクトリ = EISDIR）ときは書かずに止め、利用者への文面を返す", { skip: process.platform === "win32" }, () => {
  const root = scratch();
  const file = path.join(root, ".gitignore");
  fs.mkdirSync(file);
  fs.writeFileSync(path.join(file, "keep"), "中身\n", "utf8");
  const result = addProjectsToGitignore(file, "projects");
  assert.equal(result.ok, false);
  if (result.ok) return;
  assert.equal(result.step, "read");
  assert.equal(result.code, "EISDIR");
  assert.match(result.message, /^\.gitignore を読めないので書きませんでした: /);
  assert.ok(fs.statSync(file).isDirectory());
  assert.deepEqual(fs.readdirSync(file), ["keep"]);
  assert.equal(fs.readFileSync(path.join(file, "keep"), "utf8"), "中身\n");
});
