"""テストをモジュールごとに別プロセスで、同時に何本か回す。

    uv run python tools/run_tests.py                  全件
    uv run python tools/run_tests.py tests/ticket     グループを名指し
    uv run python tools/run_tests.py --jobs 2         同時に回す本数を決める
    uv run python tools/run_tests.py --plan           何をどの順で回すか出すだけ

`python -m unittest discover -s tests -t .` の代わりに使う。回す中身は同じで、
分け方と並べ方だけが違う。1 プロセスで直列に回すと、この機械で 3 分ほどかかる。

**ターンの終わりの hook（`.claude/hooks/test-py.sh`）はこれを使わない。**
あちらは `--failfast` で「最初に落ちた 1 件」を返す作りで、落ちた 1 件が毎回同じに
なることに寄りかかっている。同時に回すと「最初」が走るたびに変わるので、差し戻しの
文面が回ごとにぶれる。ここは人が全件を打つ場面（統合先へ戻す前、MR に出す前）のための
道具で、hook の経路は直列のまま置く。

落ちたら、そこで新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。全部の
失敗を並べても、直す順番は結局 1 件ずつなので読む量だけが増える（`test-py.sh` と同じ考え）。
走り始めていたぶんは終わるまで待つ。途中で殺すと、そのプロセスが作った一時ディレクトリが
残る。

**並べる順は、ファイルの大きさの降順。** 大きいものを先に起こさないと、最後に
一番重いものが 1 本だけ残って、他の枠が空いたまま待つ。「どれが重いか」を表で持たないのは、
テストを足した日に黙って古くなるため。大きさは当てずっぽうだが、表と違って自分で更新される。

同時に回す本数の既定は CPU の数。それ以上に増やしても、この分け方の下限は一番重い
1 モジュールの時間なので縮まない。

**同じワークツリーの中で走らせること。** ワークツリーを何本も並行させると、Windows では
`.venv` の中の PyYAML の `.pyd` を掴んだまま `worktree remove` が落ちる（pyproject.toml の
`link-mode` のコメント）。1 本のツリーの中で何プロセス起こしても、掴むのは同じ実体なので
その問題は起きない。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")


def groups() -> list[str]:
    """`tests/` の下のグループの名前。表では持たず、置き場を読んで決める。"""
    found = []
    for name in sorted(os.listdir(TESTS)):
        directory = os.path.join(TESTS, name)
        if not os.path.isdir(directory):
            continue
        # `__init__.py` が無いディレクトリは discover が飛ばすので、グループではない
        # （`tests/fixtures/` がこれ。固定データの置き場で、テストは入っていない）。
        # `__pycache__` もここで落ちる。
        if not os.path.isfile(os.path.join(directory, "__init__.py")):
            continue
        if any(n.startswith("test_") and n.endswith(".py") for n in os.listdir(directory)):
            found.append(name)
    return found


def modules(targets: list[str]) -> list[str]:
    """回すテストモジュールを、重いと思われる順（ファイルの大きさの降順）に並べて返す。

    `targets` が空なら全グループ。`tests/ticket` のようなグループの綴りか、
    `tests.ticket.test_ticket` のようなモジュール名を受ける。
    """
    wanted = {_normalize(target) for target in targets}

    found = []
    for group in groups():
        directory = os.path.join(TESTS, group)
        for name in sorted(os.listdir(directory)):
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            dotted = f"tests.{group}.{name[:-3]}"
            if wanted and not ({dotted, f"tests.{group}"} & wanted):
                continue
            found.append((os.path.getsize(os.path.join(directory, name)), dotted))
    return [dotted for _, dotted in sorted(found, key=lambda pair: -pair[0])]


def _normalize(target: str) -> str:
    """`tests/ticket`・`tests/ticket/`・`tests/ticket/test_ticket.py`・
    `tests.ticket.test_ticket` のどれで書かれても、同じ点区切りの綴りにする。"""
    name = target.replace("\\", "/").strip("/")
    if name.endswith(".py"):
        name = name[:-3]
    return name.replace("/", ".")


def run_one(dotted: str) -> tuple[str, float, int, str]:
    """1 モジュールを別プロセスで回す。返すのは（名前、秒、終了コード、出力）。"""
    started = time.perf_counter()
    done = subprocess.run(
        [sys.executable, "-m", "unittest", dotted],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    spent = time.perf_counter() - started
    return dotted, spent, done.returncode, done.stdout + done.stderr


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("targets", nargs="*", help="グループかモジュール。省くと全件")
    parser.add_argument("--jobs", type=int, default=0, help="同時に回す本数。既定は CPU の数")
    parser.add_argument("--plan", action="store_true", help="何を回すか出すだけ。走らせない")
    args = parser.parse_args()

    planned = modules(args.targets)
    if not planned:
        print("回すものがありません", file=sys.stderr)
        return 1
    if args.plan:
        for dotted in planned:
            print(dotted)
        return 0

    jobs = args.jobs if args.jobs > 0 else (os.cpu_count() or 1)
    jobs = max(1, min(jobs, len(planned)))
    print(f"{len(planned)} モジュールを {jobs} 本ずつ回します")

    started = time.perf_counter()
    failed: tuple[str, float, int, str] | None = None
    results: list[tuple[str, float, int, str]] = []
    stop = False

    def guarded(dotted: str) -> tuple[str, float, int, str] | None:
        # 落ちたあとに順番が回ってきたぶんは起こさない。走り出したぶんは最後まで待つ。
        if stop:
            return None
        return run_one(dotted)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for result in pool.map(guarded, planned):
            if result is None:
                continue
            results.append(result)
            dotted, spent, code, _ = result
            if code != 0 and failed is None:
                failed = result
                stop = True
            print(f"  {'落' if code else 'ok'}  {spent:6.1f}s  {dotted}")

    wall = time.perf_counter() - started
    print(f"\n{len(results)} モジュール / {wall:.1f} 秒")

    if failed is None:
        return 0
    dotted, _, _, output = failed
    print(f"\n--- 落ちたのは {dotted} ---", file=sys.stderr)
    print(output.strip(), file=sys.stderr)
    skipped = len(planned) - len(results)
    if skipped:
        print(f"\n（{skipped} モジュールは起こしていません）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # `| head` のように読み手が途中で閉じた。Python は終了のときに stdout を
        # もう一度 flush して同じ例外を出すので、その前に devnull へ差し替える。
        # 差し替えないと、正しく動いた回に traceback が出る。
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0) from None
