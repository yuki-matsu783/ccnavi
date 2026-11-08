/**
 * 同梱の ccnavi（Go を GOOS=js でビルドした WASM）を起こし、要求の JSON を渡す関数を返す。Worker と Node の試験が
 * 同じここを通る。
 *
 * Go の `wasm_exec.js` は、読み込んだときに `globalThis.fs` が無ければ何もできない仮の fs を置く。そこで先に
 * メモリの上の fs（`memfs.ts`）を置いてから読む。Go の実行時は起動のときに `globalThis.fs` を掴むので、同じ
 * プロセスで起こす ccnavi は全部この fs を使う。
 */
import { createMemFs, type MemFs } from "./memfs.js";

interface GoRuntime {
  importObject: WebAssembly.Imports;
  run(instance: WebAssembly.Instance): Promise<void>;
}

type Handle = (request: string) => string;

interface Global {
  fs?: MemFs;
  process?: Record<string, unknown>;
  Go?: new () => GoRuntime;
  ccnaviHandle?: Handle;
}

/** ブラウザ（Worker）には process が無い。Go が読む分（作業ディレクトリ・umask・利用者）だけを置く */
function processStub(): Record<string, unknown> {
  const enosys = () => Object.assign(new Error("not implemented"), { code: "ENOSYS" });
  return {
    getuid: () => -1,
    getgid: () => -1,
    geteuid: () => -1,
    getegid: () => -1,
    getgroups: () => [],
    pid: -1,
    ppid: -1,
    umask: () => 0o022,
    cwd: () => "/",
    chdir: () => {
      throw enosys();
    },
  };
}

export interface Ccnavi {
  readonly handle: Handle;
  /** 起動にかかった時間（WASM の組み立てと Go の起動） */
  readonly bootMs: number;
}

/**
 * `wasm` は ccnavi.wasm の中身、`loadExec` は `wasm_exec.js` を読む手順（Worker は動的 import、試験はファイルを読む）。
 */
export async function startCcnavi(wasm: BufferSource, loadExec: () => Promise<unknown>): Promise<Ccnavi> {
  const g = globalThis as unknown as Global;
  g.fs ??= createMemFs();
  g.process ??= processStub();
  if (g.Go === undefined) await loadExec();
  const t0 = performance.now();
  const go = new g.Go!();
  const { instance } = await WebAssembly.instantiate(wasm, go.importObject);
  void go.run(instance);
  const handle = g.ccnaviHandle;
  if (handle === undefined) throw new Error("ccnavi.wasm が入口（ccnaviHandle）を置きませんでした");
  return { handle, bootMs: performance.now() - t0 };
}
