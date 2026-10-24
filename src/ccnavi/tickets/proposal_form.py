"""提案の形式検査（最小版）。規則で決まる 2 つだけを見る。

- 雛形の空欄の目印が残っている
- 親の新規の提案に、必須の見出しが欠けている

`ticket new` の雛形（scaffold）と承認の検査（agree_candidates）の両方が読む定数と検査を置く。
scaffold が agree_candidates を import しているので、どちらかに置くと循環 import になる。
このモジュールが import してよいのは `ticket_model` と `..policy.rules` だけ。

## 決まり

- 検査は文字列を調べるだけ。ファイルも git も読まない
- 目印の残りは、新規の提案なら error、改版（`revision=True`）なら warn。改版は承認済みチケットの
  コピーなので、古い残りで止めない
- 見出しの欠けは、親の新規の提案だけを見て warn にまとめる。子と改版には言わない
- 見出しは、コードフェンス（行頭が ``` か ~~~）の外にある `## <名前>` の行だけを数える。
  名前は前後の空白を除いてちょうど一致すること。深さは `##` だけ（`###` は数えない）
"""

from __future__ import annotations

from ..policy import rules
from . import ticket_model

# 親の必須の見出し（雛形の本文の並び）。「背景」は「目的」にまとめ、
# 「計画の理由」は計画の説明として残す。
PARENT_HEADINGS: tuple[str, ...] = ("目的", "範囲", "対象外", "完了の確かめ方", "計画の理由")

# rationale の空欄の目印。
RATIONALE_TODO = "（ここにこの作業をする理由を書く）"

# 見出しごとの空欄の目印。雛形は見出しの下に 1 行ずつ置く。
HEADING_TODO: dict[str, str] = {
    "目的": "（ここにこの作業の目的を書く）",
    "範囲": "（ここにこの作業の範囲を書く）",
    "対象外": "（ここにこの作業の対象外を書く）",
    "完了の確かめ方": "（ここに完了をどう確かめるかを書く）",
    "計画の理由": "（ここに計画の理由を書く）",
}

# 予約した目印。提案に 1 つでも残っていれば、空欄が埋まっていない。
PLACEHOLDERS: tuple[str, ...] = (RATIONALE_TODO, *HEADING_TODO.values())

_FENCE_CHARS = ("`", "~")
# 字下げがこれより深い行はコードブロックで、フェンスではない（Markdown の決まり）。
_FENCE_INDENT = 3


def _fence_of(line: str) -> tuple[str, int, str] | None:
    """フェンスの行なら (記号, 記号の連続の長さ, 記号のあとの文字列)。そうでなければ None。"""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > _FENCE_INDENT:
        return None
    for ch in _FENCE_CHARS:
        run = len(stripped) - len(stripped.lstrip(ch))
        if run >= 3:
            return ch, run, stripped[run:]
    return None


def headings_in(body: str) -> set[str]:
    """本文のコードフェンスの外にある `## <名前>` の名前の集まり。

    フェンスは、開きと同じ記号で、開き以上の長さの、記号だけの行で閉じる。閉じなければ末尾まで続く。
    """
    found: set[str] = set()
    open_fence: tuple[str, int] | None = None
    for line in body.splitlines():
        fence = _fence_of(line)
        if open_fence is None:
            if fence:
                open_fence = (fence[0], fence[1])
                continue
        else:
            closes = fence and fence[0] == open_fence[0] and fence[1] >= open_fence[1]
            if closes and not fence[2].strip():
                open_fence = None
            continue
        if line.startswith("## "):
            found.add(line[3:].strip())
    return found


def form_problems(t: ticket_model.Ticket, *, revision: bool = False) -> list[rules.Problem]:
    """提案の形式の苦情。目印の残りと、親の新規の提案の見出しの欠け。"""
    out: list[rules.Problem] = []
    severity = rules.SEVERITY_WARN if revision else rules.SEVERITY_ERROR
    for place, text in (("rationale", t.rationale), ("本文", t.body)):
        left = [mark for mark in PLACEHOLDERS if mark in text]
        if not left:
            continue
        marks = "、".join(f"「{mark}」" for mark in left)
        out.append(
            rules.Problem(
                severity,
                t.ticket,
                f"{place}に雛形の空欄の目印が残っている: {marks}。"
                "実際の内容に書き換えてから承認を頼みます",
            )
        )
    if revision or t.is_child:
        return out
    present = headings_in(t.body)
    missing = [name for name in PARENT_HEADINGS if name not in present]
    if missing:
        names = "、".join(f"`## {name}`" for name in missing)
        out.append(
            rules.Problem(
                rules.SEVERITY_WARN,
                t.ticket,
                f"親の提案の本文に見出しが欠けている: {names}。"
                "Stop の判定は親の提案に目的・範囲・対象外・完了の確かめ方を求める",
            )
        )
    return out
