"""判定に達せなかったとき（内部の例外・設定が読めない・内部の期限切れ）に、通してよい呼び出しか。

設定もルールも読まずに答える。読めないことが理由で呼ばれるので、ここが設定に頼ると同じ理由で
落ちる。見るのは payload のツール名と対象の文字列だけ。

- Read・Grep・Glob は、組み込みの最低限の deny（認証情報の形、`.git` の中、操作記録の置き場）に
  当たらなければ通す。この評価もできなければ通さない
- Bash・PowerShell は、読み切れて、連結・置換・リダイレクト・glob を含まない単独のコマンドで、
  下の許可リストに当たるときだけ通す
- 報告用のツール（`modes.REPORT_TOOLS`）は通す（体が報告を返せなくなるのを避ける）
- それ以外のツールは通さない（呼び出し側が `modes.fail_closed` で扱いを決める）

`phase_forms._READERS` は流用しない。あれは区切りで分けた各コマンドの先頭の語しか見ず、
`rg --pre` や `less` の `!` のようにコマンドを走らせられる読み取りの道具を数えている。
ここは「判定できない間に通してよいもの」なので、同じ表を共有すると一方の緩みがもう一方の判定にそのまま及ぶ。
"""

from __future__ import annotations

import os
import re

from ..infra import hookio, modes, pathcase, shellread

READ_TOOLS = ("Read", "Grep", "Glob")
SHELL_TOOLS = ("Bash", "PowerShell")

# 認証情報の形、`.git` の中、操作記録の置き場。通常の判定の `builtin:credentials` が止める形のうち、
# 設定を読まずに言える分を写す。
# failsafe 独自に、通常より広く止めるものがある: `.gnupg`、`id_ecdsa`、`.p12` / `.pfx`、
# `.git` の中と操作記録の置き場の読み取り、先頭が区切りでない `credentials/`。
# 片方だけ直る事故を避けるため、`builtin:credentials` に形を足したらここも見直す。
# 承認済みの置き場のうち秘密に当たるものは無いので、ここには入れない。
_BOUND = r"(?:^|[ \\/\x00=])"
_MINIMAL_DENY = re.compile(
    rf"{_BOUND}\.(env|netrc|npmrc|pypirc|dev\.vars)"
    rf"|{_BOUND}\.(ssh|aws|gnupg)(?:[\\/]|$)"
    r"|\b(id_rsa|id_ed25519|id_ecdsa)\b"
    rf"|{_BOUND}credentials(?:[\\/]|\.json)"
    rf"|{_BOUND}secrets?[\\/]"
    r"|[\w\-\\/]\.(pem|key)\b"
    r"|\.(p12|pfx)(?:\s|$)"
    rf"|{_BOUND}\.git(?:[\\/]|$)"
    rf"|{_BOUND}logs[\\/]state[\\/]ops(?:[\\/]|$)",
    re.IGNORECASE,
)

# 読み取りのツールが対象にする欄。Grep は探す文字列（pattern）にも認証情報の名前が書けるが、
# 探す先のパスと glob を見れば足りる。
_READ_FIELDS = ("file_path", "path", "glob", "notebook_path")

# 判定できないときに通すコマンド。単独で、オプションは取らず、引数は 1 つのパスだけ
# （`pwd` と `ls` は無くてもよい）。パスは `-` で始めない（`wc --files0-from=.env` のように
# オプションの値で別のファイルを読ませない）。出力や入力が止まらない `/dev/`・`/proc/` も除く。
_PATH = r"(?!/dev/|/proc/)[^-\s\x00;&|<>()$`*?\[\]{}!\"'\\][^\s\x00;&|<>()$`*?\[\]{}!\"'\\]*"
_SINGLE = re.compile(
    rf"^(?:pwd|ls(?:\s+{_PATH})?|(?:cat|head|tail|wc)\s+{_PATH})$",
)
# 状態を見る入口。`sh <ルート>/.ccnavi/scripts/ccnavi-ticket.sh status|--help` だけ。
# 実行するスクリプトは、このワークスペースのものに固定する（置かれた別のスクリプトを走らせない）。
_STATUS = re.compile(
    r"^(?:sh|bash)\s+(?P<script>\S+)\s+(?:status|--help)$",
    re.IGNORECASE,
)
_RELATIVE = (".ccnavi/scripts/ccnavi-ticket.sh", "./.ccnavi/scripts/ccnavi-ticket.sh")


def _is_workspace_status(command: str, cwd: str, root: str) -> bool:
    """`sh <このワークスペースの ccnavi-ticket.sh> status|--help` か。"""
    found = _STATUS.match(command)
    if not found or not root:
        return False
    script = found.group("script")
    if ".." in script.replace("\\", "/").split("/"):
        return False
    want = pathcase.fold(os.path.join(root, ".ccnavi", "scripts", "ccnavi-ticket.sh"))
    if script in _RELATIVE:
        return pathcase.fold(cwd) == pathcase.fold(root) if cwd else False
    return pathcase.fold(script) == want


def _paths(payload: hookio.Input) -> list[str]:
    return [v for name in _READ_FIELDS if (v := payload.field_value(name))]


def read_is_safe(payload: hookio.Input) -> bool:
    """Read・Grep・Glob を、最低限の deny に当てて通してよいか。評価できなければ偽。"""
    try:
        found = _paths(payload)
        # 探す場所を省いた Grep・Glob は cwd を探す。cwd が操作記録の置き場の中でない限り、
        # 省略は通す（置き場の中に居る場合は cwd の文字列で当たる）。
        if not found and payload.cwd:
            found.append(payload.cwd)
        return not any(_MINIMAL_DENY.search(pathcase.fold(p).replace("\\", "/")) for p in found)
    except Exception:  # noqa: BLE001 評価できなければ通さない
        return False


def shell_is_safe(payload: hookio.Input, root: str = "") -> bool:
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
        if _is_workspace_status(command, payload.cwd, root):
            return not _MINIMAL_DENY.search(command)
        if not _SINGLE.match(command):
            return False
        return not _MINIMAL_DENY.search(command)
    except Exception:  # noqa: BLE001 読めなければ通さない
        return False


def is_safe(payload: hookio.Input, root: str = "") -> bool:
    """判定に達せなかった呼び出しのうち、通してよいもの。"""
    if payload.tool_name in modes.REPORT_TOOLS:
        return True
    if payload.tool_name in READ_TOOLS:
        return read_is_safe(payload)
    if payload.tool_name in SHELL_TOOLS:
        return shell_is_safe(payload, root)
    return False
