"""ユーザへの確認（ask）の前に、直前のエージェントの発言が背景と結果を言っているかを調べる。

確認の画面に出るのはコマンドや質問だけで、なぜその操作が要るのか、許可と拒否で何が起きるのかは
出ない。ユーザが判断できるように、確認の前の発言で言わせる。対象は、ccnavi が確認に出す呼び出し
（ルールの `ask`・ターン分離の関門の確認）と `AskUserQuestion`。

- 見るのは会話記録（`transcript_path`）の末尾。最後のユーザの行（プロンプトかツールの結果）より
  後ろの、サイドチェーンでないアシスタントの text を集め、2 つの節の見出しがあるかを調べる
- PreToolUse の時点では、直前の発言がまだ書き込まれていないことがある（実測は 3 回中 2 回、
  0.5 秒以内に現れた）。節が揃うまで、期限の手前まで 0.1 秒ごとに読み直す
- 記録が読めない・パスが無いときは検査しない。促しの検査であって、保護ではないため
- 見出しの語（`SECTIONS` の別名を含む）があるかだけを見る。中身が適切かどうかは機械では判断しない
"""

from __future__ import annotations

import json
import os
import re
import time

from ..infra import tunables
from ..policy import texts

CODE = "DENY_ASK_NEEDS_CONTEXT"
RULE_ID = "builtin:ask-context"
MESSAGE_ID = "DENY_ASK_NEEDS_CONTEXT"

# 直前の発言に要る節。キーは文面（deny.yml）が言う名前で、値はその節として数える見出しの言い方。
SECTIONS = {
    "前提・背景": ("前提・背景", "背景", "前提"),
    "想定結果": ("想定結果", "選んだ時の結果"),
}

# 会話記録の末尾から読む長さ。現在のステップの発言はこの中に収まる。
_TAIL_BYTES = 512 * 1024
_POLL_SECONDS = 0.1
# 期限の手前で読み直しを止める余裕（秒）。
_MARGIN_SECONDS = 0.5


def _heading(words: tuple[str, ...]) -> re.Pattern[str]:
    """行頭の見出し。`## 背景`・`**背景**`・`【背景】`・`背景:` が当たり、`背景を説明すると…` は外れる。"""
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(
        r"^[ \t#>*\-・【\[]*(?:" + alternatives + r")[*】\]]*(?:[:：]|[ \t]*$)", re.MULTILINE
    )


_PATTERNS = {name: _heading(words) for name, words in SECTIONS.items()}


def _tail_lines(path: str) -> list[str]:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - _TAIL_BYTES)
        handle.seek(start)
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    # 途中から読んだときの先頭の行は切れているので捨てる。
    return lines[1:] if start else lines


def step_text(path: str) -> str:
    """最後のユーザの行より後ろの、アシスタントの text を連ねたもの。

    ユーザの行は、ユーザのプロンプトとツールの結果の両方。ツールを呼ぶたびに区切るので、
    確認ごとにその直前の発言を見る。サイドチェーン（サブエージェント）とメタの行は読み飛ばす。
    """
    found: list[str] = []
    for line in reversed(_tail_lines(path)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("isSidechain") or row.get("isMeta"):
            continue
        kind = row.get("type")
        if kind == "user":
            break
        if kind != "assistant":
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    found.append(text)
    return "\n".join(found)


def absent_sections(text: str) -> list[str]:
    """text に見出しが無い節の名前。"""
    return [name for name in SECTIONS if not _PATTERNS[name].search(text)]


def wait_until(deadline: float) -> float:
    """読み直しを続ける限りの時刻（`time.monotonic`）。hook の判定の期限より手前に収める。"""
    return min(time.monotonic() + tunables.ASK_CONTEXT_WAIT.value(), deadline - _MARGIN_SECONDS)


def missing(path: str, until: float) -> list[str]:
    """直前の発言に無い節。揃うか `until` を過ぎるまで読み直す。読めないときは空（検査しない）。"""
    if not path:
        return []
    while True:
        try:
            absent = absent_sections(step_text(path))
        except OSError:
            return []
        if not absent or time.monotonic() >= until:
            return absent
        time.sleep(_POLL_SECONDS)


def stop_text(root: str, path: str, deadline: float) -> str:
    """止める文。直前の発言に節が揃っていれば空。"""
    absent = missing(path, wait_until(deadline))
    if not absent:
        return ""
    return texts.say(root, MESSAGE_ID, {"missing": "・".join(f"「{name}」" for name in absent)})
