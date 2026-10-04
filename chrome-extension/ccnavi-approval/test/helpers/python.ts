/**
 * 試験から Python の入口を呼ぶ手段を 2 つ作る。
 *
 * - `pyodidePy`: Node の上の Pyodide に、ビルドが組んだ zip（dist/py/ccnavi-py.zip）を展開したもの。
 *   拡張の Worker と同じ中身
 * - `nativePy`: 手元の CPython（リポジトリの uv の環境）で入口を 1 行 1 要求で回すもの
 *
 * 同じ要求を両方に投げて答えを比べ、拡張と手元が同じ判定を出すことを確かめる。
 */
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import type { PyCall } from "../../src/core/py.js";

export const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
export const REPO = path.resolve(HERE, "..", "..");
export const ZIP = path.join(HERE, "dist", "py", "ccnavi-py.zip");

export async function pyodidePy(): Promise<{ call: PyCall; version: string }> {
  if (!fs.existsSync(ZIP)) throw new Error(`${ZIP} が無い。先に node scripts/build.js を回す`);
  const { loadPyodide } = await import("pyodide");
  const py = await loadPyodide({ indexURL: path.join(HERE, "node_modules", "pyodide") + path.sep });
  py.unpackArchive(new Uint8Array(fs.readFileSync(ZIP)), "zip", { extractDir: "/app" });
  py.runPython("import sys\nsys.path.insert(0, '/app')\nimport ccnavi_chrome\nhandle = ccnavi_chrome.handle");
  const handle = py.globals.get("handle") as (s: string) => string;
  return { call: async (req) => JSON.parse(handle(JSON.stringify(req))), version: py.version };
}

/** uv が無ければ null（試験は飛ばす理由を言う） */
export function nativePy(): { call: PyCall; close(): void } | null {
  if (spawnSync("uv", ["--version"], { stdio: "ignore" }).status !== 0) return null;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-chrome-"));
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && !k.startsWith("CCNAVI_") && k !== "CLAUDE_PROJECT_DIR") env[k] = v;
  }
  env.PYTHONPATH = REPO;
  env.PYTHONUTF8 = "1";
  const child = spawn("uv", ["run", "--project", REPO, "--quiet", "python", path.join(HERE, "py", "ccnavi_chrome.py"), root], {
    env,
    stdio: ["pipe", "pipe", "inherit"],
  });
  const lines = readline.createInterface({ input: child.stdout });
  const waiting: ((line: string) => void)[] = [];
  lines.on("line", (line) => waiting.shift()?.(line));
  return {
    call: (req) =>
      new Promise((resolve) => {
        waiting.push((line) => resolve(JSON.parse(line)));
        child.stdin.write(JSON.stringify(req) + "\n");
      }),
    close() {
      child.stdin.end();
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}
