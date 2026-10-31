"""直接作業のコミットのあとに、レビュー手順の次の一手を足す（PostToolUse）。

直接作業（チケットに結び付かないワークツリー）は、コミットのあと `integrate` の前に、
敵対的レビューとフラットな評価を挟む。この手順は、セッションの頭には渡さない。
コミットが済んだ時点で言う（連続する作業は、前の作業の完了のときに次だけを案内する）。
Stop の促しはターンを伸ばすので、案内の主経路にしない。

出すのは次をすべて満たすときだけ。判定できないとき（読み切れないコマンド行・git の失敗・期限不足）は
何も出さない。

- メインのエージェントの Bash（サブエージェントの中ではない）
- `ccnavi-git.sh commit` を打った場所が、直接作業のワークツリー（`.claude/worktrees/<名前>`。
  ブランチが名前と同じで、チケットに結び付かない。判定は Stop の促しと同じ `direct_check`）
- 実際にコミットが積まれた（`--dry-run` の類いではなく、HEAD の履歴の最後が直前の commit）

ワークツリーの名前ごとにセッションで 1 回だけ出す。覚え方は `additionalContextOnce` と同じ
（`ctxfile`）。新しい記録は作らない。起動・再開・compact で忘れるので、そのあとは言い直す。
"""

from __future__ import annotations

import re
import time
from typing import TextIO

from ..infra import gitcmd, hookio, pathcase, settings, shellread, tree
from ..policy import ctxfile, rules, texts
from ..tickets.lifecycle import stop_integrate as osi
from . import wrapguard

# 数えのキー（`ctxfile` の記録）。ワークツリーの名前ごとに分ける。
ONCE_ID = "builtin:direct-commit-review"
TEXT_ID = "NOTICE_DIRECT_COMMIT"
# 判定に使う時間（秒）。PostToolUse を長く待たせない。
SECONDS = 2.0
# 直前の commit と見なす古さ（秒）。コミット前の検査が長引く分を見込む。
RECENT_SECONDS = 600
# コミットせずに内容だけを出すオプション（`git commit --dry-run` と、それを含意する出力形式）。
_NO_COMMIT = frozenset({"--dry-run", "--short", "--porcelain", "--long"})
_REFLOG_AT = re.compile(r"@\{(\d+)\}")


def notice(stderr: TextIO, conf: settings.Settings, root: str, payload: hookio.Input) -> str:
    """案内の文。出さないときは空。何が起きても実行後チェックを止めない。"""
    command = str(payload.tool_input.get("command") or "")
    if payload.agent_id or payload.tool_name != "Bash" or not conf.state:
        return ""
    if "ccnavi-git.sh" not in command.lower():
        return ""
    try:
        return _notice(stderr, conf, root, payload, command)
    except Exception:  # noqa: BLE001 - 案内の足しなので、何が起きても実行後チェックを止めない
        return ""


def _notice(
    stderr: TextIO, conf: settings.Settings, root: str, payload: hookio.Input, command: str
) -> str:
    """処理フロー:
    1. コマンド行から `commit` の呼び出しと、走った場所を取り出す。
    2. 場所ごとに、直接作業のワークツリーで直前にコミットが積まれたかを確かめる（`_direct_name`）。
    3. 名前ごとに 1 回だけの文脈として、文面を渡す。
    """
    deadline = time.monotonic() + SECONDS
    names: dict[str, str] = {}
    for args, place in wrapguard.git_calls(
        payload.cwd, command, shellread.read(command).degraded, "commit"
    ):
        if place is None or _NO_COMMIT.intersection(args):
            continue
        name = _direct_name(root, conf, place, deadline)
        if name:
            names.setdefault(pathcase.fold(name), name)
    carriers = [
        rules.Rule(
            id=f"{ONCE_ID}:{key}",
            additional_context_once=texts.say(root, TEXT_ID, {"name": name}),
        )
        for key, name in names.items()
    ]
    return ctxfile.for_rules(stderr, conf.state, payload, carriers) if carriers else ""


def _direct_name(root: str, conf: settings.Settings, place: str, deadline: float) -> str:
    """`place` が、いま直前にコミットが積まれた直接作業のワークツリーなら、その名前。違えば空。"""
    here = tree.tree_of(root, place, conf.projects)
    if here is None or here.is_main:
        return ""
    reason, unsure = osi.direct_check(root, conf, here, deadline)
    if reason or unsure or not _just_committed(here.root, deadline):
        return ""
    return here.name


def _just_committed(worktree: str, deadline: float) -> bool:
    """HEAD の履歴の最後が、直前の commit か。読めなければ偽。

    `--dry-run` や、コミットが積まれなかった呼び出しでは HEAD が動かない。そのとき最後の項目は
    古い commit のままなので、時刻で切る。
    """
    done = gitcmd.run_until(worktree, ["reflog", "-1", "--date=unix", "--format=%gd|%gs"], deadline)
    if not done.ok:
        return False
    when, _, what = done.out.strip().partition("|")
    stamp = _REFLOG_AT.search(when)
    if stamp is None or not what.startswith("commit"):
        return False
    return time.time() - int(stamp.group(1)) <= RECENT_SECONDS
