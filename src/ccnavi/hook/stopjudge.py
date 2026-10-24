"""Stop の AI 判定の判定役（agent hook）の呼び出しを見分け、ccnavi の各 hook から除く。

判定役は独立したコンテキストで `ccnavi stop-review material|record` を打つ。この呼び出しを
作業者のものと同じに扱うと、失敗の連続・範囲の読み取り・フェーズの知らせ・実行中の印・
範囲の差し戻しが動いて、既存の挙動が変わる。そこで、見分けられた判定役の呼び出しは次のように扱う。

| hook | 判定役の呼び出し |
|---|---|
| PreToolUse | `stop-review material|record` の Bash と返り値の道具だけ通し、ほかは止める |
| PostToolUse・PostToolUseFailure | 何もしない（失敗の連続・範囲・知らせ・報告を動かさない） |
| SubagentStart・SubagentStop | 何もしない（実行中の印を置かず、範囲の検査・差し戻しをしない） |
| Stop | 何もしない |

作業者が `stop-review` を打ったときは止めて、汚染の印を付ける（試行の横取り・偽の記録・
評価の汚染・差し戻しの回避を防ぐ。dry-run では止めず、印だけ付ける）。

見分けるのは payload の `agent_id`。実機（Claude Code 2.1）の agent 型 hook の中の呼び出しは、
`agent_id` が `hook-agent-<uuid>` で `agent_type` は無い。SubagentStart・SubagentStop は来ない。
登録していないワークスペースでは見分けない。ユーザ自身の agent 型 hook も同じ接頭辞になるので、
登録したワークスペースでは、それも判定役として扱う（Bash は止める）。
"""

from __future__ import annotations

import os
import re
import time
from typing import TextIO

from ..infra import fsio, hookio, modes, pathcase, settings, shellread
from ..records import audit, redact, stopreview

EXIT_OK = modes.EXIT_OK

VERBS = ("material", "record")
# 判定役が結果を返すために呼ぶ道具（agent 型 hook の返り値）。止めると判定役が返せない。
RETURN_TOOL = "StructuredOutput"
# 実機で確かめた、agent 型 hook の中の呼び出しの agent_id の接頭辞。
JUDGE_AGENT_ID_PREFIX = "hook-agent-"


def is_judge(conf: settings.Settings, payload: hookio.Input) -> bool:
    """この呼び出しが判定役（agent 型 hook の中）のものか。登録していなければ偽。"""
    return bool(conf.stop_review_agent and payload.agent_id.startswith(JUDGE_AGENT_ID_PREFIX))


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


_HARMLESS_REDIRECT = re.compile(r"^(?:\d?>>?|&>)(?:/dev/null|&\d)$")
_REDIRECT_OP = re.compile(r"^(?:\d?>>?|&>|>&)$")
_FD = re.compile(r"^\d$")


def _redirects_ok(words: list[str]) -> bool:
    """語の並びのリダイレクトが、`/dev/null` か別の fd への複製だけか。

    シェルの読み取りは `2>/dev/null` を `2`・`>`・`/dev/null`（`2>&1` を `2`・`>&`・`1`）の
    ように語に割ることがあるので、並びで見る。ほかの行き先は許さない。
    """
    at = 0
    while at < len(words):
        word = words[at]
        if _HARMLESS_REDIRECT.match(word):
            at += 1
            continue
        if _REDIRECT_OP.match(word):
            target = words[at + 1] if at + 1 < len(words) else ""
            if target == "/dev/null" or (word.endswith("&") and _FD.match(target)):
                at += 2
                continue
            return False
        if _REDIRECT.match(word):
            return False
        at += 1
    return True


def _same_dir(a: str, b: str) -> bool:
    return bool(a and b) and pathcase.fold(a).rstrip("/\\") == pathcase.fold(b).rstrip("/\\")


def _strip_redirects(words: list[str]) -> list[str] | None:
    """無害なリダイレクト（`/dev/null`・`&N`）の語を除く。ほかのリダイレクトがあれば None。"""
    if not _redirects_ok(words):
        return None
    out: list[str] = []
    at = 0
    while at < len(words):
        word = words[at]
        following = words[at + 1] if at + 1 < len(words) else ""
        if _HARMLESS_REDIRECT.match(word):
            at += 1
        elif _FD.match(word) and _REDIRECT_OP.match(following):
            # `2>/dev/null` は語に `2`・`>`・`/dev/null` と割れることがある。
            at += 3
        elif _REDIRECT_OP.match(word):
            at += 2
        else:
            out.append(word)
            at += 1
    return out


_LAUNCHER = ".ccnavi/scripts/ccnavi-launcher.sh"
_SHELLS = frozenset({"sh", "bash"})
# 動詞のあとの引数の数。material は <session_id> <transcript_path>、record は JSON を 1 つ足せる。
_ARGS = {"material": (2, 2), "record": (2, 3)}


def _strict_call(words: list[str], cwd: str) -> bool:
    """語の並びが、許す形の `stop-review material|record` そのものか（先頭語から完全一致）。"""
    if words and words[0] == "ccnavi":
        rest = words[1:]
    elif len(words) > 1 and words[0] in _SHELLS:
        launcher = pathcase.fold(words[1]).replace("\\", "/")
        own = pathcase.fold(cwd).replace("\\", "/").rstrip("/") + "/" + _LAUNCHER if cwd else ""
        if launcher != _LAUNCHER and launcher != own:
            return False
        rest = words[2:]
    else:
        return False
    if len(rest) < 2 or rest[0] != "stop-review" or rest[1] not in _ARGS:
        return False
    low, high = _ARGS[rest[1]]
    return low <= len(rest) - 2 <= high


def judge_command_ok(command: str, cwd: str = "") -> bool:
    """判定役に許す Bash か。`ccnavi stop-review material|record …` の 1 本だけ。

    標準入力の JSON を渡すための `echo`・`printf`・`cat`（パイプの左）とヒアドキュメントは許す。
    実機で、判定役が付けた `cd <プロジェクト> 2>/dev/null;` を拒否して、材料を取れずに期限切れに
    なった。そのため、判定役自身の作業ディレクトリ（`cwd`）への `cd` と、`/dev/null` や `&1` への
    リダイレクトだけは許す。
    許すのは、先頭語から完全一致する形だけ（`ccnavi` か `sh <launcher>`、`cd <cwd>`、パスの付かない
    `echo`・`printf`・`cat`）。ほかのコマンドの連結（`;`・`&&`・`||`）・ほかのリダイレクト・
    プロセス置換・コマンド置換・読み切れない形は許さない。
    判定役は transcript を読むので、そこに書かれた命令でほかのコマンドを通されないようにする。
    """
    reading = shellread.read(command)
    head = _heredoc_head(command)
    if head is not None:
        # 単一引用符の中はシェルが展開しない（JSON を引数で渡す形）。引用の外だけを見る。
        head = re.sub(r"'[^']*'", "''", head)
    if (
        reading.degraded
        or head is None
        or "$(" in head
        or "`" in head
        or "${" in head
        or "<(" in head
        or ">(" in head
        or not _heredoc_body_inert(command)
    ):
        return False
    calls = 0
    for words, _place in shellread.placed(command):
        words = _strip_redirects(words) if words else None
        if not words:
            return False
        if _strict_call(words, cwd):
            calls += 1
        elif words[0] == "cd" and len(words) == 2 and _same_dir(words[1], cwd):
            pass
        elif words[0] not in _FEEDERS:
            return False
    return calls == 1


_HEREDOC = re.compile(r"<<-?\s*([\"']?)(\w+)\1")


def _heredoc_body_inert(command: str) -> bool:
    """ヒアドキュメントの本文でシェルが何も展開しないか。

    区切りが引用されていれば本文は展開されない。引用が無いときは、`$`・`` ` ``・`\\` があれば偽。
    """
    lines = command.strip("\n").split("\n")
    if len(lines) == 1:
        return True
    found = _HEREDOC.search(lines[0])
    if not found or found.group(1):
        return True
    return not any(c in "\n".join(lines[1:]) for c in ("$", "`", "\\"))


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
        if line.strip() == found.group(2):
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
    """PreToolUse の入口。通常の判定へ回さないなら真を返す（応答はすでに書き終えている）。

    処理フロー:
    1. 判定役を使わない設定なら偽を返す。
    2. Bash で `stop-review` を呼ぶ形なら `asks` を立てる（`stop_review_call`）。
    3. 判定役の呼び出し（`is_judge`）は、許す形（`judge_command_ok`）か返り値の道具だけを
       ALLOW で通す。それ以外は DENY にして、真を返す。
    4. 判定役でなく `asks` も立たなければ偽を返す。
    5. `asks` が立ったら、呼び出しを印に残す（`mark_taint`）。
       dry-run か、設定が ENABLE でなければ偽を返す。
    6. ENABLE なら、作業からの `stop-review` の呼び出しを DENY にして真を返す。
    """
    if not conf.stop_review_agent:
        return False
    command = payload.tool_input.get("command")
    asks = payload.tool_name == "Bash" and isinstance(command, str) and stop_review_call(command)
    if is_judge(conf, payload):
        record.reason = "stop-review-judge"
        if (asks and judge_command_ok(command, payload.cwd)) or payload.tool_name == RETURN_TOOL:
            # 許すと答える。agent 型 hook の中は dontAsk で動き、許可の規則が無いと Bash が
            # 拒否される（実機で確かめた）。hook の許可で通す。返り値を返す道具も同じ。
            record.decision = audit.ALLOW
            hookio.write_verdict(stdout, hookio.ALLOW, "ccnavi: Stop の判定役の呼び出し")
            return True
        record.decision = audit.SKIP
        hookio.write_verdict(
            stdout,
            hookio.DENY,
            "ccnavi: Stop の判定役が使えるのは、次の形の 1 本だけです。連結・リダイレクトは付けず、"
            "作業ディレクトリのまま実行してください: sh .ccnavi/scripts/ccnavi-launcher.sh "
            "stop-review material <session_id> <transcript_path>",
        )
        record.decision, record.enforced = audit.DENY, True
        return True
    if not asks:
        return False
    if conf.state and payload.session_id:
        mark_taint(conf.state, payload.session_id, command, time.time())
    if mode == modes.DRY_RUN or stopreview.setting(mode, conf.stop_review) != modes.ENABLE:
        return False
    record.decision, record.enforced = audit.DENY, True
    record.reason = "stop-review-worker"
    hookio.write_verdict(
        stdout,
        hookio.DENY,
        "ccnavi: `stop-review` は Stop の判定役が使う入口で、作業では使いません。",
    )
    return True
