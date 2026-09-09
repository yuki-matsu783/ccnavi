"""hook が標準入力に書く payload の解釈と、標準出力に返す応答の組み立て。

両方の形を知っているのはここだけ。上流が変わったとき、直す場所が 1 ファイルで
済むようにしてある。
"""

from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO

# イベント名。payload が自分で名乗るので、登録を間違えても黙って別の判定が
# 走ることはない。
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
STOP = "Stop"

# 1 回の呼び出しに対する判定。緩い順に並べてある。
ALLOW = "allow"
ASK = "ask"
DENY = "deny"


class NoPayload(Exception):
    """標準入力が空だった。hook ではなく人が直接叩いている。

    独立した型にしてある。ここで 0 を返すと、設置を誤った hook が
    正常に動いている hook と見分けられなくなる。
    """


class Unusable(Exception):
    """payload は届いたが解釈できない。"""


@dataclass
class Input:
    """payload のうち判定が読む部分。

    知らないフィールドは無視する。上流にフィールドが 1 つ増えただけで
    判定が壊れないようにするため。
    """

    event: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    cwd: str = ""
    permission_mode: str = ""
    tool_use_id: str = ""

    def field_value(self, name: str) -> str:
        """tool_input から文字列を 1 つ取り出す。"command" や "file_path" など。"""
        value = self.tool_input.get(name)
        return value if isinstance(value, str) else ""


def decode(stream: TextIO) -> Input:
    """payload を 1 件読む。"""
    raw = stream.read()
    if not raw.strip():
        raise NoPayload("標準入力に hook の payload が無い")

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Unusable(f"payload を解釈できない: {exc}") from exc
    if not isinstance(data, dict):
        raise Unusable("payload がオブジェクトではない")

    event = data.get("hook_event_name") or ""
    if not event:
        raise Unusable("payload に hook_event_name が無い")

    tool_input = data.get("tool_input")
    return Input(
        event=event,
        tool_name=data.get("tool_name") or "",
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        session_id=data.get("session_id") or "",
        cwd=data.get("cwd") or "",
        permission_mode=data.get("permission_mode") or "",
        tool_use_id=data.get("tool_use_id") or "",
    )


def write_verdict(stream: TextIO, decision: str, reason: str) -> None:
    """PreToolUse の判定を書き出す。

    理由には「なぜ止めたか」と「代わりに何をすればよいか」が入る。
    通すときは理由が要らないので何も書かない。
    """
    _write(
        stream,
        {
            "hookEventName": PRE_TOOL_USE,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        },
    )


def write_context(stream: TextIO, event: str, text: str) -> None:
    """止められないイベントで、モデルに届く文を書き出す。

    PostToolUse と SessionStart にはこの経路しかない。素の標準出力は捨てられる。
    """
    _write(stream, {"hookEventName": event, "additionalContext": text})


def write_system_message(stream: TextIO, text: str) -> None:
    """人に見せる文を書き出す。

    `additionalContext` との違いは宛先。あちらはモデルが読み、これは
    トランスクリプトに出て人が読む。ターンの終わりに「宣言した保護領域が
    こう変わっている」と言う相手は、次の一手を打つエージェントではなく、
    それを見ている人になる。エージェントには実行後の監視が呼び出しごとに
    返しているので、同じことを 2 度モデルへ送らない。

    このキーは hookSpecificOutput の中ではなく、応答の一番外に置く。
    """
    stream.write(json.dumps({"systemMessage": text}, ensure_ascii=False))
    stream.write("\n")


def _write(stream: TextIO, payload: dict[str, Any]) -> None:
    # Claude Code は標準出力が "{" で始まり "}" で終わるときだけ JSON として
    # 読む。だから他のものを一緒に出してはいけない。
    stream.write(json.dumps({"hookSpecificOutput": payload}, ensure_ascii=False))
    stream.write("\n")


def rebind_streams() -> None:
    """標準入出力を、コンソールのコードページに関係なく UTF-8 で扱う。

    Windows は既定のインストールだと UTF-8 ではないコードページを
    Python のプロセスに渡す。日本語のルール文面が入った payload が、
    判定に入る前に壊れる。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is None:
            continue
        # 再設定できない差し替え済みのストリームは黙って飛ばす。
        # テストが渡してくる文字列バッファがこれにあたる。
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
