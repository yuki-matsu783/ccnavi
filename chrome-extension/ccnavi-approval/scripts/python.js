// 同梱する Python を 1 本の zip に組む（ADR-0093 の 7.2）。`build.js` と試験が呼ぶ。
//
// 中身は 3 つ。
//
//   yaml/            PyYAML の純 Python 版。版と取ってくる先とハッシュはリポジトリの uv.lock から読む
//                    （手元の ccnavi と同じ版に揃う）。C 拡張は入れない（Pyodide では読めない）
//   ccnavi/          リポジトリの ccnavi パッケージの .py をそのまま
//   ccnavi_chrome.py 拡張の入口（py/）
//
// 組むのは Node の上の Pyodide（同梱するのと同じ版）で、.pyc を unchecked-hash で作って一緒に入れる
// （import が速くなる。ADR の 7.2 の「.pyc 同梱で import 0.43 秒」）。組んだ後に入口を import して、
// 読めない形なら組み立てを止める。
//
// 取ってくるのは PyYAML の sdist だけ。置き場は .cache/（追跡しない）。プロキシの内側では
// NODE_USE_ENV_PROXY=1 を付けて回すと HTTPS_PROXY を通る（Node 22.21 から）。
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const HERE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const REPO = path.resolve(HERE, "..", "..");
const CACHE = path.join(HERE, ".cache");

/** uv.lock の `[[package]] name = "pyyaml"` の sdist の url と sha256 */
export function pyyamlPin(lockText) {
  const blocks = lockText.split(/^\[\[package\]\]$/m);
  for (const block of blocks) {
    if (!/^name = "pyyaml"$/m.test(block)) continue;
    const version = /^version = "([^"]+)"$/m.exec(block)?.[1];
    const sdist = /^sdist = \{ url = "([^"]+)", hash = "sha256:([0-9a-f]{64})"/m.exec(block);
    if (!version || !sdist) break;
    return { version, url: sdist[1], sha256: sdist[2] };
  }
  throw new Error("uv.lock に pyyaml の sdist が無い");
}

function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

async function download(pin) {
  fs.mkdirSync(CACHE, { recursive: true });
  const file = path.join(CACHE, path.basename(new URL(pin.url).pathname));
  if (fs.existsSync(file) && sha256(fs.readFileSync(file)) === pin.sha256) {
    return fs.readFileSync(file);
  }
  const res = await fetch(pin.url);
  if (!res.ok) throw new Error(`PyYAML を取ってこられない: ${res.status} ${pin.url}`);
  const buf = Buffer.from(await res.arrayBuffer());
  const got = sha256(buf);
  if (got !== pin.sha256) throw new Error(`PyYAML のハッシュが uv.lock と違う: ${got}`);
  fs.writeFileSync(file, buf);
  return buf;
}

/** tar.gz から `<top>/lib/yaml/*.py` を取り出す（ustar と pax の長い名前を読む） */
export function yamlSources(tgz) {
  const tar = zlib.gunzipSync(tgz);
  const out = new Map();
  let offset = 0;
  let longName = "";
  const str = (b) => b.toString("utf8").replace(/\0.*$/s, "");
  while (offset + 512 <= tar.length) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((x) => x === 0)) break;
    const size = parseInt(str(header.subarray(124, 136)).trim() || "0", 8);
    const type = String.fromCharCode(header[156] || 48);
    const prefix = str(header.subarray(345, 500));
    let name = longName || (prefix ? `${prefix}/${str(header.subarray(0, 100))}` : str(header.subarray(0, 100)));
    longName = "";
    const data = tar.subarray(offset + 512, offset + 512 + size);
    if (type === "x") {
      const m = /\d+ path=([^\n]+)\n/.exec(data.toString("utf8"));
      if (m) longName = m[1];
    } else if (type === "L") {
      longName = str(data);
    } else if (type === "0" || type === "\0") {
      const m = /^[^/]+\/lib\/yaml\/([A-Za-z0-9_]+\.py)$/.exec(name);
      if (m) out.set(m[1], Buffer.from(data));
    }
    offset += 512 + Math.ceil(size / 512) * 512;
  }
  if (!out.has("__init__.py")) throw new Error("PyYAML の sdist に lib/yaml/__init__.py が無い");
  return out;
}

function ccnaviSources() {
  const dir = path.join(REPO, "ccnavi");
  const out = new Map();
  for (const name of fs.readdirSync(dir).sort()) {
    if (name.endsWith(".py")) out.set(name, fs.readFileSync(path.join(dir, name)));
  }
  if (!out.has("cli.py")) throw new Error(`ccnavi のソースが無い: ${dir}`);
  return out;
}

export async function loadNodePyodide() {
  const { loadPyodide } = await import("pyodide");
  return loadPyodide({ indexURL: path.join(HERE, "node_modules", "pyodide") + path.sep });
}

/** zip を組んで `outFile` に書く。中身の数と大きさを返す */
export async function buildPythonZip(outFile) {
  const pin = pyyamlPin(fs.readFileSync(path.join(REPO, "uv.lock"), "utf8"));
  const yaml = yamlSources(await download(pin));
  const ccnavi = ccnaviSources();
  const entry = fs.readFileSync(path.join(HERE, "py", "ccnavi_chrome.py"));

  const py = await loadNodePyodide();
  py.FS.mkdirTree("/app/yaml");
  py.FS.mkdirTree("/app/ccnavi");
  for (const [n, b] of yaml) py.FS.writeFile(`/app/yaml/${n}`, b);
  for (const [n, b] of ccnavi) py.FS.writeFile(`/app/ccnavi/${n}`, b);
  py.FS.writeFile("/app/ccnavi_chrome.py", entry);
  py.runPython(`
import compileall, py_compile, sys, zipfile, os
ok = compileall.compile_dir("/app", quiet=1, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
if not ok:
    raise SystemExit("compileall が失敗した")
sys.path.insert(0, "/app")
import ccnavi_chrome, yaml
assert not hasattr(yaml, "CLoader") or yaml.__with_libyaml__ is False
with zipfile.ZipFile("/tmp/ccnavi-py.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for base, dirs, files in os.walk("/app"):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            info = zipfile.ZipInfo(os.path.relpath(full, "/app"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as f:
                z.writestr(info, f.read())
`);
  const zip = py.FS.readFile("/tmp/ccnavi-py.zip");
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, zip);
  return { pyyaml: pin.version, python: py.runPython("import sys; sys.version.split()[0]"), files: yaml.size + ccnavi.size + 1, bytes: zip.length };
}
