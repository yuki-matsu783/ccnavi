"""ユーザへの確認（ask）の前に、直前のエージェントの発言が背景と結果を言っているかを調べる。

確認の画面に出るのはコマンドや質問だけで、なぜその操作が要るのか、許可と拒否で何が起きるのかは
出ない。ユーザが判断できるように、確認の前の発言で言わせる。対象は、ccnavi が確認に出す呼び出し
（ルールの `ask`・チケットの `ask`・ターン分離の関門の確認・どのルールも言及しない呼び出しの確認。
`judge.refuse` が返す確認すべて）と `AskUserQuestion`。`ExitPlanMode` と、Claude Code 自身の権限の確認
（ccnavi が返さないもの）は対象外。

- 見るのは会話記録（`transcript_path`）の末尾。最後のユーザの発言（ツールの結果ではない行）より
  後ろの、サイドチェーンでないアシスタントの text を集め、2 つの節の見出しがあるかを調べる。
  見出しを書いたあとに別のツールを呼んでも、同じ発言の中なら数える
- PreToolUse の時点では、直前の発言がまだ書き込まれていないことがある（`claude -p` の実測では
  3 回中 2 回が未書き込みで、0.5 秒以内に現れた）。書き込み済みかは、この呼び出し自身の行
  （`tool_use_id` を持つ tool_use）が記録にあるかで分かる。自分の行があり、見出しが無ければ、
  待たずに止める。自分の行がまだ無ければ、見出しが現れるまで、最大 2.5 秒（`CCNAVI_ASK_CONTEXT_WAIT_SECONDS`）、
  0.1 秒ごとに読み直す
- 判定できないときは通す。記録が読めない・パスや `tool_use_id` が無い、待っても自分の行も見出しも
  現れない、のどれか。促しの検査で、保護ではない。止めすぎるより通すほうを選ぶ
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

# 直前の発言に要る節。キーは文面（deny.yml）が言う名前で、値はその節として数える見出しの言い方。
SECTIONS = {
    "前提・背景": ("前提・背景", "背景", "前提"),
    "想定結果": ("想定結果", "選んだ時の結果"),
}

# 会話記録の末尾から読む長さ。現在のステップの発言はこの中に収まる。
_TAIL_BYTES = 512 * 1024
_POLL_SECONDS = 0.1


def _heading(words: tuple[str, ...]) -> re.Pattern[str]:
    """行頭の見出し。`## 背景`・`### 1. 背景`・`**背景**`・`【背景】`・`背景:` が当たり、`背景を説明すると…` は外れる。"""
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(
        r"^[ \t　#>*\-・【\[]*(?:\d+[.)．][ \t　]*)?(?:"
        + alternatives
        + r")[*】\]]*(?:[:：]|[ \t\r　]*$)",
        re.MULTILINE,
    )


_PATTERNS = {name: _heading(words) for name, words in SECTIONS.items()}


def _tail_lines(path: str) -> list[str]:
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - _TAIL_BYTES)
        handle.seek(start)
        data = handle.read()
    # 行の区切りは改行だけ。`splitlines` は U+2028 などでも割り、JSON の行を壊す。
    lines = data.decode("utf-8", errors="replace").split("\n")
    # 途中から読んだときの先頭の行は切れているので捨てる。
    return lines[1:] if start else lines


def _is_tool_result(row: dict) -> bool:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def step_text(path: str, tool_use_id: str) -> tuple[str, bool]:
    """最後のユーザの発言より後ろの、アシスタントの text を連ねたものと、書き込み済みかどうか。

    書き込み済みは、この呼び出し自身の tool_use の行（`tool_use_id`）が記録にあること。text の行は
    その行より前に書かれるので、自分の行があれば、直前の発言は読み切れている。

    区切るのはユーザの発言（プロンプト）だけ。ツールの結果の行は区切らない。見出しを書いた発言の
    あとに別のツールを呼んで確認を出しても、同じ発言の中の見出しを数えるため。
    サイドチェーン（サブエージェント）とメタの行は読み飛ばす。
    """
    found: list[str] = []
    written = False
    for line in reversed(_tail_lines(path)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("isSidechain") or row.get("isMeta"):
            continue
        kind = row.get("type")
        if kind == "user":
            if _is_tool_result(row):
                continue
            break
        if kind != "assistant":
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                written = True
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                found.append(block["text"])
    return "\n".join(found), written


def absent_sections(text: str) -> list[str]:
    """text に見出しが無い節の名前。"""
    return [name for name in SECTIONS if not _PATTERNS[name].search(text)]


def wait_until() -> float:
    """読み直しを続ける限りの時刻（`time.monotonic`）。

    hook の判定の期限（`CCNAVI_HOOK_DEADLINE_SECONDS`）には縛らない。期限はルール照合を打ち切る
    ためのもので、この待ちはその前後の短い読み取りだけだから。上限は hook の timeout（settings.json
    の `timeout`、既定 10 秒）より短くする。
    """
    return time.monotonic() + tunables.ASK_CONTEXT_WAIT.value()


def missing(path: str, tool_use_id: str, until: float) -> list[str]:
    """直前の発言に無い節。判定できないときは空（検査しない）。

    自分の行があって見出しが無ければ、すぐ返す。自分の行がまだ無いあいだは `until` まで読み直し、
    過ぎても自分の行も見出しも現れなければ、判定できないので通す。
    """
    if not path or not tool_use_id:
        return []
    while True:
        try:
            text, written = step_text(path, tool_use_id)
        except OSError:
            return []
        absent = absent_sections(text)
        if not absent:
            return []
        if written:
            return absent
        if time.monotonic() >= until:
            return []
        time.sleep(_POLL_SECONDS)


def stop_text(root: str, path: str, tool_use_id: str) -> str:
    """止める文。直前の発言に節が揃っているか、判定できなければ空。"""
    absent = missing(path, tool_use_id, wait_until())
    if not absent:
        return ""
    return texts.say(root, CODE, {"missing": "・".join(f"「{name}」" for name in absent)})
