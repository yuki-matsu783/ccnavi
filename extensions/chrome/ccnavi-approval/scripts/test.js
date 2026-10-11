// 試験を回す。
//
//   node scripts/test.js         組み立て（dist/）→ 単体・Pyodide（Node）・手元の CPython との突き合わせ
//   node scripts/test.js --e2e   試験用の通信先（127.0.0.1）で組み立て（dist-e2e/）→ 拡張を読み込んだ Chromium で実機の試験
//
// 実機の試験は Playwright の Chromium を使う（PLAYWRIGHT_BROWSERS_PATH。`playwright install` はしない）。
// 無ければ 3 で終わる（失敗した 1 とは分ける）。
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "./build.js";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const e2e = process.argv.includes("--e2e");

function tests(dir) {
  return fs
    .readdirSync(dir)
    .filter((n) => n.endsWith(".test.js"))
    .sort()
    .map((n) => path.join(dir, n));
}

if (e2e) {
  await build({ hostsFile: path.join(HERE, "test", "fixtures", "hosts.e2e.json"), out: path.join(HERE, "dist-e2e") });
  const { chromium } = await import("playwright-core");
  if (!fs.existsSync(chromium.executablePath())) {
    console.error(`Chromium が無い: ${chromium.executablePath()}（PLAYWRIGHT_BROWSERS_PATH を見る）`);
    process.exit(3);
  }
} else {
  await build();
}
const files = tests(path.join(HERE, "out", "test", e2e ? "e2e" : ""));
const r = spawnSync(process.execPath, ["--test", "--test-reporter=spec", "--test-concurrency=1", ...files], { cwd: HERE, stdio: "inherit" });
process.exit(r.status ?? 1);
