"""配布物を組み立てる。走っているプラットフォーム向けの実行ファイルを作る。

onedir で作る。onefile は起動のたびにランタイムを一時ディレクトリへ展開するので、
このプロジェクトの実測で 1 呼び出しあたり 1.0〜1.5 秒かかった。hook はすべての
ツール呼び出しで走るから、そのぶんが全部に乗る。onedir は 0.2 秒前後で収まる。
単一ファイルではなくフォルダごと置くことになるが、払う代わりに得るものが大きい。

    uv run --with pyinstaller python build.py

組み立てた `dist/ccnavi/` は `.ccnavi/bin/<os>-<arch>/` へ写す。hook が起動する振り分けの sh
（`.ccnavi/scripts/ccnavi-launcher.sh`）は `../bin/` のこちらを探すので、写すまで hook は
新しい実行ファイルを起動しない。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from ccnavi import platformtag  # noqa: E402

# 組み立ての出力。導入スクリプトはここから配り、ゲートの sh は env が無いときここを探す。
DIST = os.path.join(ROOT, "dist")
NAME = "ccnavi"
# どの機械向けに組み立てたかの印。scripts/ccnavi-setup.sh が配る前に読む。
# dist/ccnavi/ の外に置く。中に置くと、配布が実行ファイルと一緒に配布先へ写す。
TARGET = os.path.join(DIST, NAME + ".target")
# 振り分けの sh が起動する実行ファイルの置き場（ワークスペースルートからの相対）。
# <os>-<arch>/ はこの下に並ぶ。
BIN_ROOT = os.path.join(".ccnavi", "bin")


def executable() -> str:
    """このプラットフォームでの実行ファイルのパス。"""
    suffix = ".exe" if sys.platform == "win32" else ""
    return os.path.join(DIST, NAME, NAME + suffix)


def build_target() -> str:
    """組み立てた実行ファイルが動く機械の `<os>-<arch>`。

    PyInstaller の実行ファイルは、組み立てた機械の OS と CPU でしか動かない。
    導入スクリプトはこの語を配布先のディレクトリ名にする（ccnavi/platformtag.py）。
    """
    return platformtag.host_target()


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
    target = build_target()
    with open(TARGET, "w", encoding="utf-8", newline="\n") as f:
        f.write(target + "\n")
    print(f"built {executable()} ({target})")

    try:
        live = install(os.path.join(DIST, NAME), ROOT, target)
    except OSError as e:
        where = os.path.join(BIN_ROOT, target)
        print(f"{where} へ写せなかった: {e}", file=sys.stderr)
        print(f"dist/ は新しい。{where}{os.sep} は前のまま", file=sys.stderr)
        return 1
    print(f"installed {live}")
    return 0


def install(dist_dir: str, root: str, target: str) -> str:
    """組み立ての出力 `dist_dir`（`dist/ccnavi/` そのもの）を `.ccnavi/bin/<target>/` へ写す。

    `.ccnavi/bin/` は `root` の下に取る。

    写すのであって移すのではない。`dist/` は導入スクリプトの配布元で、ゲートの sh の既定の
    探し先でもある。起動中の置き場へ上書きで写すと、onedir の `_internal/` が前後の版で
    混ざるので、隣の `<target>.new` に写し切ってから `_swap` で入れ替える。前の版にだけ
    あったファイルは、入れ替えで退避した側ごと消える。

    落ちたら OSError をそのまま投げる。置き場は前のままで、写しかけの `<target>.new` は消す。
    写した先のパスを返す。
    """
    live = os.path.join(root, BIN_ROOT, target)
    new = live + ".new"
    shutil.rmtree(new, ignore_errors=True)
    try:
        # PyInstaller の出力にはシンボリックリンクが入ることがある（macOS）。辿らずにそのまま写す。
        shutil.copytree(dist_dir, new, symlinks=True)
        _swap(new, live)
    finally:
        shutil.rmtree(new, ignore_errors=True)
    return live


def _swap(new: str, live: str) -> None:
    """組み上がったものを置き場に入れ替える。

    Windows は走っている実行ファイルを上書きできない。名前の変更はできるので、
    古いほうを退避してから新しいほうを移し、退避した側を消す。hook は 0.2 秒で
    終わるが、ちょうど走っている瞬間に当たることはある。数回やり直せば抜ける。
    やり直しきれなかったら、退避した側を置き場に戻してから投げる。
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
                if os.path.exists(old) and not os.path.exists(live):
                    os.rename(old, live)
                raise
            time.sleep(0.3)

    shutil.rmtree(old, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(build())
