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
        message: ガード自身の設定です。利用者に依頼してください。
    ask:
      - id: migrations
        match: Write|Edit
        glob: "*/migrations/*"
        message: 移行ファイルは実行前に人が中身を見ます。
    allow:
      - id: source
        match: Read|Write|Edit
        glob: "*/ccnavi/*"

文面は `deny` と `ask` では必須。止めるなら代わりの手段を、聞くなら何を見て
判断すればよいかを、ルール自身が言わなければならない。`allow` では要らない。
通した呼び出しに返るのは `additionalContext` だけで、止めも聞きもしないので、
言うべき代わりの手段が無い。

## 強さ

同じ呼び出しに複数のタイプが当たることがある。強い順に `deny` `ask` `allow`。
1 件でも `deny` に当たれば拒否で、`ask` があれば確認、どちらも無く `allow` に
当たれば許可になる。どこにも当たらなければ、ccnavi は判定を持たず、
Claude Code の権限モードに従う（判定は cli.py）。

強い側を先に見るのは、緩める側のルールを 1 行足しただけで守りが消える形を
作らないため。`allow` は「まだ何も言われていない場所」に許可を置くもので、
`deny` の穴を開ける道具ではない。

`allow` に当たっても、Claude Code へ「許可」は返さない。判定を返すのは `deny` と
`ask` のときだけで（judge.refuse）、`allow` は記録に残して終わる。効き目は、
ccnavi 自身の確認（言及が無いときの `ask`）と拒否（`dontAsk` / `bypassPermissions`）を
起こさないことと、`additionalContext` を当てる先になること。Claude Code 側の権限の
扱いはルールファイルからは変えられない。

## 綴りの大文字小文字

`glob` も `regex` も、どの機械でも大文字小文字を区別せずに当てる。`*/.ccnavi/*` は
`.Ccnavi/config/rules.yml` にも当たる。当たり方が機械で変わると、同じルールファイルが
ある機械では止め、別の機械では通す（risk.py と phasetypes.py の範囲も同じ）。
区別が要る `regex` は `(?-i:...)` で囲む。囲むのは区別したい部分だけでよく、
否定の文字クラス（`[^c]` のような除外）を持つルールは、区別せずに当てると除外の側が
広がるのでそこを囲む。

## ワークスペースルートの合言葉

`{root}` はワークスペースルート（hook なら CLAUDE_PROJECT_DIR、端末なら --root）の実パスに
読み込み時に置き換わる。「ワークスペースの下」を絶対パスの直書きなしに書くための
もので、Windows と WSL と Linux で綴りが割れない。glob なら `{root}/wip/*`、regex なら
`^{root}[\\/]` のように書く。ワークスペースルートが渡らない読み方をしたルールは error で名指しする。
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import yaml

from .globmatch import translate

# このビルドが読めるルールファイルの書式の版。`deny` `ask` `allow` の 3 タイプで、探すものは
# `glob`（fnmatch の glob）か `regex`。
VERSION = 1

# 深刻度。ガードを壊すものと、弱めるだけのものを分ける。
SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
# info は「そう書いてあるとおりに効いているが、書いた人が知りたいはずのこと」。
# 層をまたいで同じ定義が重複していて後ろを捨てた、がこれにあたる（設計 11.4）。
# warn と分けるのは、重複は普通の形（見本から始めたプロジェクト）で、これを warn に
# 混ぜると本当に緩んでいる warn が埋もれるため。
SEVERITY_INFO = "info"

# タイプの名前。強い順。cli.py の判定もこの順に見る。
DENY = "deny"
ASK = "ask"
ALLOW = "allow"
SECTIONS = (DENY, ASK, ALLOW)

# 文面が要るタイプ。deny だけ。ask の文面は人の確認ダイアログにしか出ず、allow は
# 通すだけで届く先が無い（どちらも実測済み、設計 付録 C）。モデルに渡す文は
# additionalContext に書く。ask と allow に書いた文面は lint が error にするが、
# ここで落とすとその文面のせいでルールごと外れて通ってしまうので、読み込みは通す。
_NEEDS_MESSAGE = (DENY,)

# Python の正規表現エンジンは後方参照と先読みを扱えるが、ルールファイルの契約は
# それを使わないことになっている。書ける範囲を狭いままにしておくと、ルールが
# エンジンをまたいでも同じ意味を保つ。加えて、この 2 つは組み合わせ爆発を起こす
# 書き方の入口でもあり、判定の途中で固まった hook は期限に達して素通りになる。
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
# 読み込み時に、先読みを使わない入れ子の式へ展開する（not_root_pattern、設計 i0061 2）。
NOT_ROOT_PLACEHOLDER = "{!root}"

# 展開の最内と、各段の「ここで文字列が終わる」に置く綴り。`$` ではなく `\Z` なのは、
# `$` が末尾の改行の直前にも当たるため。言いたいのは「終わる」であって「行末」ではない。
_NOT_ROOT_END = r"\Z"

# ルートがこの長さ以上なら展開しない。Python 3.12（再帰の上限 1000）では、この形の
# 入れ子は 493 字で `re.compile` が `RecursionError` になる（tests/core/bench_not_root.py）。
# 半分以下で止めるのは、Python の版が変わって上限が動いたときに黙って壊れないため。
# Windows の MAX_PATH は 260 なので、256 字のルートはその時点で実用にならない。
#
# **組み立てが落ちる長さは、展開の形で変わる。** 1 文字あたりの入れ子の段数を変えたら、
# bench_not_root.py を回して測り直すこと（設計 i0061 4.1）。
MAX_ROOT_LEN = 256

# 生成できなかった `deny` / `ask` に持たせる式。どの文字列にも当たるので、そのルールが
# 見るツールの呼び出しは全部止まる。守りが消えるより止まるほうがよい、という向きへ
# 倒すために使う（設計 i0061 4.3）。
_MATCH_EVERYTHING = "^"

# 生成した式に現れてはいけない書き方。`_unsupported` は繰り返しを見ないので、そこへ
# 掛け直すだけでは「繰り返しを含まない」ことを確かめられない（設計 i0061 2.5）。
#
# `?` は入れていない。生成した式は捕獲しないグループ `(?:` を使うので、`?` を量化子と
# 数えると式自身の構造に当たってしまう。ルートの文字は `re.escape` を通るため、
# `*` と `+` がルートの名前に含まれていても `\*` `\+` になり、この式には当たらない。
_GENERATED_QUANTIFIER = re.compile(r"(?<!\\)[*+]|(?<!\\)\{\d")

# `{!root}` の直後に続けてよい書き始め。生成した式が読み終える位置はパスの区切りとは
# 限らないので、任意の位置まで進む書き方で受けていないものは warn で伝える
# （設計 i0061 3.3）。
_SKIPS_ANYWHERE = re.compile(r"(?:\.[*+]|\[\\s\\S\][*+])")

# 層の名前と id の間に入る文字（`self:docs` / `lib:source`、ruleload.prefix_ids）。
# 書かれたままの id にこれが入っていると、層を添えた形と見分けが付かない。
# 名前の綴りを 1 文字予約するほうが、前置きの綴りを別にするより安い（設計 11.4）。
ID_SEPARATOR = ":"

# 苦情の出どころ（`Problem.kind`）。「いまは通らないが、書いた側に直すものは無い」もの。
# 前のフェーズが閉じていない子がこれで、閉じれば同じ提案がそのまま通る。承認は落とすが、
# 全体を見る `--lint` は warn に落とす（提案 1 本で設定画面の保存と CI が止まらないように）。
KIND_NOT_YET = "not-yet"

# 組み込みの守りの名前の頭。ルールファイルからは書けない（読み込まずに error）。
# 同じ id で書いたルールがあると組み込みを足さない形にすると、何にも当たらない 1 本を
# その名前で書くだけで守りを黙って消せてしまう。ルールファイルの記述に依らず足すのが
# 組み込みの役目なので、置き換えの道ごと閉じる。外したいなら env で面ごと切る。
RESERVED_ID_PREFIX = "builtin-guard-"


def root_pattern(root: str) -> str:
    """ワークスペースルートの実パスを、regex に埋めて安全な形にする。

    区切りは `/` と `\\` のどちらにも当たる形にする。当てる対象は行き着く先まで
    解いた綴り（judge.full_path）で、Windows では `\\` になるが、ルールを書く人は
    `/` で考える。大文字小文字は式ごと区別せずに当てるので（_build）、ここでは機械を
    見ない。見ると `c:` と `C:` の扱いが機械で割れる。
    """
    real = os.path.realpath(root).rstrip("\\/")
    return "".join("[\\\\/]" if ch in "\\/" else re.escape(ch) for ch in real)


def root_glob(root: str) -> str:
    """ワークスペースルートの実パスを、glob に埋める形にする。

    区切りは `/` に寄せ、翻訳の側が両方に当てる。
    """
    return os.path.realpath(root).replace("\\", "/").rstrip("/")


def real_root(root: str) -> str:
    """ルートを、展開に使える形に正規化する。

    `os.path.realpath` で解き、**末尾の区切りを落とす**。落とさないと、最後の区切りを
    「同じ」の側で消費した直後に最内へ落ち、ルート直下の普通のファイル名が「外」に
    化ける。Windows では `os.path.realpath('/')` が `C:\\` を返すので、ドライブ直下を
    ワークスペースにした環境は必ずここを踏む（設計 i0061 2.0）。

    `root_pattern` / `root_glob` も同じ 2 手を踏んでいる。`{!root}` だけ違う扱いにしない。
    """
    return os.path.realpath(root).rstrip("\\/")


def _differs(ch: str) -> str:
    """その 1 文字と一致しない文字に当たる文字クラス。区切りは両方の書き方を除く。"""
    return r"[^\\/]" if ch in "\\/" else "[^" + re.escape(ch) + "]"


def _same(ch: str) -> str:
    """その 1 文字に当たる式。区切りは `\\` と `/` のどちらにも当たるようにする。"""
    return r"[\\/]" if ch in "\\/" else re.escape(ch)


def not_root_pattern(root: str) -> str:
    """「ワークスペースルートで始まらない」を、先読みを使わずに書いた式。

    ルートを 1 文字ずつ「ここで終わる（ルートより上）／ここが違う／ここは同じ」の
    入れ子に分解する。最内はルートを全部なぞり切った先で、そこに区切りでない 1 文字が
    続けば `…/ccnavi-fork/x` のような別のディレクトリなので「外」と数える。

        ^(?:\\Z|[^c]|c(?:\\Z|[^:]|:(?:\\Z|[^\\\\/]|[\\\\/](?:…))))

    **繰り返しを 1 つも含まない。** 各段の選択肢は最初の 1 文字でほぼ排他なので、
    戻る余地がほとんど無い（設計 i0061 2.5）。判定は 1 回 3.5 マイクロ秒で、
    ルートの長さに比例して伸びるだけ。

    列挙型（前置きを 1 文字ずつ伸ばして並べる形）を採らないのは、式の長さが
    ルートの長さの 2 乗で伸びるため。436 字のルートで 141,365 字・組み立て 481 ms に
    なり、判定の期限（3 秒）に達してしまう（設計 i0061 2.7）。

    引数には `real_root` で正規化したパスを渡す。`^` は呼ぶ側が書く。
    """
    # 最内に `\Z` を置いてはいけない。ここは「ルートを最後までなぞり切った」位置なので、
    # そこで文字列が終わるなら対象はルート**そのもの**で、中と数えるのが正しい。
    # `\Z` を混ぜると、`Glob` を `path` 省略で呼んだとき（対象が cwd = ルートになる）
    # のような、いちばん普通の呼び出しが「外」と誤判定される。
    # 各段の `\Z` は「ルートより上」を拾うためのもので、こことは意味が違う。
    body = _differs("/")
    for ch in reversed(root):
        body = "(?:" + _NOT_ROOT_END + "|" + _differs(ch) + "|" + _same(ch) + body + ")"
    return body


def fill_root(text: str, root: str) -> str:
    """モデルへ渡す文の `{root}` を、ワークスペースルートの実パスに置き換える。

    文面で sh を案内するときは `{root}` から書く（スクリプトはワークスペースにしか無く、
    ワークツリーやプロジェクトの中からは相対の綴りが届かない）。綴りは glob に埋めるのと
    同じ形で、区切りは `/`。ルートが渡らない読み方（診断で `--root` が無い）では
    置き換えずにそのまま返す。
    """
    if not root or ROOT_PLACEHOLDER not in text:
        return text
    return text.replace(ROOT_PLACEHOLDER, root_glob(root))


# every を書かなかったときの刻み。当たるたびが渡す回になる。
EVERY_DEFAULT = 1


def readable_every(written: object) -> int:
    """書かれた `every` から読み取れる刻み。刻みとして読めない値なら 0。

    読めるのは 1 以上の整数だけ。`0` と負は刻みにならず、`"5"` のような文字列は
    YAML が数として読まなかった値（引用符を書いた）なので、こちらも読まない。
    真偽値は `True == 1` で整数として通ってしまうので、先に外す。

    読めなかったときに何をするかは呼ぶ側が決める。判定（_build）は 1 に倒して
    毎回渡す側へ、--lint は error にして名指しする。同じ「読めるか」を 2 か所で
    別々に書くと、黙って無視される値と咎められる値がずれる。
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
class Rule:
    """探すものと、見つけたときに言うことの組。"""

    # id は報告でルールを名指しするための名前。壊れたものを指させるように。
    # 層の和では `lib:schema` のように層の名前が前に付く（ruleload.prefix_ids）。
    id: str = ""
    # bare_id は書かれたままの id。層の名前を添える前の綴りで、層をまたいで
    # 同じルールかどうかを見るときの鍵になる（設計 11.4「重複は後ろを捨てる」）。
    # id から前置きを剥がして求める形にすると、`:` を含む id を書いた人の定義が
    # 剥がされる側に倒れるので、書いたときの綴りをそのまま持つ。
    bare_id: str = ""
    # source はこのルールが来た層（common / self / プロジェクトの名前）。記録の
    # `source` 欄と `--explain` がこれを読む（設計 11.9）。
    source: str = ""
    # match は対象のツール名を "|" で並べたもの。"Write|Edit" など。
    match: str = ""
    # glob は fnmatch と同じ意味の glob。文字列全体に当たるので、部分一致が
    # 欲しければ前後に "*" を自分で書く。"*git push*"、"*.pem"、"*/secrets/*" など。
    # 詳しくは globmatch.translate を参照。
    glob: str = ""
    # regex は本当に正規表現が要るときの逃げ道。これに手を伸ばしたルールこそ
    # いちばん厳しく見直す対象なので、glob の別の綴りではなく別の欄にしてある。
    # glob では書けない「語の左側の切れ目」が要るときも、こちらを使う。
    regex: str = ""
    # message は、なぜ止めたかと、代わりに何をすればよいかを言う。
    message: str = ""
    # additionalContext は、当たったときにモデルへ渡す文。message と違って
    # 止められた側ではなく進む側に向けた言葉で、allow でも ask でも deny でも
    # 応答の `additionalContext` に載る。通すが踏まえてほしいことを書く。
    additional_context: str = ""
    # additionalContextOnce は、1 つの文脈（セッション、サブエージェントならその 1 回の
    # 起動）で最初に当たったときだけ渡す文。セッションの開始（起動・再開・compact の後）で
    # 忘れる。毎回読ませる必要が無い長い説明のために。additionalContext と両方あれば、
    # 初回は両方を並べ、2 回目からは additionalContext だけを渡す。
    additional_context_once: str = ""
    # additionalContextFile / additionalContextOnceFile は、文の代わりに（または文に
    # 続けて）本文を渡すファイル。ルートからの相対パスで、行き先のワークツリーにあれば
    # そちらを、無ければルートのものを読む。無ければ何も足さない。長さは
    # ctxfile.MAX_CHARS で切り、切ったことを本文の末尾に添える。
    additional_context_file: str = ""
    additional_context_once_file: str = ""
    # every は「渡す回」の刻み。当たった回数がこの倍数になった回だけが渡す回になり、
    # additionalContext は渡す回のたび、additionalContextOnce は渡す回の最初の 1 回に
    # 渡る（ctxfile.for_rules）。既定は 1 で、これは「当たるたびが渡す回」＝ every を
    # 書かないときと同じ。書いていないときの場合分けをどこにも持たないための既定値。
    every: int = 1
    # every_written は書かれたままの値。読めない値（0・負・整数でない）でもルールは
    # 組み上げ、every は 1（毎回渡す）に倒す。ここで弾いてルールごと捨てると、--lint の
    # 名指しが `allow[3]` の形になり、どの id を直せばよいかを言えなくなる。咎めるのは
    # --lint の仕事で、そのために書かれた値をそのまま持つ。書いていなければ None。
    every_written: object = None
    # decision はこのルールが置かれていたタイプ。当たったルールを 1 件だけ
    # 取り出しても、それがどの判定だったのかを言えるようにする。
    decision: str = ""
    # root は読んだときのワークスペースルート。文面の `{root}` を、モデルへ渡すときに
    # 置き換える先（spoken_message）。書いた文面（message）は置き換えずに持つ。
    # 報告と --explain は書いた綴りを出す（glob / regex と同じ扱い）。
    root: str = ""

    # degraded_message は、ルールが書かれたとおりに組み立てられず、守りを消さないために
    # 別の形で効かせているときに、止められた側へ返す文面。`{!root}` を展開できなかった
    # `deny` / `ask` がこれにあたる（_ungeneratable）。書いた文面（message）を上書きすると
    # 書いた人の言葉が消え、`--explain` と記録にも出なくなるので、別の欄に持つ。
    degraded_message: str = ""

    compiled: re.Pattern | None = None

    def spoken_message(self) -> str:
        """モデルへ渡す文面。`{root}` をワークスペースルートの実パスにしたもの。

        組み立てに失敗して別の形で効かせているときは、書いた文面ではそれを説明できない
        （「外への書き込みです」としか言わない）ので、そちらを先に返す。
        """
        return fill_root(self.degraded_message or self.message, self.root)

    def key(self) -> tuple:
        """層をまたいで「同じ定義」と言えるかどうかの鍵（設計 11.4、11.8）。

        比べるのは書いた綴りではなく、`{root}` を置き換えたあとの式。共通層と
        プロジェクトの層に同じ `{root}/...` を書いた定義は、置き換え先が同じ
        ワークスペースルートなので、ここで一致する。書いた綴りで比べると、
        同じ場所を指す 2 本を別物として両方効かせることになる。
        """
        return (
            self.bare_id,
            self.match,
            self.compiled.pattern if self.compiled else "",
            self.message,
            self.additional_context,
            self.additional_context_once,
            self.additional_context_file,
            self.additional_context_once_file,
            # 刻みだけが違う 2 件は別の定義。同じ文を違う頻度で渡すルールを、
            # 層をまたいで片方に潰さない。比べるのは読み取ったあとの刻みで、
            # 書かれたままの値ではない（`5` と `"5"` は同じ刻みではない。後者は
            # 読めない値として 1 に倒れるので、そこで分かれる）。
            self.every,
            self.decision,
        )

    def matches(self, tool: str, subject: str) -> bool:
        """このルールがこのツールと対象に当たるかどうか。"""
        if self.compiled is None or not subject:
            return False
        for want in self.match.split("|"):
            if want.strip() == tool:
                return self.compiled.search(subject) is not None
        return False


@dataclass
class RuleSet:
    """ルールファイル 1 本ぶん。タイプごとに分けて持つ。

    1 本の並びにして各ルールが自分の判定を名乗る形にもできるが、分けておくと
    「強い順に見る」が並びの順そのものになる。判定の側がタイプを選び違える形を
    残さないほうが、あとからタイプを足したときに事故が起きにくい。
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

    解釈できないルールは、黙って飛ばさずに落として名指しで報告する。
    黙って消えたルールは、誰も気づかないガードの穴になるから。
    組み立てられたルールはそのまま返すので、1 件の不備がガード全体を落とさない。

    読むのは safe_load に限る。任意の Python の型を組み立てる load は、
    エージェントが書ける場所にあるファイルに向けては使わない。

    YAML の解析の失敗は ValueError に変換して投げ直す。呼び手はここが投げるものを
    OSError と ValueError の 2 つで受けていて、素通りする例外が 1 つでもあると、
    ルールファイルの書き損じがそのまま hook の異常終了になる。既定に落ちる
    経路（REQ-PRE-06）を通らずに落ちるので、壊れたファイルを直す呼び出しも
    止まる。読み手を替えるたびに、変換の側も一緒に見ること。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return parse(_decode(text, path), root)


def readable(content: bytes) -> bool:
    """その中身を、load がルールファイルとして読めるか。

    読めなければ呼び手（ruleload.load_rules）は組み込みの既定に落ちる。控えの中身を見て
    「この呼び出しの直前に既定に落ちていたか」を決めるのに使う（selfguard）。線を load と
    別に引くと、既定に落ちていたのに落ちていないと読む、あるいはその逆が起きる。
    """
    try:
        _decode(content.decode("utf-8"), "(content)")
    except ValueError:
        return False
    return True


def _decode(text: str, path: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} を YAML として読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} のルールがキーと値の並びではない")
    return data


def parse(data: dict, root: str = "", builtin: bool = False) -> tuple[RuleSet, list[Problem]]:
    """読み込み済みのルールを組み立てる。

    ファイルを開く部分と分けてあるのは、組み込みの既定ルールが同じ経路を通るため。
    別の道で組み立てると、ファイルから読んだときと既定に落ちたときで
    ルールの意味が食い違いうる。食い違えば、ガードが落ちている最中に
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
                f"書式は `{'` `'.join(SECTIONS)}` の 3 タイプで、探すものは `glob` か `regex`。"
                "`glob` は fnmatch の glob で文字列全体にヒットするので、"
                "部分一致が要るなら前後に `*` を書く",
            )
        )

    for name in SECTIONS:
        raw_section = data.get(name)
        if raw_section is None:
            continue
        if not isinstance(raw_section, list):
            problems.append(Problem(SEVERITY_ERROR, f"({name})", f"`{name}` が並びではない"))
            continue
        for i, raw in enumerate(raw_section):
            rule, problem = _build(raw, name, i, root, builtin)
            # 苦情とルールは同時に返りうる。`{!root}` を組み立てられなかった deny / ask が
            # これで、「報告する」と「守りを消さない」を両立させる唯一の道（設計 i0061 4.4）。
            # 捨てるだけにすると、生成に失敗した deny が黙って消えて素通りになる。
            if problem is not None:
                problems.append(problem)
            if rule is not None:
                rule_set.section(name).append(rule)

    return rule_set, problems


def _build(
    raw: object, section: str, index: int, root: str = "", builtin: bool = False
) -> tuple[Rule | None, Problem | None]:
    """ルール 1 件を組み立てる。返るのは (ルール, 苦情) で、どちらも無いことがありうる。

    3 通りある。

    - `(rule, None)`   組み立てられた
    - `(None, problem)` 受け付けられない。判定に使わない（REQ-RUL-08）
    - `(rule, problem)` 苦情はあるが判定には使う。`{!root}` を組み立てられなかった
      `deny` / `ask` がこれで、`match` の全部に当たる式を持って止める側に倒れる。
      捨てると「ワークスペースの外の全部」が素通りになるので、報告だけでは足りない
      （REQ-RUL-10、設計 i0061 4.3）
    """
    where = f"{section}[{index}]"
    if not isinstance(raw, dict):
        return None, Problem(SEVERITY_ERROR, where, "ルールがキーと値の並びではない")

    written_id = str(raw.get("id") or "")
    rule = Rule(
        id=written_id,
        bare_id=written_id,
        match=str(raw.get("match") or ""),
        glob=str(raw.get("glob") or ""),
        regex=str(raw.get("regex") or ""),
        message=str(raw.get("message") or ""),
        additional_context=str(raw.get("additionalContext") or ""),
        additional_context_once=str(raw.get("additionalContextOnce") or ""),
        additional_context_file=str(raw.get("additionalContextFile") or ""),
        additional_context_once_file=str(raw.get("additionalContextOnceFile") or ""),
        every=readable_every(raw.get("every")) or EVERY_DEFAULT,
        every_written=raw.get("every"),
        decision=section,
        root=root,
    )
    name = f"{section}:{rule.id}" if rule.id else where

    if ID_SEPARATOR in written_id:
        return None, Problem(
            SEVERITY_ERROR,
            name,
            f"id に `{ID_SEPARATOR}` は書けない。層の名前を添えた形"
            f"（`self{ID_SEPARATOR}id` / `<プロジェクト名>{ID_SEPARATOR}id`）と"
            "見分けが付かず、記録を読んだ人がどのファイルを直すのか決められない",
        )
    if not builtin and written_id.lower().startswith(RESERVED_ID_PREFIX):
        return None, Problem(
            SEVERITY_ERROR,
            name,
            f"`{RESERVED_ID_PREFIX}` で始まる id は組み込みの守りの名前で、"
            "ルールファイルには書けない。同じ名前で書いても組み込みは置き換わらない。"
            "別の名前にすること。"
            "組み込みを外したいなら env（CCNAVI_GUARD_CORE_FILES など）で面ごと切る",
        )
    if not rule.message and section in _NEEDS_MESSAGE:
        return None, Problem(
            SEVERITY_ERROR, name, "文面が無い。ルールは代わりに何をすべきかを言わなければならない"
        )
    if not rule.match:
        return None, Problem(SEVERITY_ERROR, name, "match が無い。どのツールにもヒットしない")
    if not rule.glob and not rule.regex:
        return None, Problem(SEVERITY_ERROR, name, "glob も regex も無い")
    if rule.glob and rule.regex:
        return None, Problem(
            SEVERITY_ERROR, name, "glob と regex の両方がある。どちらで判定するのか決められない"
        )

    # `{root}` はワークスペースルートの実パスに置き換える。書いた綴り（rule.glob / rule.regex）は
    # そのまま残し、置き換えるのは翻訳後の式だけ。報告と --explain は書いた綴りを出す。
    uses_root = ROOT_PLACEHOLDER in rule.regex or ROOT_PLACEHOLDER in rule.glob
    if uses_root and not root:
        return None, Problem(
            SEVERITY_ERROR, name, f"`{ROOT_PLACEHOLDER}` を使うにはワークスペースルートが要る"
        )

    # `{!root}` は `regex` 専用。`glob` は fnmatch に翻訳されるので、入れ子の選択肢を持つ
    # 展開結果を埋める場所が無い（設計 i0061 3.1）。
    if NOT_ROOT_PLACEHOLDER in rule.glob:
        return None, Problem(
            SEVERITY_ERROR,
            name,
            f"`{NOT_ROOT_PLACEHOLDER}` は `glob` には書けない。"
            f"`regex: '^{NOT_ROOT_PLACEHOLDER}'` と書くこと",
        )

    note: Problem | None = None
    uses_not_root = NOT_ROOT_PLACEHOLDER in rule.regex
    if uses_not_root:
        misplaced = _not_root_placement(rule.regex)
        if misplaced:
            return None, Problem(SEVERITY_ERROR, name, misplaced)
        if not root:
            return None, Problem(
                SEVERITY_ERROR,
                name,
                f"`{NOT_ROOT_PLACEHOLDER}` を使うにはワークスペースルートが要る",
            )
        suffix = _not_root_suffix(rule.regex)
        if suffix:
            note = Problem(SEVERITY_WARN, name, suffix)

    if rule.regex:
        unsupported = _unsupported(rule.regex)
        if unsupported:
            return None, Problem(SEVERITY_ERROR, name, f"{unsupported}は使えない")
        expression = (
            rule.regex.replace(ROOT_PLACEHOLDER, root_pattern(root)) if uses_root else rule.regex
        )
        if uses_not_root:
            expanded, refused = _expand_not_root(expression, root)
            if refused:
                return _ungeneratable(rule, section, name, refused)
            expression = expanded
    else:
        glob = rule.glob.replace(ROOT_PLACEHOLDER, root_glob(root)) if uses_root else rule.glob
        expression = translate(glob)

    # `glob` も `regex` も、どの機械でも大文字小文字を区別せずに当てる。区別するかを機械で
    # 変えると、同じルールが Windows では当たり Linux では当たらない。`*/.ccnavi/*` と書いた
    # 守りを `.Ccnavi/` で素通りでき、どの機械でも区別しないチケットの範囲とも食い違う。
    # ルールは人が宣言する場所の意図なので、機械の都合ではなく綴りの意味で読む
    # （phasetypes._globs / risk._factors / selfguard._folded / チケットの範囲と同じ形）。
    # 区別が要る `regex` は `(?-i:...)` で囲む。
    flags = re.IGNORECASE

    try:
        rule.compiled = re.compile(expression, flags)
    except re.error as exc:
        return None, Problem(SEVERITY_ERROR, name, f"正規表現として組み立てられない: {exc}")
    except RecursionError:
        # `re` の組み立ては入れ子を再帰で読む。捕まえないとプロセスごと落ち、
        # hook の異常終了は呼び出しを素通りさせる入口になる（設計 i0061 4.2）。
        # shellread が同じ形で `RecursionError` を安全側に倒しているのに倣う。
        if uses_not_root:
            return _ungeneratable(rule, section, name, "入れ子が深すぎて組み立てられない")
        return None, Problem(SEVERITY_ERROR, name, "入れ子が深すぎて組み立てられない")

    return rule, note


def _not_root_placement(expression: str) -> str:
    """`{!root}` の置き場所への苦情。無ければ空。

    置けるのは `^` の直後だけで、1 つの式に 1 回。展開されるのは
    「先頭から、外だと確定するところまで」を読み進める式なので、前に何かを置いても
    意味を持たない（設計 i0061 3.2）。
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
    # 「外だけを止める」はずのルールが**どの対象にも当たる**式に化ける。
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

    続けてよいかどうかは意味の問題で、機械には判定できない。展開結果が食い終わる
    位置がパスの区切りである保証は無いので、区切りを前提にした綴り
    （`^{!root}[\\\\/]foo`）は壊れてはいないが、まず書いた人の勘違い。
    止めるほどではないので warn（設計 i0061 3.3）。
    """
    rest = expression[len("^" + NOT_ROOT_PLACEHOLDER) :]
    if not rest or _SKIPS_ANYWHERE.match(rest):
        return ""
    return (
        f"`{NOT_ROOT_PLACEHOLDER}` の直後に `{rest[:16]}` が続いている。"
        "展開結果が読み終える位置はパスの区切りとは限らないので、区切りを前提にした"
        "綴りは意図どおりに動かない。任意の位置から続けるなら `.*` で受けること"
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
    # 見るのは `not_root_pattern` が作った部分だけで、書いた人が後ろに続けた式は含めない。
    # `.*` のような繰り返しをそこに書くのは許している（設計 3.3）ので、
    # 全体に掛けると正しい書き方まで弾く。
    #
    # いまの `not_root_pattern` は `re.escape` で組み立てるので、この検査は通常は発火しない。
    # 将来ここを書き換えたときに黙って契約が破れるのを防ぐための置き石で、
    # 外から叩いて発火させることはできない。
    if _GENERATED_QUANTIFIER.search(generated) or _unsupported(generated):
        return expression, "展開した式が契約を守っていない（組み立ての不具合）"
    return expression.replace(NOT_ROOT_PLACEHOLDER, generated), ""


def _ungeneratable(rule: Rule, section: str, name: str, detail: str) -> tuple[Rule | None, Problem]:
    """`{!root}` を組み立てられなかったときの倒れ方（設計 i0061 4.3）。

    強い側に倒す。`deny` と `ask` は `match` の全部に当てて止め、`allow` は
    どれにも当てない。`allow` を当たる扱いにすると ccnavi が黙る範囲が広がるので、
    そこだけ逆になる。

    **これは `{!root}` の生成失敗に限った扱い。** 正規表現の書き損じなど既存の
    壊れ方は今までどおり REQ-RUL-08 の「報告して捨てる」のまま。ここだけ特別なのは、
    守られなくなる範囲が「ワークスペースの外の全部」に広がるから。

    ## 設計から変えた 2 点（実装フェーズの判断。文書に反映すること）

    - **記録に `root-too-long` の欄は足さない**（設計 4.6 を取り消す）。`audit` の
      `REASON_*` は「判定に至らなかった理由」で、ここは判定に至っている（deny する）。
      記録には `deny` と当たったルールの id が既に残り、なぜ止めたかはこの文面が言う。
      新しい名前を足すと、判定に至らなかったものと同じ欄に別の意味が混ざる
    - **SessionStart では何も言わない**（設計 6.1 を「何もしない」に決めた）。
      256 字のルートは Windows の MAX_PATH の外で実用にならず、踏む人はまずいない。
      踏んだときはこの文面が理由と直し方を言う。めったに起きないことのために
      SessionStart を毎回重くしない
    """
    problem = Problem(SEVERITY_ERROR, name, detail)
    if section == ALLOW:
        return None, problem
    rule.compiled = re.compile(_MATCH_EVERYTHING)
    # 書いた文面（message）は残す。`--explain` と記録が書いた綴りを出す約束は、
    # glob / regex だけでなく文面にも掛かる（Rule の message のコメント）。
    rule.degraded_message = (
        f"{detail}。守りが消えるのを避けるため、ルール '{rule.id or name}' が見るツール"
        f"（{rule.match}）を止めています。ワークスペースをもっと浅い場所へ移すか、"
        "利用者に、このルールが書かれているルールファイルから外してもらってください。"
    )
    return rule, problem


def _unsupported(expression: str) -> str:
    for literal, label in _UNSUPPORTED:
        if literal in expression:
            return label
    if _BACKREFERENCE.search(expression):
        return "後方参照"
    return ""
