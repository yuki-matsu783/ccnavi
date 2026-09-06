"""コマンド文字列を、シェルが割るとおりに読む。

ルールを当てる先を「文字列にその語が含まれるか」から「コマンドとして実行されるか」
に変えるための層。

問題は狭く、実際に踏んだもの。生の文字列を探すガードは "git push origin main" を
止めるが、それを grep するコマンドも、echo するコマンドも、それを説明する
ヒアドキュメントの本文も同じように止める。返る拒否は本物の拒否と区別が付かないので、
語を書いただけの読み手は「禁止された操作をした」と言われて次の一手を失う。
ガードについて書く作業が、いちばんガードに引っかかる。

分割そのものは `shlex` に任せる。シェル自身の字句規則なので、こちらが間違える
筋合いのものではない。ここに残るのは shlex が概念として持っていない 2 つだけ。
ヒアドキュメントの本文と、仕事を文字列として受け取って実行するコマンド。

完全なシェルパーサではないし、回避しようとする相手に対する境界でもない。
変数と alias は、シェルを実際に走らせない限りどうやっても届かない。
やるのは、普通の作業で書かれるコマンドについて、その語が実行されるのか
書かれただけなのかを判定すること。判定できないときはそう言って、
呼び出し側が生の文字列との一致に落とせるようにする。そちらが厳しい側の読み。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

# SEP は、シェルなら一致がまたげない場所に置く印。置く先は 3 つある。
# コマンドとコマンドの間、引用が 2 語を 1 語につないだ場所、そして
# 語の中に入った演算子の文字の両側。ルールはコマンドと引数の間に空白を求めるが
# これは空白ではないので、grep 'git push' が push として読まれなくなる。
# 一方で rm -rf /x は 1 つのコマンドとして読まれ続ける。
#
# 3 つめが要るのは、`>` や `<<` が、演算子として書かれたのか引数の中にあるのかで
# 意味がまるで違うから。`grep -n "regex: '(>" rules.yml` の `>` は文字であって
# リダイレクトではない。区別はトークンの側に既に在って（演算子は独立した
# トークンとして返る）、繋ぎ直すところで消えていた。文字は消さずに両側へ
# 印を置くので、記録に残る文面は書かれたとおりのままになる。
#
# 実際のコマンドラインはこの文字を運べないので、入力の側がこれを騙ることはできない。
# read() が入力から取り除いて、その前提を保つ。
SEP = "\x00"

# シェルが演算子として読む文字。shlex の punctuation_chars と同じ並び。
# 演算子はこの文字だけでできたトークンとして返ってくるので、語の中に
# 同じ文字が現れたときと見分けられる。
#
# 引用符だけで書かれた 1 語（`echo ">"`）は演算子と区別が付かない。shlex は
# 引用されていたかどうかを返さないので、ここでは演算子として扱う。今までと
# 同じ読みなので、この変更で新しく緩む場所ではない。
_PUNCTUATION = "();<>|&"

# 読み切れなかった理由。何が読みを止めたかまで名指しする。
# 「読めなかった」だけでは、読み手が直す先を持てない。
REASON_UNTERMINATED = "unterminated-quote"
REASON_TAKEN_AS_CODE = "command-taken-as-code"

# shlex が区切り記号として返すトークン。コマンドとコマンドを分ける。
# コマンド置換の括弧もここに落ちるので、$( ) の中身が独立したコマンドとして
# 扱われる。こちらで何もしなくてよい。
_OPERATORS = frozenset({";", ";;", "&", "&&", "|", "||", "|&", "(", ")"})

# 文字列を受け取って実行することが仕事のコマンド。
# 何を実行するかはトークンの中に無いので、ここからは見えない。
_RUNS_A_STRING = frozenset({"eval", "source", ".", "xargs"})

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


@dataclass
class Reading:
    """1 回の読みの結果。"""

    # シェルが実行しないものを取り除いたあとのコマンド。コメント、
    # ヒアドキュメントの本文、引用が作った語のつなぎ目が落ちている。
    # degraded のときは空。
    text: str = ""
    # 読み切れなかったかどうか。呼び出し側は代わりに生の文字列に当てて、
    # そうしたことを返す文面に書かなければならない。
    degraded: bool = False
    # 何が読みを止めたか。
    reason: str = ""


def read(src: str) -> Reading:
    """コマンド文字列を 1 本読む。"""
    src = src.replace(SEP, " ")
    # 改行の前のバックスラッシュはシェルの行継続で、2 文字とも消える。
    # 2 行に割ったコマンドは 1 行で書いたのと同じ語の並びになる。
    # shlex はこれを知らず、トークンの中に改行を残す。それは語のつなぎ目として
    # 読まれてしまう。シングルクォートの中では 2 文字とも文字通りなので
    # そこでは取りこぼすが、誰も書かない綴りだし、変わるのは引数の文字列であって
    # どのコマンドが走るかではない。
    src = src.replace("\\\r\n", "").replace("\\\n", "")

    try:
        tokens = _tokenize(src)
    except ValueError:
        # 閉じない引用符で shlex が投げる。途中で切れたコマンドは読めないし、
        # 残りを推測するのは判定の土台そのものを推測することになる。
        return Reading(degraded=True, reason=REASON_UNTERMINATED)

    tokens, closed = _drop_heredoc_bodies(tokens)
    if not closed:
        return Reading(degraded=True, reason=REASON_UNTERMINATED)

    commands = _split_commands(tokens)
    why = _classify(commands)
    if why:
        return Reading(degraded=True, reason=why)

    return Reading(text=_render(commands))


def _tokenize(src: str) -> list[tuple[str, int]]:
    """トークンに割る。それぞれ、何行目で読んだかを添える。

    punctuation_chars が ";" や "&&" を独立したトークンとして返させる。
    これが無いと "ls;git push" が "ls;git" という名前のコマンド 1 本になる。
    """
    lexer = shlex.shlex(src, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True

    tokens: list[tuple[str, int]] = []
    while True:
        # 行番号はトークンの前に読む。トークンを読むと後ろの空白まで食うので、
        # 行末のトークンは返ってきた時点で次の行に数えられている。
        line = lexer.lineno
        token = lexer.get_token()
        if token is None or token is lexer.eof:
            return _drop_arithmetic(tokens)
        tokens.append((token, line))


def _drop_arithmetic(tokens: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """算術式 $(( )) を落とす。

    中でコマンドは走らないので、判定に足すものが何も無い。落とす理由は別にあって、
    左シフトの `<<` がヒアドキュメントの区切り記号と同じ綴りだから。
    `echo $((1 << 2))` を本文の始まりと読むと、閉じない本文としてコマンド全体が
    読めなくなる。
    """
    kept: list[tuple[str, int]] = []
    i = 0
    while i < len(tokens):
        if tokens[i][0] == "$" and i + 1 < len(tokens) and tokens[i + 1][0] == "((":
            depth = 0
            i += 1
            while i < len(tokens):
                token = tokens[i][0]
                depth += token.count("(") - token.count(")")
                i += 1
                if depth <= 0:
                    break
            continue
        kept.append(tokens[i])
        i += 1
    return kept


def _drop_heredoc_bodies(
    tokens: list[tuple[str, int]],
) -> tuple[list[tuple[str, int]], bool]:
    """ヒアドキュメントの本文を落とし、全部閉じていたかを返す。

    本文はプログラムに渡される文字であって、コマンドが走る場所ではない。
    そこを実行位置として数えることが、禁止語を引用した文書を書けなくしていた。
    shlex はヒアドキュメントを知らないので本文が普通の語として出てくる。
    ここで見分ける。

    区切り記号と同じ行に残っている語は残す。後ろに書かれたリダイレクトは
    出力先を言っているので。
    """
    kept: list[tuple[str, int]] = []
    i = 0
    while i < len(tokens):
        token, line = tokens[i]
        if token not in ("<<", "<<-") or i + 1 >= len(tokens):
            kept.append((token, line))
            i += 1
            continue

        kept.append((token, line))
        # "<<-EOF" は shlex に "<<" と "-EOF" として届く。
        # ダッシュは区切り記号の一部で、終端子の名前ではない。
        delimiter = tokens[i + 1][0]
        if token == "<<" and delimiter.startswith("-"):
            delimiter = delimiter[1:]
        kept.append(tokens[i + 1])
        i += 2

        while i < len(tokens) and tokens[i][1] == line:
            kept.append(tokens[i])
            i += 1

        while i < len(tokens) and tokens[i][0] != delimiter:
            i += 1
        if i >= len(tokens):
            # 閉じない本文は、コマンドがどこかで切れたということ。
            # そこから先は読めない。
            return kept, False
        i += 1  # 終端子の行そのもの

    return kept, True


def _split_commands(tokens: list[tuple[str, int]]) -> list[list[str]]:
    """区切り記号でトークン列を切り、コマンド 1 本ずつのリストにする。"""
    commands: list[list[str]] = []
    current: list[str] = []
    for token, _ in tokens:
        if token in _OPERATORS:
            if current:
                commands.append(current)
                current = []
            continue
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _classify(commands: list[list[str]]) -> str:
    """読みを止めたものがあれば、それが何かを返す。"""
    for command in commands:
        # 文字列を実行することが仕事のコマンドは、先頭の語だけを見る。
        # 引数に現れた同じ綴りは実行体ではない。"." はシェルの source だが、
        # 引数としては「このディレクトリ」で、ruff check . のような書き方は
        # いくらでもある。ここを全語で見ると、そういう普通の作業が読めなくなる。
        # パイプや ";" の後ろは別のコマンドになるので、xargs や eval も
        # 先頭の語として現れる。
        if command and _base(command[0]) in _RUNS_A_STRING:
            return REASON_TAKEN_AS_CODE

        # フラグを付けて初めて文字列を実行するものは、先頭でなくてもよい。
        # sudo -u root sh -c は 4 語目に来る。
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
    """どのプログラムを指すかを変えない部分を落とす。
    git について書いたルールが /usr/bin/git と git.exe にも当たるように。"""
    for separator in ("/", "\\"):
        name = name.rsplit(separator, 1)[-1]
    lowered = name.lower()
    for extension in (".exe", ".cmd", ".bat"):
        if lowered.endswith(extension):
            return name[: -len(extension)]
    return name


def _render(commands: list[list[str]]) -> str:
    """ルールを当てる先の文字列に組み直す。

    トークンの中に空白があるなら、それは引用が置いた空白。引用されていない空白は
    shlex が最初に割った場所だから。そこを置き換えることが、grep 'git push' を
    「コマンドとその引数」として読ませないことにあたる。

    同じことを演算子の文字にもする。語の中の `>` は書かれた文字であって、
    リダイレクトではない。ここで両側に印を置かないと、`grep -n "x>" f` が
    `x > f` と同じ形になり、書き込み先を見るルールが読み手に当たる。
    """
    return SEP.join(" ".join(_join(token) for token in command) for command in commands if command)


def _join(token: str) -> str:
    """1 つのトークンを、ルールを当てる形に直す。

    演算子のトークンはそのまま。それ以外は語なので、中の空白を印に置き換え、
    中の演算子の文字を印で挟む。
    """
    if _is_operator(token):
        return token
    out = []
    for c in token:
        if c.isspace():
            out.append(SEP)
        elif c in _PUNCTUATION:
            out.append(SEP + c + SEP)
        else:
            out.append(c)
    return "".join(out)


def _is_operator(token: str) -> bool:
    """演算子のトークンかどうか。punctuation_chars が返す塊は
    この文字だけでできている。"""
    return bool(token) and all(c in _PUNCTUATION for c in token)
