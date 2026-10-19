"""判定に達せなかったとき（内部の例外・設定が読めない・内部の期限切れ）に、通してよい呼び出しか。

設定もルールも読まずに答える。読めないことが理由で呼ばれるので、ここが設定に頼ると同じ理由で
落ちる。見るのは payload のツール名と対象の文字列だけ。

- Read・Grep・Glob は、組み込みの最低限の deny（認証情報の形、`.git` の中、操作記録の置き場）に
  当たらなければ通す。この評価もできなければ通さない
- Bash・PowerShell は、読み切れて、連結・置換・リダイレクト・glob を含まない単独のコマンドで、
  下の許可リストに当たるときだけ通す
- それ以外のツールは通さない（呼び出し側が `modes.fail_closed` で扱いを決める）

`phase_forms._READERS` は流用しない。あれは区切りで分けた各コマンドの先頭の語しか見ず、
`rg --pre` や `less` の `!` のようにコマンドを走らせられる読み取りの道具を数えている。
ここは「判定できない間に通してよいもの」なので、同じ表を共有すると片方の緩みがもう片方に移る。
"""

from __future__ import annotations

import re

from ..infra import hookio, pathcase, shellread

READ_TOOLS = ("Read", "Grep", "Glob")
SHELL_TOOLS = ("Bash", "PowerShell")

# 認証情報の形（組み込みの `builtin:credentials` と同じ形）、`.git` の中、操作記録の置き場。
# 承認済みの置き場のうち秘密に当たるものは無いので、ここには入れない（読むだけなら中身は
# チケットと記録で、認証情報ではない）。
_MINIMAL_DENY = re.compile(
    r"(^|[ \\/\x00])\.(env|netrc|npmrc)"
    r"|(^|[ \\/\x00])\.ssh[\\/]"
    r"|\b(id_rsa|id_ed25519)\b"
    r"|(^|[\\/])\.git([\\/]|$)"
    r"|(^|[\\/])logs[\\/]state[\\/]ops([\\/]|$)",
    re.IGNORECASE,
)

# 読み取りのツールが対象にする欄。Grep は探す文字列（pattern）にも認証情報の名前が書けるが、
# 探す先のパスと glob を見れば足りる。
_READ_FIELDS = ("file_path", "path", "glob", "notebook_path")

# 判定できないときに通すコマンド。単独で、引数は 1 つのパスだけ（`pwd` と `ls` は無くてもよい）。
_PATH = r"[^\s\x00;&|<>()$`*?\[\]{}!\"'\\]+"
_SINGLE = re.compile(
    rf"^(?:pwd|ls(?:\s+{_PATH})?|(?:cat|head|tail|wc)\s+{_PATH})$",
)
# 状態を見る入口。`sh <ルート>/.ccnavi/scripts/ccnavi-ticket.sh status|--help` だけ。
_STATUS = re.compile(rf"^(?:sh|bash)\s+{_PATH}ccnavi-ticket\.sh\s+(?:status|--help)$")


def _paths(payload: hookio.Input) -> list[str]:
    return [v for name in _READ_FIELDS if (v := payload.field_value(name))]


def read_is_safe(payload: hookio.Input) -> bool:
    """Read・Grep・Glob を、最低限の deny に当てて通してよいか。評価できなければ偽。"""
    try:
        found = _paths(payload)
        # 探す場所を省いた Grep・Glob は cwd を探す。cwd が操作記録の置き場の中でない限り、
        # 省略は通す（置き場の中に居る場合は cwd の文字列で当たる）。
        if payload.cwd:
            found.append(payload.cwd) if not found else None
        return not any(_MINIMAL_DENY.search(pathcase.fold(p).replace("\\", "/")) for p in found)
    except Exception:  # noqa: BLE001 評価できなければ通さない
        return False


def shell_is_safe(payload: hookio.Input) -> bool:
    """Bash・PowerShell を、判定できないとき専用の許可リストに当てて通してよいか。"""
    if payload.tool_name != "Bash":
        # PowerShell は shellread で読めない。
        return False
    try:
        command = payload.field_value("command").strip()
        if not command or "\n" in command or "\r" in command:
            return False
        reading = shellread.read(command)
        # 最後まで読めたこと。引用・置換・ヒアドキュメントなどで読み切れなければ通さない。
        if reading.degraded or reading.rewrites:
            return False
        parts = [c.strip() for c in reading.text.split(shellread.SEP) if c.strip()]
        if len(parts) != 1 or parts[0] != command:
            return False
        if _STATUS.match(command):
            return not _MINIMAL_DENY.search(command)
        if not _SINGLE.match(command):
            return False
        return not _MINIMAL_DENY.search(command)
    except Exception:  # noqa: BLE001 読めなければ通さない
        return False


def is_safe(payload: hookio.Input) -> bool:
    """判定に達せなかった呼び出しのうち、通してよいもの。"""
    if payload.tool_name in READ_TOOLS:
        return read_is_safe(payload)
    if payload.tool_name in SHELL_TOOLS:
        return shell_is_safe(payload)
    return False
