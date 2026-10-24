"""パスと名前を比べるためのキー。

大文字小文字だけが違う名前は同じパスとして扱い、両立させない。どの機械でも同じ答えにするため、
`os.path.normcase` は単独では使わない（Linux では何もしない）。比べるときは必ずここを通す。
返すのは比べるための文字列で、ファイルを開く表記には使わない（Linux では別の場所になる）。
"""

from __future__ import annotations

import os


def fold(path: str) -> str:
    """同じ場所を同じ文字列にする。区切りは機械のもの、大文字小文字はどの機械でも区別しない。"""
    return os.path.normcase(path).casefold()
