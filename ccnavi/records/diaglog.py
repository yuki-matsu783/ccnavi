"""診断ログ。`logs/diag/<出どころ>.log` に 1 行ずつ足す（docs/claude/logging.md）。

sh（`.ccnavi/scripts/ccnavi-common.sh` の log_*）と拡張（`src/log.ts`）と同じ形の行を出す。

    2026-09-27T10:15:03+09:00 DEBUG ccnavi[4242] hook を判定した event=PreToolUse decision=deny

標準の logging は使わない。ハンドラの組み立てと import の重さを hook のホットパスに
持ち込まないためと、3 つの言語で行の形を 1 字まで揃えるため。

**標準出力にも標準エラーにも何も出さない。** 書けない（置き場が作れない・権限・容量）ときは
何も出さずに捨て、例外を外へ出さない。ログの成否で本体の判定・出力・終了コードは変わらない。
ユーザやモデルに見せる文面（`ccnavi: …` の標準エラー、hook の JSON）は契約として決まっている出力で、
診断ログとは分けてある。そちらはこのモジュールと関係なく今のまま書く。

本文と値は書く前に、まず mask_userinfo（URL と scp 形式の資格情報を `***` に伏せる。sh と拡張と
同じ規則）を、次に redact を通す。秘密の形を伏せる最後の安全網で、秘密の値をそもそも
渡さないのが先（規約）。

**シンボリックリンクはたどらない。** `logs`・`logs/diag`・書き込み先のファイルのどれかが
シンボリックリンクなら書かずに捨てる（書き込み先は O_NOFOLLOW で開く。無い OS では lstat で
見る）。ファイルは 0600 で作る。出どころの名前が `[A-Za-z0-9_-]` 以外を含むときも書かない。
"""

from __future__ import annotations

import datetime
import os
import re

from . import redact

LEVEL_ENV = "CCNAVI_LOG_LEVEL"
DIAG_DIR = os.path.join("logs", "diag")

DEBUG = 10
INFO = 20
WARN = 30
ERROR = 40
_NAMES = {DEBUG: "DEBUG", INFO: "INFO ", WARN: "WARN ", ERROR: "ERROR"}
_BY_WORD = {"DEBUG": DEBUG, "INFO": INFO, "WARN": WARN, "ERROR": ERROR}

# 出どころに使える名前。これ以外の字（区切り・`.`・空白）を含む名前では書かない。
_NAME = re.compile(r"[A-Za-z0-9_-]+")
# 資格情報を伏せるときに語を切る文字（sh の ccnavi_log_space と同じ）。
_SPACE = re.compile(r"([ \t\n\r]+)")
MASK = "***"

# 値を `"` で囲む字。
_NEEDS_QUOTE = (" ", "\t", '"', "=", "\n", "\r")


def threshold() -> int:
    """CCNAVI_LOG_LEVEL から閾値を読む。空と読めない値は INFO。"""
    return _BY_WORD.get(os.environ.get(LEVEL_ENV, "").upper(), INFO)


class Logger:
    """1 つの出どころの書き手。get で作る。"""

    def __init__(self, name: str, root: str | None) -> None:
        self.name = name
        self.root = root
        self.level = threshold()

    def enabled(self, level: int) -> bool:
        return level >= self.level

    def debug(self, msg: str, **fields: object) -> None:
        if self.level <= DEBUG:
            self._emit(DEBUG, msg, fields)

    def info(self, msg: str, **fields: object) -> None:
        if self.level <= INFO:
            self._emit(INFO, msg, fields)

    def warn(self, msg: str, **fields: object) -> None:
        if self.level <= WARN:
            self._emit(WARN, msg, fields)

    def error(self, msg: str, **fields: object) -> None:
        if self.level <= ERROR:
            self._emit(ERROR, msg, fields)

    def _emit(self, level: int, msg: str, fields: dict[str, object]) -> None:
        try:
            root = self.root if self.root is not None else os.environ.get("CLAUDE_PROJECT_DIR", "")
            if not root or not _NAME.fullmatch(self.name):
                return
            safe = {key: scrub(text(value)) for key, value in fields.items()}
            line = format_line(stamp(), level, self.name, os.getpid(), scrub(msg), safe)
            append(os.path.join(root, DIAG_DIR, f"{self.name}.log"), line)
        except Exception:  # noqa: BLE001 ログの失敗で本体を止めない
            return


def get(name: str, root: str | None = None) -> Logger:
    """出どころ `name` の書き手。`root` はワークスペースルートで、省くと CLAUDE_PROJECT_DIR。

    どちらも無ければ何も書かない（置き場を当て推量で決めない）。
    """
    return Logger(name, root)


def stamp(now: datetime.datetime | None = None) -> str:
    """現地時刻と時差を秒まで。`2026-09-27T10:15:03+09:00`。"""
    moment = now if now is not None else datetime.datetime.now()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="seconds")


def text(value: object) -> str:
    """値を文字にする。真偽は true / false、None は空（拡張の String と揃える）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def scrub(value: str) -> str:
    """書く前に伏せる。mask_userinfo（3 つの言語で共通）のあと redact（Python だけ）。"""
    return redact.redact(mask_userinfo(value))


def mask_userinfo(value: str) -> str:
    """URL と scp 形に埋まった資格情報を `***` にする（sh の ccnavi_log_mask と同じ読み）。

    空白・タブ・LF・CR で切った語ごとに見る。`://` を含む語は、`://` の後ろから次の `/` までを
    authority とし、`@` があれば最後の `@` より前を伏せる。含まない語は、最初の `/` より前に `@` が
    あり、最後の `@` より前に `:` があれば、そこを伏せる（`user:tok@host:path`）。
    """
    if "@" not in value:
        return value
    parts = _SPACE.split(value)
    return "".join(part if i % 2 else _mask_word(part) for i, part in enumerate(parts))


def _mask_word(word: str) -> str:
    if "@" not in word:
        return word
    if "://" not in word:
        head = word.split("/", 1)[0]
        if "@" not in head:
            return word
        user = head.rsplit("@", 1)[0]
        if ":" not in user:
            return word
        return MASK + "@" + word[len(user) + 1 :]
    out = []
    rest = word
    while "://" in rest:
        before, rest = rest.split("://", 1)
        out.append(before + "://")
        authority = rest.split("/", 1)[0]
        if "@" in authority:
            out.append(MASK + "@" + authority.rsplit("@", 1)[1])
            rest = rest[len(authority) :]
    out.append(rest)
    return "".join(out)


def fold(value: str) -> str:
    """改行（CR LF・CR・LF）を `\\n` の 2 字に置き換える。"""
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def quote(value: str) -> str:
    """logfmt の値。空白・タブ・`"`・`=`・改行を含めば囲み、`\\` と `"` の前に `\\` をつける。"""
    if not any(ch in value for ch in _NEEDS_QUOTE):
        return value
    return '"' + fold(value.replace("\\", "\\\\").replace('"', '\\"')) + '"'


def format_line(
    when: str, level: int, name: str, pid: int, msg: str, fields: dict[str, str]
) -> str:
    """1 行（改行を含まない）。値は渡された順に並べる。"""
    tail = "".join(f" {key}={quote(value)}" for key, value in fields.items())
    return f"{when} {_NAMES[level]} {name}[{pid}] {fold(msg)}{tail}"


def append(path: str, line: str) -> None:
    """1 行を O_APPEND で 1 度に書く。並行するプロセスの行と混ざらない。

    `logs`・`logs/diag`・書き込み先のどれかがリンクなら書かない。書き込み先は O_NOFOLLOW で開き
    （リンクなら開けずに OSError）、それが無い OS では lstat で先に見る。新しいファイルは 0600。
    """
    directory = os.path.dirname(path)
    for place in (os.path.dirname(directory), directory):
        if os.path.islink(place):
            return
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow and os.path.islink(path):
        return
    data = (line + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileNotFoundError:
        os.makedirs(directory, exist_ok=True)
        fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
