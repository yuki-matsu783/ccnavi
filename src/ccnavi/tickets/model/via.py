"""いまの起動を誰がどの経路で動かしたか。マーカーの `via`・`actor` に書く値の持ち主。

状態の履歴は書かない。置き場とマーカーが状態のすべてで、ここは 1 回の起動の間だけ
経路とアカウントを覚える。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

# 動かした経路。
#   cli       エージェントが sh（ccnavi-ticket.sh / ccnavi-review.sh）から打ったサブコマンド
#   terminal  ユーザがターミナルで打った判断（--agree / --reviewed / --close-early）
#   board     VS Code のボードから押した判断（--agree --yes / --reviewed --yes）
#   hook      hook（フェーズの終わりの告知が置くマーカー）
VIA_CLI = "cli"
VIA_TERMINAL = "terminal"
VIA_BOARD = "board"
VIA_HOOK = "hook"
#   chrome    Chrome 拡張から押した判断（承認・レビュー済みなど）
VIA_CHROME = "chrome"

_state: dict = {"via": VIA_CLI, "actor": ""}


@contextlib.contextmanager
def session(via: str, actor: str = "") -> Iterator[None]:
    """1 回の起動の間、経路とアカウントを覚える。抜けるときに前の値へ戻す。

    テストは同じプロセスで何度も起動するので、前の起動の値が次へ引き継がれないようにする。
    """
    before = dict(_state)
    _state["via"] = via
    _state["actor"] = actor
    try:
        yield
    finally:
        _state.update(before)


def set_via(via: str) -> None:
    """いまの起動の経路を差し替える。`session` の中で、経路が後から分かったときに使う。"""
    _state["via"] = via


def set_actor(actor: str) -> None:
    """いまの起動のアカウントを足す（decide が使う）。空なら何もしない。"""
    if actor:
        _state["actor"] = actor


def actor() -> str:
    return str(_state["actor"])


def via() -> str:
    return str(_state["via"])
