"""閉じた親チケットのコピーの、同じ親かを決めるのに要る欄。

手元の退避（`logs/archive/`）の `done/` や、統合先の `git show` で読んだ `done/<識別子>.md` から、
識別子・親・着手と取り消しの欄だけを取り出し、手元の親と同じ親チケットかを比べる。
チケットとしての検査（範囲の欄など）は掛けない（検査に失敗する古いチケットでも、閉じた記録として読む）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ticket as ticket_mod


@dataclass(frozen=True)
class DoneCopy:
    """閉じたチケットの、閉じたかを決めるのに要る欄だけ。

    照合に使うのは、スクリプトだけが書く着手と取り消しの欄（`base_sha`・`started_at`・
    `cancelled_at`）。
    """

    ticket: str
    parent: str
    base_sha: str = ""
    started_at: str = ""
    cancelled_at: str = ""


# 同じ親かを比べる欄。最初に両方が値を持つ欄で決める。
MATCH_FIELDS = ("base_sha", "started_at", "cancelled_at")


def same_parent(mine: DoneCopy | None, closed: DoneCopy) -> bool:
    """手元の親（`mine`）と閉じた側の親（`closed`）が同じ親チケットか。

    `base_sha` → `started_at` → `cancelled_at` の順に、最初に両方が値を持つ欄が同じなら同じとする。
    空どうしは一致としない（空文字は無いとみなす）。どの欄でも照合できないとき、手元に親が無いときは
    同じとしない（閉じていない側に倒し、止めて戻し方を出す）。
    """
    if mine is None:
        return False
    for name in MATCH_FIELDS:
        a, b = getattr(mine, name), getattr(closed, name)
        if a and b:
            return a == b
    return False


def _text(value: object) -> str:
    # 欄の値を文字列で。None と空白だけの値は無いとみなす。
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def copy_fields(text: str) -> DoneCopy | None:
    """チケットの本文（frontmatter つき）から `DoneCopy` を取り出す。読めなければ None。"""
    # 同じパッケージの frontmatter の読み方を使う（チケットの検査は掛けない）。
    front, _, problems = ticket_mod._frontmatter(text)
    if not isinstance(front, dict) or problems:
        return None
    return DoneCopy(
        ticket=_text(front.get("ticket")),
        parent=_text(front.get("parent")),
        base_sha=_text(front.get("base_sha")),
        started_at=_text(front.get("started_at")),
        cancelled_at=_text(front.get("cancelled_at")),
    )
