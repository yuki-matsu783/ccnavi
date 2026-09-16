/**
 * 同じ答えを同時に取りに行く呼び出しを 1 つにまとめる。
 *
 * 画面が別々に子プロセスを起こすと、1 つの変化で同じ問いが何本も走る。走っている間に来た呼び出しは
 * その答えを待ち、**返った後は分け合わない**（次に呼ぶ人は新しい答えを得る）。失敗も控えないので、
 * 走り終わった後の呼び出しはもう一度走る。
 *
 * 「どこまでを同じ問いとするか」は鍵の作り方で決める。呼ぶ側が鍵に世代を混ぜれば、変化の前に
 * 始まった読みには合流しない。
 */
export function shareInFlight<A extends readonly unknown[], T>(
  run: (...args: A) => Promise<T>,
  keyOf: (...args: A) => string,
): (...args: A) => Promise<T> {
  const running = new Map<string, Promise<T>>();
  return (...args: A): Promise<T> => {
    const key = keyOf(...args);
    const now = running.get(key);
    if (now !== undefined) {
      return now;
    }
    // run が同期で投げても、返すのは必ず Promise（呼ぶ側は待つだけでよい）
    const started = (async () => run(...args))().finally(() => {
      running.delete(key);
    });
    running.set(key, started);
    return started;
  };
}
