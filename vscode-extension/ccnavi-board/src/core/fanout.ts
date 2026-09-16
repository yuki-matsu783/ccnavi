/**
 * 1 つの出来事を、聞いている人たち全員に配る。
 *
 * 画面ごとに監視を持つのをやめて 1 組を分け合うと、配るのは自前の仕事になる。VS Code が
 * 1 つずつ隔離してくれていたものが素の `for` になるので、次の 3 つを自分で守る。
 *
 * - 1 人が投げても残りに配る（1 画面の不具合で他の画面が更新を受け取らなくなるのを防ぐ）
 * - 配っている途中に外れた人には配らない（破棄済みの画面を延命しない）
 * - 同じ関数を 2 回渡しても 2 つの購読として数える（片方を外して両方が消えるのを防ぐ）
 */
export class Fanout {
  private readonly entries = new Set<{ readonly run: () => void }>();

  /** 聞いている人の数。0 になったら、配る側は監視を畳んでよい */
  get size(): number {
    return this.entries.size;
  }

  /** 外す手を返す。同じ関数を 2 回渡せば、購読も 2 つ */
  add(listener: () => void): () => void {
    const entry = { run: listener };
    this.entries.add(entry);
    return () => {
      this.entries.delete(entry);
    };
  }

  /** 今いる人に配る。投げた人がいたら `onError` に渡し、残りには配り続ける */
  fire(onError?: (error: unknown) => void): void {
    for (const entry of [...this.entries]) {
      if (!this.entries.has(entry)) {
        // 配っている途中に外れた
        continue;
      }
      try {
        entry.run();
      } catch (error) {
        onError?.(error);
      }
    }
  }
}
