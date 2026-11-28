/**
 * 1つの答えを読み直し、聞き手みんなに配る仕組み。拡張全体で`--explain --json`の答えを1つだけ持つ（`board-feed.ts`）ために使う。
 * VS CodeのAPIに触れないので単体で試せる。
 *
 * - `refresh`は読み直しを頼む。読んでいる最中に頼まれたら、終わってからもう1回だけ読む（何度頼まれても1回にまとめる）。
 *   返るPromiseは、頼んだ後に始まった読み込みが終わったところで解ける
 * - 読めた答えは`latest`に残し、`subscribe`した聞き手に順に渡す。聞き手が増えても読み直しは増えない
 * - `load`は投げないこと（失敗は答えの形で返す）
 */
export interface Feed<R> {
  /** 最後に読めた答え。まだ1度も読んでいなければundefined */
  latest(): R | undefined;
  refresh(): Promise<void>;
  /** 答えが届くたびに呼ぶ。返す関数で聞くのをやめる */
  subscribe(listener: (result: R) => void): () => void;
}

export function makeFeed<R>(load: () => Promise<R>): Feed<R> {
  let latest: R | undefined;
  let running: Promise<void> | undefined;
  let queued: Promise<void> | undefined;
  const listeners = new Set<(result: R) => void>();

  const once = async (): Promise<void> => {
    const result = await load();
    latest = result;
    for (const listener of [...listeners]) {
      listener(result);
    }
  };

  const refresh = (): Promise<void> => {
    if (running === undefined) {
      running = once().finally(() => {
        running = undefined;
      });
      return running;
    }
    // 読んでいる最中。頼まれた時点より後の変化を拾うため、終わってからもう1回だけ読む
    if (queued === undefined) {
      queued = running.then(() => {
        queued = undefined;
        return refresh();
      });
    }
    return queued;
  };

  return {
    latest: () => latest,
    refresh,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
