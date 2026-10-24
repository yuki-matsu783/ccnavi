"""`ccnavi idle start|check|notice|reset`。放置したセッションの引き継ぎ促しの入口。

    ccnavi idle notice                      Stop（同期）。ユーザへの知らせを 1 セッション 1 回出す
    ccnavi idle start                       Stop（`ccnavi-idle.sh` 経由）。待機の指示を返す
    ccnavi idle check <セッション> <印> <待ち秒>   待機のあと。起こすべきか、起こすときの指示を返す
    ccnavi idle reset                       UserPromptSubmit。ユーザが打ったら待機をやめる

payload は標準入力（`notice`・`start`・`reset`）。どれも終了コード 0 で終わる。促しの失敗が作業を
止めてはならないため。判断は `records.idlehandoff`、待機（`sleep`）は sh が受け持つ。

`start` の標準出力は 1 行 1 項目（`<キー> <値>`。sh は jq を使わない）。始めるときは `wait`・
`session`・`token` の 3 行、始めないときは `skip <理由>` の 1 行。
`check` は 1 行目が `fire` か `skip`。`fire` のときは 2 行目以降が起こすときの指示。
`notice` は、出すときだけ `{"systemMessage": …}` の JSON を 1 行書く（ユーザに見える）。
"""

from __future__ import annotations

import json
import os
from typing import TextIO

from ..infra import hookio, settings
from ..records import idlehandoff

EXIT_OK = 0

VERBS = ("notice", "start", "check", "reset")


def run(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    words: list[str],
    env: dict[str, str] | None = None,
) -> int:
    """`idle` の入口。動詞に振り分ける。

    処理フロー:
    1. 動詞が合わなければ、使い方を標準エラーに書いて 0 で終える。
    2. 状態の置き場が無いか、閾値が 0（無効）なら、何もせず `skip` で終える。
    3. 動詞ごとに `_notice`・`_start`・`_check`・`_reset` へ渡す。
    """
    verb = words[1] if len(words) > 1 else ""
    if verb not in VERBS:
        stderr.write("使い方: ccnavi idle notice|start|check|reset\n")
        return EXIT_OK
    env = dict(os.environ) if env is None else env
    limit = idlehandoff.threshold(conf.handoff_suggest_tokens)
    if not conf.state or limit == 0:
        _skip(stdout, verb, "disabled")
        return EXIT_OK
    if verb == "check":
        return _check(stdout, conf, words[2:], limit)
    try:
        payload = hookio.decode(stdin)
    except (hookio.NoPayload, hookio.Unusable):
        _skip(stdout, verb, "no-payload")
        return EXIT_OK
    if verb == "reset":
        idlehandoff.reset(
            conf.state, payload.session_id, payload.prompt, payload.from_background_task()
        )
        return EXIT_OK
    why = _not_for_this(payload, env)
    if why:
        _skip(stdout, verb, why)
        return EXIT_OK
    usage = idlehandoff.usage_of(payload.transcript_path)
    if verb == "notice":
        return _notice(stdout, conf, payload, usage, limit)
    return _start(stdout, conf, payload, usage, limit)


def _skip(stdout: TextIO, verb: str, why: str) -> None:
    if verb in ("start", "check"):
        stdout.write(f"skip {why}\n")


def _not_for_this(payload: hookio.Input, env: dict[str, str]) -> str:
    """この呼び出しが対象でない理由。対象なら空。"""
    if payload.event != hookio.STOP:
        return "not-stop"
    if payload.agent_id:
        return "subagent"
    if idlehandoff.unattended(env):
        return "unattended"
    return ""


def _notice(stdout: TextIO, conf, payload: hookio.Input, usage, limit: int) -> int:
    text = idlehandoff.notice(
        conf.state, payload.session_id, usage, limit, conf.handoff_wait_seconds
    )
    if text:
        stdout.write(json.dumps({"systemMessage": text}, ensure_ascii=False) + "\n")
    return EXIT_OK


def _start(stdout: TextIO, conf, payload: hookio.Input, usage, limit: int) -> int:
    idlehandoff.prune(conf.state)
    plan = idlehandoff.start(
        conf.state,
        payload.session_id,
        usage,
        limit,
        conf.handoff_wait_seconds,
        payload.transcript_path,
    )
    if plan is None:
        stdout.write("skip not-due\n")
        return EXIT_OK
    stdout.write(f"wait {plan.wait}\nsession {payload.session_id}\ntoken {plan.token}\n")
    return EXIT_OK


def _check(stdout: TextIO, conf: settings.Settings, rest: list[str], limit: int) -> int:
    if len(rest) != 3 or not (rest[2].isascii() and rest[2].isdigit()):
        stdout.write("skip usage\n")
        return EXIT_OK
    session, token, wait = rest[0], rest[1], int(rest[2])
    text = idlehandoff.check(conf.state, session, token, wait, limit)
    if text is None:
        stdout.write("skip not-waiting\n")
        return EXIT_OK
    stdout.write("fire\n" + text)
    return EXIT_OK
