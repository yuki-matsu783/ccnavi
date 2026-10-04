/**
 * ボードの自動の読み直し。前回の読み直しから 5 分たつと、`run` を呼ぶ。
 *
 * - 間隔は固定（`AUTO_REFRESH_MS`）。手動の読み直しや操作の後の読み直しも「前回」に数える（`done` を呼ぶ）
 * - タブが見えていない間は呼ばない。見える状態に戻ったとき、前回から 5 分以上たっていれば、すぐ呼ぶ
 * - 呼ぶのは 1 つずつ。`run` が走っている間と、`busy` が真の間（操作中・入力中）は飛ばし、次の周期に回す
 */

export const AUTO_REFRESH_MS = 5 * 60 * 1000;

export interface AutoRefreshEnv {
  readonly doc: { readonly visibilityState: string; addEventListener(type: "visibilitychange", cb: () => void): void };
  readonly now: () => number;
  readonly setTimeout: (cb: () => void, ms: number) => unknown;
  readonly clearTimeout: (id: unknown) => void;
  /** 読み直す。失敗は呼び手が表に出す（ここでは握りつぶして次の周期に回す） */
  readonly run: () => Promise<void>;
  /** 真の間は読み直さない（書く操作の最中、入力欄を触っている最中） */
  readonly busy?: () => boolean;
  readonly intervalMs?: number;
}

export interface AutoRefresh {
  /** 読み直しが終わったことを知らせる（自動でも手動でも）。次の周期を数え直す */
  done(): void;
}

export function startAutoRefresh(env: AutoRefreshEnv): AutoRefresh {
  const interval = env.intervalMs ?? AUTO_REFRESH_MS;
  let last = env.now();
  let timer: unknown = null;
  let running = false;

  const schedule = (): void => {
    if (timer !== null) env.clearTimeout(timer);
    timer = env.setTimeout(tick, interval);
  };
  const done = (): void => {
    last = env.now();
    schedule();
  };
  const attempt = async (): Promise<void> => {
    if (running || env.busy?.()) {
      schedule();
      return;
    }
    running = true;
    try {
      await env.run();
    } catch {
      // 次の周期でまた試す
    } finally {
      running = false;
      done();
    }
  };
  function tick(): void {
    timer = null;
    // 見えない間は止めておく。見える状態に戻ったときに数え直す
    if (env.doc.visibilityState === "hidden") return;
    void attempt();
  }
  env.doc.addEventListener("visibilitychange", () => {
    if (env.doc.visibilityState === "hidden") return;
    if (env.now() - last >= interval) void attempt();
    else if (timer === null) schedule();
  });
  schedule();
  return { done };
}
