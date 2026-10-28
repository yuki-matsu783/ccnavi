"""判定を動かすルール集合の読み込み。

ルールは実行ファイルの外に置く。作り直さずに足せるようにするため。
ルールは自分の文面を持つ。これが「代わりの手段」を忘れさせない仕組みで、
ルールを 1 件足すことが、代わりに何をすべきかを書くことを強制する。

## 3 つのタイプ

ルールファイルは `deny` `ask` `allow` の 3 つに分かれる。どれも同じ形の
ルールを並べるだけで、置かれたタイプがその判定になる。

    version: 1
    deny:
      - id: guard-config
        match: Write|Edit
        glob: "*/.ccnavi/*"
        message: ガード自身の設定です。ユーザに依頼してください。
    ask:
      - id: migrations
        match: Write|Edit
        glob: "*/migrations/*"
        message: 移行ファイルは実行前にユーザが中身を見ます。
    allow:
      - id: source
        match: Read|Write|Edit
        glob: "*/ccnavi/*"

文面は `deny` と `ask` では必須。止めるなら代わりの手段を、聞くなら何を見て
判断すればよいかを、ルール自身が言わなければならない。`allow` では要らない。
通した呼び出しに返るのは `additionalContext` だけで、止めも聞きもしないので、
言うべき代わりの手段が無い。

## 強さ

同じ呼び出しに複数のタイプがヒットすることがある。強い順に `deny` `ask` `allow`。
1 件でも `deny` にヒットすれば拒否で、`ask` があれば確認、どちらも無く `allow` に
ヒットすれば許可になる。どこにもヒットしなければ、ccnavi は判定を持たず、
Claude Code の権限モードに従う（判定は cli.py）。

強い側を先に見るのは、緩める側のルールを 1 行足しただけで保護が消える形を
作らないため。`allow` は「まだ何も言われていない場所」に許可を置くもので、
`deny` に例外を作る手段ではない。

`allow` にヒットしても、Claude Code へ「許可」は返さない。判定を返すのは `deny` と
`ask` のときだけで（judge.refuse）、`allow` は記録に残して終わる。ヒットして変わるのは、
ccnavi 自身の確認（言及が無いときの `ask`）と拒否（`dontAsk` / `bypassPermissions`）を
起こさないことと、`additionalContext` を適用する先になること。Claude Code 側の権限の
扱いはルールファイルからは変えられない。

## 表記の大文字小文字

`glob` も `regex` も、どの機械でも大文字小文字を区別せずに適用する。`*/.ccnavi/*` は
`.Ccnavi/config/project/rules.yml` にもヒットする。ヒットの仕方が機械で変わると、
同じルールファイルが
ある機械では止め、別の機械では通す（tickets/config/risk.py と tickets/config/phasetypes.py の範囲も同じ）。
区別が要る `regex` は `(?-i:...)` で囲む。囲むのは区別したい部分だけでよく、
否定の文字クラス（`[^c]` のような除外）を持つルールは、区別せずに適用すると除外の側が
広がるのでそこを囲む。

## ワークスペースルートの合言葉

`{root}` はワークスペースルート（hook なら CLAUDE_PROJECT_DIR、ターミナルなら --root）の実パスに
読み込み時に置き換わる。「ワークスペースの下」を絶対パスの直書きなしに書くための
もので、Windows と WSL と Linux で表記が分かれない。glob なら `{root}/wip/*`、regex なら
`^{root}[\\/]` のように書く。ワークスペースルートが渡らない読み方をしたルールは error で名指しする。

## 除く（except）

ルールは省略できるキー `except:` を持てる。値はリストで、各要素は `glob` か `regex` の
どちらか 1 つだけを持つマッピング。本体の `glob` / `regex` にヒットしても、`except` の
どれかにヒットすれば、そのルールはヒットしない扱いになる。`match`（ツール名）の見方は変わらない。

    deny:
      - id: workspace-root
        match: Write|Edit|NotebookEdit
        regex: '^{root}[\\/]'
        except:
          - regex: '^{root}[\\/]wip[\\/]handoff(?:[\\/]|$)'
          - regex: '^{root}[\\/]scratchpad(?:[\\/]|$)'
          - regex: '^{root}[\\/]\\.claude[\\/]worktrees(?:[\\/]|$)'
        message: ...

先読みを禁じているので、「A だが B は除く」を 1 本の regex で書くと 1 文字ずつの
場合分けになる。`except` はそれを 2 本の式に分けて書くためのもの。各パターンには本体と
同じ読み方（`{root}` の置き換え、`{!root}` の扱い、大文字小文字の無視、先読み・後方参照の
禁止、glob の翻訳）を適用する。適用する文字列も本体と同じで、Bash ならコマンド列の全体。
コマンドのどこかに除く語を足すだけで外れるので、Bash の `deny` / `ask` に付けるときは
狭く書く（lint が warn で言う）。

空のリスト、マッピングでない要素、`glob` と `regex` の両方がある・どちらも無い要素、
知らないキーを持つ要素は、ルールごと読み込まずに error で名指しする。

## 組み合わせ条件（when）

本体は `glob` / `regex` の代わりに `when:` でも書ける。ルールが持つのは 3 つのうち
ちょうど 1 つ。`when` の値はノードで、ノードは次のどれか 1 つのキーだけを持つマッピング。

    glob: '<glob>'          個別条件。本体の glob と同じ読み方
    regex: '<regex>'        個別条件。本体の regex と同じ読み方
    all: [ノード, ...]       全部にヒットすればヒットする（1 個以上）
    any: [ノード, ...]       どれかにヒットすればヒットする（1 個以上）
    not: ノード              ヒットしなければヒットする

    deny:
      - id: secrets
        match: Write|Edit
        when:
          any:
            - glob: '*.pem'
            - all:
                - glob: '*/secrets/*'
                - not: {glob: '*.md'}
        message: ...

個別条件には本体と同じ読み方（`{root}`、`{!root}`、大文字小文字の無視、先読み・後方参照の禁止、
glob の翻訳）を適用し、適用する文字列も本体と同じ（Bash ならコマンド列の全体）。`except:` と
併せて書けて、意味は `all: [when, not: {any: except}]`。

判定が重くならないように、入れ子は 8 段、ノードは 64 個まで。知らないキー、キーが 0 個・
2 個以上のノード、空のリスト、型の違う値と合わせて、ルールごと読み込まずに error で名指しする。
個別条件の `{!root}` を組み立てられないときは、
本体と同じく `deny` / `ask` は `match` の全部に適用して
止め、`allow` はルールごと使わない。

「除く」だけなら `except` のほうが読みやすい。`when` は「A か B」「A かつ B」のように、
1 本の式に詰めると読めなくなる組み合わせのために使う。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import yaml

from ..infra import settings, yamlread
from ..infra.globmatch import translate

# このビルドが読めるルールファイルの書式の版。`deny` `ask` `allow` の 3 タイプで、探すものは
# `glob`（fnmatch の glob）か `regex`。
VERSION = 1

# 深刻度。ガードを無効にするものと、弱めるだけのものを分ける。
SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
# info は「そう書いてあるとおりに判定に使われているが、書いたユーザが知りたいはずのこと」。
# レイヤーをまたいで同じ定義が重複していて後ろを破棄した、がこれにあたる。
# warn と分けるのは、重複は普通の形（見本から始めたプロジェクト）で、これを warn に
# 混ぜると本当に緩んでいる warn が見落とされるため。
SEVERITY_INFO = "info"

# タイプの名前。強い順。cli.py の判定もこの順に見る。
DENY = "deny"
ASK = "ask"
ALLOW = "allow"
SECTIONS = (DENY, ASK, ALLOW)

# `match` に書ける、ツールではない名前。メインエージェントのターンの終わり（Stop）にヒットする。
# 見るのは `allow` だけで、渡す回（`every`）にだけ Stop を止めて文を渡す。
# 名前をイベント名と同じにしたのは、ツール名と取り違えないため。適用する先の文字列は
# 無いので固定の文字列に適用し、ルールは `glob: "*"` と書く。
STOP_MATCH = "Stop"
STOP_SUBJECT = "(stop)"

# 文面が要るタイプ。deny だけ。ask の文面はユーザの確認ダイアログにしか出ず、allow は
# 通すだけで届く先が無い（どちらも実際に動かして確かめた）。モデルに渡す文は
# additionalContext に書く。ask と allow に書いた文面は lint が error にするが、
# ここで破棄するとその文面のせいでルールごと外れて通ってしまうので、読み込みは通す。
_NEEDS_MESSAGE = (DENY,)

# Python の正規表現エンジンは後方参照と先読みを扱えるが、ルールファイルの契約は
# それを使わないことになっている。書ける範囲を狭いままにしておくと、ルールが
# エンジンをまたいでも同じ意味を保つ。加えて、この 2 つは組み合わせ爆発を起こしやすい
# 書き方でもあり、判定の途中で固まった hook は期限に達し、呼び出しは止められずに通る。
_UNSUPPORTED = (
    (r"(?=", "先読み"),
    (r"(?!", "否定先読み"),
    (r"(?<=", "後読み"),
    (r"(?<!", "否定後読み"),
)
_BACKREFERENCE = re.compile(r"\\[1-9]")

# ワークスペースルートを指す合言葉。glob と regex の中で使え、読み込み時にその実パスに置き換わる。
ROOT_PLACEHOLDER = "{root}"

# 「ワークスペースルートの外」を指す合言葉。`regex` にだけ書け、`^` の直後に 1 回だけ。
# 読み込み時に、先読みを使わない入れ子の式へ展開する（not_root_pattern）。
NOT_ROOT_PLACEHOLDER = "{!root}"

# 展開の最内と、各段の「ここで文字列が終わる」に置く表記。`$` ではなく `\Z` なのは、
# `$` が末尾の改行の直前にもヒットするため。言いたいのは「終わる」であって「行末」ではない。
_NOT_ROOT_END = r"\Z"

# ルートがこの長さ以上なら展開しない。Python 3.12（再帰の上限 1000）では、この形の
# 入れ子は 493 字で `re.compile` が `RecursionError` になる（tests/core/bench_not_root.py）。
# 半分以下で止めるのは、Python の版が変わって上限が動いたときに、気づかないうちに
# 処理が破綻しないようにするため。
# Windows の MAX_PATH は 260 なので、256 字のルートはその時点で実用にならない。
#
# 組み立てが失敗する長さは、展開の形で変わる。 1 文字あたりの入れ子の段数を変えたら、
# bench_not_root.py を回して測り直すこと。
MAX_ROOT_LEN = 256

# 生成できなかった `deny` / `ask` に持たせる式。どの文字列にもヒットするので、そのルールが
# 見るツールの呼び出しは全部止まる。保護が消えるより止まるほうがよい、という向きを
# 採るために使う。
_MATCH_EVERYTHING = "^"

# 生成した式に現れてはいけない書き方。`_unsupported` は繰り返しを見ないので、そこへ
# 掛け直すだけでは「繰り返しを含まない」ことを確かめられない。
#
# `?` は入れていない。生成した式は捕獲しないグループ `(?:` を使うので、`?` を量化子と
# 数えると式自身の構造にヒットしてしまう。ルートの文字は `re.escape` を通るため、
# `*` と `+` がルートの名前に含まれていても `\*` `\+` になり、この式にはヒットしない。
_GENERATED_QUANTIFIER = re.compile(r"(?<!\\)[*+]|(?<!\\)\{\d")

# `{!root}` の直後に続けてよい書き始め。生成した式が読み終える位置はパスの区切りとは
# 限らないので、任意の位置まで進む書き方で受けていないものは warn で伝える。
_SKIPS_ANYWHERE = re.compile(r"(?:\.[*+]|\[\\s\\S\][*+])")

# ルールの本体にヒットしても、ここに並べたどれかにヒットすればヒットしない扱いにするキー。
# 各要素は `glob` か `regex` のどちらか 1 つだけを持つマッピングで、`when:` の個別条件と同じ形。
# 書き方を 1 つにしておけば、except から when へ書き換えるときに個別条件をコピーするだけで済む。
EXCEPT_KEY = "except"
EXCEPT_FIELDS = ("glob", "regex")

# 組み合わせ条件を書くキーと、ノードに書けるキー。
# 個別条件は except の要素と同じ形（`glob` / `regex`）。
WHEN_KEY = "when"
WHEN_LEAVES = EXCEPT_FIELDS
WHEN_ALL = "all"
WHEN_ANY = "any"
WHEN_NOT = "not"
WHEN_KINDS = (*WHEN_LEAVES, WHEN_ALL, WHEN_ANY, WHEN_NOT)
# 入れ子の段数とノードの数の上限。判定は呼び出しのたびに組み合わせ条件をたどるので、書き方しだいで
# 重くならないように頭打ちにする。最上位のノードが 1 段目。
MAX_WHEN_DEPTH = 8
MAX_WHEN_NODES = 64

# レイヤーの名前と id の間に入る文字（`self:docs` / `lib:source`、ruleload.prefix_ids）。
# 書かれたままの id にこれが入っていると、レイヤーをつけた形と見分けが付かない。
# 名前の表記に 1 文字を予約するほうが、前置きの表記を別にするより安い。
ID_SEPARATOR = ":"

# 苦情の出どころ（`Problem.kind`）。「いまは通らないが、書いた側に直すものは無い」もの。
# 前のフェーズが閉じていない子がこれで、閉じれば同じ提案がそのまま通る。承認は通さないが、
# 全体を見る `--lint` は warn にする（提案 1 本で設定画面の保存と CI が止まらないように）。
KIND_NOT_YET = "not-yet"

# 組み込みルールの名前の頭（`builtin:` で始まる id は全部組み込み）。ルールファイルからは書けない
# （読み込まずに error）。
# 同じ id で書いたルールがあると組み込みを足さない形にすると、何にもヒットしない 1 本を
# その名前で書くだけで組み込みの保護を気づかれないうちに消せてしまう。ルールファイルの記述に依らず
# 足すのが組み込みの役目なので、置き換える経路をなくす。外したいなら env でその保護ごと切る。
RESERVED_ID_PREFIX = "builtin:"


def root_pattern(root: str) -> str:
    """ワークスペースルートの実パスを、regex に埋めて安全な形にする。

    区切りは `/` と `\\` のどちらにもヒットする形にする。適用する対象は行き着く先まで
    解いたパス（judge.full_path）で、Windows では `\\` になるが、ルールを書くユーザは
    `/` で考える。大文字小文字は式ごと区別せずに適用するので（_build）、ここでは機械を
    見ない。見ると `c:` と `C:` の扱いが機械で分かれる。
    """
    return "".join(_same(ch) for ch in real_root(root))


def root_glob(root: str) -> str:
    """ワークスペースルートの実パスを、glob に埋める形にする。

    区切りは `/` にそろえ、翻訳の側が両方に適用する。
    """
    return os.path.realpath(root).replace("\\", "/").rstrip("/")


def real_root(root: str) -> str:
    """ルートを、展開に使える形に正規化する。

    `os.path.realpath` で解き、末尾の区切りを除く。除かないと、最後の区切りを
    「同じ」の側で消費した直後に最内の段へ進み、ルート直下の普通のファイル名が「外」に
    なってしまう。Windows では `os.path.realpath('/')` が `C:\\` を返すので、ドライブ直下を
    ワークスペースにした環境は必ずこの場合になる。

    `root_pattern` / `root_glob` も同じ 2 つの処理をしている。`{!root}` だけ違う扱いにしない。
    """
    return os.path.realpath(root).rstrip("\\/")


def _differs(ch: str) -> str:
    """その 1 文字と一致しない文字にヒットする文字クラス。区切りは両方の書き方を除く。"""
    return r"[^\\/]" if ch in "\\/" else "[^" + re.escape(ch) + "]"


def _same(ch: str) -> str:
    """その 1 文字にヒットする式。区切りは `\\` と `/` のどちらにもヒットするようにする。"""
    return r"[\\/]" if ch in "\\/" else re.escape(ch)


def not_root_pattern(root: str) -> str:
    """「ワークスペースルートで始まらない」を、先読みを使わずに書いた式。

    ルートを 1 文字ずつ「ここで終わる（ルートより上）／ここが違う／ここは同じ」の
    入れ子に分解する。最内はルートを全部たどり終えた先で、そこに区切りでない 1 文字が
    続けば `…/ccnavi-fork/x` のような別のディレクトリなので「外」と数える。

        ^(?:\\Z|[^c]|c(?:\\Z|[^:]|:(?:\\Z|[^\\\\/]|[\\\\/](?:…))))

    繰り返しを 1 つも含まない。 各段の選択肢は最初の 1 文字でほぼ排他なので、
    戻る余地がほとんど無い。判定は 1 回 3.5 マイクロ秒で、
    ルートの長さに比例して伸びるだけ。

    列挙型（前置きを 1 文字ずつ伸ばして並べる形）を採らないのは、式の長さが
    ルートの長さの 2 乗で伸びるため。436 字のルートで 141,365 字・組み立て 481 ms に
    なり、判定の期限（3 秒）に達してしまう。

    引数には `real_root` で正規化したパスを渡す。`^` は呼ぶ側が書く。
    """
    # 最内に `\Z` を置いてはいけない。ここは「ルートを最後までたどり終えた」位置なので、
    # そこで文字列が終わるなら対象はルートそのもので、中と数えるのが正しい。
    # `\Z` を混ぜると、`Glob` を `path` 省略で呼んだとき（対象が cwd = ルートになる）
    # のような、いちばん普通の呼び出しが「外」と誤判定される。
    # 各段の `\Z` は「ルートより上」を拾うためのもので、こことは意味が違う。
    body = _differs("/")
    for ch in reversed(root):
        body = "(?:" + _NOT_ROOT_END + "|" + _differs(ch) + "|" + _same(ch) + body + ")"
    return body


# 文の差し込み `{script:<名前>}`。名前は `.ccnavi/scripts/` のファイル名。
SCRIPT_PLACEHOLDER = re.compile(r"\{script:([^{}]*)\}")
SCRIPT_NAME = re.compile(r"[A-Za-z0-9._-]+")


# `.ccnavi/scripts/` のヒット。直前の `./` と `../` の連なりは、相対の書き方なので手前にたどる。
_RELATIVE_SCRIPTS = re.compile(r"(?:\.{1,2}/)*\.ccnavi/scripts/")
# その手前の 1 文字がこれなら、ヒットはパスの途中（絶対パス・`$VAR/`・`{root}/`・`~/`・
# ディレクトリ名の続き）。空白・引用符・括弧・`=`・`:` や語頭は、パスの始まりとして相対に数える。
_PATH_MID = frozenset("/\\}~$._-")


def has_relative_scripts(text: str) -> bool:
    """文面に、cwd に左右される相対パスの `.ccnavi/scripts/` があるか。

    ヒットの直前（`./` と `../` の連なりを除いた位置）の 1 文字を見る。`/` `\\` `}` `~` `$`
    `.` 英数字 `_` `-` ならパスの途中（絶対パス、`{root}/`、`$VAR/`、`~/` など）なので言わない。
    語頭や、空白・引用符・括弧・`=`・`:` の直後なら相対パスの始まりとして言う。
    """
    for hit in _RELATIVE_SCRIPTS.finditer(text):
        before = text[hit.start() - 1] if hit.start() > 0 else ""
        if before and (before.isascii() and before.isalnum() or before in _PATH_MID):
            continue
        return True
    return False


def fill_text(text: str, root: str) -> str:
    """モデルへ渡す文の差し込みを置き換える。

    - `{root}` は、これまでどおりワークスペースルートの実パス（引用しない）。
    - `{script:<名前>}` は `settings.script_command(root, <名前>)`（`sh <引用したパス>`）。
      `<名前>` は `[A-Za-z0-9._-]+` だけを受ける。形が不正なものは置き換えずに残す
      （`--lint` が warn を出す）。スクリプトが無くても置き換える（そのままでは実行できないので、
      これも lint が warn を出す）。

    ルートが渡らない読み方（診断で `--root` が無い）では、どちらも置き換えずに返す。
    """
    if not root:
        return text

    def script(found: re.Match) -> str:
        name = found.group(1)
        if not SCRIPT_NAME.fullmatch(name):
            return found.group(0)
        return settings.script_command(root, name)

    return fill_root(SCRIPT_PLACEHOLDER.sub(script, text), root)


def fill_root(text: str, root: str) -> str:
    """モデルへ渡す文の `{root}` を、ワークスペースルートの実パスに置き換える。

    文面で sh を案内するときは `{root}` から書く（スクリプトはワークスペースにしか無く、
    ワークツリーやプロジェクトの中からは相対パスが届かない）。パスは glob に埋めるのと
    同じ形で、区切りは `/`。ルートが渡らない読み方（診断で `--root` が無い）では
    置き換えずにそのまま返す。
    """
    if not root or ROOT_PLACEHOLDER not in text:
        return text
    return text.replace(ROOT_PLACEHOLDER, root_glob(root))


# every を書かなかったときの刻み。ヒットするたびが渡す回になる。
EVERY_DEFAULT = 1


def readable_every(written: object) -> int:
    """書かれた `every` から読み取れる刻み。刻みとして読めない値なら 0。

    読めるのは 1 以上の整数だけ。`0` と負は刻みにならず、`"5"` のような文字列は
    YAML が数として読まなかった値（引用符を書いた）なので、こちらも読まない。
    真偽値は `True == 1` で整数として通ってしまうので、先に外す。

    読めなかったときに何をするかは呼ぶ側が決める。判定（_build）は 1 として扱って
    毎回渡し、--lint は error にして名指しする。同じ「読めるか」を 2 か所で
    別々に書くと、何も言われずに無視される値と指摘される値が食い違う。
    """
    if written is None:
        return EVERY_DEFAULT
    if isinstance(written, bool) or not isinstance(written, int) or written < 1:
        return 0
    return written


@dataclass
class Problem:
    """ルールファイルへの苦情 1 件。直せるように名指しする。"""

    severity: str
    rule: str
    detail: str
    # 苦情の出どころ。既定は空で、どこから来たかを問わない苦情。`KIND_NOT_YET` は
    # 「いまは通らないが、書いた側に直すものは無い」を表す（フェーズの順序がこれ）。
    # 読み手によって重さを変えたいのはここだけなので、種類は 1 つしか無い。
    kind: str = ""

    def __str__(self) -> str:
        return f"{self.severity}: {self.rule or '(file)'}: {self.detail}"


@dataclass
class Exclusion:
    """ルールの `except:` の 1 件。本体と同じ読み方で組み立てた式を持つ。"""

    glob: str = ""
    regex: str = ""
    compiled: re.Pattern | None = None

    def written(self) -> dict:
        """書いた表記に戻した形。`{"glob": ...}` か `{"regex": ...}`。"""
        return {"glob": self.glob} if self.glob else {"regex": self.regex}


@dataclass
class Condition:
    """`when:` のノード 1 つ。個別条件（glob / regex）か、all / any / not。

    個別条件は本体と同じ読み方で組み立てた式を持つ。`where` は苦情と lint で名指しするための
    位置（`when.any[1].not` のような形）で、比較（key）と書き戻し（written）には入れない。
    """

    kind: str
    text: str = ""
    children: list[Condition] = field(default_factory=list)
    compiled: re.Pattern | None = None
    where: str = ""

    def is_leaf(self) -> bool:
        return self.kind in WHEN_LEAVES

    def hits(self, subject: str) -> bool:
        if self.is_leaf():
            return self.compiled is not None and self.compiled.search(subject) is not None
        if self.kind == WHEN_ALL:
            return all(c.hits(subject) for c in self.children)
        if self.kind == WHEN_ANY:
            return any(c.hits(subject) for c in self.children)
        return not self.children[0].hits(subject)

    def written(self) -> dict:
        """書いた表記に戻した形。"""
        if self.is_leaf():
            return {self.kind: self.text}
        if self.kind == WHEN_NOT:
            return {WHEN_NOT: self.children[0].written()}
        return {self.kind: [c.written() for c in self.children]}

    def key(self) -> tuple:
        """比べるための形。個別条件は `{root}` を置き換えたあとの式で比べる（Rule.key と同じ）。"""
        if self.is_leaf():
            return (self.kind, self.compiled.pattern if self.compiled else self.text)
        return (self.kind, tuple(c.key() for c in self.children))

    def describe(self) -> str:
        """1 行で読める形。`any(glob '*.pem', all(glob '*/secrets/*', not(glob '*.md')))`。"""
        if self.is_leaf():
            return f"{self.kind} '{self.text}'"
        return f"{self.kind}({', '.join(c.describe() for c in self.children)})"

    def lines(self, indent: str = "") -> list[str]:
        """字下げで読める形。1 ノード 1 行。"""
        if self.is_leaf():
            return [indent + self.describe()]
        out = [indent + self.kind]
        for c in self.children:
            out.extend(c.lines(indent + "  "))
        return out

    def walk(self) -> Iterator[Condition]:
        """自分と、その下のノードを最上位のノードから順に。"""
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Rule:
    """探すものと、見つけたときに言うことの組。"""

    # id は報告でルールを名指しするための名前。不備のあるものを名指しできるように。
    # レイヤーの和では `lib:schema` のようにレイヤーの名前が前に付く（ruleload.prefix_ids）。
    id: str = ""
    # bare_id は書かれたままの id。レイヤーの名前をつける前の表記で、レイヤーをまたいで
    # 同じルールかどうかを見るときのキーになる。
    # id から前置きを取り除いて求める形にすると、`:` を含む id を書いたユーザの定義が
    # 取り除かれる側になるので、書いたときの表記をそのまま持つ。
    bare_id: str = ""
    # source はこのルールが来たレイヤー（common / self / プロジェクトの名前）。記録の
    # `source` 欄と `--explain` がこれを読む。
    source: str = ""
    # match は対象のツール名を "|" で並べたもの。"Write|Edit" など。
    match: str = ""
    # glob は fnmatch と同じ意味の glob。文字列全体にヒットするので、部分一致が
    # 欲しければ前後に "*" を自分で書く。"*git push*"、"*.pem"、"*/secrets/*" など。
    # 詳しくは globmatch.translate を参照。
    glob: str = ""
    # regex は本当に正規表現が要るときの最後の手段。これを使ったルールこそ
    # いちばん厳しく見直す対象なので、glob の別の表記ではなく別の欄にしてある。
    # glob では書けない「語の左側の切れ目」が要るときも、こちらを使う。
    regex: str = ""
    # message は、なぜ止めたかと、代わりに何をすればよいかを言う。
    message: str = ""
    # message_id は、文面ファイルの識別子（`texts.spoken` が判定の時点で引く）。組み込みのルール
    # （`from_file` が偽）だけが持ち、ルールファイルからは書けない。組み立てで引かないのは、
    # ルールを hook の呼び出しごとに組み立てるので、通る呼び出しに文面ファイルの冷たい読みを
    # 乗せないため。持つときは `message` を空にする。値（message_values）と場合（message_variants）は
    # `texts.say` に渡す。
    message_id: str = ""
    message_values: dict[str, str] = field(default_factory=dict)
    message_variants: dict[str, str] = field(default_factory=dict)
    # additionalContext は、ヒットしたときにモデルへ渡す文。message と違って
    # 止められた側ではなく進む側に向けた言葉で、allow でも ask でも deny でも
    # 応答の `additionalContext` に載る。通すが踏まえてほしいことを書く。
    additional_context: str = ""
    # additionalContextOnce は、1 つの文脈（セッション、サブエージェントならその 1 回の
    # 起動）で最初にヒットしたときだけ渡す文。セッションの開始（起動・再開・compact の後）で
    # 忘れる。毎回読ませる必要が無い長い説明のために。additionalContext と両方あれば、
    # 初回は両方を並べ、2 回目からは additionalContext だけを渡す。
    additional_context_once: str = ""
    # additionalContextFile / additionalContextOnceFile は、文の代わりに（または文に
    # 続けて）本文を渡すファイル。ルートからの相対パスで、行き先のワークツリーにあれば
    # そちらを、無ければルートのものを読む。無ければ何も足さない。長さは
    # ctxfile.MAX_CHARS で切り、切ったことを本文の末尾につける。
    additional_context_file: str = ""
    additional_context_once_file: str = ""
    # every は「渡す回」の刻み。ヒットした回数がこの倍数になった回だけが渡す回になり、
    # additionalContext は渡す回のたび、additionalContextOnce は渡す回の最初の 1 回に
    # 渡る（ctxfile.for_rules）。既定は 1 で、これは「ヒットするたびが渡す回」＝ every を
    # 書かないときと同じ。書いていないときの場合分けをどこにも持たないための既定値。
    every: int = 1
    # every_written は書かれたままの値。読めない値（0・負・整数でない）でもルールは
    # 組み上げ、every は 1（毎回渡す）として扱う。ここで受け付けずにルールごと破棄すると、--lint の
    # 名指しが `allow[3]` の形になり、どの id を直せばよいかを言えなくなる。指摘するのは
    # --lint の仕事で、そのために書かれた値をそのまま持つ。書いていなければ None。
    every_written: object = None
    # base は、共通レイヤーと config の有無・状態によらず常にヒットする組み込みの deny（土台）で
    # あること（`builtin.load_base`）。同じ呼び出しがレイヤーの deny にもヒットしたときは、そちらを
    # 文面と記録に載せ、土台は載せない（同じ拒否を 2 度言わない。拒否は変わらない。
    # `judge.decide_before`）。
    base: bool = False
    # decision はこのルールが置かれていたタイプ。ヒットしたルールを 1 件だけ
    # 取り出しても、それがどの判定だったのかを言えるようにする。
    decision: str = ""
    # root は読んだときのワークスペースルート。文面の `{root}` を、モデルへ渡すときに
    # 置き換える先（spoken_message）。書いた文面（message）は置き換えずに持つ。
    # 報告と --explain は書いた表記を出す（glob / regex と同じ扱い）。
    root: str = ""
    # from_file は、ルールファイル（共通レイヤー・自身のレイヤー・プロジェクトのレイヤー）から
    # 読んだルールであること。組み込み（土台・退避した既定・設定ファイルの保護・チケットの保護）は
    # 偽。`--test` がヒットしたルールを「ルールファイルの中」と「外から来た根拠」に分けるのに読む。
    # id の前置きでは分けられない（`builtin:ticket-*` のように `builtin:guard-` で始まらない
    # 組み込みがある）し、`source` でも分けられない（退避した既定は共通レイヤーの名前を持つ）。
    from_file: bool = field(default=False, compare=False)
    # file はこのルールが来たルールファイルの、ワークスペースのルートからの相対パス（`/` 区切り）。
    # 組み込みは空。記録名（record_name）の前半になる。読んだ側（ruleload）が入れる。
    file: str = field(default="", compare=False)

    # degraded_message は、ルールが書かれたとおりに組み立てられず、保護を消さないために
    # 別の形で判定に使っているときに、止められた側へ返す文面。`{!root}` を展開できなかった
    # `deny` / `ask` がこれにあたる（_ungeneratable）。書いた文面（message）を上書きすると
    # 書いたユーザの言葉が消え、`--explain` と記録にも出なくなるので、別の欄に持つ。
    degraded_message: str = ""

    compiled: re.Pattern | None = None
    # exempt は、式を適用する前に対象から外すものを決める関数。組み込みの保護だけが持ち、
    # ルールファイルからは書けない。式は先読みを使えないので、「この形の中でも、これだけは
    # 通す」は式の外で決める（selfguard_shell.handoff_copy_exempt）。返すのは式を適用する
    # 対象で、外すものが無ければ受け取ったまま返す。
    exempt: Callable[[str], str] | None = field(default=None, repr=False, compare=False)

    # excepts は `except:` の各パターン。本体（compiled）にヒットしても、どれかにヒットすれば
    # このルールはヒットしない。適用する文字列は本体と同じ（Bash ならコマンド列の全体）。
    excepts: list[Exclusion] = field(default_factory=list)

    # when は `when:` で書いた本体（組み合わせ条件）。glob / regex で書いたルールでは None。
    # `when` のルールの compiled はふつう None で、個別条件を組み立てられずに止める側へ倒した
    # deny / ask だけが、何にでもヒットする式を compiled に持つ（そちらを先に見る）。
    # 組み合わせ条件は
    # 書いた表記を出すために残す。
    when: Condition | None = None

    def built(self) -> bool:
        """判定に使える本体を持つか。"""
        return self.compiled is not None or self.when is not None

    def written_body(self) -> str:
        """本体を書いた表記で 1 行に。`when` は組み合わせ条件を 1 行にした形。"""
        if self.when is not None:
            return f"{WHEN_KEY}: {self.when.describe()}"
        return self.glob or self.regex

    def spoken_message(self) -> str:
        """モデルへ渡す文面。`{root}` をワークスペースルートの実パスにしたもの。

        組み立てに失敗して別の形で判定に使っているときは、書いた文面ではそれを説明できない
        （「外への書き込みです」としか言わない）ので、そちらを先に返す。
        """
        if self.degraded_message:
            # 名前は読み込みのあとで決まる（ファイルの相対パス）ので、ここで入れる。
            text = self.degraded_message.replace(DEGRADED_RULE_NAME, record_name(self))
            return fill_text(text, self.root)
        return fill_text(self.message, self.root)

    def key(self) -> tuple:
        """レイヤーをまたいで「同じ定義」と言えるかどうかのキー。

        比べるのは書いた表記ではなく、`{root}` を置き換えたあとの式。共通レイヤーと
        プロジェクトのレイヤーに同じ `{root}/...` を書いた定義は、置き換え先が同じ
        ワークスペースルートなので、ここで一致する。書いた表記で比べると、
        同じ場所を指す 2 本を違うものとして両方判定に使うことになる。
        """
        return (
            self.bare_id,
            self.match,
            self.compiled.pattern if self.compiled else "",
            # 組み合わせ条件。個別条件の式と組み方の両方を比べる。
            self.when.key() if self.when is not None else (),
            # 除くものが違えば別の定義。比べないと、except だけが違う後ろのレイヤーの 1 本を
            # 重複として破棄し、前のレイヤーの except で通るはずのないものが通ってしまう。
            tuple(e.compiled.pattern if e.compiled else "" for e in self.excepts),
            self.message,
            self.message_id,
            tuple(sorted(self.message_values.items())),
            tuple(sorted(self.message_variants.items())),
            self.additional_context,
            self.additional_context_once,
            self.additional_context_file,
            self.additional_context_once_file,
            # 刻みだけが違う 2 件は別の定義。同じ文を違う頻度で渡すルールを、
            # レイヤーをまたいで片方にまとめない。比べるのは読み取ったあとの刻みで、
            # 書かれたままの値ではない（`5` と `"5"` は同じ刻みではない。後者は
            # 読めない値として 1 になるので、そこで分かれる）。
            self.every,
            self.decision,
        )

    def matches(self, tool: str, subject: str) -> bool:
        """このルールがこのツールと対象にヒットするかどうか。"""
        if not self.built() or not subject:
            return False
        if tool_matches(self.match, tool):
            if self.exempt is not None:
                subject = self.exempt(subject)
            return self.hits(subject)
        return False

    def hits(self, subject: str) -> bool:
        """ツール名を見ずに、対象の文字列だけでヒットするか。

        本体にヒットし、except のどれにもヒットしないときにヒットする。
        """
        if not self.body_hits(subject):
            return False
        return not any(e.compiled is not None and e.compiled.search(subject) for e in self.excepts)

    def body_hits(self, subject: str) -> bool:
        """本体（glob / regex / when）だけでヒットするか。"""
        if self.compiled is not None:
            return self.compiled.search(subject) is not None
        return self.when is not None and self.when.hits(subject)


def record_name(rule: Rule) -> str:
    """決定ログの rules 欄と文面に出す名前。

    ルールファイル由来は `<ワークスペースのルートからの相対パス>:<書いた id>`。
    組み込みは id そのもの（`builtin:git-push`）。レイヤーの前置きは付けない。
    """
    if rule.from_file:
        return f"{rule.file}{ID_SEPARATOR}{rule.bare_id}"
    return rule.id


def tool_matches(match: str, tool: str) -> bool:
    """`match:`（`|` 区切り）がこのツール名を含むか。大文字小文字を区別した完全一致。

    `Rule.matches` のツール名の側。組み込みの保護を、ヒットしようの無いツールで組み立てずに
    済ませる判断（`selfguard_shell.add_rules`）も、同じこの 1 本で決める。
    """
    return any(want.strip() == tool for want in match.split("|"))


@dataclass
class RuleSet:
    """ルールファイル 1 本ぶん。タイプごとに分けて持つ。

    1 本のリストにして各ルールが自分の判定を持つ形にもできるが、分けておくと
    「強い順に見る」がリストの順そのものになる。判定の側がタイプを選び違える形を
    残さないほうが、あとからタイプを足したときに間違いが起きにくい。
    """

    version: int = 0
    deny: list[Rule] = field(default_factory=list)
    ask: list[Rule] = field(default_factory=list)
    allow: list[Rule] = field(default_factory=list)

    def section(self, name: str) -> list[Rule]:
        return {DENY: self.deny, ASK: self.ask, ALLOW: self.allow}[name]

    def all(self) -> Iterator[Rule]:
        """全タイプを強い順に。検証と、タイプをまたいだ数え上げのために。"""
        for name in SECTIONS:
            yield from self.section(name)


def load(path: str, root: str = "") -> tuple[RuleSet, list[Problem]]:
    """ルールファイルを読んで組み立てる。root は `{root}` の置き換え先。

    解釈できないルールは、除外して名指しで報告する。
    何も言われずに消えたルールは、誰も気づかないガードの穴になるから。
    組み立てられたルールはそのまま返すので、1 件の不備がガード全体を止めない。

    読むのは safe_load に限る。任意の Python の型を組み立てる load は、
    エージェントが書ける場所にあるファイルに向けては使わない。

    YAML の解析の失敗は ValueError に変換して投げ直す。呼び手はここが投げるものを
    OSError と ValueError の 2 つで受けていて、受けられずに抜ける例外が 1 つでもあると、
    ルールファイルの書き損じがそのまま hook の異常終了になる。既定に戻る
    経路を通らずに異常終了するので、破損したファイルを直す呼び出しも
    止まる。読み手を替えるたびに、変換の側も一緒に見ること。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return parse(_decode(text, path), root)


def readable(content: bytes) -> bool:
    """その中身を、load がルールファイルとして読めるか。

    読めなければ呼び手（ruleload.load_rules）は組み込みの既定を使う。バックアップの中身を見て
    「この呼び出しの直前に既定を使っていたか」を決めるのに使う（selfguard）。線を load と
    別に引くと、既定を使っていたのに使っていないと読む、あるいはその逆が起きる。
    """
    try:
        _decode(content.decode("utf-8"), "(content)")
    except ValueError:
        return False
    return True


def _decode(text: str, path: str) -> dict:
    try:
        data = yamlread.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} を YAML として読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} のルールがマッピングではない")
    return data


def parse(data: dict, root: str = "", builtin: bool = False) -> tuple[RuleSet, list[Problem]]:
    """読み込み済みのルールを組み立てる。

    ファイルを開く部分と分けてあるのは、組み込みの既定ルールが同じ経路を通るため。
    別の経路で組み立てると、ファイルから読んだときと既定に戻ったときで
    ルールの意味が食い違いうる。食い違えば、ガードが機能していない間に
    さらに読み違えることになる。

    builtin は組み込み自身（builtin / selfguard）が組み立てるという目印。`RESERVED_ID_PREFIX` で
    始まる id を書けるのはこちらだけ。
    """
    rule_set = RuleSet(version=data.get("version") or 0)
    problems: list[Problem] = []

    if rule_set.version != VERSION:
        problems.append(
            Problem(
                SEVERITY_ERROR,
                "",
                f"ルール書式の版 {rule_set.version} は扱えない（このビルドが読むのは {VERSION}）。"
                f"書式は `{'` `'.join(SECTIONS)}` の 3 タイプで、"
                "照合に指定できるのは `glob` か `regex`。"
                "`glob` は fnmatch の glob で、文字列全体と一致したときだけヒットするので、"
                "部分一致が必要なら前後に `*` を書く",
            )
        )

    for name in SECTIONS:
        raw_section = data.get(name)
        if raw_section is None:
            continue
        if not isinstance(raw_section, list):
            problems.append(Problem(SEVERITY_ERROR, f"({name})", f"`{name}` がリストではない"))
            continue
        for i, raw in enumerate(raw_section):
            rule, found = _build(raw, name, i, root, builtin)
            # 苦情とルールは同時に返りうる。`{!root}` を組み立てられなかった deny / ask が
            # これで、「報告する」と「保護を消さない」を両立させる唯一の方法。
            # 破棄するだけにすると、生成に失敗した deny が気づかないうちに消えて、
            # 止められずに通るようになる。
            problems.extend(found)
            if rule is not None:
                rule_set.section(name).append(rule)

    return rule_set, problems


def _text_map(raw: object) -> dict[str, str]:
    """組み込みのルールが文面に渡す値・場合の表（名前 → 文字列）。表でなければ空。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _build(
    raw: object, section: str, index: int, root: str = "", builtin: bool = False
) -> tuple[Rule | None, list[Problem]]:
    """ルール 1 件を組み立てる。返るのは (ルール, 苦情のリスト) で、どちらも無いことがありうる。

    3 通りある。

    - `(rule, [])`        組み立てられた
    - `(None, problems)`  受け付けられない。判定に使わない
    - `(rule, problems)`  苦情はあるが判定には使う。`{!root}` を組み立てられなかった
      `deny` / `ask` がこれで、`match` の全部にヒットする式を持って止める側になる。
      破棄すると「ワークスペースの外の全部」が止められずに通るので、報告だけでは足りない。
      `{!root}` の後ろの書き方への warn もここに入る

    苦情をリストにしてあるのは、本体と `except` の各パターンがそれぞれ warn を出しうるため。

    処理フロー:
    1. マッピングでなければ止める。id が無ければ止める（組み込みを除く）
    2. `Rule` を組み立てる（`every` は `readable_every` で読む）
    3. 予約の id 接頭辞と `ID_SEPARATOR` を止める（組み込みを除く）。
       `message`（`_NEEDS_MESSAGE` の区分のみ）と `match` の欠けを止める
    4. 本体の数を決める。`when` があれば `glob` / `regex` / `when` のキーの有無で、
       なければ `glob` / `regex` の値の有無で数え、0 と 2 以上は止める
    5. `when` があれば `_when` で組み合わせ条件にし、`_excepts` で `except` を読む
    6. 本体の式を `_compile` で作る。`{!root}` を展開できなければ `_ungeneratable` で
       止める側に倒す（組み合わせ条件では個別条件の 1 つでも同じ）
    7. `except` の各パターンを `_compile` で作る。書式の誤りはルールごと止める。
       `{!root}` を展開できないときは、allow は判定に使わず、deny / ask は except を外す
    """
    where = f"{section}[{index}]"
    if not isinstance(raw, dict):
        return None, [Problem(SEVERITY_ERROR, where, "ルールがマッピングではない")]

    written_id = str(raw.get("id") or "")
    if not builtin and not written_id.strip():
        return None, [
            Problem(
                SEVERITY_ERROR,
                where,
                "id がありません。ルールには id を書いてください（記録と文面にこの名前が出る）",
            )
        ]
    rule = Rule(
        id=written_id,
        bare_id=written_id,
        match=str(raw.get("match") or ""),
        glob=str(raw.get("glob") or ""),
        regex=str(raw.get("regex") or ""),
        message=str(raw.get("message") or ""),
        message_id=str(raw.get("message_id") or "") if builtin else "",
        message_values=_text_map(raw.get("message_values")) if builtin else {},
        message_variants=_text_map(raw.get("message_variants")) if builtin else {},
        additional_context=str(raw.get("additionalContext") or ""),
        additional_context_once=str(raw.get("additionalContextOnce") or ""),
        additional_context_file=str(raw.get("additionalContextFile") or ""),
        additional_context_once_file=str(raw.get("additionalContextOnceFile") or ""),
        every=readable_every(raw.get("every")) or EVERY_DEFAULT,
        every_written=raw.get("every"),
        decision=section,
        root=root,
        from_file=not builtin,
    )
    name = f"{section}:{rule.id}"

    if not builtin and written_id.lower().startswith(RESERVED_ID_PREFIX):
        return None, [
            Problem(
                SEVERITY_ERROR,
                name,
                f"`{RESERVED_ID_PREFIX}` で始まる id は組み込みルールの名前で、"
                "ルールファイルには書けない。同じ名前で書いても組み込みは置き換わらない。"
                "別の名前にしてください。"
                "組み込みを外したいなら env（CCNAVI_GUARD_CORE_FILES など）で"
                "その保護ごと切ってください",
            )
        ]
    if not builtin and ID_SEPARATOR in written_id:
        return None, [
            Problem(
                SEVERITY_ERROR,
                name,
                f"id に `{ID_SEPARATOR}` は書けない。レイヤーの名前を添えた形"
                f"（`self{ID_SEPARATOR}id` / `<プロジェクト名>{ID_SEPARATOR}id`）と"
                "見分けが付かず、記録を読んだユーザがどのファイルを直すのか決められない",
            )
        ]
    if not rule.message and not rule.message_id and section in _NEEDS_MESSAGE:
        return None, [
            Problem(
                SEVERITY_ERROR,
                name,
                "message がありません。ルールには、代わりに何をすべきかを書く必要がある",
            )
        ]
    if not rule.match:
        return None, [
            Problem(SEVERITY_ERROR, name, "match がありません。どのツールにもヒットしない")
        ]
    # `when` と並べたかどうかはキーの有無で見る。値の真偽で見ると `glob: ''` や `glob: 0` を
    # 添えた組み合わせ条件が通り、書いた人の意図（どちらで判定するのか）が
    # 決まらないまま読み込まれる。
    # 拡張の画面（rules-doc.ts の readRules）もキーの有無で見ている。
    has_when = WHEN_KEY in raw
    bodies = [
        key
        for key, present in (
            ("glob", "glob" in raw if has_when else bool(rule.glob)),
            ("regex", "regex" in raw if has_when else bool(rule.regex)),
            (WHEN_KEY, has_when),
        )
        if present
    ]
    if not bodies:
        return None, [Problem(SEVERITY_ERROR, name, "glob も regex も when もありません。")]
    if len(bodies) == 2:
        return None, [
            Problem(
                SEVERITY_ERROR,
                name,
                f"{bodies[0]} と {bodies[1]} の両方がある。どちらで判定するのか決められない",
            )
        ]
    if len(bodies) > 2:
        return None, [
            Problem(
                SEVERITY_ERROR,
                name,
                "glob と regex と when が並んでいる。書けるのは 1 つだけで、"
                "どれで判定するのか決められない",
            )
        ]

    tree: Condition | None = None
    if WHEN_KEY in raw:
        tree, problem = _when(raw.get(WHEN_KEY), name)
        if problem is not None:
            return None, [problem]

    excepts, problem = _excepts(raw, name)
    if problem is not None:
        return None, [problem]

    problems: list[Problem] = []
    if tree is not None:
        refused = ""
        for leaf in tree.walk():
            if not leaf.is_leaf():
                continue
            label = f"{leaf.where} の "
            glob, regex = (leaf.text, "") if leaf.kind == "glob" else ("", leaf.text)
            part = _compile(glob, regex, root, name, label)
            if part.error is not None:
                return None, problems + [part.error]
            if part.note is not None:
                problems.append(part.note)
            if part.refused and not refused:
                refused = label + part.refused
            leaf.compiled = part.compiled
        rule.when = tree
        if refused:
            # 個別条件を 1 つでも組み立てられなければ、組み合わせ条件の意味が決まらない。
            # `not` の下の個別条件なら外すとヒットする範囲が狭まり、上なら広がるので、
            # 個別条件だけ外す形は向きが定まらない。
            # 本体を組み立てられなかったときと同じく、deny / ask は全部止め、allow は使わない。
            degraded, refused_problem = _ungeneratable(rule, section, name, refused)
            return degraded, problems + [refused_problem]
    else:
        built = _compile(rule.glob, rule.regex, root, name, "")
        if built.error is not None:
            return None, [built.error]
        if built.note is not None:
            problems.append(built.note)
        if built.refused:
            # 本体を組み立てられなかった deny / ask は、`match` の全部に適用して止める。
            # except は付けない。付けると「全部止める」に穴が空き、保護を消さないための形が崩れる。
            degraded, refused_problem = _ungeneratable(rule, section, name, built.refused)
            return degraded, problems + [refused_problem]
        rule.compiled = built.compiled

    for i, (glob, regex) in enumerate(excepts):
        label = f"except[{i}] の "
        part = _compile(glob, regex, root, name, label)
        if part.error is not None:
            return None, problems + [part.error]
        if part.note is not None:
            problems.append(part.note)
        if part.refused:
            # except を組み立てられないときは、その except を持たないルールとして使う。deny / ask は
            # ヒットする範囲が広がる（止める側に外れる）。allow は広がると通す範囲が増えるので、
            # ルールごと判定に使わない。
            problems.append(Problem(SEVERITY_ERROR, name, label + part.refused))
            if section == ALLOW:
                return None, problems
            continue
        rule.excepts.append(Exclusion(glob=glob, regex=regex, compiled=part.compiled))

    return rule, problems


@dataclass
class _Built:
    """式 1 つを組み立てた結果。`refused` は `{!root}` を展開できなかった理由。"""

    compiled: re.Pattern | None = None
    error: Problem | None = None
    note: Problem | None = None
    refused: str = ""


def _compile(glob: str, regex: str, root: str, name: str, label: str) -> _Built:
    """`glob` か `regex` の 1 つを、判定に使う式に組み立てる。

    ルールの本体と `except` の各パターンが同じこの 1 本を通る。`{root}` の置き換え、
    `{!root}` の展開、先読み・後方参照の禁止、大文字小文字の無視を、両方で食い違わせないため。
    `label` は苦情の頭に付ける語（本体なら空、except なら `except[0] の `）。

    処理フロー:
    1. `{root}` を使うのに root が無ければ止める
    2. `{!root}` を `glob` に書いていれば止める（`regex` 専用）
    3. `{!root}` を `regex` に書いていれば、置き場所（`_not_root_placement`）と root を見る。
       後ろの書き方は warn にする（`_not_root_suffix`）
    4. `regex` なら `_unsupported` で先読み・後方参照を止め、`{root}` を置き換え、
       `{!root}` を `_expand_not_root` で展開する。展開できなければ refused。
       `glob` なら `{root}` を置き換えて `translate` で正規表現にし、先頭に `^` を付ける
    5. 大文字小文字を区別せずに `re.compile` する。失敗は error。
       入れ子が深すぎるときは、`{!root}` があれば refused、無ければ error
    """
    # `{root}` はワークスペースルートの実パスに置き換える。書いた表記（glob / regex）は
    # そのまま残し、置き換えるのは翻訳後の式だけ。報告と --explain は書いた表記を出す。
    uses_root = ROOT_PLACEHOLDER in regex or ROOT_PLACEHOLDER in glob
    if uses_root and not root:
        return _Built(
            error=Problem(
                SEVERITY_ERROR,
                name,
                f"{label}`{ROOT_PLACEHOLDER}` を使うにはワークスペースルートが必要です",
            )
        )

    # `{!root}` は `regex` 専用。`glob` は fnmatch に翻訳されるので、入れ子の選択肢を持つ
    # 展開結果を埋める場所が無い。
    if NOT_ROOT_PLACEHOLDER in glob:
        return _Built(
            error=Problem(
                SEVERITY_ERROR,
                name,
                f"{label}`{NOT_ROOT_PLACEHOLDER}` は `glob` には書けない。"
                f"`regex: '^{NOT_ROOT_PLACEHOLDER}'` と書いてください",
            )
        )

    note: Problem | None = None
    uses_not_root = NOT_ROOT_PLACEHOLDER in regex
    if uses_not_root:
        misplaced = _not_root_placement(regex)
        if misplaced:
            return _Built(error=Problem(SEVERITY_ERROR, name, label + misplaced))
        if not root:
            return _Built(
                error=Problem(
                    SEVERITY_ERROR,
                    name,
                    f"{label}`{NOT_ROOT_PLACEHOLDER}` を使うにはワークスペースルートが必要です",
                )
            )
        suffix = _not_root_suffix(regex)
        if suffix:
            note = Problem(SEVERITY_WARN, name, label + suffix)

    if regex:
        unsupported = _unsupported(regex)
        if unsupported:
            return _Built(
                error=Problem(SEVERITY_ERROR, name, f"{label}{unsupported}は利用できない")
            )
        expression = regex.replace(ROOT_PLACEHOLDER, root_pattern(root)) if uses_root else regex
        if uses_not_root:
            expanded, refused = _expand_not_root(expression, root)
            if refused:
                return _Built(note=note, refused=refused)
            expression = expanded
    else:
        # glob は文字列全体にヒットする約束なので、先頭に固定する。
        # 固定しないと search が照合を始める
        # 位置ごとに末尾まで読み、`*git push*` のような式が入力の長さの 2 乗で遅くなる（#241）。
        # 先頭に `*` を書かない glob が文字列の途中にヒットすることも、この約束どおりに無くなる。
        expression = "^" + translate(
            glob.replace(ROOT_PLACEHOLDER, root_glob(root)) if uses_root else glob
        )

    # `glob` も `regex` も、どの機械でも大文字小文字を区別せずに適用する。区別するかを機械で
    # 変えると、同じルールが Windows ではヒットし Linux ではヒットしない。`*/.ccnavi/*` と書いた
    # 保護を `.Ccnavi/` と書くだけで止められずに通せ、どの機械でも区別しないチケットの範囲とも
    # 食い違う。
    # ルールはユーザが宣言する場所の意図なので、機械の都合ではなく書かれたパスの意味で読む
    # （phasetypes._globs / risk._factors / selfguard_shell._folded / チケットの範囲と同じ形）。
    # 区別が要る `regex` は `(?-i:...)` で囲む。
    flags = re.IGNORECASE

    try:
        return _Built(compiled=re.compile(expression, flags), note=note)
    except re.error as exc:
        return _Built(
            error=Problem(SEVERITY_ERROR, name, f"{label}正規表現として組み立てられない: {exc}")
        )
    except RecursionError:
        # `re` の組み立ては入れ子を再帰で読む。捕捉しないとプロセスごと異常終了し、
        # hook の異常終了は呼び出しを止めずに通してしまう。
        # shellread が同じ形で `RecursionError` を安全側にしているのに倣う。
        if uses_not_root:
            return _Built(note=note, refused="入れ子が深すぎて組み立てられない")
        return _Built(
            error=Problem(SEVERITY_ERROR, name, f"{label}入れ子が深すぎて組み立てられない")
        )


def compile_condition(glob: str, regex: str, name: str) -> tuple[re.Pattern | None, str]:
    """ルールの外で `glob` か `regex` の 1 つを、ルールと同じ読み方で組み立てる。

    ルールファイルに書かない組み込みの宣言（ターン分離の関門など）が、条件の読み方を
    ルールと食い違わせないための入口。大文字小文字の無視、先読み・後方参照の禁止、
    glob の翻訳は `_compile` と同じ。`{root}` と `{!root}` はワークスペースルートを
    持たないので使えない（使うと苦情になる）。返すのは (式, 苦情)。苦情があれば式は None。
    """
    built = _compile(glob, regex, "", name, "")
    if built.error is not None:
        return None, built.error.detail
    if built.compiled is None:
        return None, built.refused or "式を組み立てられない"
    return built.compiled, ""


def _excepts(raw: dict, name: str) -> tuple[list[tuple[str, str]], Problem | None]:
    """`except:` を読む。返すのは (glob, regex) の組のリストと、書式の苦情。

    値はリストで、各要素は `glob` か `regex` のどちらか 1 つだけを持つマッピング。
    キーが無ければ空のリスト。書いてあるのに空のリスト・`null` は error にする。
    書いたユーザは何かを除いたつもりでいるので、何も除かない形を黙って受け取らない。
    """
    if EXCEPT_KEY not in raw:
        return [], None
    written = raw.get(EXCEPT_KEY)
    if not isinstance(written, list):
        return [], Problem(
            SEVERITY_ERROR,
            name,
            f"`{EXCEPT_KEY}` がリストではない。"
            "`- glob: '...'` か `- regex: '...'` を並べてください",
        )
    if not written:
        return [], Problem(
            SEVERITY_ERROR,
            name,
            f"`{EXCEPT_KEY}` が空。除くものが無いなら `{EXCEPT_KEY}` ごと削除してください",
        )
    out: list[tuple[str, str]] = []
    for i, item in enumerate(written):
        where = f"{EXCEPT_KEY}[{i}]"
        if not isinstance(item, dict):
            return [], Problem(
                SEVERITY_ERROR,
                name,
                f"{where} がマッピングではない。"
                "`glob: '...'` か `regex: '...'` の形で書いてください",
            )
        unknown = sorted(str(k) for k in item if k not in EXCEPT_FIELDS)
        if unknown:
            return [], Problem(
                SEVERITY_ERROR,
                name,
                f"{where} に未対応のキー（{', '.join(unknown)}）がある。"
                f"書けるのは `{'` か `'.join(EXCEPT_FIELDS)}` の 1 つだけ",
            )
        # 書いた値は文字列だけを受け取る。引用符の無い `on` は PyYAML が真偽値に読むので、
        # 文字にすると 'True' に適用する except になってしまう（when の個別条件と同じ扱い）
        for key in EXCEPT_FIELDS:
            if key in item and not isinstance(item[key], str):
                return [], Problem(
                    SEVERITY_ERROR,
                    name,
                    f"{where} の {key} が文字列ではない。引用符で囲んでください",
                )
        glob = item.get("glob") or ""
        regex = item.get("regex") or ""
        if not glob and not regex:
            return [], Problem(SEVERITY_ERROR, name, f"{where} に glob も regex もありません。")
        if glob and regex:
            return [], Problem(
                SEVERITY_ERROR,
                name,
                f"{where} に glob と regex の両方がある。どちらで判定するのか決められない",
            )
        out.append((glob, regex))
    return out, None


class _WhenError(Exception):
    """`when:` の書式の誤り。組み合わせ条件をたどる途中から一気に抜けるために使う。"""


def _when(written: object, name: str) -> tuple[Condition | None, Problem | None]:
    """`when:` を読んで組み合わせ条件にする。個別条件の式はまだ組み立てない（_build が組み立てる）。

    書式の誤りは最初の 1 件を返す。書いたユーザは条件を書いたつもりでいるので、
    読めないノードを黙って外すと、ヒットする範囲が書いたものと気づかないうちに食い違う。

    処理フロー:
    1. `node` を根から再帰で読む。入れ子の段数（`MAX_WHEN_DEPTH`）と
       ノード数（`MAX_WHEN_NODES`）を超えたら止める
    2. ノードは 1 つのキーだけを持つマッピングで、キーは `WHEN_KINDS` の 1 つに限る
    3. 個別条件（`WHEN_LEAVES`）は空でない文字列。`not` は下に 1 つのノード。
       `all` / `any` は空でないリスト
    4. 読めたものを `Condition` にして返す。個別条件の式は `_build` が `_compile` で組み立てる
    """
    count = 0

    def node(raw: object, where: str, depth: int) -> Condition:
        nonlocal count
        if depth > MAX_WHEN_DEPTH:
            raise _WhenError(
                f"{where} で入れ子が {MAX_WHEN_DEPTH} 段を超える。判定が重くならないように、"
                "段を浅くするか、ルールを分けてください"
            )
        count += 1
        if count > MAX_WHEN_NODES:
            raise _WhenError(
                f"{WHEN_KEY} のノードが {MAX_WHEN_NODES} 個を超える。判定が重くならないように、"
                "ルールを分けてください"
            )
        if not isinstance(raw, dict):
            raise _WhenError(
                f"{where} がマッピングではない。"
                f"`{'` / `'.join(WHEN_KINDS)}` のどれか 1 つのキーを持つ形で書いてください"
            )
        unknown = sorted(str(k) for k in raw if k not in WHEN_KINDS)
        if unknown:
            raise _WhenError(
                f"{where} に未対応のキー（{', '.join(unknown)}）がある。"
                f"書けるのは `{'` / `'.join(WHEN_KINDS)}` のどれか 1 つだけ"
            )
        if len(raw) != 1:
            got = "キーがありません" if not raw else f"キーが {len(raw)} つある（{', '.join(raw)}）"
            raise _WhenError(
                f"{where} に{got}。1 つのノードに書けるキーは 1 つだけで、"
                f"組み合わせるなら `{WHEN_ALL}` / `{WHEN_ANY}` の下に並べてください"
            )
        ((kind, value),) = raw.items()
        if kind in WHEN_LEAVES:
            if not isinstance(value, str) or not value:
                raise _WhenError(f"{where} の {kind} が空か、文字列ではない")
            return Condition(kind, text=value, where=f"{where}.{kind}")
        if kind == WHEN_NOT:
            return Condition(
                kind, children=[node(value, f"{where}.{kind}", depth + 1)], where=where
            )
        if not isinstance(value, list):
            raise _WhenError(f"{where} の {kind} がリストではない。`- ノード` を並べてください")
        if not value:
            raise _WhenError(f"{where} の {kind} が空。1 つ以上のノードを並べてください")
        children = [node(v, f"{where}.{kind}[{i}]", depth + 1) for i, v in enumerate(value)]
        return Condition(kind, children=children, where=where)

    try:
        return node(written, WHEN_KEY, 1), None
    except _WhenError as exc:
        return None, Problem(SEVERITY_ERROR, name, str(exc))


def _not_root_placement(expression: str) -> str:
    """`{!root}` の置き場所への苦情。無ければ空。

    置けるのは `^` の直後だけで、1 つの式に 1 回。展開されるのは
    「先頭から、外だと確定するところまで」を読み進める式なので、前に何かを置いても
    意味を持たない。
    """
    if expression.count(NOT_ROOT_PLACEHOLDER) > 1:
        return f"`{NOT_ROOT_PLACEHOLDER}` は 1 つの式に 1 回だけ書ける"
    if not expression.startswith("^" + NOT_ROOT_PLACEHOLDER):
        return (
            f"`{NOT_ROOT_PLACEHOLDER}` は `^` の直後にしか書けない。"
            "展開されるのは「先頭から、外だと確定するところまで」を読み進める式で、"
            "前に何かを置くと意味を持たない"
        )
    # 直後の量化子は error。`?` や `{0}` を付けると展開結果ごと省略できるようになり、
    # 「外だけを止める」はずのルールがどの対象にもヒットする式になってしまう。
    # ワークスペースの中への書き込みまで止まるので、warn では済ませない。
    rest = expression[len("^" + NOT_ROOT_PLACEHOLDER) :]
    if rest[:1] in ("?", "*", "+", "{"):
        return (
            f"`{NOT_ROOT_PLACEHOLDER}` の直後に量化子（`{rest[:1]}`）は書けない。"
            "展開結果ごと省略したり繰り返したりできてしまい、"
            "「外だけを止める」はずの式がどの対象にもヒットするようになる"
        )
    return ""


def _not_root_suffix(expression: str) -> str:
    """`{!root}` の後ろに続く式への苦情（warn）。無ければ空。

    続けてよいかどうかは意味の問題で、機械には判定できない。展開結果が読み終える
    位置がパスの区切りである保証は無いので、区切りを前提にした表記
    （`^{!root}[\\\\/]foo`）は不正ではないが、まず書いたユーザの勘違い。
    止めるほどではないので warn。
    """
    rest = expression[len("^" + NOT_ROOT_PLACEHOLDER) :]
    if not rest or _SKIPS_ANYWHERE.match(rest):
        return ""
    return (
        f"`{NOT_ROOT_PLACEHOLDER}` の直後に `{rest[:16]}` が続いている。"
        "展開した式がマッチし終える位置はパスの区切りとは限らないので、区切りを前提にした"
        "表記は意図どおりに動かない。任意の位置から続けるなら `.*` で受けてください"
    )


def _expand_not_root(expression: str, root: str) -> tuple[str, str]:
    """`{!root}` を展開した式を返す。組み立てられなければ (元の式, 理由)。"""
    real = real_root(root)
    if not real:
        return expression, "ワークスペースルートが区切りだけで、「外」を決められない"
    if len(real) >= MAX_ROOT_LEN:
        return expression, (
            f"ワークスペースルートが長すぎて `{NOT_ROOT_PLACEHOLDER}` を組み立てられない"
            f"（{len(real)} 字 / 上限 {MAX_ROOT_LEN - 1} 字）"
        )
    generated = not_root_pattern(real)
    # 生成した部分が契約（繰り返しも先読みも含まない）を守っているかを自分で見る。
    # 見るのは `not_root_pattern` が作った部分だけで、書いたユーザが後ろに続けた式は含めない。
    # `.*` のような繰り返しをそこに書くのは許しているので、
    # 全体に掛けると正しい書き方まで弾く。
    #
    # いまの `not_root_pattern` は `re.escape` で組み立てるので、この検査は通常は発火しない。
    # 将来ここを書き換えたときに気づかないうちに契約が守られなくなるのを防ぐための備えで、
    # 外から呼んで発火させることはできない。
    if _GENERATED_QUANTIFIER.search(generated) or _unsupported(generated):
        return expression, "展開した式が契約を守っていない（組み立ての不具合）"
    return expression.replace(NOT_ROOT_PLACEHOLDER, generated), ""


def _ungeneratable(rule: Rule, section: str, name: str, detail: str) -> tuple[Rule | None, Problem]:
    """`{!root}` を組み立てられなかったときの扱い。

    強い側を採る。`deny` と `ask` は `match` の全部に適用して止め、`allow` は
    どれにも適用しない。`allow` にヒットしたものとして扱うと、ccnavi が判定を返さない範囲が
    広がるので、そこだけ逆になる。

    これは `{!root}` の生成失敗に限った扱い。 正規表現の書き損じなど既存の
    失敗の扱いは今までどおり「報告して破棄する」のまま。ここだけ特別なのは、
    守られなくなる範囲が「ワークスペースの外の全部」に広がるから。

    ## 設計から変えた 2 点（実装フェーズの判断。文書に反映すること）

    - 記録に `root-too-long` の欄は足さない。`audit` の
      `REASON_*` は「判定に至らなかった理由」で、ここは判定に至っている（deny する）。
      記録には `deny` とヒットしたルールの id が既に残り、なぜ止めたかはこの文面が言う。
      新しい名前を足すと、判定に至らなかったものと同じ欄に別の意味が混ざる
    - SessionStart では何も言わない。
      256 字のルートは Windows の MAX_PATH の外で実用にならず、該当するユーザはまずいない。
      該当したときはこの文面が理由と直し方を言う。めったに起きないことのために
      SessionStart を毎回重くしない
    """
    problem = Problem(SEVERITY_ERROR, name, detail)
    if section == ALLOW:
        return None, problem
    rule.compiled = re.compile(_MATCH_EVERYTHING)
    # 書いた文面（message）は残す。`--explain` と記録が書いた表記を出す約束は、
    # glob / regex だけでなく文面にも該当する（Rule の message のコメント）。
    rule.degraded_message = (
        f"{detail}。保護が消えるのを避けるため、ルール '{DEGRADED_RULE_NAME}' が見るツール"
        f"（{rule.match}）を止めています。ワークスペースをもっと浅い場所へ移すか、"
        "ユーザに、このルールが書かれているルールファイルから削除してもらってください。"
    )
    return rule, problem


# degraded_message の中のルールの名前。読み込みのあとで記録名に置き換える（spoken_message）。
DEGRADED_RULE_NAME = "{rule}"


def _unsupported(expression: str) -> str:
    for literal, label in _UNSUPPORTED:
        if literal in expression:
            return label
    if _BACKREFERENCE.search(expression):
        return "後方参照"
    return ""
