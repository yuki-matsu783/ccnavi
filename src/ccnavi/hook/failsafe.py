"""判定に達せなかったとき（内部の例外・設定が読めない・内部の期限切れ）に、通してよい呼び出しか。

設定もルールも読まずに答える。読めないことが理由で呼ばれるので、ここが設定に頼ると同じ理由で
落ちる。見るのは payload のツール名と対象の文字列だけ。

- Read・Grep・Glob は、組み込みの最低限の deny（認証情報の形、`.git` の中、操作記録の置き場）に
  ヒットしなければ通す。通常の判定と同じく、cwd から解いた場所と渡された表記の両方を見る
  （ワークスペースのルートより上の祖先の名前は見ない）。Glob は pattern も探す先として見る。
  探す場所を省いた Grep・Glob は cwd を見る。この評価もできなければ通さない
- Bash・PowerShell は、読み切れて、連結・置換・リダイレクト・glob を含まない単独のコマンドで、
  下の許可リストにヒットし、引数のパス（引数の無い `ls` は cwd）が最低限の deny にヒットしない
  ときだけ通す
- 報告用のツール（`toolnames.REPORT_TOOLS`）は通す（体が報告を返せなくなるのを避ける）
- それ以外のツールは通さない（呼び出し側が `modes.fail_closed` で扱いを決める）

`phase_forms._READERS` は流用しない。あれは区切りで分けた各コマンドの先頭の語しか見ず、
`rg --pre` や `less` の `!` のようにコマンドを走らせられる読み取りの道具を数えている。
ここは「判定できない間に通してよいもの」なので、同じ表を共有すると一方の緩みがもう一方の判定にそのまま及ぶ。
"""

from __future__ import annotations

import os
import re

from ..infra import fsio, hookio, pathcase, shellread, toolnames

# 認証情報の形、`.git` の中、操作記録の置き場。通常の判定の `builtin:credentials` が止める形のうち、
# 設定を読まずに言える分をコピーする。
# failsafe 独自に、通常より広く止めるものがある: `.gnupg`、`id_ecdsa`、`.p12` / `.pfx`、
# `.git` の中と操作記録の置き場の読み取り、先頭が区切りでない `credentials/`。
# 片方だけ直る事故を避けるため、`builtin:credentials` に形を足したらここも見直す。
# 承認済みの置き場のうち秘密にヒットするものは無いので、ここには入れない。
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
# 探す先のパスと glob を見れば足りる。Glob の pattern は探す先そのもの（`/home/u/.ssh/*`）なので、
# Glob だけ見る（`_paths`）。
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
    names = _READ_FIELDS + (("pattern",) if payload.tool_name == "Glob" else ())
    return [v for name in names if (v := payload.field_value(name))]


def _below_root(form: str, root: str) -> str:
    """比べる形にして、ワークスペースのルートより上の祖先を除く。

    ルートの置き場が `secrets/` や `.aws/` の下にあっても、中の読み取りまで止めないため。
    ルートの中の名前（`.git` など）は残る。ルートの外のパスは、そのまま比べる。
    """
    folded = pathcase.fold(form).replace("\\", "/")
    if root:
        for spelled in {root, fsio.full_path(root, root)}:
            head = pathcase.fold(spelled).replace("\\", "/").rstrip("/")
            if head and (folded == head or folded.startswith(head + "/")):
                return folded[len(head) :]
    return folded


def _denied(path: str, cwd: str, root: str = "") -> bool:
    """このパスが最低限の deny にヒットするか。渡された表記と、解いた先の両方を見る。

    通常の判定は `fsio.full_path` で場所を解いてからルールに当てる。相対パス・`..`・symlink を
    挟んだ読み取りが、この関数だけ素通りしないようにする。解いた先で名前が変わる symlink
    （`.ssh` という名前の symlink が別の場所を指す場合など）もあるので、渡された表記も見る。
    解けなければ例外になり、呼び側が通さない。
    """
    return any(
        _MINIMAL_DENY.search(_below_root(form, root)) for form in (path, fsio.full_path(path, cwd))
    )


def read_is_safe(payload: hookio.Input, root: str = "") -> bool:
    """Read・Grep・Glob を、最低限の deny に適用して通してよいか。評価できなければ偽。"""
    try:
        found = _paths(payload)
        # 探す場所を省いた Grep・Glob は cwd を探す。`glob` だけ書いても探す場所は省かれたまま。
        # cwd が操作記録の置き場や `.git` の中でない限り、省略は通す。
        if payload.tool_name in toolnames.SEARCH_TOOLS:
            omitted = not payload.field_value("path")
        else:
            omitted = not found
        if omitted:
            found.append(payload.cwd or os.getcwd())
        return not any(_denied(p, payload.cwd, root) for p in found)
    except Exception:  # noqa: BLE001 評価できなければ通さない
        return False


def shell_is_safe(payload: hookio.Input, root: str = "") -> bool:
    """Bash・PowerShell を、判定できないとき専用の許可リストに適用して通してよいか。"""
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
        if _MINIMAL_DENY.search(command):
            return False
        # 引数のパスは cwd から解いて見る。引数の無い `ls` は cwd を一覧する。
        words = command.split()
        target = words[1] if len(words) > 1 else "." if words[0] == "ls" else ""
        return not (target and _denied(target, payload.cwd, root))
    except Exception:  # noqa: BLE001 読めなければ通さない
        return False


def is_safe(payload: hookio.Input, root: str = "") -> bool:
    """判定に達せなかった呼び出しのうち、通してよいもの。"""
    if payload.tool_name in toolnames.REPORT_TOOLS:
        return True
    if payload.tool_name in toolnames.READ_TOOLS:
        return read_is_safe(payload, root)
    if payload.tool_name in toolnames.SHELL_TOOLS:
        return shell_is_safe(payload, root)
    return False
