/**
 * 同梱の ccnavi（Go を GOOS=js でビルドした WASM）を動かす Web Worker。拡張のページ（ボード）が起こす。判定を TS で
 * 書き直さず、手元と同じ ccnavi をここで動かすことで、判定を 1 か所に保つ。
 *
 * ビルドが dist/ に置いた `ccnavi.wasm` と Go の `wasm_exec.js` を読み、メモリの上の fs に仮のツリーを組ませる。
 * PAT も拡張の API も触らない。受けるのは要求の JSON だけ。
 */
import { CCNAVI_SCHEMA } from "../core/ccnavi.js";
import { startCcnavi } from "../core/wasm.js";

type Inbound = { id: number; kind: "init" } | { id: number; kind: "call"; request: string };

const violations: string[] = [];
self.addEventListener("securitypolicyviolation", (e) => {
  const ev = e as SecurityPolicyViolationEvent;
  violations.push(`${ev.violatedDirective} ${ev.blockedURI}`);
});

const started = performance.now();
const ready = (async () => {
  const t0 = performance.now();
  const wasm = await (await fetch(new URL("./ccnavi.wasm", import.meta.url))).arrayBuffer();
  // wasm_exec.js はバンドルしない（ビルドが Go の版に合うものを置く）。fs を置いてから読むので動的に import する
  const ccnavi = await startCcnavi(wasm, () => import(new URL("./wasm_exec.js", import.meta.url).href));
  const boot = performance.now() - t0;
  const info = JSON.parse(ccnavi.handle(JSON.stringify({ schema: CCNAVI_SCHEMA, op: "version" }))) as { version?: string };
  return { handle: ccnavi.handle, timings: { boot_ms: Math.round(boot), version: String(info.version ?? "") } };
})();

self.addEventListener("message", async (event: MessageEvent<Inbound>) => {
  const msg = event.data;
  try {
    const { handle, timings } = await ready;
    if (msg.kind === "init") {
      postMessage({ id: msg.id, ok: true, value: { ...timings, total_ms: Math.round(performance.now() - started), violations } });
      return;
    }
    postMessage({ id: msg.id, ok: true, value: handle(msg.request) });
  } catch (err) {
    postMessage({ id: msg.id, ok: false, error: String((err as Error)?.message ?? err), violations });
  }
});
