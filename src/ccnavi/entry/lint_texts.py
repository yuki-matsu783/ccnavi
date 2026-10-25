"""`--lint` のうち、文面ファイル（`messages/*.yml`）の検査。

読むのは共通レイヤーと自身のレイヤーの置き場だけ。
検査の中身は `policy/texts.py`（下書きの取り込み前の
検査と同じ関数）で、ここは出どころの表記（`(messages)`）を付ける。置き場が無いのは正常で、何も言わない。
"""

from __future__ import annotations

from ..policy import texts
from ..policy.rules import Problem

WHERE = "(messages)"


def _messages(root: str) -> list[Problem]:
    found = texts.load(root).problems
    return [Problem(p.severity, WHERE, f"{p.rule}: {p.detail}") for p in found]
