/**
 * 試験から同梱の ccnavi を呼ぶ手段を 2 つ作る。
 *
 * - `wasmCcnavi`: Node の上で、ビルドが組んだ dist/ccnavi.wasm を起こしたもの。拡張の Worker と同じ中身で、
 *   仮のツリーはメモリの上の fs に組む
 * - `nativeCcnavi`: 同じ入口（go/cmd/ccnavi-chrome）を手元向けに組んだもの（dist の外の .cache/）。手元の
 *   ファイルシステムの上で 1 行 1 要求で回す
 *
 * 同じ要求を両方に投げて答えを比べ、メモリの上の fs と手元のファイルシステムで同じ判定を出すことを確かめる。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { CcnaviCall } from "../../src/core/ccnavi.js";
import { startCcnavi } from "../../src/core/wasm.js";

export const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
export const REPO = path.resolve(HERE, "..", "..", "..");
export const WASM = path.join(HERE, "dist", "ccnavi.wasm");
const WASM_EXEC = path.join(HERE, "dist", "wasm_exec.js");
/** 手元向けの入口。ビルド（scripts/build.js）が組む */
export const NATIVE = path.join(HERE, ".cache", process.platform === "win32" ? "ccnavi-chrome.exe" : "ccnavi-chrome");

export async function wasmCcnavi(): Promise<{ call: CcnaviCall; bootMs: number }> {
  if (!fs.existsSync(WASM)) throw new Error(`${WASM} が無い。先に node scripts/build.js を回す`);
  const ccnavi = await startCcnavi(fs.readFileSync(WASM), () => import(pathToFileURL(WASM_EXEC).href));
  return { call: async (req) => JSON.parse(ccnavi.handle(JSON.stringify(req))), bootMs: ccnavi.bootMs };
}

export function nativeCcnavi(): { call: CcnaviCall; close(): void } {
  if (!fs.existsSync(NATIVE)) throw new Error(`${NATIVE} が無い。先に node scripts/build.js を回す`);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "ccnavi-chrome-"));
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (v !== undefined && !k.startsWith("CCNAVI_") && k !== "CLAUDE_PROJECT_DIR") env[k] = v;
  }
  const child = spawn(NATIVE, [root], { env, stdio: ["pipe", "pipe", "inherit"] });
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
