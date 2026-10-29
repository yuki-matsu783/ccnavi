"""プロジェクトのツリーにある、共通のコピー（共通レイヤーのミラーと工程スキル）のずれの見分けと案内。

ミラー（`.ccnavi/config/common/`）と工程スキルの `skills/` は、ccnavi が配るコピーで、エージェントの
作業の変更ではない。そのずれがコミットや取り消しを促すと、承認の範囲を超えるので、
ユーザに `ccnavi-user-mirror.sh` で揃えてもらう案内に変える。`ready`・`finish`・`confirm` が
同じ見分けと文面を使う。判定（止めるか通すか）は呼び手が持ち、ここは見分けと文面だけ。
"""

from __future__ import annotations

import os

from ...infra import pathcase, settings
from . import configsync, skillsync

_SHOWN = 10

NOT_WORK = "ずれの分は作業による変更ではないため、コミットも取り消しもしない。"
ASK = (
    "ユーザには、`ccnavi-user-mirror.sh <プロジェクト名>` で差分と値を確かめ、"
    "`--yes --expect <値>` で `config/common` も `skills/` も揃えて、"
    "統合先にコミットしてもらうよう頼む"
)


def prefixes(conf: settings.Settings) -> tuple[str, ...]:
    """プロジェクトのツリーで ccnavi がコピーを置く場所。ルートからの相対パスで、末尾に `/` を付け、
    大文字小文字は畳んである。共通レイヤーのミラーと工程スキルの 2 つ。"""
    mirror = configsync.mirror_dir(conf, "").replace(os.sep, "/").strip("/")
    return (pathcase.fold(mirror + "/"), pathcase.fold(skillsync.SKILLS_DIR + "/"))


def split(conf: settings.Settings, paths: list[str]) -> tuple[list[str], list[str]]:
    """パスを（ミラー側, それ以外）に分ける。"""
    marks = prefixes(conf)
    mirrored = [p for p in paths if pathcase.fold(p).startswith(marks)]
    return mirrored, [p for p in paths if not pathcase.fold(p).startswith(marks)]


def label(mirrored: list[str]) -> str:
    """ずれたパスの表示（先頭 10 件まで）。"""
    shown = "、".join(mirrored[:_SHOWN])
    if len(mirrored) > _SHOWN:
        shown += f" ほか {len(mirrored) - _SHOWN} 件"
    return f"共通レイヤーのコピーとのずれ: {shown}"


def others_note(others: int) -> str:
    """ずれ以外の変更があるときの一文。無ければ空。"""
    return f"ずれ以外の変更が {others} 件ある。そちらは作業の変更として扱う。" if others else ""
