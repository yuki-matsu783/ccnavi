/**
 * ボードから Pyodide の Worker を呼ぶ手段。
 */
import type { PyCall } from "../core/py.js";

export interface PyWorker {
  readonly call: PyCall;
  init(): Promise<{ boot_ms: number; import_ms: number; total_ms: number; pyodide: string; violations: string[] }>;
}

export function startWorker(): PyWorker {
  const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
  let next = 1;
  const waiting = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
  worker.addEventListener("message", (e: MessageEvent<{ id: number; ok: boolean; value?: unknown; error?: string }>) => {
    const w = waiting.get(e.data.id);
    if (!w) return;
    waiting.delete(e.data.id);
    if (e.data.ok) w.resolve(e.data.value);
    else w.reject(new Error(e.data.error ?? "Worker が失敗した"));
  });
  worker.addEventListener("error", (e) => {
    for (const w of waiting.values()) w.reject(new Error(`Worker が落ちた: ${e.message}`));
    waiting.clear();
  });
  const send = (body: Record<string, unknown>) =>
    new Promise<unknown>((resolve, reject) => {
      const id = next++;
      waiting.set(id, { resolve, reject });
      worker.postMessage({ id, ...body });
    });
  return {
    init: () => send({ kind: "init" }) as ReturnType<PyWorker["init"]>,
    call: async (request) => JSON.parse((await send({ kind: "call", request: JSON.stringify(request) })) as string),
  };
}
