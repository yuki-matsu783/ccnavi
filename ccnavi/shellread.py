"""コマンド文字列を、シェルが割るとおりに読む。

ルールを当てる先を「文字列にその語が含まれるか」から「コマンドとして実行されるか」
に変えるための層。

生の文字列を探すガードは "git push origin main" を
止めるが、それを grep するコマンドも、echo するコマンドも、それを説明する
ヒアドキュメントの本文も同じように止める。返る拒否は本物の拒否と区別が付かないので、
語を書いただけの読み手は「禁止された操作をした」と言われて次の一手を失う。
ガードについて書く作業が、いちばんガードに引っかかる。

逆向きの穴もある。`grep -n "$(git push)" f` の `$( )` は二重引用の中でもシェルが
実行するが、shlex は引用の中を 1 語として返すので、中身がコマンドとして読まれない。
改行、プロセス置換 `<( )`、語の途中の `#` も、shlex はシェルと違う読み方をする。
どれも失敗の向きが素通りで、allow が後ろのコマンドまで通してしまう。

だから読みは 2 段にしてある。先に原文を 1 回走査して（_Scanner）、引用の状態を
自分で持ったまま、シェルが実行するのに shlex が見ないもの（コマンド置換、
プロセス置換、ヒアドキュメントの本文、コメント、改行）を片付ける。書き直す道が必ずあって、
読み分けると規則が増えるか、読み違えると素通りに倒れる形（バッククォート、ブレース展開、
実行するときに決まるコマンド名、シェルで読みが割れる形）は、読み解かずに並べて判定が止める。
語の分割はそのあと shlex に任せる。引用の規則を 2 か所で持つことになるが、shlex の
状態機械は公開されておらず、中身を写すと Python の版で壊れる
（wip/design/shellread-subst.md 1.1）。

仕事を文字列として受け取って実行するコマンドは、読み切れないものとして扱う。
その代わり、`env` `sudo` `sh -c` のような実行役のコマンドが中で実行するコマンドを、
層として別に並べる（下の「中で実行されるコマンド」）。判定はそれを止める側のルールに
だけ当てる。

完全なシェルパーサではないし、回避しようとする相手に対する境界でもない。
変数の値と alias は、シェルを実際に走らせない限りどうやっても届かない（変数をコマンド名に
使った形は止める）。
やるのは、普通の作業で書かれるコマンドについて、その語が実行されるのか
書かれただけなのかを判定すること。判定できないときはそう言って、
呼び出し側が生の文字列との一致に落とせるようにする。そちらが厳しい側の読み。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

# シェルなら一致がまたげない場所に置く目印。目印は 2 つ。
# SEP はコマンドとコマンドの間。WORD_SEP は引用が 2 語を 1 語につないだ場所と、
# 語の中に入った演算子の文字の両側。ルールはコマンドと引数の間に空白を求めるが
# どちらも空白ではないので、grep 'git push' が push として読まれなくなる。
# 一方で rm -rf /x は 1 つのコマンドとして読まれ続ける。
#
# 2 つを別の文字にするのは、ルールの `[^\x00]*` が「同じコマンドの中」だけを
# 指せるように。同じ目印だと `[^\x00]*$`（後ろにコマンドが続かない）が引用の空白でも
# 止まり、`grep -n "git push" f` が読みのルールに当たらなくなる。
#
# 演算子の両側に目印が要るのは、`>` や `<<` が、演算子として書かれたのか引数の中に
# あるのかで意味がまるで違うから。`grep -n "regex: '(>" rules.yml` の `>` は文字で
# あってリダイレクトではない。区別はトークンの側に既に在って（演算子は独立した
# トークンとして返る）、繋ぎ直すところで消えていた。文字は消さずに両側へ
# 目印を置くので、記録に残る文面は書かれたとおりのままになる。
#
# 実際のコマンドラインはこの文字を運べないので、入力の側がこれを騙ることはできない。
# read() が入力から取り除いて、その前提を保つ。
# `cd` で移った先から見たコマンドの層に付ける、実行役のコマンドの名前。
# 実行役のコマンドの一覧（_RUNNERS）に `cd` は無いので、この名前は重ならない。
MOVED = "cd"

SEP = "\x00"
WORD_SEP = "\x01"

# シェルが演算子として読む文字。shlex の punctuation_chars と同じ並び。
# 演算子はこの文字だけでできたトークンとして返ってくるので、語の中に
# 同じ文字が現れたときと見分けられる。
#
# 引用符だけで書かれた 1 語（`echo ">"`）は演算子と区別が付かない。shlex は
# 引用されていたかどうかを返さないので、ここでは演算子として扱う。走査は引用の
# 範囲を知っているので直せるが、それは止まっていたものが通る向きの変更になるので
# 別に決める（設計 6）。
_PUNCTUATION = "();<>|&"

# 読み切れなかった理由。何が読みを止めたかまで名指しする。
# 「読めなかった」だけでは、読み手が直す先を持てない。
REASON_UNTERMINATED = "unterminated-quote"
REASON_TAKEN_AS_CODE = "command-taken-as-code"
# 閉じない `$((`、引用の外で閉じない `$(`。
# unterminated-quote に寄せないのは、引用は閉じているのに、と読み手が迷うから。
REASON_UNTERMINATED_SUBST = "unterminated-substitution"
# シェルによって答えが割れる形と、深すぎる入れ子。`$( )` の中の `case` の `)` は
# bash 3.2 が置換の終わりと読んで構文エラーにし、zsh は case の一部と読んで実行する。
# どちらかに決めて読むと、決めなかった側のシェルで素通りになりうる。
REASON_AMBIGUOUS_SUBST = "ambiguous-substitution"
# バッククォート。中のエスケープの規則を持たず、見つけたところで読むのをやめる。
REASON_BACKQUOTE = "backquote"

# ---- 書き直しを求める形
#
# 書き直す道が必ずあり、読み分けると規則が増えるか、読み違えると素通りに倒れる形。読みはこれを
# 読み解かずに Reading.rewrites に（形, 綴り）で並べ、判定がルールより先に止めて、形ごとの
# 書き直し方を案内する（ADR-0046、ADR-0047）。
#
# 引用の外のブレース展開。`{git,push,origin,main}` は 1 語に見えるが、bash は
# `git push origin main` を実行する。広げ方はシェルで割れる（`{1..5..2}` と `${x:-{a,b}}` は
# zsh だけが広げ、`{01..03}` は bash 3.2 だけが 0 を落とす）。
FORM_BRACE = "brace-expansion"
# 実行するときにシェルが決めるコマンド名。変数（`$c push`）、置換（`$(echo git) push`）、
# グロブ（`/usr/bin/gi? push`）。どのプログラムが走るかが綴りに無いので、どのルールも当たらない。
FORM_COMMAND_NAME = "command-name-expansion"
# バッククォート。二重引用の中でも実行される。`$( )` か単一引用で必ず書き直せる。
FORM_BACKQUOTE = "backquote"
# シェルで読みが割れる形（REASON_AMBIGUOUS_SUBST の形）と、作業で使わない予約語。
FORM_AMBIGUOUS = "ambiguous-form"

# 読みを止めた理由のうち、書き直しを求める形になるもの。
_FORM_OF_REASON = {REASON_BACKQUOTE: FORM_BACKQUOTE, REASON_AMBIGUOUS_SUBST: FORM_AMBIGUOUS}

# シェルで読みが割れる形の綴り。文面に並べる。
_CASE_IN_SUBST = "case inside $( )"
_ARITHMETIC_OR_SUBST = "$((…) …)"
_TOO_DEEP = "$( ) nested more than 16 deep"
# コマンドの位置に立つと読みが割れるか、エージェントの作業で使わない予約語。`coproc NAME cmd` は
# bash 4 以降が NAME を名前と読み、zsh は NAME を実行する。`select` は入力を待つ。
_AMBIGUOUS_RESERVED = frozenset({"coproc", "select"})

# ブレース展開として並べるのは、どちらかのシェルが広げる形。数の範囲か 1 文字の範囲で、
# 刻みが付いてもよい。
_SEQUENCE = re.compile(r"(-?\d+\.\.-?\d+|.\.\..)(\.\.-?\d+)?")
# 語の中でこの文字が引用の外に出たら、語が終わっている。開いたブレースは閉じない。
# 空白で語を割るのは空白とタブだけ。生の CR は語の中の文字で、bash は `{git,<CR>push}` も広げる。
# ここに CR を入れると、そこでブレースを閉じたことにして後ろのカンマを見なくなる。
_BRACE_BREAK = " \t;&|"

# 入れ子の深さの上限。越えたら読み切れないとして縮退する。実際の作業で
# 数段を超える入れ子は書かれないし、上限が無いと 1 本のコマンドで判定の期限を使い切れる。
_MAX_DEPTH = 16

# shlex が区切り記号として返すトークン。コマンドとコマンドを分ける。
# 置換の中身はここに届く前に走査が取り出してあるので、括弧が残るのは
# サブシェル `( )` と case の `)` だけ。
_OPERATORS = frozenset({";", ";;", "&", "&&", "|", "||", "|&", "(", ")"})

# 文字列を受け取って実行することが仕事のコマンド。
# 何を実行するかはトークンの中に無いので、ここからは見えない。
_RUNS_A_STRING = frozenset({"eval", "source", ".", "xargs"})

# コマンドの位置に立ったときに予約語になる語。予約語の後ろはコマンドの先頭なので、
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

# ---- `cd` が移った先
#
# 守りもルールも、当てる先は語の綴り。`cd` で入ってから書くと、行き先の綴りから
# ディレクトリの名前が消えるので当たらない（`cd .claude && echo x > settings.json`）。
# だから `cd` の行き先を読んで、後ろのコマンドの引数の綴りに継ぎ足す（ADR-0069）。
#
# 継ぎ足した綴りは text には入れず、当てる先として足すだけ（Reading.moved）。書かれた
# 綴りを動かすと、コマンドの頭に名前を固定して書かれたルールが外れて、いま止まって
# いるものが止まらなくなる。同じ理由で、コマンドの位置の語には継ぎ足さない。
#
# 行き先が読めなくなったら、そこから先は継ぎ足さない。読みそのものは変えない。
# 縮退させると生の文字列に落ちて、`cd - && rm -f <守られた場所>` のように
# **今は止まっている形**が止まらなくなる（敵対的レビュー 2026-09-20）。

# 行き先を綴りに持たないもの。読めない行き先として扱う。
_PUSHD = frozenset({"pushd", "popd"})

# 後ろの語をそのまま実行する組み込みコマンド。`builtin cd x` と `command cd x` は
# `cd` そのものなので、1 語飛ばして読む。
_RUNS_BUILTIN = frozenset({"builtin", "command"})

# `cd` のオプション。どれも値を取らない。
_CD_OPTIONS = frozenset({"-L", "-P", "-e", "-@"})

# 中で実行されるコマンドの居場所だけを移す実行役のコマンドのオプション。
# 値は次の語か `=` の後ろ。まとめ書き（`-iC /tmp`）は読まない（継ぎ足さない側に倒れる）。
_MOVES_TO = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}

# 左がサブシェルになる区切り。`cd a | b` と `cd a & b` の `cd` は、右にも後ろにも効かない。
_FORKED = frozenset({"|", "|&", "&"})

# 綴りの中にあると、行き先が実行するときまで決まらない文字。変数と置換（走査が `$` 1 文字に
# 置き換えたもの）、グロブの `*` `?` と閉じた `[…]`。先頭の `~` は別に見る。
_PATH_EXPANDS = re.compile(r"[$*?]|\[[^\]]*\]")

# 絶対パス。継ぎ足さずにそのまま使う。Windows のドライブ文字も絶対。
_ABSOLUTE = re.compile(r"[\\/]|[A-Za-z]:[\\/]")

# 居場所の綴りの長さの上限。越えたら、そこから先は継ぎ足さない。`cd a` を数千回
# 並べると居場所が伸び、畳み直す値段が長さに比例するので全体が二乗になる。判定の期限
# （judge の 3 秒）は読み終えたあとに見るので、読みの中で伸びるものには自分で上限を置く
# （`$( )` の _MAX_DEPTH と同じ向き）。実際のパスはここまで深くならない。
_HERE_LIMIT = 256

# 1 回の読みで継ぎ足す語数の上限。越えたら、そこから先のコマンドには継ぎ足さない。
# 層の UNWRAP_WORDS と同じ役割で、当てる先の総量を抑える。
MOVED_WORDS = 2000


# ---- 中で実行されるコマンド
#
# `env rm x` の `rm x` のように、別のコマンドを実行することが仕事のコマンド
# （実行役のコマンド）がある。ルールの多くはコマンドの先頭に固定して書かれるので、
# 実行役のコマンドを前に置くだけで外れる。ここでは実行役のコマンドを 1 枚ずつ外して、
# 中で実行されるコマンドを並べる。判定はそれを止める側のルールにだけ当てる（judge）。
#
# 外す先は、走査と shlex で読んだコマンドのそれぞれ。置換の中身から切り出したコマンドも
# 含む（`echo "$(env rm x)"` の `rm x`）。
#
# 一覧は組み込みで持つ。ルールファイルから足せるようにすると、消すこともできる。
# 守りの根拠を守られる側に置かない。一覧に無い実行役のコマンド（`script -c`・`watch`・
# `ssh host cmd`・`python -c` など）は外さない。

# 深さの上限。元の形は数えない。判定の期限に効くので、層の数をここで抑える。
UNWRAP_DEPTH = 4
# 1 回の読みで作る層の語数の上限。`eval` や `sh -c` の文字列は読み直すと語が増えるので、
# 深さだけでは抑えきれない。超えたら、そこから先の層は作らない。
UNWRAP_WORDS = 2000

# オプションと値を飛ばした残りがコマンドになるもの。値は、値を取るオプション
# （`-u me` のように次の語を値に取るもの）と、コマンドの前に置く位置引数の数。
# 一覧に無いオプションは値を取らないものとして読む。読み違えると値をコマンドと読んで
# 層がずれるが、ずれた層は当たらないだけで、元の形の判定は変わらない。
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

# `command -v jq` はコマンドを探すだけで、実行しない。
_LOOKUP_ONLY = {"command": frozenset("vV")}

# ファイルを渡せばそれを、`-c` なら文字列を実行するシェル。
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# シェルのオプションで、次の語を値に取るもの。
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})

# 最初の引数のファイルを、今のシェルで実行するもの。
_SOURCES = frozenset({".", "source"})

_XARGS_VALUE_OPTIONS = frozenset(
    {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "--max-args", "--max-procs", "--delimiter"}
)

# find の、後ろに書いたコマンドを実行する述語。`;` か `+` までがコマンド。
_FIND_EXEC = frozenset(_TAKES_CODE_FLAG["find"])

# コマンド名を探すときに飛ばす代入。配列の要素（`a[1]=x`）と足し込み（`x+=1`）も代入。
# シェルはこの形の語をコマンドの前に並べられ、残りをコマンドとして実行する。
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")

# `env` と `sudo` が代入として読む引数。どちらも `=` を含む引数を名前の綴りを問わず代入に
# 数える（`env a+=1 rm x` は `a+` という変数を置いて rm を実行する）。シェルの代入より広い。
_RUNNER_ASSIGNMENT = re.compile(r"[^=-][^=]*=")

# コマンド名の中で、実行するときにシェルが中身を決める文字。変数と置換（走査が `$` 1 文字に
# 置き換えたもの）、グロブの `*` `?` と閉じた `[…]`。`[` だけの test コマンドと `[[` は当たらない。
_NAME_EXPANDS = re.compile(r"[$*?]|\[[^\]]*\]")

# bash 4.1 以降の名前付き fd。`{fd}>/dev/null cmd` のリダイレクトの前に付く。
_NAMED_FD = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")

# 語の終わりになる文字（コマンドの文脈）。ヒアドキュメントの区切りの語を読むときに使う。
_WORD_END = " \t\r\n;&|()<>"

# この文字の直後は語の始まり。`#` がコメントの始まりになるのは語の始まりだけで、
# `a#b` や `$#` の `#` は文字。`case` を予約語として見つけるのにも使う。
_BEFORE_WORD = " \t\r;&|()<>"

# 切り出した中身の代わりに外側へ残す 1 文字。消さずに残すのは、外側の語の数と形を
# 保つため。`> "$(pwd)/x"` は `> $/x` になり、リダイレクト先の後半が語として残る。
_PLACEHOLDER = "$"

# 走査が立ち止まる文字。それ以外は正規表現でまとめて飛ばす。1 文字ずつ見ると、
# ファイル 1 本分を運ぶヒアドキュメントで判定の期限に響く。
_COMMAND_STOP = re.compile(r"[\\'\"`$#\n<>()]")
_DOUBLE_STOP = re.compile(r'[\\`$"]')
_BODY_STOP = re.compile(r"[\\`$]")


@dataclass
class Reading:
    """1 回の読みの結果。"""

    # シェルが実行しないものを取り除いたあとのコマンド。コメント、
    # ヒアドキュメントの本文、引用が作った語のつなぎ目が落ちている。
    # 置換の中身は外側のコマンドの後ろに、独立したコマンドとしてつないである。
    # degraded のときは空。
    text: str = ""
    # 読み切れなかったかどうか。呼び出し側は代わりに生の文字列に当てて、
    # そうしたことを返す文面に書かなければならない。
    degraded: bool = False
    # 何が読みを止めたか。
    reason: str = ""
    # text から、引用の中（二重引用と、区切りを引用しないヒアドキュメントの本文）で
    # 切り出したコマンドを除いたもの。ルールが text に当たって bare に当たらなければ、
    # 書いた側が文字を書いただけのつもりでいる場所に当たったことになる。
    # そのときに回避策を文面で名指しするために持つ。判定そのものは text で下す。
    bare: str = ""
    # 実行役のコマンドを 1 枚ずつ外した層を、SEP でつないだもの。コマンドごとに、
    # 外側から内側の順に並ぶ。元の形は含めない。層が無ければ空。
    # 読み切れない形（degraded）でも、トークンに割れる限り作る。`sh -c` や `xargs` の
    # ような形こそ、中で何が実行されるかを見たい。閉じない引用と閉じない
    # ヒアドキュメントのように、走査か shlex が止まった形では作らない。
    unwrapped: str = ""
    # `cd` で移った先から見たコマンド。SEP でつないだもの。綴りの変わったコマンドだけが並ぶ。
    # text には足さない。継ぎ足した綴りで当たる形を増やすだけにして、いま当たっている形を
    # 動かさないため（ADR-0069）。呼び手は中で実行されるコマンドの層と同じに扱い、
    # 止める側のルール（deny と ask）にだけ当てる。degraded のときは空。
    moved: str = ""
    # 層ごとの、その層を実行する実行役のコマンドの名前。unwrapped と同じ並び。
    # 文面で「`env` が実行する `rm x`」と言うために持つ。
    runners: list[str] = field(default_factory=list)
    # 層ごとの、引用の中から切り出したコマンドから作った層かどうか。unwrapped と同じ並び。
    # 層は bare に当て直せないので、文面の断り（引用の中に当たった）はこれで決める。
    quoted_layers: list[bool] = field(default_factory=list)
    # 書き直しを求める形の（形, 綴り）。形は FORM_* のどれか。書かれた順で、重なりは除く。
    # 置換の中身、`sh -c` と `eval` に渡った文字列、実行役のコマンドを外した層の中のものも含む。
    # degraded でも、見つけたものは並ぶ。判定はこれがあればルールより先に止める。
    rewrites: list[tuple[str, str]] = field(default_factory=list)

    @property
    def braces(self) -> list[str]:
        """ブレース展開の綴り。"""
        return [text for form, text in self.rewrites if form == FORM_BRACE]


class _Unreadable(Exception):
    """走査が読みを止めた。理由を運ぶだけの例外。"""

    def __init__(self, reason: str, form: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        # 止めたのが書き直しを求める形なら、文面に並べる綴り。
        self.form = form


@dataclass
class _OpenBrace:
    """開いたまま閉じていないブレース 1 つ。"""

    # 原文での `{` の位置。
    start: int
    # 引用の外にカンマがあったか。
    comma: bool = False
    # 中身の文字。引用・エスケープ・置換・入れ子が混ざったら None で、範囲の端にならない。
    inner: str | None = ""
    # 中で見つけた展開。自分も展開なら、自分の綴りに置き換わる。
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
            # 入れ子は外側の綴りだけを並べる。`{a,{b,c}}` を 2 件と言っても直す先は 1 つ。
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
    """shlex に渡す前に原文を 1 回なめる。

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
        # read() が、shlex の返した `<<` と数を突き合わせる。
        self.heads = 0
        # 引用の外で見つけたブレース展開の綴り。
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

        case の `)` を置換の終わりと読むか case の一部と読むかが、シェルで割れる。
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
        """単一引用。中は全部文字。文字として書く道は必ずここに残す。"""
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
                    # `$((cmd) | x)` のような形。算術かコマンド置換かが綴りから決まらない。
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
        本文として捨てる（素通りの側）。CRLF の行でも閉じるのはこのおかげ。
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


def read(src: str) -> Reading:
    """コマンド文字列を 1 本読む。"""
    reading, commands = _read(src, 0)
    # `sh -c` と `eval` に渡った文字列はシェルが読み直すので、そこで見つけた形も並べる。
    reread: list[tuple[str, str]] = []
    marks: list[bool] = []
    layers = _unwrap(commands, reread, marks)
    reading.unwrapped = SEP.join(_render_command(layer) for _, layer, _ in layers)
    reading.runners = [runner for runner, _, _ in layers]
    reading.quoted_layers = [quoted for _, _, quoted in layers]
    # コマンド名は、読んだコマンドと、実行役のコマンドを外した層の両方で見る。
    # `env $c push` の `$c` と `sh $SCRIPT` の `$SCRIPT` は、層の先頭にしか立たない。
    # `eval` と `sh -c` の文字列を読み直した層は、外側が縮退していれば見ない。そのときは確認に
    # 落ちるので、止めて書き直させると、書き直し先がファイルになって中身が見えなくなる（ADR-0047）。
    # 縮退していない（`FOO=1 eval "$c"`）なら確認に落ちる保証が無いので、見る。
    looked = [
        layer
        for (_, layer, _), reread_layer in zip(layers, marks, strict=True)
        if not (reread_layer and reading.degraded)
    ]
    names = _expanded_names([command for command, _ in commands] + looked)
    rewrites = reading.rewrites + reread + [(FORM_COMMAND_NAME, name) for name in names]
    reading.rewrites = list(dict.fromkeys(rewrites))
    return reading


def placed(src: str) -> list[tuple[list[str], str | None]]:
    """外側のコマンド 1 本ずつの（語の並び, そのコマンドが居る場所）。

    居る場所は `cd` を追った先で、読みの起点（打たれた場所）から見た綴り。起点そのものは
    空文字、読めなくなったら None（6.3.2 と同じ追い方）。置換の中身は並べない。
    読み切れない形（走査か shlex が止まる）なら空のリストを返す。呼び手は read() の
    degraded を先に見て、そちらで扱う。
    """
    src = src.replace(SEP, " ").replace(WORD_SEP, " ")
    src = src.replace("\\\r\n", "").replace("\\\n", "")
    try:
        outer, _, _, _ = _scan(src)
        tokens = _tokenize(outer)
    except (_Unreadable, ValueError):
        return []
    places: list[tuple[list[str], str | None]] = []
    _resolve_cd(tokens, places)
    return places


def _read(src: str, depth: int) -> tuple[Reading, list[tuple[list[str], bool]]]:
    """読みと、層を作る先のコマンドの並びを返す。

    並びの 1 つずつは（コマンド, 引用の中から切り出したコマンドか）。外側のコマンドが先、
    切り出した中身のコマンドが後ろ。読み切れない形でも、トークンに割れる限り返す。
    """
    if depth > _MAX_DEPTH:
        return _stopped(_Unreadable(REASON_AMBIGUOUS_SUBST, _TOO_DEEP)), []
    src = src.replace(SEP, " ").replace(WORD_SEP, " ")
    # 改行の前のバックスラッシュはシェルの行継続で、2 文字とも消える。
    # 2 行に割ったコマンドは 1 行で書いたのと同じ語の並びになる。
    # 走査より先に消すのは、改行をコマンドの区切りに読ませないため。
    # シングルクォートの中では 2 文字とも文字通りなのでそこでは取りこぼすが、
    # 誰も書かない綴りだし、変わるのは引数の文字列であってどのコマンドが走るかではない。
    src = src.replace("\\\r\n", "").replace("\\\n", "")

    try:
        outer, found, heads, braces = _scan(src)
    except _Unreadable as e:
        return _stopped(e), []
    rewrites = [(FORM_BRACE, brace) for brace in braces]

    try:
        tokens = _tokenize(outer)
    except ValueError:
        # 閉じない引用符で shlex が投げる。走査は通ったのに shlex が閉じないと読むのは、
        # `$'…\'…'` のように 2 つの読みが割れる形。読み切れないものとして扱う。
        return Reading(degraded=True, reason=REASON_UNTERMINATED, rewrites=rewrites), []

    # `cd` が移った先から見た綴りを別に組む（ADR-0069）。元のトークン列は動かさない。
    moved_tokens = _resolve_cd(tokens)

    commands = _split_commands(tokens)
    # `for select in` と `case coproc in` の語は変数名と調べる値で、予約語ではない。
    previous: list[str] = []
    for command in commands:
        if (
            len(command) == 1
            and command[0] in _AMBIGUOUS_RESERVED
            and not (len(previous) == 1 and previous[0] in _TAKES_A_WORD)
        ):
            rewrites.append((FORM_AMBIGUOUS, command[0]))
        previous = command
    # 層を作る先は、読みを止める理由があっても集める。そのために中身も先に読んでおく。
    runnable = [(command, False) for command in _with_time(commands)]
    inners: list[tuple[Reading, bool]] = []
    for body, quoted in found:
        inner, inner_commands = _read(body, depth + 1)
        inners.append((inner, quoted))
        rewrites.extend(inner.rewrites)
        runnable.extend((command, quoted or q) for command, q in inner_commands)

    if sum(t in ("<<", "<<-") for t in tokens) > heads:
        # 走査がヒアドキュメントと読まなかった `<<` が、トークンに出た。引用が `<<` だけの
        # 1 語（`grep -n "<<" f`）で、shlex からは演算子と区別が付かない。今までどおり
        # 閉じない本文として縮退する（ccnavi.md 12.2 の許容した誤検知）。
        return Reading(degraded=True, reason=REASON_UNTERMINATED, rewrites=rewrites), runnable

    why = _classify(commands)
    if why:
        return Reading(degraded=True, reason=why, rewrites=rewrites), runnable

    # 中身は外側の後ろにつなぐ。置換のあった位置で挟むと外側のコマンドが 2 本に割れ、
    # `find $(pwd) -name x -delete` の -delete が find と別のコマンドに見える。
    # 後ろに置けば、`[^\x00]*` で「同じコマンドの中」を見るルールが外側を丸ごと見られ、
    # 中身は `\x00` の直後に立つので `(^|\x00)` のルールもそのまま当たる。
    texts = [_render(commands)]
    bares = [texts[0]]
    # 移った先から見た綴りは、外側のコマンドと、切り出した中身の両方から集める。
    # 中身は別の読みなので外側の行き先は届かないが、中身の中で `cd` した先は届く
    # （`echo "$(cd .claude && rm settings.json)"`）。
    moveds = [_render(_moved_layers(commands, moved_tokens, tokens))]
    for inner, quoted in inners:
        if inner.degraded:
            # 中身が 1 つでも読めなければ全体を読めないとする。複合コマンドで 1 区間が
            # 読めないときと同じ扱い。理由は中身のものを返す。
            return Reading(degraded=True, reason=inner.reason, rewrites=rewrites), runnable
        texts.append(inner.text)
        moveds.append(inner.moved)
        if not quoted:
            bares.append(inner.bare)
    reading = Reading(
        text=SEP.join(t for t in texts if t),
        bare=SEP.join(t for t in bares if t),
        moved=SEP.join(m for m in moveds if m),
        rewrites=rewrites,
    )
    return reading, runnable


def _stopped(e: _Unreadable) -> Reading:
    """走査が止めた読み。止めたのが書き直しを求める形なら、それも並べる。"""
    form = _FORM_OF_REASON.get(e.reason)
    rewrites = [(form, e.form)] if form and e.form else []
    return Reading(degraded=True, reason=e.reason, rewrites=rewrites)


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
    区切り記号と同じ綴りだから。`echo $((1 << 2))` の `<<` を残すと、走査が
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


def _split_commands(tokens: list[str]) -> list[list[str]]:
    """区切り記号でトークン列を切り、コマンド 1 本ずつのリストにする。"""
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


def _resolve_cd(
    tokens: list[str], places: list[tuple[list[str], str | None]] | None = None
) -> list[str]:
    """`cd` の行き先を、後ろのコマンドの引数の綴りに継ぎ足したトークン列を返す。

    継ぎ足すものが無ければ、元のリストをそのまま返す（呼び手はそれを見て、移った先の
    組み立てを丸ごと飛ばす）。行き先が読めなくなったら、そこから先は継ぎ足さない。
    読みそのものは変えないので、いま当たっているものは当たったまま。

    区切りで居場所の続き方が変わる。サブシェル `( )` は出入りで戻し、`|` `|&` `&` の
    左はサブシェルなので、そこで移っても右と後ろには効かない。`case` の枝の `)` は
    サブシェルの閉じではないので、`case` から `esac` までは読まない（取りこぼす側）。

    places を渡すと、コマンド 1 本ずつの（語の並び, そのコマンドが居る場所）を足していく。
    居る場所は読みの起点から見た綴りで、起点そのものは空文字、読めなければ None。
    """
    out: list[str] = []
    segment: list[str] = []
    here: str | None = ""
    # いまの並び（`;` `&&` `||` で区切られる単位）の頭で居た場所。
    start: str | None = ""
    stack: list[str | None] = []
    in_case = 0
    budget = [MOVED_WORDS]
    for token in tokens:
        if token not in _OPERATORS:
            segment.append(token)
            continue
        here, in_case = _flush(segment, here, out, in_case, budget, places)
        segment = []
        out.append(token)
        if in_case:
            continue
        if token in _FORKED:
            here = start
        elif token == "(":
            stack.append(here)
            start = here
        elif token == ")":
            here = stack.pop() if stack else here
            start = here
        else:
            start = here
    _flush(segment, here, out, in_case, budget, places)
    return out if out != tokens else tokens


def _flush(
    segment: list[str],
    here: str | None,
    out: list[str],
    in_case: int,
    budget: list[int],
    places: list[tuple[list[str], str | None]] | None = None,
) -> tuple[str | None, int]:
    """コマンド 1 本を out に足し、次のコマンドが居る場所と `case` の深さを返す。"""
    if segment[:1] == ["case"]:
        in_case += 1
    elif segment[:1] == ["esac"] and in_case:
        in_case -= 1
    if places is not None and segment:
        places.append((list(segment), None if in_case else here))
    return _chdir(segment, None if in_case else here, out, budget), in_case


def _moved(commands: list[list[str]], resolved: list[list[str]]) -> list[list[str]]:
    """移った先から見た綴りが、書かれた綴りと変わったコマンドだけ。

    語の数は変わらないので並びは 1 対 1 で対応する。対応しなければ何も返さない。
    継ぎ足した綴りは当てる先を増やすためのもので、数が合わないまま並べると、
    どのコマンドのことを言っているのか分からない文面になる。
    """
    if len(commands) != len(resolved):
        return []
    return [after for before, after in zip(commands, resolved, strict=True) if before != after]


def _moved_layers(
    commands: list[list[str]], moved_tokens: list[str], tokens: list[str]
) -> list[list[str]]:
    """移った先から見たコマンドと、その中で実行されるコマンドの層。

    層まで並べるのは、止める側のルールの多くが名前をコマンドの頭に固定して書かれて
    いるから。`cd .claude && sudo rm settings.json` の移った先は
    `sudo rm .claude/settings.json` で、`(^|\x00)rm` はそこに当たらない。書かれた綴りと
    同じように 1 枚ずつ外した層（ADR-0045）も並べて、はじめて当たる。
    """
    if moved_tokens is tokens:
        return []
    moved = _moved(commands, _split_commands(moved_tokens))
    layers = _unwrap([(command, False) for command in moved], [], [])
    return moved + [command for _, command, _ in layers]


def _chdir(segment: list[str], here: str | None, out: list[str], budget: list[int]) -> str | None:
    """コマンド 1 本を、行き先を継ぎ足した形で out に足す。

    返すのは、次のコマンドが居る場所。読めなければ None で、そこから先は継ぎ足さない。
    """
    if not segment:
        return here
    # 予約語はそれだけで 1 本に切られる語なので、後ろがコマンドの先頭になる
    # （`then rm x`）。`for` `case` `select` の後ろの 1 本は変数名と調べる値で、
    # パスではない。
    i = 0
    while i < len(segment) and segment[i] in _RESERVED:
        out.append(segment[i])
        if segment[i] in _TAKES_A_WORD:
            out.extend(segment[i + 1 :])
            return here
        i += 1
    rest = segment[i:]
    if not rest:
        return here

    k = _name_index(rest)
    # `builtin cd x`・`command cd x` は `cd` そのもの。`command -v cd` は探すだけなので、
    # オプションが続く形では飛ばさない。
    while k + 1 < len(rest) and _base(rest[k]) in _RUNS_BUILTIN and rest[k + 1][:1] != "-":
        k += 1
    name = _base(rest[k]) if k < len(rest) else ""

    if name == "cd":
        at = _destination(rest, k)
        if at < 0:
            out.extend(rest)
            return None
        # 行き先の語そのものは書かれた綴りのまま残す。`cd` は書き込みではないので、
        # 当てる先に並べても止めるものが無く、`cd a && cd b && …` のぶんだけ当てる先が
        # 増えるだけになる。移った先は here が持つ。
        out.extend(rest)
        return _target(here, rest[at])
    if name in _PUSHD:
        out.extend(rest)
        return None
    if here is None or budget[0] <= 0 or _classify([rest]):
        # 行き先が読めない、語数の上限を越えた、または文字列をコードとして実行する形
        # （`sh -c` `eval` `xargs` `find -exec`）。最後のものは、どの語がパスかが綴りに
        # 無い。この読み全体が縮退するので、判定は生の文字列で下る。
        out.extend(rest)
        return here
    # 居場所が動いていなくても、`env -C <パス>` はその 1 本だけ動かす。
    at, inside = _inside(rest, k, here)
    if not inside:
        out.extend(rest)
        return here
    budget[0] -= len(rest)
    out.extend(_with_here(rest, at, inside))
    return here


def _inside(rest: list[str], k: int, here: str) -> tuple[int, str | None]:
    """実行役のコマンドの中で実行されるコマンドの位置と、そこでの居場所。

    `env rm x` の `rm` がどの語に立つかは、オプションの読み方で決まる。名前に継ぎ足すと
    意味の無い層になるので、`_peel` と同じ読み方で位置を出す。中で実行されるコマンドは
    元の語の並びの後ろ側そのものなので、語の数の差がそのまま位置になる。

    `env -C <パス>` と `sudo --chdir=<パス>` は、その 1 本だけ居場所を移す。綴りから
    決まらなければ None を返す（その 1 本には継ぎ足さない）。
    """
    depth = 0
    while k < len(rest) and depth < UNWRAP_DEPTH:
        name = _base(rest[k])
        if name not in _RUNNERS:
            break
        args = rest[k + 1 :]
        moved = _moves_to(name, args, here)
        if moved is None:
            return k, None
        here = moved
        inner = _runner_command(name, args)
        if not inner or not inner[0]:
            break
        k = len(rest) - len(inner[0])
        depth += 1
    return k, here


def _moves_to(name: str, args: list[str], here: str) -> str | None:
    """実行役のコマンドのオプションが移す先。移さないなら今の居場所のまま。"""
    options = _MOVES_TO.get(name)
    if not options:
        return here
    value_options, _ = _RUNNERS[name]
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--" or token[:1] != "-" or token == "-":
            break
        width = _option_width(token, args[i + 1 :], value_options)
        head, _, value = token.partition("=")
        if token in options and width == 2:
            value = args[i + 1]
        elif not (head in options and value):
            value = ""
        if value:
            return _target(here, value)
        i += width
    return here


def _with_here(rest: list[str], inner: int, here: str) -> list[str]:
    """コマンド 1 本の引数に、いま居る場所を継ぎ足す。

    継ぎ足すのは、リダイレクトの行き先（コマンドの位置の前に書いたものも）と、
    中で実行されるコマンドの名前より後ろの語。名前そのもの、前に置いた代入、
    実行役のコマンドとそのオプション、`-` で始まる語、絶対パス、そして `2>&1` のように
    ファイルではない fd の番号には継ぎ足さない。
    """
    out: list[str] = []
    j = 0
    while j < len(rest):
        width = _redirect_width(rest, j)
        if not width:
            out.append(_under(here, rest[j]) if j > inner else rest[j])
            j += 1
            continue
        # リダイレクトは `fd? 演算子 行き先` の塊。パスなのは行き先だけで、
        # `>&` `<&` のように `&` で閉じる演算子の後ろは fd の番号。
        end = min(j + width, len(rest))
        target = j + width - 1
        for m in range(j, end):
            takes_path = m == target and not rest[target - 1].endswith("&")
            out.append(_under(here, rest[m]) if takes_path else rest[m])
        j = end
    return out


def _destination(rest: list[str], k: int) -> int:
    """`cd` の行き先の語の位置。読み切れなければ -1。

    読み切れないのは、引数なし（`$HOME` へ移る）、`cd -`（直前の場所）、`cd old new`
    （綴りの置き換え）、一覧に無いオプション、先頭の `~`、変数・置換・グロブを含む綴り。
    """
    where: list[int] = []
    j = k + 1
    while j < len(rest):
        width = _redirect_width(rest, j)
        if width:
            j += width
            continue
        where.append(j)
        j += 1
    i = 0
    while i < len(where) and rest[where[i]] in _CD_OPTIONS:
        i += 1
    if i < len(where) and rest[where[i]] == "--":
        i += 1
    where = where[i:]
    if len(where) != 1:
        return -1
    target = rest[where[0]]
    if not target or target.startswith(("-", "~")) or _PATH_EXPANDS.search(target):
        return -1
    return where[0]


def _target(here: str | None, word: str) -> str | None:
    """移る先を、読みの起点から見た綴りにする。決まらなければ None。"""
    if _PATH_EXPANDS.search(word) or word.startswith("~"):
        return None
    if _ABSOLUTE.match(word):
        return _capped(_normal(word))
    if here is None:
        return None
    return _capped(_normal(here + "/" + word if here else word))


def _capped(path: str) -> str | None:
    """長すぎる居場所は持たない。上限は _HERE_LIMIT。"""
    return None if len(path) > _HERE_LIMIT else path


def _under(here: str, word: str) -> str:
    """いま居る場所から見た綴りを、読みの起点から見た綴りに直す。

    起点は、元のコマンドが打たれた場所。絶対パスと `-` で始まる語はそのまま返す。
    `dd of=x` のように値にパスを取る語は、名前を残して値だけ継ぎ足す。
    """
    if not here or not word:
        return word
    if _ABSOLUTE.match(word) or word.startswith("-"):
        return word
    if _ASSIGNMENT_WORD.match(word):
        head, _, value = word.partition("=")
        if not value or value.startswith("-") or _ABSOLUTE.match(value):
            return word
        return head + "=" + _normal(here + "/" + value)
    return _normal(here + "/" + word)


def _normal(path: str) -> str:
    """`.` と `..` を畳む。区切りはどちらの綴りも受け、`/` で返す。

    先頭の区切り（絶対パス）と末尾の区切り（ディレクトリと書いた形）は残す。末尾を
    落とすと、名前がそこで終わる形に当てる守り（`_END`）の当たり方が変わる。
    起点より上に出る `..` は畳まずに残す。
    """
    parts: list[str] = []
    for part in re.split(r"[\\/]", path):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part)
    lead = "/" if path[:1] in ("/", "\\") else ""
    tail = "/" if path[-1:] in ("/", "\\") and parts else ""
    return (lead + "/".join(parts) or lead or ".") + tail


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


def _with_time(commands: list[list[str]]) -> list[list[str]]:
    """層を作る先の並び。予約語として 1 本に切った `time` を、後ろのコマンドとつなぎ直す。

    _split_commands は予約語を 1 本にするので、`time -f %e rm x` は `time` と
    `-f %e rm x` の 2 本になり、`rm x` がどちらのコマンドの先頭にも立たない。`time` を
    実行役のコマンドとして外すために、層を作るときだけつなぐ。後ろのコマンドもそのまま
    残すので、つなぎ違えても層が増えるだけで、止める側に倒れる。
    """
    out: list[list[str]] = []
    for k, command in enumerate(commands):
        if command == ["time"] and k + 1 < len(commands):
            out.append(["time", *commands[k + 1]])
        else:
            out.append(command)
    return out


def _expanded_names(commands: list[list[str]]) -> list[str]:
    """コマンドの位置に、実行するときにシェルが決める語があれば並べる。"""
    found: list[str] = []
    # 並べた綴りの、パスを落とした形。`/usr/bin/gi?` は、パスを落とした層にも `gi?` として立つ。
    # 同じものを 2 度並べない。コマンドを数万本並べても線形で済むよう、集合で引く。
    seen: set[str] = set()
    previous: list[str] = []
    for command in commands:
        name = _command_name(command)
        if len(previous) == 1 and previous[0] in _TAKES_A_WORD:
            name = ""
        if name and _NAME_EXPANDS.search(name) and name not in seen:
            found.append(name)
            seen.update((name, _base(name)))
        previous = command
    return found


def _command_name(command: list[str]) -> str:
    """コマンドの位置の語。前に置いた代入とリダイレクト（`FOO=1 >/dev/null cmd`）を飛ばす。"""
    i = _name_index(command)
    return command[i] if i < len(command) else ""


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
    """
    fd = command[i].isdigit() or _NAMED_FD.fullmatch(command[i])
    k = i + 1 if fd and i + 1 < len(command) else i
    return k - i + 2 if _is_operator(command[k]) else 0


def _unwrap(
    commands: list[tuple[list[str], bool]],
    rewrites: list[tuple[str, str]],
    marks: list[bool],
) -> list[tuple[str, list[str], bool]]:
    """全コマンドの層を、コマンドの順・外側から内側の順に並べる。

    1 つずつが（実行役のコマンドの名前, 中で実行されるコマンド, 引用の中から切り出したか）。
    読み直した文字列の中で見つけた、書き直しを求める形は rewrites に足す。marks には層と同じ
    並びで、`eval` か `sh -c` の文字列を読み直した層（とその内側）かどうかを足す。
    """
    out: list[tuple[str, list[str], bool]] = []
    budget = [UNWRAP_WORDS]
    for command, quoted in commands:
        _layers(command, 0, out, budget, quoted, rewrites, marks, False)
    return out


def _layers(
    command: list[str],
    depth: int,
    out: list[tuple[str, list[str], bool]],
    budget: list[int],
    quoted: bool,
    rewrites: list[tuple[str, str]],
    marks: list[bool],
    reread: bool,
) -> None:
    """1 本のコマンドから、実行役のコマンドを 1 枚ずつ外した層を out に足す。

    途中の層も残す。`env sh …approve.sh` の `sh …approve.sh` の層に、承認のルールの
    `sh` から始まる枝が当たる。
    """
    if depth >= UNWRAP_DEPTH:
        return
    runner, inners, further = _peel(command, rewrites)
    # シェルの層をさらに外してよいのは `-c` の文字列を読み直したときだけ。
    reread = reread or runner == "eval" or (runner in _SHELLS and further)
    for inner in inners:
        budget[0] -= len(inner)
        if budget[0] < 0:
            return
        out.append((runner, inner, quoted))
        marks.append(reread)
        if further:
            _layers(inner, depth + 1, out, budget, quoted, rewrites, marks, reread)


def _peel(command: list[str], rewrites: list[tuple[str, str]]) -> tuple[str, list[list[str]], bool]:
    """実行役のコマンドを 1 枚だけ外す。

    返すのは、外した実行役のコマンドの名前、中で実行されるコマンドの並び（無ければ空）、
    その中をさらに外してよいか。シェルと `.` に渡したファイルはスクリプトであって
    コマンドの名前ではないので、その先は外さない。
    """
    if not command:
        return "", [], False
    head = command[0]

    # `>/dev/null env rm x`。前に置いたリダイレクトはコマンドではない。外さないと、
    # 実行役のコマンドが先頭に立たず、中のコマンドもコマンド名も見えなくなる。
    k = 0
    while k < len(command) and _redirect_width(command, k):
        k += _redirect_width(command, k)
    if k:
        rest = command[k:]
        return " ".join(command[:k]), [rest] if rest else [], True

    # `FOO=1 rm x`。代入はコマンドではない。
    if _ASSIGNMENT_WORD.match(head):
        i = 0
        while i < len(command) and _ASSIGNMENT_WORD.match(command[i]):
            i += 1
        rest = command[i:]
        return head, [rest] if rest else [], True

    # `/bin/sh x`・`git.exe x`。どのプログラムを指すかを変えない部分を落とした名前で読む。
    name = _base(head)
    if name != head and name:
        return head, [[name, *command[1:]]], True

    # find の述語の `;` は `\;` と書くが、shlex は区切り記号の `;` と同じ綴りで返すので、
    # 2 つめの `-exec` からは別のコマンドの頭に来る。そこも find の続きとして読む。
    if name == "find" or name in _FIND_EXEC:
        return "find", _find_commands(command), True
    if name in _RUNNERS:
        return name, _runner_command(name, command[1:]), True
    if name in _SHELLS:
        return name, *_shell_commands(command[1:], rewrites)
    if name in _SOURCES:
        rest = command[1:]
        return name, [rest] if rest else [], False
    if name == "eval":
        return name, _reread(" ".join(command[1:]), rewrites), True
    if name == "xargs":
        rest = _skip_options(command[1:], _XARGS_VALUE_OPTIONS)
        return name, [rest] if rest else [], True
    return "", [], False


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


def _shell_commands(
    args: list[str], rewrites: list[tuple[str, str]]
) -> tuple[list[list[str]], bool]:
    """`sh <ファイル> <引数>` ならファイルと引数、`sh -c '<文字列>'` なら読み直したコマンド。"""
    takes_code = False
    from_stdin = False
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            i += 1
            break
        if token in _SHELL_VALUE_OPTIONS:
            i += 2
            continue
        if token[:1] in ("-", "+") and len(token) > 1:
            if not token.startswith("--"):
                takes_code = takes_code or "c" in token
                from_stdin = from_stdin or "s" in token
            i += 1
            continue
        break
    rest = args[i:]
    if not rest:
        return [], False
    if takes_code:
        return _reread(rest[0], rewrites), True
    if from_stdin:
        # `sh -s a b` は標準入力を読み、後ろの語は引数。
        return [], False
    return [rest], False


def _find_commands(command: list[str]) -> list[list[str]]:
    """find の `-exec` の類の後ろの、`;` か `+` までのコマンド。複数あればそれぞれ。"""
    out: list[list[str]] = []
    i = 0
    while i < len(command):
        if command[i] not in _FIND_EXEC:
            i += 1
            continue
        j = i + 1
        while j < len(command) and command[j] not in (";", "+"):
            j += 1
        if command[i + 1 : j]:
            out.append(command[i + 1 : j])
        i = j + 1
    return out


def _reread(src: str, rewrites: list[tuple[str, str]]) -> list[list[str]]:
    """`sh -c` と `eval` に渡った文字列を、コマンドとして読み直す。

    外側と同じ走査で読むので、文字列の中のコマンド置換の中身も並ぶ。
    トークンに割れなければ層を作らない。中で見つけた、書き直しを求める形は rewrites に足す。
    """
    reading, commands = _read(src, 0)
    rewrites.extend(reading.rewrites)
    return [command for command, _ in commands]


def _render_command(command: list[str]) -> str:
    """コマンド 1 本を、ルールを当てる形に組み直す。"""
    return " ".join(_join(token) for token in command)


def _render(commands: list[list[str]]) -> str:
    """ルールを当てる先の文字列に組み直す。

    トークンの中に空白があるなら、それは引用が置いた空白。引用されていない空白は
    shlex が最初に割った場所だから。そこを置き換えることが、grep 'git push' を
    「コマンドとその引数」として読ませないことにあたる。

    同じことを演算子の文字にもする。語の中の `>` は書かれた文字であって、
    リダイレクトではない。ここで両側に目印を置かないと、`grep -n "x>" f` が
    `x > f` と同じ形になり、書き込み先を見るルールが読み手に当たる。
    """
    return SEP.join(" ".join(_join(token) for token in command) for command in commands if command)


def _join(token: str) -> str:
    """1 つのトークンを、ルールを当てる形に直す。

    演算子のトークンはそのまま。それ以外は語なので、中の空白を目印に置き換え、
    中の演算子の文字を目印で挟む。
    """
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
    """演算子のトークンかどうか。punctuation_chars が返す塊は
    この文字だけでできている。"""
    return bool(token) and all(c in _PUNCTUATION for c in token)
