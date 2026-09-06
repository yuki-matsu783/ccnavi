"""配布物を組み立てる。走っているプラットフォーム向けの実行ファイルを作る。

onedir で作る。onefile は起動のたびにランタイムを一時ディレクトリへ展開するので、
このプロジェクトの実測で 1 呼び出しあたり 1.0〜1.5 秒かかった。hook はすべての
ツール呼び出しで走るから、そのぶんが全部に乗る。onedir は 0.2 秒前後で収まる。
単一ファイルではなくフォルダごと置くことになるが、払う代わりに得るものが大きい。

    uv run --with pyinstaller python build.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
# 実行ファイルの置き場。hook はここを指す。
DIST = os.path.join(ROOT, "dist")
NAME = "ccnavi"


def executable() -> str:
    """このプラットフォームでの実行ファイルのパス。"""
    suffix = ".exe" if sys.platform == "win32" else ""
    return os.path.join(DIST, NAME, NAME + suffix)


def build() -> int:
    work = os.path.join(ROOT, "build")
    staging = os.path.join(work, "dist")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconfirm",
        "--name",
        NAME,
        "--distpath",
        staging,
        "--workpath",
        os.path.join(work, "work"),
        "--specpath",
        work,
        "--paths",
        ROOT,
        os.path.join(ROOT, "main.py"),
    ]
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    _swap(os.path.join(staging, NAME), os.path.join(DIST, NAME))
    print(f"built {executable()}")
    return 0


def _swap(new: str, live: str) -> None:
    """組み上がったものを置き場に入れ替える。

    Windows は走っている実行ファイルを上書きできない。名前の変更はできるので、
    古いほうを退避してから新しいほうを移し、退避した側を消す。hook は 0.2 秒で
    終わるが、ちょうど走っている瞬間に当たることはある。数回やり直せば抜ける。
    """
    os.makedirs(os.path.dirname(live), exist_ok=True)
    old = live + ".old"
    shutil.rmtree(old, ignore_errors=True)

    for attempt in range(5):
        try:
            if os.path.exists(live):
                os.rename(live, old)
            os.rename(new, live)
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.3)

    shutil.rmtree(old, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(build())
