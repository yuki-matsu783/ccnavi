"""原文の走査。引用の状態を持ったまま、置換・ヒアドキュメント・コメント・改行を片付ける。

語の分割の前の段で、shlex が見ないものをここで読む。shellread から分けた。shellread を読まない。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# 読み切れなかった理由。何が読みを止めたかまで名指しする。
# 「読めなかった」だけでは、読み手は直す先が分からない。
REASON_UNTERMINATED = "unterminated-quote"
# 閉じない `$((`、引用の外で閉じない `$(`。
# unterminated-quote にまとめないのは、引用は閉じているのに、と読み手が迷うから。
REASON_UNTERMINATED_SUBST = "unterminated-substitution"
# シェルによって答えが分かれる形と、深すぎる入れ子。`$( )` の中の `case` の `)` は
# bash 3.2 が置換の終わりと読んで構文エラーにし、zsh は case の一部と読んで実行する。
# どちらかに決めて読むと、決めなかった側のシェルで止まらずに通りうる。
REASON_AMBIGUOUS_SUBST = "ambiguous-substitution"
# バッククォート。中のエスケープの規則を持たず、見つけたところで読むのをやめる。
REASON_BACKQUOTE = "backquote"

# シェルによって読みが分かれる形の表記。文面に並べる。
_CASE_IN_SUBST = "case inside $( )"
_ARITHMETIC_OR_SUBST = "$((…) …)"
_TOO_DEEP = "$( ) nested more than 16 deep"

# ブレース展開として並べるのは、どちらかのシェルが広げる形。数の範囲か 1 文字の範囲で、
# 刻みが付いてもよい。
_SEQUENCE = re.compile(r"(-?\d+\.\.-?\d+|.\.\..)(\.\.-?\d+)?")
# 語の中でこの文字が引用の外に出たら、語が終わっている。開いたブレースは閉じない。
# 空白で語を割るのは空白とタブだけ。生の CR は語の中の文字で、bash は `{git,<CR>push}` も広げる。
# ここに CR を入れると、そこでブレースを閉じたことにして後ろのカンマを見なくなる。
_BRACE_BREAK = " \t;&|"

# 語の終わりになる文字（コマンドの文脈）。ヒアドキュメントの区切りの語を読むときに使う。
_WORD_END = " \t\r\n;&|()<>"

# この文字の直後は語の始まり。`#` がコメントの始まりになるのは語の始まりだけで、
# `a#b` や `$#` の `#` は文字。`case` を予約語として見つけるのにも使う。
_BEFORE_WORD = " \t\r;&|()<>"

# 切り出した中身の代わりに外側へ残す 1 文字。消さずに残すのは、外側の語の数と形を
# 保つため。`> "$(pwd)/x"` は `> $/x` になり、リダイレクト先の後半が語として残る。
_PLACEHOLDER = "$"

# 走査が止まって調べる文字。それ以外は正規表現でまとめて飛ばす。1 文字ずつ見ると、
# ファイル 1 本分の中身を持つヒアドキュメントで判定の期限に影響する。
_COMMAND_STOP = re.compile(r"[\\'\"`$#\n<>()]")
_DOUBLE_STOP = re.compile(r'[\\`$"]')
_BODY_STOP = re.compile(r"[\\`$]")


class _Unreadable(Exception):
    """走査が読みを止めた。理由を渡すだけの例外。"""

    def __init__(self, reason: str, form: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        # 止めたのが書き直しを求める形なら、文面に並べる表記。
        self.form = form


@dataclass
class _OpenBrace:
    """開いたまま閉じていないブレース 1 つ。"""

    # 原文での `{` の位置。
    start: int
    # 引用の外にカンマがあったか。
    comma: bool = False
    # 中身の文字。引用・エスケープ・置換・入れ子が入ったら None で、範囲の端にならない。
    inner: str | None = ""
    # 中で見つけた展開。自分も展開なら、自分の表記に置き換わる。
    found: list[str] = field(default_factory=list)


class _Braces:
    """1 つの文脈（引用の外のコマンド、`${ }` の中）でブレース展開を探す。

    引用・エスケープ・置換の中身はここに届かない（走査の別の関数が読む）ので、そこにある
    `,` や `}` は数えない。`{"a,b"}` や `{a\\,b}` はシェルでも広がらない。
    """

    def __init__(self, scanner: _Scanner, record: bool) -> None:
        self.x = scanner
        self.record = record
        self.open: list[_OpenBrace] = []

    def text(self, chunk: str, at: int) -> None:
        """引用の外の文字。at は chunk の先頭の、原文での位置。"""
        if not self.open and "{" not in chunk:
            return
        for k, c in enumerate(chunk):
            if c in _BRACE_BREAK:
                self.reset()
            elif c == "{":
                self.opaque()
                self.open.append(_OpenBrace(at + k))
            elif not self.open:
                continue
            elif c == ",":
                self.open[-1].comma = True
            elif c == "}":
                self.close(at + k + 1)
            elif self.open[-1].inner is not None:
                self.open[-1].inner += c

    def opaque(self) -> None:
        """引用・エスケープ・置換が語に入った。"""
        if self.open:
            self.open[-1].inner = None

    def close(self, end: int) -> None:
        brace = self.open.pop()
        found = brace.found
        if brace.comma or (brace.inner is not None and _SEQUENCE.fullmatch(brace.inner)):
            # 入れ子は外側の表記だけを並べる。`{a,{b,c}}` を 2 件と言っても直す先は 1 つ。
            found = [self.x.s[brace.start : end]]
        self.keep(found)

    def reset(self) -> None:
        """語が終わった。閉じなかったブレースの中の展開は、シェルでも広がる（`{x{a,b}`）。"""
        while self.open:
            self.keep(self.open.pop().found)

    def keep(self, found: list[str]) -> None:
        if self.open:
            self.open[-1].found.extend(found)
        elif self.record:
            self.x.braces.extend(found)


class _Scanner:
    """shlex に渡す前に原文を 1 回通して読む。

    out には shlex に渡す文字列を組む。found には切り出した中身と、それが引用の中に
    あったかを積む。collect が偽の走査は、閉じる位置を探すだけで何も積まない。
    中身は取り出したあとに read() がもう一度読むので、入れ子の中身はそこで積まれる。
    """

    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0
        self.out: list[str] = []
        self.found: list[tuple[str, bool]] = []
        # 本文を待っているヒアドキュメント: (区切り, 区切りが引用されていたか)
        self.pending: list[tuple[str, bool]] = []
        # 引用の外でヒアドキュメントの始まりとして読んだ `<<` の数。
        # read() が、shlex の返した `<<` と数を比べる。
        self.heads = 0
        # 引用の外で見つけたブレース展開の表記。
        self.braces: list[str] = []

    def emit(self, text: str, collect: bool) -> None:
        if collect:
            self.out.append(text)

    def add(self, body: str, quoted: bool, collect: bool) -> None:
        if collect:
            self.found.append((body, quoted))
            self.out.append(_PLACEHOLDER)

    # --- コマンドの文脈

    def command(self, collect: bool, closing: bool) -> None:
        """引用の外。closing が真なら `$( )` の中で、対になる `)` の手前で戻る。"""
        s = self.s
        depth = 0
        word_start = True
        # 閉じる位置を探すだけの走査（collect が偽）では並べない。中身は read() が読み直す。
        braces = _Braces(self, collect)
        while self.i < len(s):
            m = _COMMAND_STOP.search(s, self.i)
            end = m.start() if m else len(s)
            if end > self.i:
                chunk = s[self.i : end]
                if closing:
                    self.refuse_case(chunk, word_start)
                braces.text(chunk, self.i)
                self.emit(chunk, collect)
                word_start = chunk[-1] in _BEFORE_WORD
                self.i = end
                continue
            c = s[self.i]
            if c == "\\":
                braces.opaque()
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                word_start = False
                continue
            if c == "'":
                braces.opaque()
                self.single(collect)
                word_start = False
                continue
            if c == '"':
                braces.opaque()
                self.double(collect)
                word_start = False
                continue
            if c == "`":
                self.backquote()
            if c == "$" and self.dollar(collect, quoted=False):
                braces.opaque()
                word_start = False
                continue
            if c == "#" and word_start:
                # コメントは行末まで。shlex に任せないのは、shlex が語の途中の `#` からも
                # コメントにするから（`echo a#b; git push` の後ろが消える）。
                j = s.find("\n", self.i)
                self.i = len(s) if j < 0 else j
                continue
            if c == "\n":
                # 引用の外の改行はコマンドの区切り。shlex はただの空白として読むので、
                # 2 行目のコマンドが 1 行目の引数になり、allow が 2 行目まで通してしまう。
                braces.reset()
                self.emit(" ; ", collect)
                self.i += 1
                word_start = True
                self.heredoc_bodies(collect)
                continue
            if s.startswith("<<<", self.i):
                # here-string。本文を持たないので、演算子のまま shlex に渡す。
                braces.reset()
                self.emit("<<<", collect)
                self.i += 3
                word_start = True
                continue
            if s.startswith("<<", self.i):
                braces.reset()
                self.heredoc_head(collect)
                word_start = True
                continue
            if c in "<>" and s.startswith("(", self.i + 1):
                # プロセス置換。中身は $( ) と同じく独立したコマンドとして走る。
                braces.reset()
                self.substitution(collect, quoted=False)
                word_start = False
                continue
            if closing:
                if c == "(":
                    depth += 1
                elif c == ")":
                    if depth == 0:
                        braces.reset()
                        return
                    depth -= 1
            if c in "<>()":
                braces.reset()
            else:
                braces.text(c, self.i)
            self.emit(c, collect)
            self.i += 1
            word_start = c in _BEFORE_WORD
        braces.reset()
        if closing:
            raise _Unreadable(REASON_UNTERMINATED_SUBST)

    def refuse_case(self, chunk: str, word_start: bool) -> None:
        """`$( )` の中に予約語の `case` があれば読みを止める。

        case の `)` を置換の終わりと読むか case の一部と読むかが、シェルによって分かれる。
        """
        p = chunk.find("case")
        while p >= 0:
            before = word_start if p == 0 else chunk[p - 1] in _BEFORE_WORD
            after = self.s[self.i + p + 4 : self.i + p + 5]
            if before and (after == "" or after in _WORD_END):
                raise _Unreadable(REASON_AMBIGUOUS_SUBST, _CASE_IN_SUBST)
            p = chunk.find("case", p + 1)

    # --- 引用

    def single(self, collect: bool) -> None:
        """単一引用。中は全部文字。文字として書く方法は必ずここに残す。"""
        j = self.s.find("'", self.i + 1)
        if j < 0:
            raise _Unreadable(REASON_UNTERMINATED)
        self.emit(self.s[self.i : j + 1], collect)
        self.i = j + 1

    def ansi_c(self, collect: bool) -> None:
        """`$'…'`。中は文字だが、`\\'` で引用が閉じない。"""
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
        """二重引用の中、または区切りを引用しないヒアドキュメントの本文（terminator が空）。

        どちらも、シェルが見るのは `$` とバッククォートと `\\` だけ。
        """
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
                self.backquote()
            if c == "$" and self.dollar(collect, quoted=True):
                continue
            self.emit(c, collect)
            self.i += 1
        if terminator:
            raise _Unreadable(REASON_UNTERMINATED)

    # --- `$` で始まるもの

    def dollar(self, collect: bool, quoted: bool) -> bool:
        """`$` の後ろが置換か展開なら読み進めて真を返す。ただの `$` なら偽。"""
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
        """`$(` `<(` `>(` の 2 文字の後ろから、対になる `)` まで。

        中では引用の状態が最初からに戻る。`"$(echo "a)b")"` の内側の `"` は
        外側の引用を閉じない。
        """
        self.i += 2
        start = self.i
        self.command(collect=False, closing=True)
        body = self.s[start : self.i]
        self.i += 1
        self.add(body, quoted, collect)

    def backquote(self) -> None:
        """バッククォート。中身を読まずに読みを止める。

        中で `\\` が外れる文字は二重引用の中かどうかで変わり、入れ子は外したあとの中身を
        もう一度読まないと分からない。`$( )` で必ず書き直せるので、その規則を持たない。
        """
        j = self.s.find("`", self.i + 1)
        raise _Unreadable(REASON_BACKQUOTE, self.s[self.i : j + 1] if j >= 0 else "`")

    def arithmetic(self, collect: bool, quoted: bool) -> None:
        """`$(( ))`。算術そのものではコマンドは走らないが、中の置換は走る。

        算術の文字は残して shlex に渡し、_drop_arithmetic が落とす。
        """
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
                self.backquote()
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
                    # `$((cmd) | x)` のような形。算術かコマンド置換かが表記から決まらない。
                    raise _Unreadable(REASON_AMBIGUOUS_SUBST, _ARITHMETIC_OR_SUBST)
                depth -= 1
            self.emit(c, collect)
            self.i += 1
        raise _Unreadable(REASON_UNTERMINATED_SUBST)

    def brace(self, collect: bool, quoted: bool) -> None:
        """`${ }`。既定値 `${x:-$(cmd)}` や添字 `${a[$(cmd)]}` の中の置換は走る。

        中の `{ }` は対にして数える（`${x:-{a}}` を閉じるのは 2 つめの `}`）。引用の外なら
        中のブレース展開も並べる。bash は広げないが、zsh は `${x:-{a,b}}` を広げる。
        """
        s = self.s
        self.emit("${", collect)
        self.i += 2
        braces = _Braces(self, collect and not quoted)
        while self.i < len(s):
            c = s[self.i]
            if c == "}" and not braces.open:
                self.emit(c, collect)
                self.i += 1
                return
            if c == "\\":
                braces.opaque()
                self.emit(s[self.i : self.i + 2], collect)
                self.i += 2
                continue
            if c == '"':
                braces.opaque()
                self.double(collect)
                continue
            if c == "'" and not quoted:
                braces.opaque()
                self.single(collect)
                continue
            if c == "`":
                self.backquote()
            if c == "$" and self.dollar(collect, quoted):
                braces.opaque()
                continue
            braces.text(c, self.i)
            self.emit(c, collect)
            self.i += 1
        raise _Unreadable(REASON_UNTERMINATED)

    # --- ヒアドキュメント

    def heredoc_head(self, collect: bool) -> None:
        """`<<` と区切りの語。本文は次の改行のあとに来るので、区切りだけを覚えておく。

        区切りの語のどこかが引用されていれば（`<<'EOF'` `<<"EOF"` `<<\\EOF`）、本文は
        文字として渡る。されていなければ本文の `$( )` とバッククォートはシェルが実行する。
        """
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
        """改行の直後。待っているヒアドキュメントの本文と終端の行を、順に読んで落とす。

        本文はプログラムに渡される文字であって、コマンドが走る場所ではない。
        そこを実行位置として数えると、禁止語を引用した文書を書けなくなる。

        終端の行は前後の空白を落として比べる。シェルは完全一致で比べるので、
        シェルより早く閉じることはあっても遅く閉じることはない。早く閉じれば本文の
        残りをコマンドとして読む（厳しい側）。遅く閉じると、本文の後ろのコマンドを
        本文として捨てる（止まらずに通る側）。CRLF の行でも閉じるのはこのおかげ。
        """
        s = self.s
        while self.pending:
            delim, quoted = self.pending.pop(0)
            start = self.i
            while True:
                if self.i >= len(s):
                    # 閉じない本文は、コマンドがどこかで切れたということ。
                    # そこから先は読めない。
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
        """区切りを引用しない本文から、置換の中身だけを拾う。本文の文字は外側に出さない。"""
        if not collect:
            return
        inner = _Scanner(text)
        inner.quoted_text(collect=True, terminator="")
        self.found.extend((b, True) for b, _ in inner.found)


def _scan(src: str) -> tuple[str, list[tuple[str, bool]], int, list[str]]:
    """走査して、shlex に渡す文字列、切り出した中身、`<<` を読んだ数、ブレース展開を返す。"""
    x = _Scanner(src)
    try:
        x.command(collect=True, closing=False)
    except RecursionError:
        # 入れ子が Python の再帰の上限を越えた。深さの上限と同じ扱いにする。
        raise _Unreadable(REASON_AMBIGUOUS_SUBST, _TOO_DEEP) from None
    if x.pending:
        # 本文が始まらないまま終わったヒアドキュメント（`cat <<'EOF' > f` だけ）。
        raise _Unreadable(REASON_UNTERMINATED)
    return "".join(x.out), x.found, x.heads, x.braces


def _tokenize(src: str) -> list[str]:
    """トークンに割る。

    punctuation_chars が ";" や "&&" を独立したトークンとして返させる。
    これが無いと "ls;git push" が "ls;git" という名前のコマンド 1 本になる。
    """
    lexer = shlex.shlex(src, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # コメントは走査が落としてある。shlex に `#` を扱わせると、語の途中の `#` からも
    # コメントにして後ろのコマンドを捨てる。
    lexer.commenters = ""
    return _drop_arithmetic(list(lexer))


def _drop_arithmetic(tokens: list[str]) -> list[str]:
    """算術式 $(( )) を落とす。

    中でコマンドは走らないので、判定に足すものが何も無い（中の置換は走査が
    切り出してある）。落とす理由は別にあって、左シフトの `<<` がヒアドキュメントの
    区切り記号と同じ表記だから。`echo $((1 << 2))` の `<<` を残すと、走査が
    ヒアドキュメントと読まなかった `<<` として縮退する。
    """
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
