/**
 * Pyodide を動かす Web Worker（ADR-0093 の 7.2・8.1）。拡張のページ（ボード）が起こす。
 *
 * 同梱の Pyodide を読み、同梱の Python（ccnavi・純 Python の PyYAML・入口 `ccnavi_chrome`）を
 * MEMFS の `/app` に展開して import する。PAT も拡張の API も触らない。受けるのは要求の JSON だけ。
 */
// @ts-expect-error 同梱の Pyodide はビルドが dist/pyodide/ に置く（バンドルしない）
import { loadPyodide } from "./pyodide/pyodide.mjs";

interface PyodideLike {
  unpackArchive(buffer: ArrayBuffer, format: string, options: { extractDir: string }): void;
  runPython(code: string): unknown;
  globals: { get(name: string): (request: string) => string };
  version: string;
}

type Inbound = { id: number; kind: "init" } | { id: number; kind: "call"; request: string };

const violations: string[] = [];
self.addEventListener("securitypolicyviolation", (e) => {
  const ev = e as SecurityPolicyViolationEvent;
  violations.push(`${ev.violatedDirective} ${ev.blockedURI}`);
});

const started = performance.now();
const ready = (async () => {
  const t0 = performance.now();
  const py = (await loadPyodide({ indexURL: new URL("./pyodide/", import.meta.url).href })) as PyodideLike;
  const boot = performance.now() - t0;
  const t1 = performance.now();
  const zip = await (await fetch(new URL("./py/ccnavi-py.zip", import.meta.url))).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/app" });
  py.runPython("import sys\nsys.path.insert(0, '/app')\nimport ccnavi_chrome\nhandle = ccnavi_chrome.handle");
  const handle = py.globals.get("handle");
  const imported = performance.now() - t1;
  return { py, handle, timings: { boot_ms: Math.round(boot), import_ms: Math.round(imported), pyodide: py.version } };
})();

self.addEventListener("message", async (event: MessageEvent<Inbound>) => {
  const msg = event.data;
  try {
    const { handle, timings } = await ready;
    if (msg.kind === "init") {
      postMessage({ id: msg.id, ok: true, value: { ...timings, total_ms: Math.round(performance.now() - started), violations } });
      return;
    }
    const response = handle(msg.request);
    postMessage({ id: msg.id, ok: true, value: response });
  } catch (err) {
    postMessage({ id: msg.id, ok: false, error: String((err as Error)?.message ?? err), violations });
  }
});
