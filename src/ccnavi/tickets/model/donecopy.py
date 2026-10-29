"""閉じた親チケットのコピーから読む欄。

手元の退避（`logs/archive/`）や承認済みの `done/` の `<識別子>.md` から、識別子と親だけを取り出す。
チケットとしての検査（範囲の欄など）は掛けない（検査に失敗する古いチケットでも、閉じた記録として読む）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ticket as ticket_mod


@dataclass(frozen=True)
class DoneCopy:
    """閉じたチケットの識別子と親だけを持つ。親かどうかは `parent` が空かで決める。"""

    ticket: str
    parent: str


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
    )
