"""`cd` で移った先の追跡。移った先から見たコマンドを、層として並べる材料を作る。

shellread から分けた。shellread を読まない。
"""

from __future__ import annotations

import re

from . import shellread_words

# ---- `cd` が移った先
#
# 保護もルールも、当てる先は語の表記。`cd` で入ってから書くと、行き先のパスから
# ディレクトリの名前が消えるので当たらない（`cd .claude && echo x > settings.json`）。
# だから `cd` の行き先を読んで、後ろのコマンドの引数のパスに継ぎ足す。
#
# 継ぎ足したパスは text には入れず、当てる先として足すだけ（Reading.moved）。書かれた
# 表記を動かすと、コマンドの頭に名前を固定して書かれたルールが外れて、いま止まって
# いるものが止まらなくなる。同じ理由で、コマンドの位置の語には継ぎ足さない。
#
# 行き先が読めなくなったら、そこから先は継ぎ足さない。読みそのものは変えない。
# 縮退させると生の文字列で見ることになり、`cd - && rm -f <守られた場所>` のように
# 今は止まっている形が止まらなくなる（敵対的レビュー）。

# 行き先を表記に持たないもの。読めない行き先として扱う。
_PUSHD = frozenset({"pushd", "popd"})

# 後ろの語をそのまま実行する組み込みコマンド。`builtin cd x` と `command cd x` は
# `cd` そのものなので、1 語飛ばして読む。
_RUNS_BUILTIN = frozenset({"builtin", "command"})

# `cd` のオプション。どれも値を取らない。
_CD_OPTIONS = frozenset({"-L", "-P", "-e", "-@"})

# 中で実行されるコマンドの居場所だけを移す実行役のコマンドのオプション。
# 値は次の語か `=` の後ろ。まとめ書き（`-iC /tmp`）は読まない（継ぎ足さない側になる）。
_MOVES_TO = {"env": ("-C", "--chdir"), "sudo": ("-D", "--chdir")}

# 左がサブシェルになる区切り。`cd a | b` と `cd a & b` の `cd` は、右にも後ろにも影響しない。
_FORKED = frozenset({"|", "|&", "&"})

# 表記の中にあると、実行するときまでシェルが中身を決める文字。変数と置換（走査が `$` 1 文字に
# 置き換えたもの）、グロブの `*` `?` と閉じた `[…]`。行き先のパスとコマンド名の両方に使う。
# 行き先では先頭の `~` を別に見る。コマンド名では `[` だけの test コマンドと `[[` は当たらない。
_EXPANDS = re.compile(r"[$*?]|\[[^\]]*\]")

# 絶対パス。継ぎ足さずにそのまま使う。Windows のドライブ文字も絶対。
_ABSOLUTE = re.compile(r"[\\/]|[A-Za-z]:[\\/]")

# 居場所のパスの長さの上限。越えたら、そこから先は継ぎ足さない。`cd a` を数千回
# 並べると居場所が伸び、整理し直す時間が長さに比例するので全体が二乗になる。判定の期限
# （judge の 3 秒）は読み終えたあとに見るので、読みの中で伸びるものには自分で上限を置く
# （`$( )` の _MAX_DEPTH と同じ向き）。実際のパスはここまで深くならない。
_HERE_LIMIT = 256

# 1 回の読みで継ぎ足す語数の上限。越えたら、そこから先のコマンドには継ぎ足さない。
# 層の UNWRAP_WORDS と同じ役割で、当てる先の総量を抑える。
MOVED_WORDS = 2000


def _resolve_cd(
    tokens: list[str], places: list[tuple[list[str], str | None]] | None = None
) -> list[str]:
    """`cd` の行き先を、後ろのコマンドの引数のパスに継ぎ足したトークン列を返す。

    継ぎ足すものが無ければ、元のリストをそのまま返す（呼び手はそれを見て、移った先の
    組み立てを丸ごと飛ばす）。行き先が読めなくなったら、そこから先は継ぎ足さない。
    読みそのものは変えないので、いま当たっているものは当たったまま。

    区切りで居場所の続き方が変わる。サブシェル `( )` は出入りで戻し、`|` `|&` `&` の
    左はサブシェルなので、そこで移っても右と後ろには影響しない。`case` の枝の `)` は
    サブシェルの閉じではないので、`case` から `esac` までは読まない（取りこぼす側）。

    places を渡すと、コマンド 1 本ずつの（語のリスト, そのコマンドが居る場所）を足していく。
    居る場所は読みの起点から見たパスで、起点そのものは空文字、読めなければ None。
    """
    out: list[str] = []
    segment: list[str] = []
    here: str | None = ""
    # いまのコマンド列（`;` `&&` `||` で区切られる単位）の頭で居た場所。
    start: str | None = ""
    stack: list[str | None] = []
    in_case = 0
    budget = [MOVED_WORDS]
    for token in tokens:
        if token not in shellread_words._OPERATORS:
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
    while i < len(segment) and segment[i] in shellread_words._RESERVED:
        out.append(segment[i])
        if segment[i] in shellread_words._TAKES_A_WORD:
            out.extend(segment[i + 1 :])
            return here
        i += 1
    rest = segment[i:]
    if not rest:
        return here

    k = shellread_words._name_index(rest)
    # `builtin cd x`・`command cd x` は `cd` そのもの。`command -v cd` は探すだけなので、
    # オプションが続く形では飛ばさない。
    while (
        k + 1 < len(rest)
        and shellread_words._base(rest[k]) in _RUNS_BUILTIN
        and rest[k + 1][:1] != "-"
    ):
        k += 1
    name = shellread_words._base(rest[k]) if k < len(rest) else ""

    if name == "cd":
        at = _destination(rest, k)
        if at < 0:
            out.extend(rest)
            return None
        # 行き先の語そのものは書かれた表記のまま残す。`cd` は書き込みではないので、
        # 当てる先に並べても止めるものが無く、`cd a && cd b && …` のぶんだけ当てる先が
        # 増えるだけになる。移った先は here が持つ。
        out.extend(rest)
        return _target(here, rest[at])
    if name in _PUSHD:
        out.extend(rest)
        return None
    if here is None or budget[0] <= 0 or shellread_words._classify([rest]):
        # 行き先が読めない、語数の上限を越えた、または文字列をコードとして実行する形
        # （`sh -c` `eval` `xargs` `find -exec`）。最後のものは、どの語がパスかが表記に
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

    `env rm x` の `rm` がどの語に来るかは、オプションの読み方で決まる。名前に継ぎ足すと
    意味の無い層になるので、`_peel` と同じ読み方で位置を出す。中で実行されるコマンドは
    元の語のリストの後ろ側そのものなので、語の数の差がそのまま位置になる。

    `env -C <パス>` と `sudo --chdir=<パス>` は、その 1 本だけ居場所を移す。表記から
    決まらなければ None を返す（その 1 本には継ぎ足さない）。
    """
    depth = 0
    while k < len(rest) and depth < shellread_words.UNWRAP_DEPTH:
        name = shellread_words._base(rest[k])
        if name not in shellread_words._RUNNERS:
            break
        args = rest[k + 1 :]
        moved = _moves_to(name, args, here)
        if moved is None:
            return k, None
        here = moved
        inner = shellread_words._runner_command(name, args)
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
    value_options, _ = shellread_words._RUNNERS[name]
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--" or token[:1] != "-" or token == "-":
            break
        width = shellread_words._option_width(token, args[i + 1 :], value_options)
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
        width = shellread_words._redirect_width(rest, j)
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
    （表記の置き換え）、一覧に無いオプション、先頭の `~`、変数・置換・グロブを含む表記。
    """
    where: list[int] = []
    j = k + 1
    while j < len(rest):
        width = shellread_words._redirect_width(rest, j)
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
    if not target or target.startswith(("-", "~")) or _EXPANDS.search(target):
        return -1
    return where[0]


def _target(here: str | None, word: str) -> str | None:
    """移る先を、読みの起点から見たパスにする。決まらなければ None。"""
    if _EXPANDS.search(word) or word.startswith("~"):
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
    """いま居る場所から見たパスを、読みの起点から見たパスに直す。

    起点は、元のコマンドが打たれた場所。絶対パスと `-` で始まる語はそのまま返す。
    `dd of=x` のように値にパスを取る語は、名前を残して値だけ継ぎ足す。
    """
    if not here or not word:
        return word
    if _ABSOLUTE.match(word) or word.startswith("-"):
        return word
    if shellread_words._ASSIGNMENT_WORD.match(word):
        head, _, value = word.partition("=")
        if not value or value.startswith("-") or _ABSOLUTE.match(value):
            return word
        return head + "=" + _normal(here + "/" + value)
    return _normal(here + "/" + word)


def _normal(path: str) -> str:
    """`.` と `..` を整理する。区切りはどちらの表記も受け、`/` で返す。

    先頭の区切り（絶対パス）と末尾の区切り（ディレクトリと書いた形）は残す。末尾を
    除くと、名前がそこで終わる形に当てる保護（`_END`）の当たり方が変わる。
    起点より上に出る `..` は整理せずに残す。
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
