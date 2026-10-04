// 拡張を dist/ に組む。
//
//   node scripts/build.js [--hosts <一覧の JSON>] [--out <出力先>]
//
// 1. 型を見る（画面・Worker・service worker は tsconfig.json、Node で回す部品は tsconfig.node.json）
// 2. 通信先の一覧（既定 hosts.json）から manifest.json を組む。`host_permissions` と CSP の
//    `connect-src` は一覧の API のオリジンだけ。組織ごとのビルドは --hosts で一覧を替える
// 3. esbuild で 4 本（background・board・options・worker）をバンドルする
// 4. 同梱の Pyodide を node_modules からコピーし、scripts/pyodide-files.json のハッシュと突き合わせる
//    （npm の lockfile の integrity とは別に、コピーした物そのものを確かめる）
// 5. 同梱の Python（PyYAML・ccnavi・入口）を zip に組む（scripts/python.js）
//
// Pyodide（約 14MB）はリポジトリに入れない。取ってくるのは pnpm install と PyYAML の sdist だけ。
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as esbuild from "esbuild";
import { buildPythonZip } from "./python.js";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? path.resolve(process.argv[i + 1]) : fallback;
}

export function tsc(project) {
  const bin = path.join(HERE, "node_modules", "typescript", "bin", "tsc");
  const r = spawnSync(process.execPath, [bin, "-p", project], { cwd: HERE, stdio: "inherit" });
  if (r.status !== 0) throw new Error(`tsc -p ${project} が落ちた`);
}

function copyPyodide(out) {
  const pin = JSON.parse(fs.readFileSync(path.join(HERE, "scripts", "pyodide-files.json"), "utf8"));
  const src = path.join(HERE, "node_modules", "pyodide");
  const have = JSON.parse(fs.readFileSync(path.join(src, "package.json"), "utf8")).version;
  if (have !== pin.version) throw new Error(`pyodide の版が ${have}。${pin.version} を入れる（pnpm install --frozen-lockfile）`);
  fs.mkdirSync(path.join(out, "pyodide"), { recursive: true });
  let bytes = 0;
  for (const [name, sha] of Object.entries(pin.files)) {
    const buf = fs.readFileSync(path.join(src, name));
    const got = createHash("sha256").update(buf).digest("hex");
    if (got !== sha) throw new Error(`pyodide/${name} のハッシュが違う: ${got}`);
    fs.writeFileSync(path.join(out, "pyodide", name), buf);
    bytes += buf.length;
  }
  return bytes;
}

export async function build({ hostsFile = path.join(HERE, "hosts.json"), out = path.join(HERE, "dist") } = {}) {
  tsc("tsconfig.json");
  tsc("tsconfig.node.json");
  const { parseHosts, manifest } = await import(path.join(HERE, "out", "src", "core", "hosts.js"));
  const hosts = parseHosts(fs.readFileSync(hostsFile, "utf8"));
  const version = JSON.parse(fs.readFileSync(path.join(HERE, "package.json"), "utf8")).version;
  // 同梱の ccnavi の互換の版（service worker が「始める」の前に統合先の CCNAVI_COMPAT と比べる）
  const compatMatch = /^COMPAT = (\d+)$/m.exec(fs.readFileSync(path.join(HERE, "..", "..", "..", "src", "ccnavi", "entry", "version.py"), "utf8"));
  if (!compatMatch) throw new Error("src/ccnavi/entry/version.py の COMPAT を読めない");
  const compat = Number(compatMatch[1]);

  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, "manifest.json"), JSON.stringify(manifest(hosts, version), null, 2) + "\n");

  await esbuild.build({
    entryPoints: {
      background: "src/background/main.ts",
      board: "src/board/main.ts",
      options: "src/options/main.ts",
      worker: "src/worker/main.ts",
    },
    absWorkingDir: HERE,
    outdir: out,
    bundle: true,
    format: "esm",
    target: "chrome116",
    minify: true,
    legalComments: "linked",
    external: ["./pyodide/pyodide.mjs"],
    define: { __CCNAVI_HOSTS__: JSON.stringify(hosts), __CCNAVI_VERSION__: JSON.stringify(version), __CCNAVI_COMPAT__: JSON.stringify(compat) },
    logLevel: "warning",
  });
  for (const name of fs.readdirSync(path.join(HERE, "static"))) {
    fs.copyFileSync(path.join(HERE, "static", name), path.join(out, name));
  }
  const pyodideBytes = copyPyodide(out);
  const python = await buildPythonZip(path.join(out, "py", "ccnavi-py.zip"));
  const total = du(out);
  return { out, hosts: hosts.map((h) => h.id), pyodideBytes, python, total };
}

function du(dir) {
  let n = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    n += e.isDirectory() ? du(p) : fs.statSync(p).size;
  }
  return n;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const info = await build({ hostsFile: arg("--hosts", undefined), out: arg("--out", undefined) });
  const mb = (n) => `${(n / 1024 / 1024).toFixed(1)}MB`;
  console.log(
    `組んだ: ${info.out}（通信先 ${info.hosts.join(", ")}、全体 ${mb(info.total)}、Pyodide ${mb(info.pyodideBytes)}、` +
      `Python ${info.python.python}・PyYAML ${info.python.pyyaml}・${info.python.files} ファイル ${mb(info.python.bytes)}）`,
  );
}
