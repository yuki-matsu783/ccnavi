"""テストの道具（GitLab の模擬・手動の GitLab）の診断ログ。

`logs/diag/<出どころ>.log` に 1 行ずつ足す。

行の形は実行ファイル・sh・拡張の logger と同じ（.agents/docs/logging.md）。秘密を伏せる処理は
持たないので、トークンなどの値を渡さない。書けないときは何も出さずに捨てる。

標準ライブラリだけを読む。`tests` パッケージ（PATH を変える）を読み込まない道具も、
ファイルのパスから読み込んで使う。
"""

from __future__ import annotations

import datetime
import os

LEVEL_ENV = "CCNAVI_LOG_LEVEL"
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
_NEEDS_QUOTE = (" ", "\t", '"', "=", "\n", "\r")


def _fold(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _quote(value: str) -> str:
    if not any(ch in value for ch in _NEEDS_QUOTE):
        return value
    return '"' + _fold(value.replace("\\", "\\\\").replace('"', '\\"')) + '"'


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Logger:
    """1 つの出どころの書き手。`root` が空なら CLAUDE_PROJECT_DIR、それも無ければ書かない。"""

    def __init__(self, name: str, root: str | None) -> None:
        self.name = name
        self.root = root
        self.level = _LEVELS.get(os.environ.get(LEVEL_ENV, "").upper(), _LEVELS["INFO"])

    def debug(self, msg: str, **fields: object) -> None:
        self._emit("DEBUG", msg, fields)

    def info(self, msg: str, **fields: object) -> None:
        self._emit("INFO", msg, fields)

    def warn(self, msg: str, **fields: object) -> None:
        self._emit("WARN", msg, fields)

    def error(self, msg: str, **fields: object) -> None:
        self._emit("ERROR", msg, fields)

    def _emit(self, level: str, msg: str, fields: dict[str, object]) -> None:
        if _LEVELS[level] < self.level:
            return
        root = self.root if self.root is not None else os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not root:
            return
        when = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        tail = "".join(f" {key}={_quote(_text(value))}" for key, value in fields.items())
        line = f"{when} {level:<5} {self.name}[{os.getpid()}] {_fold(msg)}{tail}\n"
        directory = os.path.join(root, "logs", "diag")
        try:
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, f"{self.name}.log"), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            return


def get(name: str, root: str | None = None) -> Logger:
    return Logger(name, root)
