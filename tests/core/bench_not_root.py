"""`{!root}` の展開の費用を測る。設計 wip/design/i0061-not-root.md 5 の数字の出所。

**テストではない。** 名前が `test_` で始まらないので `unittest discover` は拾わない。
測るのに 10 秒ほど掛かるうえ、絶対の時間は機械で変わるので、合否を付ける形にしていない。
見たいのは「長さに比例して伸びること」と「組み立てが落ちる長さ」で、どちらも形の話。

    uv run python -m tests.core.bench_not_root

設計の判断のうち、ここが根拠になっているもの。

- 入れ子型を採り、列挙型を捨てた（2.7）。列挙型は式の長さがルートの長さの 2 乗で伸びる
- しきい値を 256 字にした（4.1）。組み立てが落ちるのは 493 字で、その半分以下
- 呼び出し時のスタックの深さでは、組み立てが落ちる長さが変わらない（4.1）
"""

from __future__ import annotations

import re
import sys
import time

from ccnavi.rules import MAX_ROOT_LEN, not_root_pattern, real_root

FLAGS = re.IGNORECASE
ROOT = r"C:\Users\taniyama\Desktop\git\ccnavi"


def flat(root: str) -> str:
    """採らなかった列挙型。前置きを 1 文字ずつ伸ばして並べる形。

    比較のためだけに置いてある。これを使ってはいけない。
    """

    def differs(ch: str) -> str:
        return r"[^\\/]" if ch in "\\/" else "[^" + re.escape(ch) + "]"

    def same(ch: str) -> str:
        return r"[\\/]" if ch in "\\/" else re.escape(ch)

    branches = []
    for i, ch in enumerate(root):
        branches.append("".join(same(c) for c in root[:i]) + r"(?:\Z|" + differs(ch) + ")")
    branches.append("".join(same(c) for c in root) + r"(?:\Z|" + differs("/") + ")")
    return "^(?:" + "|".join(branches) + ")"


def grown(length: int) -> str:
    """ちょうどその長さのルートの綴り。末尾が区切りにならないようにする。"""
    body = ("d" * 9 + "\\") * (length // 10 + 2)
    root = ("C:\\" + body)[:length]
    return root[:-1] + "d" if root.endswith(("\\", "/")) else root


def timed(pattern: re.Pattern, subjects: list[str], rounds: int = 20000) -> float:
    """1 回の判定にかかるマイクロ秒。"""
    start = time.perf_counter()
    for _ in range(rounds):
        for subject in subjects:
            pattern.search(subject)
    return (time.perf_counter() - start) / (rounds * len(subjects)) * 1_000_000


def built(expression: str) -> float:
    """組み立てにかかるミリ秒（5 回の中央値）。hook は呼び出しごとに別プロセスなので毎回走る。"""
    times = []
    for _ in range(5):
        re.purge()
        start = time.perf_counter()
        re.compile(expression, FLAGS)
        times.append((time.perf_counter() - start) * 1000)
    return sorted(times)[2]


def main() -> None:
    print(f"Python {sys.version.split()[0]}  再帰の上限 {sys.getrecursionlimit()}")
    root = real_root(ROOT)
    subjects = [root + r"\README.md", r"C:\Users\taniyama\Desktop\git\other\x.md", r"D:\x\y.md"]

    print()
    print("=== 入れ子型（採用）と列挙型（不採用）===")
    for label, make in (("入れ子", not_root_pattern), ("列挙", lambda r: flat(r)[1:])):
        expression = "^" + make(root)
        print(
            f"  {label:<4} 式 {len(expression):>6} 字  "
            f"組み立て {built(expression):6.2f} ms  "
            f"判定 {timed(re.compile(expression, FLAGS), subjects):6.2f} μs"
        )

    print()
    print("=== ルートの長さで、どう伸びるか ===")
    print(
        f"  {'ルート':>6} {'入れ子 式長':>10} {'入れ子 ms':>10} {'列挙 式長':>10} {'列挙 ms':>10}"
    )
    for length in (36, 76, 136, 236):
        long_root = grown(length)
        nested_expression = "^" + not_root_pattern(long_root)
        flat_expression = flat(long_root)
        print(
            f"  {length:>6} {len(nested_expression):>10} {built(nested_expression):>10.2f} "
            f"{len(flat_expression):>10} {built(flat_expression):>10.2f}"
        )
    print("  入れ子は長さに比例、列挙は 2 乗で伸びる")

    print()
    print("=== 組み立てが落ちる長さ（しきい値 256 の根拠）===")
    low, high = 1, 900
    while low < high:
        mid = (low + high + 1) // 2
        try:
            re.compile("^" + not_root_pattern(grown(mid)), FLAGS)
        except Exception:
            high = mid - 1
        else:
            low = mid
    print(f"  組み立てられる最長のルート: {low} 字（{low + 1} 字で失敗）")
    print(f"  採っているしきい値: {MAX_ROOT_LEN} 字（その半分以下。Windows の MAX_PATH は 260）")

    print()
    print("=== 呼び出し時のスタックの深さで変わるか ===")

    def at_depth(depth: int, length: int) -> bool:
        if depth:
            return at_depth(depth - 1, length)
        try:
            re.compile("^" + not_root_pattern(grown(length)), FLAGS)
            return True
        except RecursionError:
            return False

    for depth in (0, 100, 300, 500):
        low, high = 1, 900
        while low < high:
            mid = (low + high + 1) // 2
            if at_depth(depth, mid):
                low = mid
            else:
                high = mid - 1
        print(f"  スタック深さ +{depth:>3}  通る最長ルート {low} 字")
    print("  変わらない。「hook は深いスタックから呼ばれるのでもっと短くて落ちる」は当たらない")


if __name__ == "__main__":
    main()
