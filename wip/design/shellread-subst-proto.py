"""試作 2: shlex に渡す前の走査で、シェルが実行するのに shlex が見ないものを先に片付ける。

担当セッションの試作（_Extractor）を土台に、次を足した。

- 改行をコマンドの区切りにする（引用の外だけ）
- `<( )` と `>( )` の中身も切り出す
- コメントは走査が落とす。shlex には `#` を扱わせない（語の途中の `#` を落とすため）
- heredoc の本文と終端の行は走査が落とす。トークンで落とす `_drop_heredoc_bodies` は使わない
- 切り出した中身は、引用の有無によらず外側のコマンドの後ろにつなぐ
- 引用の中から切り出したものを除いた読み（bare）も返す
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

SEP = "\x00"
WORD_SEP = "\x01"

_PUNCTUATION = "();<>|&"

REASON_UNTERMINATED = "unterminated-quote"
REASON_TAKEN_AS_CODE = "command-taken-as-code"
REASON_UNTERMINATED_SUBST = "unterminated-substitution"
REASON_AMBIGUOUS_SUBST = "ambiguous-substitution"

# 入れ子の深さの上限。越えたら読み切れないとして縮退する。
_MAX_DEPTH = 16

_OPERATORS = frozenset({";", ";;", "&", "&&", "|", "||", "|&", "(", ")"})

_RUNS_A_STRING = frozenset({"eval", "source", ".", "xargs"})

# コマンドの位置で予約語になる語。
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
        "!",
        "{",
        "}",
    }
)

_TAKES_CODE_FLAG = {
    "bash": ("-c",),
    "sh": ("-c",),
    "zsh": ("-c",),
    "ksh": ("-c",),
    "dash": ("-c",),
    "su": ("-c",),
    "find": ("-exec", "-execdir", "-ok", "-okdir"),
}

# 語の終わりになる文字（コマンドの文脈）。heredoc の区切りの語を読むときに使う。
_WORD_END = " \t\r\n;&|()<>"

# この文字の直後は語の始まり。コメントの `#` と `case` を見分けるのに使う。
_BEFORE_WORD = " \t\r;&|()<>"

# 切り出した中身の代わりに外側へ残す 1 文字。
_PLACEHOLDER = "$"

# 走査が立ち止まる文字。それ以外はまとめて飛ばす（1 文字ずつ見ると、大きな heredoc で遅い）。
_COMMAND_STOP = re.compile(r"[\\'\"`$#\n<>()]")
_DOUBLE_STOP = re.compile(r'[\\`$"]')
_BODY_STOP = re.compile(r"[\\`$]")
_BACKTICK_STOP = re.compile(r"[\\`]")


@dataclass
class Reading:
    text: str = ""
    degraded: bool = False
    reason: str = ""
    # text から、引用の中（二重引用と、区切りを引用しない heredoc の本文）で切り出した
    # コマンドを除いたもの。ルールが text に当たって bare に当たらなければ、
    # 書いた側が文字のつもりでいる場所に当たったことになる。
    bare: str = ""


class _Unreadable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Scanner:
    """shlex に渡す前に原文を 1 回なめる。

    out には shlex に渡す文字列を組む。found には切り出した中身と、それが引用の中に
    あったかを積む。collect が偽の走査は、閉じる位置を探すだけで何も積まない
    （中身は取り出したあとに read() がもう一度読むので、そこで積まれる）。
    """

    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0
        self.out: list[str] = []
        self.found: list[tuple[str, bool]] = []
        # 本文を待っている heredoc: (区切り, 区切りが引用されていたか)
        self.pending: list[tuple[str, bool]] = []
        # 引用の外で heredoc の始まりとして読んだ `<<` の数。
        self.heads = 0

    def emit(self, text: str, collect: bool) -> None:
        if collect:
            self.out.append(text)

    def add(self, body: str, quoted: bool, collect: bool) -> None:
        if collect:
            self.found.append((body, quoted))
            self.out.append(_PLACEHOLDER)

    # --- コマンドの文脈
    def command(self, collect: bool, closing: bool) -> None:
        s = self.s
        depth = 0
        word_start = True
        while self.i < len(s):
            m = _COMMAND_STOP.search(s, self.i)
            end = m.start() if m else len(s)
            if end > self.i:
                chunk = s[self.i : end]
                if closing:
                    p = chunk.find("case")
                    while p >= 0:
                        before = word_start if p == 0 else chunk[p - 1] in _BEFORE_WORD
                        after = s[self.i + p + 4 : self.i + p + 5]
                        if before and (after == "" or after in _WORD_END):
                            # `case` の `)` をどこで閉じると読むかが shell で割れる。
                            raise _Unreadable(REASON_AMBIGUOUS_SUBST)
                        p = chunk.find("case", p + 1)
                self.emit(chunk, collect)
                word_start = chunk[-1] in _BEFORE_WORD
                self.i = end
                continue
            c = s[self.i]
            if c == "\\":
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                word_start = False
                continue
            if c == "'":
                self.single(collect)
                word_start = False
                continue
            if c == '"':
                self.double(collect)
                word_start = False
                continue
            if c == "`":
                self.backtick(collect, quoted=False)
                word_start = False
                continue
            if c == "$" and self.dollar(collect, quoted=False):
                word_start = False
                continue
            if c == "#" and word_start:
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j
                continue
            if c == "\n":
                # 引用の外の改行はコマンドの区切り。
                self.emit(" ; ", collect)
                self.i += 1
                word_start = True
                self.heredoc_bodies(collect)
                continue
            if s.startswith("<<<", self.i):
                self.emit("<<<", collect)
                self.i += 3
                word_start = True
                continue
            if s.startswith("<<", self.i):
                self.heredoc_head(collect)
                word_start = True
                continue
            if c in "<>" and s.startswith("(", self.i + 1):
                # プロセス置換。中身は $( ) と同じく独立したコマンド。
                self.substitution(collect, quoted=False)
                word_start = False
                continue
            if closing:
                if c == "(":
                    depth += 1
                elif c == ")":
                    if depth == 0:
                        return
                    depth -= 1
            self.emit(c, collect)
            self.i += 1
            word_start = c in _BEFORE_WORD
        if closing:
            raise _Unreadable(REASON_UNTERMINATED_SUBST)

    # --- 引用
    def single(self, collect: bool) -> None:
        j = self.s.find("'", self.i + 1)
        if j < 0:
            raise _Unreadable(REASON_UNTERMINATED)
        self.emit(self.s[self.i : j + 1], collect)
        self.i = j + 1

    def ansi_c(self, collect: bool) -> None:
        s = self.s
        j = self.i + 2
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "'":
                self.emit(s[self.i : j + 1], collect)
                self.i = j + 1
                return
            j += 1
        raise _Unreadable(REASON_UNTERMINATED)

    def double(self, collect: bool) -> None:
        self.emit('"', collect)
        self.i += 1
        self.quoted_text(collect, '"')

    def quoted_text(self, collect: bool, terminator: str) -> None:
        """二重引用の中、または区切りを引用しない heredoc の本文（terminator が空）。"""
        s = self.s
        stop = _DOUBLE_STOP if terminator else _BODY_STOP
        while self.i < len(s):
            m = stop.search(s, self.i)
            end = m.start() if m else len(s)
            if end > self.i:
                self.emit(s[self.i : end], collect)
                self.i = end
                continue
            c = s[self.i]
            if terminator and c == terminator:
                self.emit(c, collect)
                self.i += 1
                return
            if c == "\\":
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                continue
            if c == "`":
                self.backtick(collect, quoted=True)
                continue
            if c == "$" and self.dollar(collect, quoted=True):
                continue
            self.emit(c, collect)
            self.i += 1
        if terminator:
            raise _Unreadable(REASON_UNTERMINATED)

    # --- `$` で始まるもの
    def dollar(self, collect: bool, quoted: bool) -> bool:
        s = self.s
        if s.startswith("$((", self.i):
            self.arithmetic(collect, quoted)
            return True
        nxt = s[self.i + 1 : self.i + 2]
        if nxt == "(":
            self.substitution(collect, quoted)
            return True
        if nxt == "{":
            self.brace(collect, quoted)
            return True
        if nxt == "'" and not quoted:
            self.ansi_c(collect)
            return True
        return False

    def substitution(self, collect: bool, quoted: bool) -> None:
        """`$(` `<(` `>(` の 2 文字の後ろから、対になる `)` まで。"""
        self.i += 2
        start = self.i
        self.command(collect=False, closing=True)
        body = self.s[start : self.i]
        self.i += 1
        self.add(body, quoted, collect)

    def backtick(self, collect: bool, quoted: bool) -> None:
        s = self.s
        j = self.i + 1
        body: list[str] = []
        while j < len(s):
            m = _BACKTICK_STOP.search(s, j)
            end = m.start() if m else len(s)
            if end > j:
                body.append(s[j:end])
                j = end
                continue
            c = s[j]
            if c == "\\" and j + 1 < len(s):
                nxt = s[j + 1]
                # バッククォートの中で `\` が外れるのは `` ` `` `$` `\`（二重引用の中なら `"` も）。
                if nxt in "`$\\" or (quoted and nxt == '"'):
                    body.append(nxt)
                else:
                    body.append(c + nxt)
                j += 2
                continue
            if c == "`":
                self.i = j + 1
                self.add("".join(body), quoted, collect)
                return
            body.append(c)
            j += 1
        raise _Unreadable(REASON_UNTERMINATED_SUBST)

    def arithmetic(self, collect: bool, quoted: bool) -> None:
        s = self.s
        self.emit("$((", collect)
        self.i += 3
        depth = 0
        while self.i < len(s):
            c = s[self.i]
            if c == "\\":
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                continue
            if c == "`":
                self.backtick(collect, quoted)
                continue
            if c == "$" and self.dollar(collect, quoted):
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    if s.startswith("))", self.i):
                        self.emit("))", collect)
                        self.i += 2
                        return
                    # `$((cmd) | x)` のような形。算術かコマンド置換かが綴りから決まらない。
                    raise _Unreadable(REASON_AMBIGUOUS_SUBST)
                depth -= 1
            self.emit(c, collect)
            self.i += 1
        raise _Unreadable(REASON_UNTERMINATED_SUBST)

    def brace(self, collect: bool, quoted: bool) -> None:
        s = self.s
        self.emit("${", collect)
        self.i += 2
        while self.i < len(s):
            c = s[self.i]
            if c == "}":
                self.emit(c, collect)
                self.i += 1
                return
            if c == "\\":
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                continue
            if c == '"':
                self.double(collect)
                continue
            if c == "'" and not quoted:
                self.single(collect)
                continue
            if c == "`":
                self.backtick(collect, quoted)
                continue
            if c == "$" and self.dollar(collect, quoted):
                continue
            self.emit(c, collect)
            self.i += 1
        raise _Unreadable(REASON_UNTERMINATED)

    # --- heredoc
    def heredoc_head(self, collect: bool) -> None:
        s = self.s
        start = self.i
        self.i += 2
        if s[self.i : self.i + 1] == "-":
            self.i += 1
        while self.i < len(s) and s[self.i] in " \t":
            self.i += 1
        delim: list[str] = []
        quoted = False
        while self.i < len(s) and s[self.i] not in _WORD_END:
            c = s[self.i]
            if c in "'\"":
                quoted = True
                j = s.find(c, self.i + 1)
                if j < 0:
                    raise _Unreadable(REASON_UNTERMINATED)
                delim.append(s[self.i + 1 : j])
                self.i = j + 1
            elif c == "\\":
                quoted = True
                delim.append(s[self.i + 1 : self.i + 2])
                self.i += 2
            else:
                delim.append(c)
                self.i += 1
        self.emit(s[start : self.i], collect)
        if collect:
            self.heads += 1
        if delim:
            self.pending.append(("".join(delim), quoted))

    def heredoc_bodies(self, collect: bool) -> None:
        """改行の直後。待っている heredoc の本文と終端の行を、順に読んで落とす。

        終端の行は前後の空白を落として比べる。シェルより早く閉じることはあっても
        遅く閉じることはない。早く閉じれば本文の残りをコマンドとして読む（厳しい側）。
        遅く閉じると、本文の後ろのコマンドを本文として捨てる（素通りの側）。
        """
        s = self.s
        while self.pending:
            delim, quoted = self.pending.pop(0)
            start = self.i
            while True:
                if self.i >= len(s):
                    raise _Unreadable(REASON_UNTERMINATED)
                j = s.find("\n", self.i)
                end = len(s) if j < 0 else j
                if s[self.i : end].strip() == delim:
                    if not quoted:
                        self.body(s[start : self.i], collect)
                    self.i = len(s) if j < 0 else end + 1
                    break
                self.i = len(s) if j < 0 else end + 1

    def body(self, text: str, collect: bool) -> None:
        if not collect:
            return
        inner = _Scanner(text)
        inner.quoted_text(collect=True, terminator="")
        self.found.extend((b, True) for b, _ in inner.found)


def _scan(src: str) -> tuple[str, list[tuple[str, bool]], int]:
    x = _Scanner(src)
    try:
        x.command(collect=True, closing=False)
    except RecursionError:
        raise _Unreadable(REASON_AMBIGUOUS_SUBST) from None
    if x.pending:
        # 本文が始まらないまま終わった heredoc。
        raise _Unreadable(REASON_UNTERMINATED)
    return "".join(x.out), x.found, x.heads


def read(src: str) -> Reading:
    """コマンド文字列を 1 本読む。"""
    return _read(src, 0)


def _read(src: str, depth: int) -> Reading:
    if depth > _MAX_DEPTH:
        return Reading(degraded=True, reason=REASON_AMBIGUOUS_SUBST)
    src = src.replace(SEP, " ").replace(WORD_SEP, " ")
    src = src.replace("\\\r\n", "").replace("\\\n", "")

    try:
        outer, found, heads = _scan(src)
    except _Unreadable as e:
        return Reading(degraded=True, reason=e.reason)

    try:
        tokens = _tokenize(outer)
    except ValueError:
        return Reading(degraded=True, reason=REASON_UNTERMINATED)
    if sum(t in ("<<", "<<-") for t in tokens) > heads:
        # 走査が heredoc と見なかった `<<` がトークンに出た。引用が `<<` だけの 1 語
        # （`grep -n "<<" f`）で、shlex からは演算子と区別が付かない。今までどおり縮退する
        # （ccnavi.md §12.2 の許容した誤検知）。
        return Reading(degraded=True, reason=REASON_UNTERMINATED)

    commands = _split_commands(tokens)
    why = _classify(commands)
    if why:
        return Reading(degraded=True, reason=why)

    texts = [_render(commands)]
    bares = [texts[0]]
    for body, quoted in found:
        inner = _read(body, depth + 1)
        if inner.degraded:
            return inner
        texts.append(inner.text)
        if not quoted:
            bares.append(inner.bare)
    return Reading(
        text=SEP.join(t for t in texts if t),
        bare=SEP.join(t for t in bares if t),
    )


def _tokenize(src: str) -> list[str]:
    lexer = shlex.shlex(src, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # コメントは走査が落としてある。shlex は語の途中の `#` からもコメントにするので渡さない。
    lexer.commenters = ""
    return _drop_arithmetic(list(lexer))


def _drop_arithmetic(tokens: list[str]) -> list[str]:
    kept: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "$" and i + 1 < len(tokens) and tokens[i + 1] == "((":
            depth = 0
            i += 1
            while i < len(tokens):
                token = tokens[i]
                depth += token.count("(") - token.count(")")
                i += 1
                if depth <= 0:
                    break
            continue
        kept.append(tokens[i])
        i += 1
    return kept


def _split_commands(tokens: list[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _OPERATORS:
            if current:
                commands.append(current)
                current = []
            continue
        if not current and token in _RESERVED:
            # コマンドの位置に立つ予約語は、それだけで 1 本にする。後ろの語を
            # コマンドの先頭として読ませるため（`then find . -delete`）。
            # 落とさないのは、`! grep x f` を `grep x f` と読んで allow に当てないため。
            commands.append([token])
            continue
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _classify(commands: list[list[str]]) -> str:
    for command in commands:
        if command and _base(command[0]) in _RUNS_A_STRING:
            return REASON_TAKEN_AS_CODE
        for i, token in enumerate(command):
            flags = _TAKES_CODE_FLAG.get(_base(token))
            if flags and _has_flag(command[i + 1 :], flags):
                return REASON_TAKEN_AS_CODE
    return ""


def _has_flag(tokens: list[str], flags: tuple[str, ...]) -> bool:
    for token in tokens:
        for flag in flags:
            if token == flag:
                return True
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
    for separator in ("/", "\\"):
        name = name.rsplit(separator, 1)[-1]
    lowered = name.lower()
    for extension in (".exe", ".cmd", ".bat"):
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def _render(commands: list[list[str]]) -> str:
    return SEP.join(" ".join(_join(token) for token in command) for command in commands if command)


def _join(token: str) -> str:
    if _is_operator(token):
        return token
    out = []
    for c in token:
        if c.isspace():
            out.append(WORD_SEP)
        elif c in _PUNCTUATION:
            out.append(WORD_SEP + c + WORD_SEP)
        else:
            out.append(c)
    return "".join(out)


def _is_operator(token: str) -> bool:
    return bool(token) and all(c in _PUNCTUATION for c in token)
