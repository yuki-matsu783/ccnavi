"""Stop の AI 判定の判定役（agent hook）の呼び出しを見分け、ccnavi の各 hook から除く。

判定役は独立したコンテキストで `ccnavi stop-review material|record` を打つ。その呼び出しが、
作業者のものと同じに扱われると、失敗の連続・範囲の読み取り・フェーズの知らせ・実行中の印・
範囲の差し戻しが動いて、既存の挙動が変わる。そこで、見分けられた判定役の呼び出しは次のように扱う。

| hook | 判定役の呼び出し |
|---|---|
| PreToolUse | `stop-review material|record` の Bash だけ通し、ほかは止める |
| PostToolUse・PostToolUseFailure | 何もしない（失敗の連続・範囲・知らせ・報告を動かさない） |
| SubagentStart・SubagentStop | 何もしない（実行中の印を置かず、範囲の検査・差し戻しをしない） |
| Stop | 何もしない |

作業者が `stop-review` を打ったときは止めて、汚染の印を付ける（試行の横取り・偽の記録・
評価の汚染・差し戻しの回避を防ぐ。dry-run では止めず、印だけ付ける）。

見分ける値は `CCNAVI_STOP_REVIEW_JUDGE_TYPE`（判定役の呼び出しの payload の `agent_type`）。
どの値で来るかは実機で確かめるまで分からないので、既定は空で、空のうちは見分けない。
見分けられないまま作業者を止めると判定役自身を止めてしまうので、止めるのも印を付けるのも、
見分ける値が設定されているときだけ。
"""

from __future__ import annotations

import os
import re
import time
from typing import TextIO

from ..infra import fsio, hookio, modes, pathcase, settings, shellread
from ..records import audit, redact

EXIT_OK = modes.EXIT_OK

VERBS = ("material", "record")


def is_judge(conf: settings.Settings, payload: hookio.Input) -> bool:
    """この呼び出しが判定役のものか。見分ける値が設定されていなければ偽。"""
    mark = conf.stop_review_judge.strip().casefold()
    return bool(conf.stop_review_agent and mark and payload.agent_type.strip().casefold() == mark)


def stop_review_call(command: str) -> bool:
    """`stop-review material|record` を打つ可能性のあるコマンドか。

    外側のコマンドが `ccnavi stop-review …` の形なら真。`sh -c '…'`・`eval`・置換の中のように
    読み取れない形は、`stop-review` と動詞が並んでいれば真にする（作業者の印のため、誤って
    真になってもよい。ただし `echo` や `grep` の引数に書いただけでは真にしない）。
    """
    outer = shellread.placed(command)
    if any(_is_stop_review(words) for words, _place in outer):
        return True
    runs_text = any(
        words and os.path.basename(pathcase.fold(words[0])) in _RUNNERS for words, _p in outer
    )
    indirect = runs_text or "$" in command or "`" in command
    return bool(indirect and _LOOSE.search(command))


_RUNNERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "eval", "env", "xargs", "exec", "source"})
_FEEDERS = frozenset({"echo", "printf", "cat"})
_LOOSE = re.compile(r"stop-review[\"'\s]+[\"']?(?:material|record)\b", re.IGNORECASE)
_REDIRECT = re.compile(r"^(?:\d*>|&>|<(?!<))")


def judge_command_ok(command: str) -> bool:
    """判定役に許す Bash か。`ccnavi stop-review material|record …` の 1 本だけ。

    標準入力の JSON を渡すための `echo`・`printf`・`cat`（パイプの左）とヒアドキュメントは許す。
    ほかのコマンドの連結（`;`・`&&`・`||`）・リダイレクト・コマンド置換・読み切れない形は許さない。
    判定役は transcript を読むので、そこに書かれた命令でほかのコマンドを通されないようにする。
    """
    reading = shellread.read(command)
    head = _heredoc_head(command)
    if head is not None:
        head = head.replace("${CLAUDE_PROJECT_DIR}", "")  # 登録した hook の prompt が使うパス
    if reading.degraded or head is None or "$(" in head or "`" in head or "${" in head:
        return False
    calls = 0
    for words, _place in shellread.placed(command):
        if not words:
            return False
        if _is_stop_review(words):
            calls += 1
        elif os.path.basename(pathcase.fold(words[0])) not in _FEEDERS:
            return False
        if any(_REDIRECT.match(word) for word in words):
            return False
    return calls == 1


_HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?")


def _heredoc_head(command: str) -> str | None:
    """コマンドの 1 行目。ヒアドキュメントの本文は除く。1 行目より後にコマンドがあれば None。

    本文は標準入力に渡るデータで、実行されない。本文の後ろ（終端の行より後）にも何も書けない。
    """
    lines = command.strip("\n").split("\n")
    if len(lines) == 1:
        return lines[0]
    found = _HEREDOC.search(lines[0])
    if not found:
        return None
    for at, line in enumerate(lines[1:], 1):
        if line.strip() == found.group(1):
            return lines[0] if not any(x.strip() for x in lines[at + 1 :]) else None
    return None


def _is_stop_review(words: list[str]) -> bool:
    for at, word in enumerate(words[:-1]):
        if word == "stop-review" and words[at + 1] in VERBS and at > 0:
            prev = os.path.basename(pathcase.fold(words[at - 1]).replace("\\", "/"))
            if prev.startswith("ccnavi"):
                return True
    return False


def _taint_path(state: str, session: str) -> str:
    return os.path.join(state, f"stoptaint-{fsio.safe_name(session)}.json")


def mark_taint(state: str, session: str, command: str, now: float) -> None:
    """作業者が `stop-review` を打った印。時刻とコマンドを残す（コマンドは伏せ字にして保つ）。"""
    row = fsio.read_dict(_taint_path(state, session)) or {"calls": []}
    calls = row["calls"][-19:] + [{"t": now, "command": redact.redact(command)[:300]}]
    fsio.write_json_atomic(_taint_path(state, session), {"calls": calls})


def tainted(state: str, session: str) -> bool:
    return bool((fsio.read_dict(_taint_path(state, session)) or {}).get("calls"))


def before(
    stdout: TextIO,
    mode: str,
    conf: settings.Settings,
    payload: hookio.Input,
    record: audit.Record,
) -> bool:
    """PreToolUse の入口。通常の判定へ回さないなら真（応答は書き終えた）。"""
    if not conf.stop_review_agent or not conf.stop_review_judge.strip():
        return False
    command = payload.tool_input.get("command")
    asks = payload.tool_name == "Bash" and isinstance(command, str) and stop_review_call(command)
    if is_judge(conf, payload):
        record.decision, record.reason = audit.SKIP, "stop-review-judge"
        if asks and judge_command_ok(command):
            return True
        hookio.write_verdict(
            stdout,
            hookio.DENY,
            "ccnavi: Stop の判定役が使えるのは `ccnavi stop-review material|record` だけです。",
        )
        record.decision, record.enforced = audit.DENY, True
        return True
    if not asks:
        return False
    if conf.state and payload.session_id:
        mark_taint(conf.state, payload.session_id, command, time.time())
    if mode == modes.DRY_RUN:
        return False
    record.decision, record.enforced = audit.DENY, True
    record.reason = "stop-review-worker"
    hookio.write_verdict(
        stdout,
        hookio.DENY,
        "ccnavi: `stop-review` は Stop の判定役が使う入口で、作業では使いません。",
    )
    return True
