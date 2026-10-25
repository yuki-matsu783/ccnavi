"""コマンドの語の見分け。コマンド名・実行役のコマンド・オプションの幅・リダイレクト・演算子。

語の並びを見るだけにする。shellread から分けた。
"""

from __future__ import annotations

import re

from . import pathcase

# シェルが演算子として読む文字。shlex の punctuation_chars と同じ文字。
# 演算子はこの文字だけでできたトークンとして返ってくるので、語の中に
# 同じ文字が現れたときと見分けられる。
#
# 引用符だけで書かれた `>` `<` の 1 語（`echo ">"`）は、shlex が引用の有無を返さないため
# 演算子と区別が付かない。走査が引用の中のこれらを身代わりの文字に置き換えて、
# 演算子として読まれないようにしている。引用の中の `(` などは今も演算子として扱う。
_PUNCTUATION = "();<>|&"
# 引用の中の `>` と `<` の身代わり。走査が shlex に渡す前に置き換えるので、shlex は
# 引用された `>` `<` を演算子ではなく普通の語として返す（`grep ">" f`）。
# `<<` だけは置き換えない。`"<<"` は演算子の `<<` と区別が付かず、ヒアドキュメントに
# 見えて縮退させる（ADR 0013）。_join が目印で挟んだ文字に戻すので、ルールが読む形は
# 書かれた文字と変わらない。入力に現れないよう、_prepare が取り除く（SEP と WORD_SEP と同じ扱い）。
QUOTED_GT = "\x02"
QUOTED_LT = "\x03"
# 身代わりの文字から、書かれた文字へ戻す表。
QUOTED_BACK = {QUOTED_GT: ">", QUOTED_LT: "<"}
REASON_TAKEN_AS_CODE = "command-taken-as-code"

# shlex が区切り記号として返すトークン。コマンドとコマンドを分ける。
# 置換の中身はここに届く前に走査が取り出してあるので、括弧が残るのは
# サブシェル `( )` と case の `)` だけ。
_OPERATORS = frozenset({";", ";;", "&", "&&", "|", "||", "|&", "(", ")"})

# 文字列を受け取って実行することが仕事のコマンド。
# 何を実行するかはトークンの中に無いので、ここからは見えない。
_RUNS_A_STRING = frozenset({"eval", "source", ".", "xargs"})

# `xargs` と `find -exec` の後ろに置いてよい、読むだけのコマンド。ここに載っていれば、
# 文字列をコードとして実行するものとは数えない。中身をそのまま出す `cat` `head` `tail` は入れない。
# 引数のファイル名が標準入力から来るので、ファイル名を見るルールは外れる（この点は許容している）。
_READONLY_RUNNEE = frozenset(
    {
        "grep",
        "egrep",
        "fgrep",
        "wc",
        "ls",
        "file",
        "stat",
        "sort",
        "uniq",
        "basename",
        "dirname",
        "echo",
    }
)

# find の述語のうち、書き込みや実行をするもの（GNU find / BSD find）。
_FIND_ACTIONS = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

# `xargs` のオプションで、次の語を値に取るもの。一覧に無いオプションは値を取らないものとして読む。
_XARGS_VALUE_OPTIONS = frozenset(
    {
        "-I",
        "-n",
        "-P",
        "-d",
        "-L",
        "-s",
        "-E",
        "-a",
        "--max-args",
        "--max-procs",
        "--delimiter",
        "--arg-file",
        "--max-chars",
        "--max-lines",
        "--process-slot-var",
    }
)

# コマンドの位置に置かれたときに予約語になる語。予約語の後ろはコマンドの先頭なので、
# `then find . -delete` の find を find として読ませるには、予約語で 1 本切る必要がある。
_RESERVED = frozenset(
    {
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "do",
        "done",
        "while",
        "until",
        "for",
        "case",
        "esac",
        "select",
        "time",
        "coproc",
        "!",
        "{",
        "}",
    }
)

# 後ろの 1 本をコマンドとして読まない予約語。`case $x in` の `$x` は調べる値、`for x in` の
# `x` は変数名で、どちらもコマンドの位置の語ではない。
_TAKES_A_WORD = frozenset({"case", "for", "select"})

# 頼まれたときだけ文字列を実行するコマンド。bash にスクリプトファイルを渡すのは
# 普通の操作で、読める状態を保たないといけないので、フラグが付いた形だけを数える。
#
# 線はシェルで引いてある。インタプリタにもコマンドを実行させられるが、
# 1 行スクリプトは禁止語に触れる文書を直すときの普通の書き方で、
# perl や python をここに入れると、いちばん禁止語に触れる作業を読むのを諦めることになる。
# 「駄目だ」と言われたエージェントが次に向かうのはシェルのほう。
_TAKES_CODE_FLAG = {
    "bash": ("-c",),
    "sh": ("-c",),
    "zsh": ("-c",),
    "ksh": ("-c",),
    "dash": ("-c",),
    "su": ("-c",),
    "find": ("-exec", "-execdir", "-ok", "-okdir"),
}


# ---- 中で実行されるコマンド
#
# `env rm x` の `rm x` のように、別のコマンドを実行することが仕事のコマンド
# （実行役のコマンド）がある。ルールの多くはコマンドの先頭に固定して書かれるので、
# 実行役のコマンドを前に置くだけで外れる。ここでは実行役のコマンドを 1 枚ずつ外して、
# 中で実行されるコマンドを並べる。判定はそれを止める側のルールにだけ適用する（judge）。
#
# 外す先は、走査と shlex で読んだコマンドのそれぞれ。置換の中身から切り出したコマンドも
# 含む（`echo "$(env rm x)"` の `rm x`）。
#
# 一覧は組み込みで持つ。ルールファイルから足せるようにすると、消すこともできる。
# 保護の根拠を守られる側に置かない。一覧に無い実行役のコマンド（`script -c`・`watch`・
# `ssh host cmd`・`python -c` など）は外さない。

# 深さの上限。元の形は数えない。判定の期限に影響するので、層の数をここで抑える。
UNWRAP_DEPTH = 4

# オプションと値を飛ばした残りがコマンドになるもの。値は、値を取るオプション
# （`-u me` のように次の語を値に取るもの）と、コマンドの前に置く位置引数の数。
# 一覧に無いオプションは値を取らないものとして読む。読み違えると値をコマンドと読んで
# 誤った層ができるが、誤った層はヒットしないだけで、元の形の判定は変わらない。
_RUNNERS: dict[str, tuple[frozenset[str], int]] = {
    "env": (frozenset({"-u", "--unset", "-C", "--chdir"}), 0),
    "command": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "nohup": (frozenset(), 0),
    "time": (frozenset({"-f", "-o", "--format", "--output"}), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "sudo": (
        frozenset(
            {
                "-u",
                "-g",
                "-C",
                "-h",
                "-p",
                "-U",
                "-r",
                "-t",
                "-T",
                "-D",
                "-R",
                "--user",
                "--group",
                "--close-from",
                "--host",
                "--prompt",
                "--other-user",
                "--role",
                "--type",
                "--command-timeout",
                "--chdir",
                "--chroot",
            }
        ),
        0,
    ),
    "doas": (frozenset({"-u", "-C"}), 0),
    "timeout": (frozenset({"-s", "--signal", "-k", "--kill-after"}), 1),
    "stdbuf": (frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), 0),
    "chrt": (frozenset(), 1),
    "ionice": (frozenset({"-c", "-n", "-p", "--class", "--classdata", "--pid"}), 0),
    "taskset": (frozenset(), 1),
}

# コマンドの前に `名前=値` を並べてよい実行役のコマンド。
_TAKES_ASSIGNMENTS = frozenset({"env", "sudo"})

# `command -v jq` はコマンドを探すだけ。
_LOOKUP_ONLY = {"command": frozenset("vV")}

# コマンド名を探すときに飛ばす代入。配列の要素（`a[1]=x`）と足し込み（`x+=1`）も代入。
# シェルはこの形の語をコマンドの前に並べられ、残りをコマンドとして実行する。
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")

# `env` と `sudo` が代入として読む引数。どちらも `=` を含む引数を名前の表記を問わず代入に
# 数える（`env a+=1 rm x` は `a+` という変数を置いて rm を実行する）。シェルの代入より広い。
_RUNNER_ASSIGNMENT = re.compile(r"[^=-][^=]*=")

# bash 4.1 以降の名前付き fd。`{fd}>/dev/null cmd` のリダイレクトの前に付く。
_NAMED_FD = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _classify(commands: list[list[str]]) -> str:
    """読みを止めたものがあれば、それが何かを返す。"""
    for command in commands:
        # 文字列を実行することが仕事のコマンドは、先頭の語だけを見る。
        # 引数に現れた同じ表記は実行体ではない。"." はシェルの source だが、
        # 引数としては「このディレクトリ」で、ruff check . のような書き方は
        # いくらでもある。ここを全語で見ると、そういう普通の作業が読めなくなる。
        # パイプや ";" の後ろは別のコマンドになるので、xargs や eval も
        # 先頭の語として現れる。
        # xargs は、実行される語が読むだけのコマンドなら数えない。
        if command and _base(command[0]) in _RUNS_A_STRING and not _xargs_readonly(command):
            return REASON_TAKEN_AS_CODE

        # `-` で始まる語が頭に来るのは、`find -exec … \;` のあとに続く述語か、
        # 引用された `(` `;` で切れた後ろの断片。書き込み・実行の述語を含むものだけ、
        # find の続きを見逃さないよう読めない形に戻す。
        if (
            command
            and command[0].startswith("-")
            and any(token in _FIND_ACTIONS for token in command)
        ):
            return REASON_TAKEN_AS_CODE

        # フラグを付けて初めて文字列を実行するものは、先頭でなくてもよい。
        # sudo -u root sh -c は 4 語目に来る。
        for i, token in enumerate(command):
            flags = _TAKES_CODE_FLAG.get(_base(token))
            # find -exec は、次の語がすべて読むだけのコマンドなら数えない。
            if (
                flags
                and _has_flag(command[i + 1 :], flags)
                and not (_base(token) == "find" and _execs_readonly(command[i + 1 :], flags))
            ):
                return REASON_TAKEN_AS_CODE
    return ""


def _readonly(words: list[str]) -> bool:
    """先頭の語が、読むだけのコマンドの一覧にあるか。"""
    return bool(words) and pathcase.fold(_base(words[0])) in _READONLY_RUNNEE


def _xargs_readonly(command: list[str]) -> bool:
    """`xargs` の、オプションと値を飛ばした最初の語が、読むだけのコマンドか。"""
    return _base(command[0]) == "xargs" and _readonly(
        _skip_options(command[1:], _XARGS_VALUE_OPTIONS)[:1]
    )


def _execs_readonly(tokens: list[str], flags: tuple[str, ...]) -> bool:
    """`-exec` などの次の語が、どれも読むだけのコマンドか。"""
    return all(_readonly(tokens[j + 1 : j + 2]) for j, token in enumerate(tokens) if token in flags)


def _skip_options(args: list[str], value_options: frozenset[str]) -> list[str]:
    """先頭のオプション（と値）を飛ばした残り。`--` はそこで終わる。"""
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            return args[i + 1 :]
        if not token.startswith("-") or token == "-":
            break
        i += _option_width(token, args[i + 1 :], value_options)
    return args[i:]


def _has_flag(tokens: list[str], flags: tuple[str, ...]) -> bool:
    for token in tokens:
        for flag in flags:
            if token == flag:
                return True
            # 短いオプションはまとめて書ける。bash -lc も bash -c。
            if (
                len(flag) == 2
                and len(token) > 1
                and token.startswith("-")
                and not token.startswith("--")
                and flag[1] in token
            ):
                return True
    return False


def _base(name: str) -> str:
    """どのプログラムを指すかを変えない部分を除く。
    git について書いたルールが /usr/bin/git と git.exe にもヒットするように。"""
    for separator in ("/", "\\"):
        name = name.rsplit(separator, 1)[-1]
    lowered = name.lower()
    for extension in (".exe", ".cmd", ".bat"):
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def _name_index(command: list[str]) -> int:
    """コマンドの位置の語が何番目か。無ければ語の数。"""
    i = 0
    while i < len(command):
        if _ASSIGNMENT_WORD.match(command[i]):
            i += 1
            continue
        if not _redirect_width(command, i):
            return i
        i += _redirect_width(command, i)
    return len(command)


def _redirect_width(command: list[str], i: int) -> int:
    """i から始まるリダイレクトが占める語の数。リダイレクトでなければ 0。

    shlex は `>/dev/null` を `>` `/dev/null` に、`2>&1` を `2` `>&` `1` に割る。区切りの演算子は
    _split_commands が切り出してあるので、コマンドの中に残る演算子の塊はリダイレクト。
    演算子の前には、fd の番号か、bash 4.1 以降の名前付き fd（`{fd}>/dev/null`）が付く。
    演算子の後ろに行き先の語が無いもの（`echo x >` のようにコマンドの最後の語が演算子）は、
    リダイレクトとして数えない。bash では構文エラーで何も書かれないので、ふつうの語として残す。
    """
    fd = command[i].isdigit() or _NAMED_FD.fullmatch(command[i])
    k = i + 1 if fd and i + 1 < len(command) else i
    if k + 1 >= len(command):
        return 0
    return k - i + 2 if _is_operator(command[k]) else 0


def _runner_command(name: str, args: list[str]) -> list[list[str]]:
    """`env` `sudo` などのオプションと値と位置引数を飛ばした残り。"""
    value_options, positionals = _RUNNERS[name]
    lookup = _LOOKUP_ONLY.get(name, frozenset())
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            i += 1
            break
        if name in _TAKES_ASSIGNMENTS and _RUNNER_ASSIGNMENT.match(token):
            i += 1
            continue
        if not token.startswith("-") or token == "-":
            break
        if lookup and not token.startswith("--") and lookup & set(token[1:]):
            return []
        i += _option_width(token, args[i + 1 :], value_options)
    while i < len(args) and name in _TAKES_ASSIGNMENTS and _RUNNER_ASSIGNMENT.match(args[i]):
        i += 1
    i += positionals
    rest = args[i:]
    return [rest] if rest else []


def _option_width(token: str, following: list[str], value_options: frozenset[str]) -> int:
    """オプション 1 つが占める語の数。値を別の語に取るなら 2。

    `--unset=HOME` と `-o0` は値が同じ語に入っている。短いオプションはまとめて
    書けるので（`-Eu me`）、値を取る文字が最後に来たときだけ次の語を値に取る。
    """
    if token.startswith("--"):
        if "=" in token:
            return 1
        return 2 if token in value_options and following else 1
    for k, letter in enumerate(token[1:], start=1):
        if "-" + letter in value_options:
            last = k == len(token) - 1
            return 2 if last and following else 1
    return 1


def _is_operator(token: str) -> bool:
    """演算子のトークンかどうか。punctuation_chars が返す塊は
    この文字だけでできている。"""
    return bool(token) and all(c in _PUNCTUATION for c in token)


# 標準入力からスクリプトを読むシェルの名前と、標準入力を指すスクリプトのパス。
_STDIN_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash"})
_STDIN_SCRIPTS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
# 標準入力から読むシェルの前に置ける語（`… | env sh`・`… | command bash`）。
_STDIN_HEADS = frozenset({"busybox", "env", "command", "exec"})
# その前置きのオプションで、次の語を値に取るもの。
_STDIN_HEAD_VALUES = frozenset({"-u", "--unset", "-C", "--chdir", "-a"})
# シェルの長いオプションで、次の語を値に取るもの。
_STDIN_SHELL_LONG_VALUES = frozenset({"--rcfile", "--init-file"})


def _stdin_base(word: str) -> str:
    return word.replace("\\", "/").rsplit("/", 1)[-1].lower()


def reads_stdin(words: list[str]) -> bool:
    """標準入力からスクリプトを読むシェルか。

    `sh`・`sh -s …`・`sh < x`・`busybox sh`・`sh /dev/stdin`。前に置いた `env`・`command`・
    `exec`（と代入・それらのオプション）は外して見る。シェルのオプションは、`--norc`
    のような長いオプション、
    値を取るオプション（`--rcfile <値>`・`-o pipefail`・`-O extglob`・`-euo pipefail`）
    を読み飛ばす。
    `-c` は文字列を実行するので読まない。

    ユーザの入口の止め（phase_forms）と、ラッパーの自己防衛（wrapguard）が使う。
    """
    rest = _stdin_heads(list(words))
    if not rest or _stdin_base(rest[0]) not in _STDIN_SHELLS:
        return False
    rest = rest[1:]
    k = 0
    while k < len(rest):
        width = _redirect_width(rest, k)
        if width:
            k += width
            continue
        word = rest[k]
        if word in _STDIN_SCRIPTS:
            return True
        if word == "--":
            return k + 1 == len(rest) or rest[k + 1] in _STDIN_SCRIPTS
        if word.startswith("--"):
            k += 2 if word in _STDIN_SHELL_LONG_VALUES else 1
            continue
        if not word.startswith(("-", "+")):
            return False
        if "c" in word[1:]:
            return False
        if "s" in word[1:]:
            return True
        # `-o pipefail`・`-O extglob`・`-euo pipefail` は次の語が値。
        k += 2 if ("o" in word[1:] or "O" in word[1:]) else 1
    return True


def _stdin_heads(rest: list[str]) -> list[str]:
    """シェルの前に置いた `busybox`・`env`・`command`・`exec` と、
    そのオプション・代入を外した残り。
    """
    while rest:
        name = _stdin_base(rest[0])
        if name not in _STDIN_HEADS:
            return rest
        rest = rest[1:]
        while rest and (_ASSIGNMENT_WORD.match(rest[0]) or rest[0].startswith("-")):
            word, rest = rest[0], rest[1:]
            if word == "--":
                break
            # `env -u NAME`・`env -C dir`・`exec -a name` は次の語が値。
            if word in _STDIN_HEAD_VALUES and rest:
                rest = rest[1:]
    return rest
