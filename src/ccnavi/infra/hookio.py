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

# イベント名。payload に自分のイベント名が入っているので、登録を間違えても
# 気づかないうちに別の判定が走ることはない。
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
# ツール自体が失敗した後に来る。止められないイベントで、返せるのは文だけ。
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
# バックグラウンド作業の完了通知が UserPromptSubmit の prompt として届くときの先頭に付く文字列。
TASK_NOTIFICATION = "<task-notification>"
# ユーザが打ったのではない UserPromptSubmit の、prompt の先頭に付く目印（`is_user_prompt`）。
# `[idle-handoff]` は ccnavi 自身が放置したセッションを起こす指示（records/idlehandoff.MARKER と
# 同じ値。tests/core/test_user_prompt.py が一致を見る）。残りは実機の payload ではなく、
# 実行環境の案内が示す、外部の出来事がセッションを起こす封筒の形。
NOT_USER_STARTS = (
    "[idle-handoff]",
    "<wake",
    "<webhook-payload",
    "<child-session-event",
    "<event ",
    "[system notification",
    "<system-reminder",
)
STOP = "Stop"
# サブエージェントの開始と終了。開始は止められないイベントで、返せるのは文だけ。
# 終了は Stop と同じで、exit 2 が「続けさせる」を意味する。
SUBAGENT_START = "SubagentStart"
SUBAGENT_STOP = "SubagentStop"

# 1 回の呼び出しに対する判定。緩い順に並べてある。
ASK = "ask"
ALLOW = "allow"
DENY = "deny"


# tool_input のうち、判定が文字列として読む欄（judge.SUBJECT_FIELDS と、書き込みの中身の欄）。
# ここが文字列でない payload は、欄が無いものとして読まずに止める。空として読むと、
# `{"command": ["git", "push"]}` が「ヒットするルールが無い」で通る。
TOOL_INPUT_TEXT_FIELDS = (
    "command",
    "file_path",
    "path",
    "notebook_path",
    "skill",
    "description",
    "url",
    "prompt",
    "pattern",
    "content",
    "old_string",
    "new_string",
)


class NoPayload(Exception):
    """標準入力が空だった。hook ではなくユーザが直接打っている。

    独立した型にしてある。ここで 0 を返すと、設置を誤った hook が
    正常に動いている hook と見分けられなくなる。
    """


class Unusable(Exception):
    """payload は届いたが解釈できない。

    event は読めたところまでのイベント名（読めなかったら空）。止めた文
    （`reasons.payload_unusable`）をイベントに合わせるために持つ。exit 2 の意味と
    文の宛先は、イベントごとに違う。
    """

    def __init__(self, message: str, event: str = "") -> None:
        super().__init__(message)
        self.event = event


@dataclass
class Input:
    """payload のうち判定が読む部分。

    知らないフィールドは無視する。上流にフィールドが 1 つ増えただけで
    判定が動かなくならないようにするため。
    """

    event: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    cwd: str = ""
    permission_mode: str = ""
    tool_use_id: str = ""
    # agent_id はサブエージェントの中で走った呼び出しにだけ付く。判定のキーには
    # 使わない（判定のキーはファイルの行き先）。使うのは、サブエージェントに
    # 許さない操作を見分けるときと、差し戻しの回数を数えるときだけ。
    agent_id: str = ""
    agent_type: str = ""
    # SubagentStart に tools が来るかは実機で確かめていない。来ていれば、編集するサブエージェント
    # かの見分けに使う（hook/agentkind）。文字列のリスト以外は無いものとして読む。
    tools: list[str] | None = None
    # tool_response は PostToolUse にだけ来る。Agent の起動の後には agentId が入る。
    tool_response: dict[str, Any] = field(default_factory=dict)
    # error と is_interrupt は PostToolUseFailure にだけ来る。error は失敗の文（形はツール次第）、
    # is_interrupt は中断による失敗。error の中身は判定に使わず、記録にも診断ログにも書かない。
    error: str = ""
    is_interrupt: bool = False
    # stop_hook_active は Stop / SubagentStop にだけ来る。真なら、この終わり方は Stop の hook が
    # 続けさせた結果（連鎖の 2 回目以降）。促しを 1 回に留めるために読む。
    stop_hook_active: bool = False
    # source は SessionStart にだけ来る。`startup` / `resume` / `clear` / `compact`。Stop の促しの
    # 数えを破棄するのは `startup` だけ（ctxfile.forget）。compact・再開・clear で破棄すると、
    # compact が N 回より先に来る長いセッションで一度も届かない。
    source: str = ""
    # prompt は UserPromptSubmit にだけ来る。ユーザの依頼文で、issue・MR の指定を探すのに読む
    # （tickets.branchfind）。記録にも診断ログにも書かない。
    prompt: str = ""
    # transcript_path は会話の記録（JSONL）の場所。Stop の AI 判定が共有キー（末尾のアシスタント行の
    # uuid）を導くためだけに読む。記録にも診断ログにも書かない。
    transcript_path: str = ""
    # busy は Stop にだけ来る。エージェントが動かしているバックグラウンド作業
    # （`background_tasks`）か、予約した繰り返し（`session_crons`）が残っているとき真。
    # そのターンの終わりのあとにも、エージェントの手がワークツリーに届きうる。
    busy: bool = False

    def from_background_task(self) -> bool:
        """この UserPromptSubmit が、ユーザの発言ではなくバックグラウンド作業の完了通知か。

        完了通知は、ユーザが何も打たなくても UserPromptSubmit を起こす。そのとき prompt は
        `<task-notification>` で始まる（実機で確かめた）。ユーザの手による変更を受け入れる
        処理は、この通知では行わない。バックグラウンドのプロセスが書いた変更を、ユーザの変更として
        受け入れてしまうため。
        """
        return TASK_NOTIFICATION in self.prompt

    def field_value(self, name: str) -> str:
        """tool_input から文字列を 1 つ取り出す。"command" や "file_path" など。"""
        value = self.tool_input.get(name)
        return value if isinstance(value, str) else ""


def is_user_prompt(payload: Input) -> bool:
    """この UserPromptSubmit は、ユーザの発言と言い切れるか。

    ユーザの返事を数えるもの（ターン分離の関門）だけが使う。迷うものは数えない。数えると、
    ユーザが何も打たなくても関門が開くため。`from_background_task` は変えない（ターンの印と、
    ユーザの手による変更の受け入れは、これまでどおりそちらで決める）。

    数えないもの:
    - イベントが UserPromptSubmit でない、サブエージェントの中、prompt が空
    - バックグラウンド作業の完了通知（prompt のどこかに `<task-notification>` を含む。先頭に
      限らないのは `from_background_task` と同じ。ユーザがその文字列を自分で打っても数えない
      非対称は、安全側として残す。普通に打ち直せば数える）
    - 先頭が `NOT_USER_STARTS` の目印で始まるもの（大文字小文字は区別しない）

    payload にユーザの発言と言い切れる欄（来歴）は無い。予約した実行（`session_crons`、
    `ScheduleWakeup`、`send_later`）の prompt は保存した文そのままで、打った文と見分けられない。
    見分けられない形は、数える側に残る。
    """
    if payload.event != USER_PROMPT_SUBMIT or payload.agent_id:
        return False
    text = payload.prompt.strip()
    if not text or payload.from_background_task():
        return False
    return not text.lower().startswith(NOT_USER_STARTS)


def _text_list(value: Any) -> list[str] | None:
    """文字列だけのリストならそのまま、それ以外は None（欄が無いのと同じに読む）。"""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def decode(stream: TextIO) -> Input:
    """payload を 1 件読む。

    処理フロー:
    1. 入力を全部読む。空なら NoPayload で止める。
    2. JSON として読み、オブジェクトでなければ Unusable で止める。
    3. hook_event_name が空でない文字列であることを確かめる。
    4. 判定のキーになる欄（tool_name・session_id など）が、あれば文字列であることを確かめる。
    5. tool_input がオブジェクトで、TOOL_INPUT_TEXT_FIELDS の欄が文字列であることを確かめる。
    6. 残りの欄は型を見て、違えば既定の値（空の文字列・空のマップ・偽）にして Input を組んで返す。
    """
    raw = stream.read()
    if not raw.strip():
        raise NoPayload("標準入力に hook の payload がありません。")

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Unusable(f"payload を解釈できない: {exc}") from exc
    if not isinstance(data, dict):
        raise Unusable("payload がオブジェクトではない")

    event = data.get("hook_event_name") or ""
    if not isinstance(event, str):
        raise Unusable("payload の hook_event_name が文字列ではない")
    if not event:
        raise Unusable("payload に hook_event_name がありません。")
    # 判定のキーになる欄が文字列でないなら、欄が無い payload として読まずに止める。
    # 無いものとして読むと、ツールの名前が分からないまま「ヒットするルールが無い」で通る。
    for name in ("tool_name", "session_id", "cwd", "permission_mode", "tool_use_id"):
        value = data.get(name)
        if value is not None and not isinstance(value, str):
            raise Unusable(f"payload の {name} が文字列ではない", event)

    tool_input = data.get("tool_input")
    if tool_input is not None and not isinstance(tool_input, dict):
        raise Unusable("payload の tool_input がオブジェクトではない", event)
    for name in TOOL_INPUT_TEXT_FIELDS:
        value = (tool_input or {}).get(name)
        if value is not None and not isinstance(value, str):
            raise Unusable(f"payload の tool_input.{name} が文字列ではない", event)
    tool_response = data.get("tool_response")
    return Input(
        tool_response=tool_response if isinstance(tool_response, dict) else {},
        event=event,
        tool_name=data.get("tool_name") or "",
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        session_id=data.get("session_id") or "",
        cwd=data.get("cwd") or "",
        permission_mode=data.get("permission_mode") or "",
        tool_use_id=data.get("tool_use_id") or "",
        agent_id=str(data.get("agent_id") or ""),
        agent_type=str(data.get("agent_type") or ""),
        tools=_text_list(data.get("tools")),
        error=data.get("error") if isinstance(data.get("error"), str) else "",
        is_interrupt=data.get("is_interrupt") is True,
        stop_hook_active=data.get("stop_hook_active") is True,
        source=str(data.get("source") or ""),
        prompt=data.get("prompt") if isinstance(data.get("prompt"), str) else "",
        transcript_path=(
            data.get("transcript_path") if isinstance(data.get("transcript_path"), str) else ""
        ),
        busy=_has_items(data.get("background_tasks")) or _has_items(data.get("session_crons")),
    )


def _has_items(value: Any) -> bool:
    """空でないリストか。欄が無い・形が違うときは偽（無いものとして読む）。"""
    return isinstance(value, list) and len(value) > 0


def write_verdict(stream: TextIO, decision: str, reason: str, context: str = "") -> None:
    """PreToolUse の判定を書き出す。

    理由には「なぜ止めたか」と「代わりに何をすればよいか」が入る。
    通すときは理由が要らないので何も書かない。

    context はルールの `additionalContext`。判定と一緒に `additionalContext` として
    載せる。deny でも ask でも、理由と一緒にモデルへ届くことは実際に確かめた
    （Claude Code 2.1）。空ならキーごと出さない。
    """
    payload: dict[str, Any] = {
        "hookEventName": PRE_TOOL_USE,
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }
    if context:
        payload["additionalContext"] = context
    _write(stream, payload)


def write_pre_tool_use(
    stream: TextIO,
    *,
    decision: str = "",
    reason: str = "",
    context: str = "",
    updated_input: dict[str, Any] | None = None,
) -> None:
    """PreToolUse の応答を 1 つの JSON に組む。判定・理由・文・入力の書き換えのどれも空にできる。

    入力の書き換え（`updatedInput`）は、判定（`permissionDecision`）と別に持つ。判定を付けなければ
    権限の確認の結末は変わらず、書き換えた入力でそのまま進む。判定を付けると、`allow` は確認を
    飛ばし、`ask` は書き換えた入力をユーザに見せる。呼び手が判定を渡したときだけ付ける。
    起動ツールの担当の案内（`hook.agentlaunch`）が使う。
    """
    body: dict[str, Any] = {"hookEventName": PRE_TOOL_USE}
    if decision:
        body["permissionDecision"] = decision
        body["permissionDecisionReason"] = reason
    if context:
        body["additionalContext"] = context
    if updated_input is not None:
        body["updatedInput"] = updated_input
    _write(stream, body)


def write_context(stream: TextIO, event: str, text: str, system: str = "") -> None:
    """判定を返さないとき、モデルに届く文を書き出す。

    PostToolUse と SessionStart は判定自体ができないのでこの経路しかない。
    PreToolUse でも、ccnavi がこの呼び出しの判定を持たない（handover）ときや
    dry-run で知らせるだけのときはこちらを使う。素の標準出力は破棄される。

    system はユーザに見せる文（`systemMessage`）。同じ 1 つの JSON の一番外に並べる。
    空ならキーごと出さない。
    """
    body: dict[str, Any] = {
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": text}
    }
    if system:
        body["systemMessage"] = system
    stream.write(json.dumps(body, ensure_ascii=False))
    stream.write("\n")


def write_system_message(stream: TextIO, text: str) -> None:
    """ユーザに見せる文を書き出す。

    `additionalContext` との違いは宛先。あちらはモデルが読み、これは
    トランスクリプトに出てユーザが読む。ターンの終わりに「宣言した保護領域が
    こう変わっている」と言う相手は、それを見ているユーザになる。
    エージェントには実行後チェックが呼び出しごとに返しているので、同じことを 2 度モデルへ送らない。

    このキーは応答の一番外に置く。
    """
    stream.write(json.dumps({"systemMessage": text}, ensure_ascii=False))
    stream.write("\n")


def write_stop_block(stream: TextIO, reason: str, system: str = "") -> None:
    """Stop を止めて、モデルに続けさせる。`reason` はモデルが読む次の一手。

    Stop の応答は一番外の `decision` / `reason` で返す。同じ Stop でユーザへの報告
    （`systemMessage`）も返したいので、1 つの JSON にまとめる（exit 2 だと標準出力の JSON は
    読まれない）。空の `system` はキーごと出さない。
    """
    body: dict[str, Any] = {"decision": "block", "reason": reason}
    if system:
        body["systemMessage"] = system
    stream.write(json.dumps(body, ensure_ascii=False))
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
    判定に入る前に中身が正しく読めなくなる。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is None:
            continue
        # 再設定できない差し替え済みのストリームは何も出さずに飛ばす。
        # テストが渡してくる文字列バッファがこれにあたる。
        with contextlib.suppress(AttributeError, ValueError):
            if stream is sys.stdin:
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                # 出す改行は LF に固定する。Windows の既定（CRLF）だと、1 行 1 項目の答えを
                # 読む sh（`sed -n 's/^キー //p'`）の値の末尾に CR が残る。
                stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
