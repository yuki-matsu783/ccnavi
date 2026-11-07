// 拡張を dist/ に組む。
//
//   node scripts/build.js [--hosts <一覧の JSON>] [--out <出力先>]
//
// 1. 型を見る（画面・Worker・service worker は tsconfig.json、Node で回す部品は tsconfig.node.json）
// 2. 通信先の一覧（既定 hosts.json）から manifest.json を組む。`host_permissions` と CSP の
//    は一覧の API のオリジンだけ。`connect-src` は固定の形（ADR-0128）。組織ごとのビルドは --hosts で一覧を替える
// 3. esbuild で 4 本（background・board・options・worker）をバンドルする。ボード（board）と設定画面（options）は React。
//    CSS は入口の src/options/style.css と src/board/style.css から options.css と board.css を組む
//    （共通の部品 @ccnavi/shared の CSS を読む）
// 4. 同梱の ccnavi（リポジトリの go/ の go/cmd/ccnavi-chrome）を GOOS=js GOARCH=wasm で ccnavi.wasm に組み、
//    組んだ Go の wasm_exec.js を一緒に置く（2 つは同じ Go の版でないと動かない）。試験が突き合わせる手元向けの
//    同じ入口を .cache/ に組む
//
// 要るのは pnpm install と Go（go/go.mod の版）だけ。Go は PATH の go を使う。
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import * as esbuild from "esbuild";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? path.resolve(process.argv[i + 1]) : fallback;
}

export function tsc(project) {
  const bin = path.join(HERE, "node_modules", "typescript", "bin", "tsc");
  const r = spawnSync(process.execPath, [bin, "-p", project], { cwd: HERE, stdio: "inherit" });
  if (r.status !== 0) throw new Error(`tsc -p ${project} が失敗した`);
}

const REPO = path.resolve(HERE, "..", "..", "..");
const GO_DIR = path.join(REPO, "go");
const ENTRY = "./cmd/ccnavi-chrome";

function go(args, env = {}) {
  const r = spawnSync("go", args, { cwd: GO_DIR, env: { ...process.env, ...env }, encoding: "utf8" });
  if (r.error) throw new Error(`go を起動できない（${r.error.message}）。Go を入れて PATH に置く`);
  if (r.status !== 0) throw new Error(`go ${args.join(" ")} が失敗した\n${r.stderr}`);
  return r.stdout.trim();
}

/** 同梱の ccnavi（WASM と wasm_exec.js）を out に、手元向けの同じ入口を .cache/ に組む。WASM の大きさを返す */
function buildCcnavi(out) {
  const wasm = path.join(out, "ccnavi.wasm");
  go(["build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w", "-o", wasm, ENTRY], { GOOS: "js", GOARCH: "wasm", CGO_ENABLED: "0" });
  const goroot = go(["env", "GOROOT"]);
  fs.copyFileSync(path.join(goroot, "lib", "wasm", "wasm_exec.js"), path.join(out, "wasm_exec.js"));
  const native = path.join(HERE, ".cache", process.platform === "win32" ? "ccnavi-chrome.exe" : "ccnavi-chrome");
  fs.mkdirSync(path.dirname(native), { recursive: true });
  go(["build", "-trimpath", "-buildvcs=false", "-o", native, ENTRY]);
  return { bytes: fs.statSync(wasm).size, go: go(["env", "GOVERSION"]) };
}

export async function build({ hostsFile = path.join(HERE, "hosts.json"), out = path.join(HERE, "dist") } = {}) {
  tsc("tsconfig.json");
  tsc("tsconfig.node.json");
  const { parseHosts, manifest } = await import(pathToFileURL(path.join(HERE, "out", "src", "core", "hosts.js")).href);
  const hosts = parseHosts(fs.readFileSync(hostsFile, "utf8"));
  const version = JSON.parse(fs.readFileSync(path.join(HERE, "package.json"), "utf8")).version;
  // 同梱の ccnavi の互換の版（service worker が「始める」の前に統合先の CCNAVI_COMPAT と比べる）
  const compatMatch = /^const Compat = (\d+)$/m.exec(fs.readFileSync(path.join(GO_DIR, "internal", "entry", "version", "version.go"), "utf8"));
  if (!compatMatch) throw new Error("go/internal/entry/version/version.go の Compat を読めない");
  const compat = Number(compatMatch[1]);

  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, "manifest.json"), JSON.stringify(manifest(hosts, version), null, 2) + "\n");

  await esbuild.build({
    entryPoints: {
      background: "src/background/main.ts",
      board: "src/board/main.tsx",
      options: "src/options/main.tsx",
      worker: "src/worker/main.ts",
    },
    absWorkingDir: HERE,
    outdir: out,
    bundle: true,
    format: "esm",
    target: "chrome116",
    minify: true,
    legalComments: "linked",
    jsx: "automatic",
    define: {
      __CCNAVI_HOSTS__: JSON.stringify(hosts),
      __CCNAVI_VERSION__: JSON.stringify(version),
      __CCNAVI_COMPAT__: JSON.stringify(compat),
      // React の開発用の検査（開発者向けの警告と遅い経路）を外す
      "process.env.NODE_ENV": '"production"',
    },
    logLevel: "warning",
  });
  // CSS は JS の入口から import させず、別に組む（VS Code 拡張の scripts/bundle-webview.js と同じ分け方）
  await esbuild.build({
    entryPoints: { options: "src/options/style.css", board: "src/board/style.css" },
    absWorkingDir: HERE,
    outdir: out,
    bundle: true,
    minify: true,
    logLevel: "warning",
  });
  for (const name of fs.readdirSync(path.join(HERE, "static"))) {
    fs.copyFileSync(path.join(HERE, "static", name), path.join(out, name));
  }
  const ccnavi = buildCcnavi(out);
  const total = du(out);
  return { out, hosts: hosts.map((h) => h.id), ccnavi, total };
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
    `組んだ: ${info.out}（通信先 ${info.hosts.join(", ")}、全体 ${mb(info.total)}、ccnavi.wasm ${mb(info.ccnavi.bytes)}・${info.ccnavi.go}）`,
  );
}
