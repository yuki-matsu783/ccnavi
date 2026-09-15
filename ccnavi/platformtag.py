"""実行ファイルがどの機械向けかを表す語（`<os>-<arch>`）。

PyInstaller の実行ファイルは、組み立てた機械の OS と CPU でしか動かない。配布先では
この語をディレクトリ名にして、機械ごとの組み立てを並べて置く（`.ccnavi/bin/<語>/`）。
hook が起動するのは `.ccnavi/scripts/ccnavi-launcher.sh` に置いた振り分けの sh で、sh が
自分の機械の語を読んで、自分の隣ではなく `../bin/` の合うディレクトリへ渡す（ADR-0044）。

語は 3 か所で揃える。ここ、scripts/ccnavi-setup.sh の host_target、
.ccnavi/scripts/ccnavi-launcher.sh。どれかだけ変えると、配った場所と探す場所がずれる。
"""

from __future__ import annotations

import os
import platform
import sys

# 組み立ての語として読めるディレクトリ名の OS の側。
SYSTEMS = ("darwin", "linux", "windows")

# 実行ファイルの名前。PyInstaller が Windows でだけ `.exe` を付ける。
EXECUTABLE_NAMES = ("ccnavi", "ccnavi.exe")

# 振り分けの sh の名前。この名前なら実体は `../bin/<語>/` に在り、それ以外の名前は
# 前の形（隣の `<語>/`）として読む。selfguard.binary_clause も同じ条件で切り替える。
# どちらかだけ条件を足すと、守る場所と控える場所が食い違う。
LAUNCHER_NAME = "ccnavi-launcher.sh"


def host_target(system: str | None = None, machine: str | None = None) -> str:
    """この機械の `<os>-<arch>`。引数はテストで機械を差し替えるためにある。"""
    system = sys.platform if system is None else system
    machine = platform.machine() if machine is None else machine
    if system == "win32":
        name = "windows"
    elif system == "darwin":
        name = "darwin"
    elif system.startswith("linux"):
        name = "linux"
    else:
        name = system
    lowered = machine.lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        lowered, lowered or "unknown"
    )
    return f"{name}-{arch}"


def runnable_targets(host: str) -> tuple[str, ...]:
    """この機械で動く組み立ての語を、先に選ぶ順に並べる。

    arm64 の macOS と Windows は x86_64 の実行ファイルを変換して動かす（Rosetta 2 /
    Windows on Arm）。自分向けがあればそちらを選び、無いときだけ変換に回す。
    """
    fallback = {"darwin-arm64": "darwin-x86_64", "windows-arm64": "windows-x86_64"}.get(host)
    return (host, fallback) if fallback else (host,)


def launched_executable(launcher: str, host: str | None = None) -> str:
    """振り分けの sh が、この機械で起動する実行ファイル。無ければ空文字。

    sh と同じ順で探す。自己防衛が sh だけを見ていると、実体を差し替えられても
    気付かない。hook が実際に走らせるのはこちら。

    名前が LAUNCHER_NAME なら `../bin/` を探し、隣は見ない。sh が起動しない置き場を
    控える場所として返すと、控えた実体と走る実体が別のものになる。それ以外の名前は
    前の形として隣を探す。`.ccnavi/bin/ccnavi` を指したままのワークスペース
    （導入スクリプトを打ち直すまで）で、隣の実体を控え続けるために残す。
    `..` は解かない。sh も `$here/../bin` をそのまま使う。
    """
    here = os.path.dirname(launcher)
    if os.path.basename(launcher) == LAUNCHER_NAME:
        places = os.path.join(os.path.dirname(here), "bin")
    else:
        places = here
    for target in runnable_targets(host or host_target()):
        for name in EXECUTABLE_NAMES:
            found = os.path.join(places, target, name)
            if os.path.isfile(found):
                return found
    return ""
