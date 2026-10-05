"""コマンド文字列を、シェルが割るとおりに読む。

原文の走査は `shellread_scan`、語の見分けは `shellread_words`、`cd` の追跡は `shellread_cd` に
分けてある。どれも shellread を読まない。

ルールを当てる先を「文字列にその語が含まれるか」から「コマンドとして実行されるか」
に変えるための層。

生の文字列を探すガードは "git push origin main" を
止めるが、それを grep するコマンドも、echo するコマンドも、それを説明する
ヒアドキュメントの本文も同じように止める。返る拒否は本物の拒否と区別が付かないので、
語を書いただけの読み手は「禁止された操作をした」と言われて次に何をすればよいか分からなくなる。
ガードについて書く作業が、いちばんガードに引っかかる。

逆向きの穴もある。`grep -n "$(git push)" f` の `$( )` は二重引用の中でもシェルが
実行するが、shlex は引用の中を 1 語として返すので、中身がコマンドとして読まれない。
改行、プロセス置換 `<( )`、語の途中の `#` も、shlex はシェルと違う読み方をする。
どれも読み違えたときに止まらずに通る向きで、allow が後ろのコマンドまで通してしまう。

だから読みは 2 段にしてある。先に原文を 1 回走査して（_Scanner）、引用の状態を
自分で持ったまま、シェルが実行するのに shlex が見ないもの（コマンド置換、
プロセス置換、ヒアドキュメントの本文、コメント、改行）を片付ける。書き直す方法が必ずあって、
読み分けると規則が増えるか、読み違えると止まらずに通る形（バッククォート、ブレース展開、
実行するときに決まるコマンド名、シェルによって読みが分かれる形）は、読み解かずに並べて判定が止める。
語の分割はそのあと shlex に任せる。引用の規則を 2 か所で持つことになるが、shlex の
状態機械は公開されておらず、中身をコピーすると Python の版によって動かなくなる
（wip/design/shellread-subst.md 1.1）。

仕事を文字列として受け取って実行するコマンドは、読み切れないものとして扱う。
その代わり、`env` `sudo` `sh -c` のような実行役のコマンドが中で実行するコマンドを、
層として別に並べる（下の「中で実行されるコマンド」）。判定はそれを止める側のルールに
だけ当てる。

完全なシェルパーサではないし、回避しようとする相手に対する境界でもない。
変数の値と alias は、シェルを実際に走らせない限りどうやっても分からない（変数をコマンド名に
使った形は止める）。
やるのは、普通の作業で書かれるコマンドについて、その語が実行されるのか
書かれただけなのかを判定すること。判定できないときはそう言って、
呼び出し側が生の文字列との一致に切り替えられるようにする。そちらが厳しい側の読み。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import shellread_cd, shellread_scan, shellread_words

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
# 実際のコマンドラインはこの文字を含められないので、入力の側がこの目印になりすますことはできない。
# read() が入力から取り除いて、その前提を保つ。
# `cd` で移った先から見たコマンドの層に付ける、実行役のコマンドの名前。
# 実行役のコマンドの一覧（_RUNNERS）に `cd` は無いので、この名前は重ならない。
MOVED = "cd"

SEP = "\x00"
WORD_SEP = "\x01"

# ---- 書き直しを求める形
#
# 書き直す方法が必ずあり、読み分けると規則が増えるか、読み違えると止まらずに通る形。読みはこれを
# 読み解かずに Reading.rewrites に（形, 表記）で並べ、判定がルールより先に止めて、形ごとの
# 書き直し方を案内する。
#
# 引用の外のブレース展開。`{git,push,origin,main}` は 1 語に見えるが、bash は
# `git push origin main` を実行する。広げ方はシェルによって分かれる（`{1..5..2}` と `${x:-{a,b}}` は
# zsh だけが広げ、`{01..03}` は bash 3.2 だけが 0 を落とす）。
FORM_BRACE = "brace-expansion"
# 実行するときにシェルが決めるコマンド名。変数（`$c push`）、置換（`$(echo git) push`）、
# グロブ（`/usr/bin/gi? push`）。どのプログラムが走るかが表記に無いので、どのルールも当たらない。
FORM_COMMAND_NAME = "command-name-expansion"
# バッククォート。二重引用の中でも実行される。`$( )` か単一引用で必ず書き直せる。
FORM_BACKQUOTE = "backquote"
# シェルによって読みが分かれる形（REASON_AMBIGUOUS_SUBST の形）と、作業で使わない予約語。
FORM_AMBIGUOUS = "ambiguous-form"

# 読みを止めた理由のうち、書き直しを求める形になるもの。
_FORM_OF_REASON = {
    shellread_scan.REASON_BACKQUOTE: FORM_BACKQUOTE,
    shellread_scan.REASON_AMBIGUOUS_SUBST: FORM_AMBIGUOUS,
}
# コマンドの位置に置かれると読みが分かれるか、エージェントの作業で使わない予約語。
# `coproc NAME cmd` は bash 4 以降が NAME を名前と読み、zsh は NAME を実行する。
# `select` は入力を待つ。
_AMBIGUOUS_RESERVED = frozenset({"coproc", "select"})

# 入れ子の深さの上限。越えたら読み切れないとして縮退する。実際の作業で
# 数段を超える入れ子は書かれないし、上限が無いと 1 本のコマンドで判定の期限を使い切れる。
_MAX_DEPTH = 16
# 1 回の読みで作る層の語数の上限。`eval` や `sh -c` の文字列は読み直すと語が増えるので、
# 深さだけでは抑えきれない。超えたら、そこから先の層は作らない。
UNWRAP_WORDS = 2000

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
_FIND_EXEC = frozenset(shellread_words._TAKES_CODE_FLAG["find"])


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
    # `cd` で移った先から見たコマンド。SEP でつないだもの。表記の変わったコマンドだけが並ぶ。
    # text には足さない。継ぎ足したパスで当たる形を増やすだけにして、いま当たっている形を
    # 動かさないため。呼び手は中で実行されるコマンドの層と同じに扱い、
    # 止める側のルール（deny と ask）にだけ当てる。degraded のときは空。
    moved: str = ""
    # 層ごとの、その層を実行する実行役のコマンドの名前。unwrapped と同じ順序。
    # 文面で「`env` が実行する `rm x`」と言うために持つ。
    runners: list[str] = field(default_factory=list)
    # 層ごとの、引用の中から切り出したコマンドから作った層かどうか。unwrapped と同じ順序。
    # 層は bare に当て直せないので、文面の断り（引用の中に当たった）はこれで決める。
    quoted_layers: list[bool] = field(default_factory=list)
    # 書き直しを求める形の（形, 表記）。形は FORM_* のどれか。書かれた順で、重なりは除く。
    # 置換の中身、`sh -c` と `eval` に渡った文字列、実行役のコマンドを外した層の中のものも含む。
    # degraded でも、見つけたものは並ぶ。判定はこれがあればルールより先に止める。
    rewrites: list[tuple[str, str]] = field(default_factory=list)

    @property
    def braces(self) -> list[str]:
        """ブレース展開の表記。"""
        return [text for form, text in self.rewrites if form == FORM_BRACE]


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
    # `env $c push` の `$c` と `sh $SCRIPT` の `$SCRIPT` は、層の先頭にしか現れない。
    # `eval` と `sh -c` の文字列を読み直した層は、外側が縮退していれば見ない。そのときは確認に
    # なるので、止めて書き直させると、書き直し先がファイルになって中身が見えなくなる。
    # 縮退していない（`FOO=1 eval "$c"`）なら確認になる保証が無いので、見る。
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
    """外側のコマンド 1 本ずつの（語のリスト, そのコマンドが居る場所）。

    居る場所は `cd` を追った先で、読みの起点（打たれた場所）から見たパス。起点そのものは
    空文字、読めなくなったら None（6.3.2 と同じ追い方）。置換の中身は並べない。
    読み切れない形（走査か shlex が止まる）なら空のリストを返す。呼び手は read() の
    degraded を先に見て、そちらで扱う。
    """
    src = _prepare(src)
    try:
        outer, _, _, _ = shellread_scan._scan(src)
        tokens = shellread_scan._tokenize(outer)
    except (shellread_scan._Unreadable, ValueError):
        return []
    places: list[tuple[list[str], str | None]] = []
    shellread_cd._resolve_cd(tokens, places)
    return places


def _prepare(src: str) -> str:
    """走査の前の下ごしらえ。目印の文字を空白に戻し、行継続を消す。

    改行の前のバックスラッシュはシェルの行継続で、2 文字とも消える。
    2 行に割ったコマンドは 1 行で書いたのと同じ語のリストになる。
    走査より先に消すのは、改行をコマンドの区切りに読ませないため。
    シングルクォートの中では 2 文字とも文字通りなのでそこでは取りこぼすが、
    誰も書かない表記だし、変わるのは引数の文字列であってどのコマンドが走るかではない。
    """
    src = src.replace(SEP, " ").replace(WORD_SEP, " ")
    return src.replace("\\\r\n", "").replace("\\\n", "")


def _read(src: str, depth: int) -> tuple[Reading, list[tuple[list[str], bool]]]:
    """読みと、層を作る先のコマンドのリストを返す。

    リストの 1 つずつは（コマンド, 引用の中から切り出したコマンドか）。外側のコマンドが先、
    切り出した中身のコマンドが後ろ。読み切れない形でも、トークンに割れる限り返す。
    """
    if depth > _MAX_DEPTH:
        return _stopped(
            shellread_scan._Unreadable(
                shellread_scan.REASON_AMBIGUOUS_SUBST, shellread_scan._TOO_DEEP
            )
        ), []
    src = _prepare(src)

    try:
        outer, found, heads, braces = shellread_scan._scan(src)
    except shellread_scan._Unreadable as e:
        return _stopped(e), []
    rewrites = [(FORM_BRACE, brace) for brace in braces]

    try:
        tokens = shellread_scan._tokenize(outer)
    except ValueError:
        # 閉じない引用符で shlex が投げる。走査は通ったのに shlex が閉じないと読むのは、
        # `$'…\'…'` のように 2 つの読みが分かれる形。読み切れないものとして扱う。
        return Reading(
            degraded=True, reason=shellread_scan.REASON_UNTERMINATED, rewrites=rewrites
        ), []

    # `cd` が移った先から見たパスを別に組む。元のトークン列は動かさない。
    moved_tokens = shellread_cd._resolve_cd(tokens)

    commands = _split_commands(tokens)
    # `for select in` と `case coproc in` の語は変数名と調べる値で、予約語ではない。
    previous: list[str] = []
    for command in commands:
        if (
            len(command) == 1
            and command[0] in _AMBIGUOUS_RESERVED
            and not (len(previous) == 1 and previous[0] in shellread_words._TAKES_A_WORD)
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
        # 閉じない本文として縮退する（設計 12.2 の許容した誤検知）。
        return Reading(
            degraded=True, reason=shellread_scan.REASON_UNTERMINATED, rewrites=rewrites
        ), runnable

    why = shellread_words._classify(commands)
    if why:
        return Reading(degraded=True, reason=why, rewrites=rewrites), runnable

    # 中身は外側の後ろにつなぐ。置換のあった位置で挟むと外側のコマンドが 2 本に分かれ、
    # `find $(pwd) -name x -delete` の -delete が find と別のコマンドに見える。
    # 後ろに置けば、`[^\x00]*` で「同じコマンドの中」を見るルールが外側を丸ごと見られ、
    # 中身は `\x00` の直後に来るので `(^|\x00)` のルールもそのまま当たる。
    texts = [_render(commands)]
    bares = [texts[0]]
    # 移った先から見たパスは、外側のコマンドと、切り出した中身の両方から集める。
    # 中身は別の読みなので外側の行き先は及ばないが、中身の中で `cd` した先は及ぶ
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


def _stopped(e: shellread_scan._Unreadable) -> Reading:
    """走査が止めた読み。止めたのが書き直しを求める形なら、それも並べる。"""
    form = _FORM_OF_REASON.get(e.reason)
    rewrites = [(form, e.form)] if form and e.form else []
    return Reading(degraded=True, reason=e.reason, rewrites=rewrites)


def _split_commands(tokens: list[str]) -> list[list[str]]:
    """区切り記号でトークン列を切り、コマンド 1 本ずつのリストにする。"""
    commands: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in shellread_words._OPERATORS:
            if current:
                commands.append(current)
                current = []
            continue
        if not current and token in shellread_words._RESERVED:
            # コマンドの位置に置かれた予約語は、それだけで 1 本にする。後ろの語を
            # コマンドの先頭として読ませるため（`then find . -delete`）。
            # 落とさないのは、`! grep x f` を `grep x f` と読んで allow に当てないため。
            commands.append([token])
            continue
        current.append(token)
    if current:
        commands.append(current)
    return commands


def _moved(commands: list[list[str]], resolved: list[list[str]]) -> list[list[str]]:
    """移った先から見たパスが、書かれたパスと変わったコマンドだけ。

    語の数は変わらないのでリストは 1 対 1 で対応する。対応しなければ何も返さない。
    継ぎ足したパスは当てる先を増やすためのもので、数が合わないまま並べると、
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
    `sudo rm .claude/settings.json` で、`(^|\x00)rm` はそこに当たらない。書かれた表記と
    同じように 1 枚ずつ外した層（実行役の中で実行されるコマンド）も並べて、はじめて当たる。
    """
    if moved_tokens is tokens:
        return []
    moved = _moved(commands, _split_commands(moved_tokens))
    layers = _unwrap([(command, False) for command in moved], [], [])
    return moved + [command for _, command, _ in layers]


def _with_time(commands: list[list[str]]) -> list[list[str]]:
    """層を作る先のリスト。予約語として 1 本に切った `time` を、後ろのコマンドとつなぎ直す。

    _split_commands は予約語を 1 本にするので、`time -f %e rm x` は `time` と
    `-f %e rm x` の 2 本になり、`rm x` がどちらのコマンドの先頭にも来ない。`time` を
    実行役のコマンドとして外すために、層を作るときだけつなぐ。後ろのコマンドもそのまま
    残すので、つなぎ違えても層が増えるだけで、止める側になる。
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
    # 並べた表記の、パスを落とした形。`/usr/bin/gi?` は、パスを落とした層にも `gi?` として現れる。
    # 同じものを 2 度並べない。コマンドを数万本並べても線形で済むよう、集合で引く。
    seen: set[str] = set()
    previous: list[str] = []
    for command in commands:
        name = _command_name(command)
        if len(previous) == 1 and previous[0] in shellread_words._TAKES_A_WORD:
            name = ""
        if name and shellread_cd._EXPANDS.search(name) and name not in seen:
            found.append(name)
            seen.update((name, shellread_words._base(name)))
        previous = command
    return found


def _command_name(command: list[str]) -> str:
    """コマンドの位置の語。前に置いた代入とリダイレクト（`FOO=1 >/dev/null cmd`）を飛ばす。"""
    i = shellread_words._name_index(command)
    return command[i] if i < len(command) else ""


def _unwrap(
    commands: list[tuple[list[str], bool]],
    rewrites: list[tuple[str, str]],
    marks: list[bool],
) -> list[tuple[str, list[str], bool]]:
    """全コマンドの層を、コマンドの順・外側から内側の順に並べる。

    1 つずつが（実行役のコマンドの名前, 中で実行されるコマンド, 引用の中から切り出したか）。
    読み直した文字列の中で見つけた、書き直しを求める形は rewrites に足す。marks には層と同じ
    順序で、`eval` か `sh -c` の文字列を読み直した層（とその内側）かどうかを足す。
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

    途中の層も残す。`env sh …agree.sh` の `sh …agree.sh` の層に、承認のルールの
    `sh` から始まる枝が当たる。
    """
    if depth >= shellread_words.UNWRAP_DEPTH:
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

    返すのは、外した実行役のコマンドの名前、中で実行されるコマンドのリスト（無ければ空）、
    その中をさらに外してよいか。シェルと `.` に渡したファイルはスクリプトであって
    コマンドの名前ではないので、その先は外さない。
    """
    if not command:
        return "", [], False
    head = command[0]

    # `>/dev/null env rm x`。前に置いたリダイレクトはコマンドではない。外さないと、
    # 実行役のコマンドが先頭に来ず、中のコマンドもコマンド名も見えなくなる。
    k = 0
    while k < len(command) and shellread_words._redirect_width(command, k):
        k += shellread_words._redirect_width(command, k)
    if k:
        rest = command[k:]
        return " ".join(command[:k]), [rest] if rest else [], True

    # `FOO=1 rm x`。代入はコマンドではない。
    if shellread_words._ASSIGNMENT_WORD.match(head):
        i = 0
        while i < len(command) and shellread_words._ASSIGNMENT_WORD.match(command[i]):
            i += 1
        rest = command[i:]
        return head, [rest] if rest else [], True

    # `/bin/sh x`・`git.exe x`。どのプログラムを指すかを変えない部分を落とした名前で読む。
    name = shellread_words._base(head)
    if name != head and name:
        return head, [[name, *command[1:]]], True

    # find の述語の `;` は `\;` と書くが、shlex は区切り記号の `;` と同じ表記で返すので、
    # 2 つめの `-exec` からは別のコマンドの頭に来る。そこも find の続きとして読む。
    if name == "find" or name in _FIND_EXEC:
        return "find", _find_commands(command), True
    if name in shellread_words._RUNNERS:
        return name, shellread_words._runner_command(name, command[1:]), True
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


def _skip_options(args: list[str], value_options: frozenset[str]) -> list[str]:
    """先頭のオプション（と値）を飛ばした残り。`--` はそこで終わる。"""
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            return args[i + 1 :]
        if not token.startswith("-") or token == "-":
            break
        i += shellread_words._option_width(token, args[i + 1 :], value_options)
    return args[i:]


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
    return SEP.join(_render_command(command) for command in commands if command)


def _join(token: str) -> str:
    """1 つのトークンを、ルールを当てる形に直す。

    演算子のトークンはそのまま。それ以外は語なので、中の空白を目印に置き換え、
    中の演算子の文字を目印で挟む。
    """
    if shellread_words._is_operator(token):
        return token
    out = []
    for c in token:
        if c.isspace():
            out.append(WORD_SEP)
        elif c in shellread_words._PUNCTUATION:
            out.append(WORD_SEP + c + WORD_SEP)
        else:
            out.append(c)
    return "".join(out)
