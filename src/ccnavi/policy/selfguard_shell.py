"""守る対象へのシェルからの書き込みを止める組み込みのルール。正規表現の組み立てと `add_rules`。

selfguard から分けた。selfguard を読まない。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from ..infra import pathcase, platformtag, settings, shellread, tree
from ..records import diaglog
from . import rules, selfguard_targets, texts

# 設定ファイルへシェルから書き込む形。戻す側と対になる、止める側。
#
# 戻せるだけでは足りない。実行後に戻すまでの間、書き換わった設定がそのまま
# 反映されている時間がある。hook の登録は同じセッションのうちに反映されるので、その間に
# 実行後のイベントごと消されると、戻す機会が来ない。だから同じ場所を実行前にも
# 止める。止めるのが基本で、戻すのは止めきれなかったぶんの備えになる。
#
# 前半の括弧が書き込みの形で、後ろに続く場所と組でヒットする。場所の名前が出ただけでは
# 止めない。`cat .ccnavi/config/common/rules.yml` も `git add <パス>` も、中身を書かない。
# 名前で止める形にすると、いちばんガードを直したいときにいちばん多く止められる。
#
#   1. リダイレクトの行き先。`>` `>>` `>|` `&>` はどれも `>` を含み、
#      shellread が `> 行き先` の形にそろえてから渡す。
#      行き先の語は、語の中の目印（下の 2 の WORD_SEP）を頭にも終わりにも置かせない。頭に
#      置かせると引用の中の `> 場所` が書き込み先に見える（引用の中の `>` は両側に目印が付くので、
#      `>` のすぐ後ろが目印になる）。語の途中の目印は、いくつ並んでも通す。引用の中の
#      空白は 1 つにつき目印 1 つ、演算子の文字（`(` `)` `&` `;` など）は両側に目印 1 つずつ
#      になるので、`"a  b/.ccnavi/..."` や `"New folder (2)/.ccnavi/..."` は目印が 2 つ続く。
#      照合の手数を増やさないため、3 つ決めてある。
#        - 適用し始めは `>` の並び（`[>|&]` の続き）の頭だけ。並びの途中の `>` から適用し直すと、
#          `>` が n 個続く入力で適用し始めが n 通りに増え、それぞれが行き先の語を読み直す。
#          後読みはルールの regex で使えない（rules._UNSUPPORTED）ので、手前の 1 文字
#          （`[>|&]` でない文字か文字列の頭）ごと適用する
#        - 行き先の語は `>` `|` `&` で始めない。shellread は演算子のあとに必ず空白を置くので、
#          語の頭にこの文字は来ない。並びと語が同じ文字を取り合うと、割り方が並びの長さの
#          ぶんだけ増える
#        - 語は「頭の 1 文字、目印も含めた途中、終わりの 1 文字」の形で書き、目印ごとに
#          区切った繰り返しにしない。どちらも同じ語にヒットするが、繰り返しの形は語の終わりを
#          探し直すたびに繰り返しの出入りを試すので、手数が倍ほどになる
#   2. 名指ししたところを必ず書き換えるコマンド。`\x00` はコマンドの切れ目に
#      shellread が置く目印で、`(^|\x00)` はコマンドの先頭を意味する。
#      語の中の切れ目（引用がつないだ空白、語の中の演算子の両側）は別の目印
#      `shellread.WORD_SEP` なので、`[^\x00]*` は同じコマンドの中の全体を指す。
#   3. sed だけは `-i` が付いた形に絞る。`sed -n 1,20p` はただの読み。`-i` は独立したオプションの語
#      （`-i` `-i.bak` `-ni` `--in-place`。GNU sed は長いオプションの省略形 `--in` `--i` も
#      受けるので `--i` で始まる語は全部）だけを数える。`feature-id` のように語の途中に
#      `-i` が出るだけのパスや式は読みなので止めない。
#
# 元と行き先がある cp / ln / install は組が違うので後ろに分けてある。見るのは
# 行き先の側だけ。元の側に出ただけの設定ファイルは読まれるだけなので止めない。
# 行き先は GNU の読み方で決める。
#
#   - `-t <dir>` / `--target-directory=<dir>` があれば、その値（_COPY_TARGET）
#   - 無ければ、選択肢でない最後の引数（_COPY_LAST）。`-t` を持たない語の並びのあとの 1 語で、
#     そのあとには後ろに回した選択肢（_COPY_OPTIONS）だけが続き、引数の並びの終わり
#     （_COPY_STOP）に届く
#
# 実行前チェックはルールの regex で適用する（rules._build を通る、組み込みのルールの 1 本）。
# 語のリストを Python で解くと、ルールの外に判定の経路がもう 1 本できて、組み込みの既定
# （builtin）と `--lint` の期待する判定（check_rules）から見えなくなる。shellread が語を空白で、
# コマンドを `\x00` で、引用がつないだ空白を WORD_SEP で区切って渡すので、語の並びは
# regex でも読める。2 つの枝は「または」で並ぶので、読み違えて両方を見る形は止まる側に倒れる。
# 読み違えて両方を見落とす形が無いように、語の分け方を _COPY_TARGET と _COPY_LAST で揃える。
_NOT_A_WORD = re.escape(shellread.SEP) + re.escape(shellread.WORD_SEP)
# 1 のリダイレクトと行き先の語。手数の約束（適用し始めと語の頭）はテストが形で確かめるので、
# 名前を分けてある。
_REDIRECT = (
    rf"(?:^|[^>|&])[|&]*>[>|&]* ?"
    rf"(?:[^ {_NOT_A_WORD}>|&](?:[^ \x00]*[^ {_NOT_A_WORD}])?)?"
)
# `sed` の枝は、最初の `-i`（その語の最初の `i` まで）で決め打ちする（`(?>...)`）。語の残りと
# 2 つ目以降の `-i` は後ろの `[^\x00]*` が飲み込めるので、`where` を試せる位置は変わらず、
# ヒットする集合は変わらない（先読みは rules が受け付けないので使わない）。決め打ちしないと `-i` が
# 出るたびに末尾まで読み直し、長さの 2 乗で遅くなる（#241）。
_VERB_WRITE = r"(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*"
_SED_WRITE = r"(^|\x00)sed\b(?>[^\x00]*?[ \x01](-[A-Za-z]*?i|--i))[^\x00]*"
_WRITE_VERBS = rf"({_REDIRECT}|{_VERB_WRITE}|{_SED_WRITE})"
_COPY_VERBS = r"(^|\x00)(cp|ln|install)\b[^\x00]*"
_COPY_NAME = r"(^|\x00)(cp|ln|install)\b"

# 短い選択肢の語を読むための文字の組。短い選択肢は 1 語に束ねられ（`-vt`）、値を取る文字が
# 来たらその語の残りが値になる（`-S.bak`・`-Svt` の `vt` は値）。文字の大小は区別する
# （`-T` は行き先をディレクトリと見ない選択肢で、`-t` とは別）。
#
# - _FLAGS: 値を取らない文字。英字から `t` と、値を取る `S` `g` `m` `o` を除いたもの
#   （cp / ln / install を合わせた組）
# - _DIR_WORD: 行き先を名指しする語。束ねた頭が値を取らない文字だけで、そのあとに `t` が来る形か、
#   `--t` で始まる長い名前（GNU は一意な頭だけでも受け取る。3 つのどれでも `--t` で始まる
#   選択肢は `--target-directory` だけ）
# - _VALUED: 次の語を値に取りうる語。値を取る文字で終わる短い束（`-S`・`-vm`）か、
#   `=` を含まない `--s…` `--o…` `--g…` `--m…` `--n…`（`--suffix .bak`・`--no-preserve all`）。
#   長い名前は頭だけでも受け取るので、名前ではなく頭の 1 文字で見る。広く取りすぎても、
#   値として飛ばした `-t` の語のぶんだけ最後の引数も見るようになるだけで、止まる側に倒れる
# - _NOT_DIR: 行き先を名指ししない語。空の語、`-` で始まらない語、値を取る文字を含む短い束、
#   値を取らない文字だけの束（後ろに英字でない残りが付いてよい）、`-` だけ、`--t` 以外の長い名前
#
# _DIR_WORD と _NOT_DIR は重ならない。_VALUED の次の語が _DIR_WORD のときだけ組で読むので、
# 語の並びの割り方は 1 通りに決まる。割り方が何通りもあると、ヒットしない長い並び
# （`-S -S -S …`）で照合の手数が語の数に対して指数的に増え、判定の期限を使い切る。
_FLAGS = r"[a-fh-lnp-su-zA-RT-Z]"
_DIR_HEAD = rf"(?:-(?-i:{_FLAGS}*t)|--(?-i:t))"
_DIR_WORD = _DIR_HEAD + r"[^ \x00]*"
_VALUED = rf"(?:-(?-i:{_FLAGS}*[Sgmo])|--(?-i:[gmnos])[^ =\x00]*)"
_DASH_NOT_DIR = (
    rf"(?:-(?-i:{_FLAGS}*[Sgmo])[^ \x00]*"
    rf"|-(?-i:{_FLAGS}+)(?:[^a-zA-Z \x00][^ \x00]*)?"
    r"|-(?:[^a-zA-Z \x00-][^ \x00]*)?"
    r"|--(?-i:[^t \x00])[^ \x00]*)"
)
_NOT_DIR = rf"(?:[^- \x00][^ \x00]*|{_DASH_NOT_DIR})?"

# 行き先を前に出す書き方。値は同じ語に続けても（`-t.ccnavi`・`--target-directory=x`）、
# 次の語にしても（`-t .ccnavi`）よい。値の前に来てよいのは空白を含まない並びだけ。
# 値の後ろには元の引数が続くので、場所は書き込み用の表記（空白で閉じる）で適用する。
_COPY_TARGET = rf"{_COPY_VERBS} (?:-(?-i:{_FLAGS}*t)|--(?-i:t)[a-z-]*)[= ]?[^ \x00]*"
# 最後の引数の手前まで。`-t` を持たない語の並びと、最後の引数の前の空白。値を取る選択肢の
# 値に出た `-t`（`-S -t`）は選択肢ではない。`--` のあとは選択肢を読まないので、何が来てもよい。
# 空白までで止め、最後の引数の中は場所の表記（_COPY_PLACES など）が語の頭から適用する。
# 場所の表記が語の途中から始められると、共通レイヤーの表記のように前の 1 文字から適用する形が
# 空白をまたいで手前の語に戻り、`-t` を持つ並びまで最後の引数の側で読んでしまう。
_COPY_LAST = (
    _COPY_NAME + rf"(?: (?:{_VALUED} {_DIR_WORD}|{_NOT_DIR}))*" + r"(?: --(?: [^ \x00]*)*)?" + " "
)
# 最後の引数の語の頭から、場所の名前の手前まで。`/abs/.ccnavi` や `projects/lib/.ccnavi` のように
# 名前の前に置き場が付く。
_IN_WORD = r"[^ \x00]*"

# 名前がそこで終わる形。空白とコマンドの切れ目（`\x00`）を語の終わりとして数える。
# `[\\/]` だけで閉じていると、区切りが続かないパスが止まらずに通る。`rm -rf .ccnavi` も
# `mv .ccnavi .ccnavi.bak` も、ccnavi ディレクトリごと消す・退かす形なので、下のファイルを 1 本ずつ
# 書き換えるのと同じだけ保護が消える。
# 語の中の目印も終わりに数える。数えないと、`rm ".ccnavi x"` のように引用がつないだ
# パスが通る。
_TERM = rf"(?:[ {_NOT_A_WORD}]|$)"
# 区切りが続く形と、そこで終わる形の両方。`.ccnavi/config/project/x` にも `.ccnavi` にも
# ヒットし、`.ccnavixyz` のような別名にはヒットしない。
_END = rf"(?:[\\/ {_NOT_A_WORD}]|$)"
# 元と行き先がある cp / ln / install のための終わり。行き先（最後の引数）だけに適用するので、
# 名前のあとに語の残りが続き、そのまま引数の並びの終わりに届く形に絞る。
#
# - _COPY_OPTIONS: 行き先のあとに回した選択肢。GNU は選択肢を引数の後ろにも置けるので
#   （`cp /tmp/x .ccnavi -f`）、飛ばさないと選択肢を 1 つ足すだけで通る。値を取る選択肢は
#   値まで飛ばす（`-S .bak`・`-m 644`・`--suffix .bak`・`-S -t`）。最後の `--` も飛ばす。
#   `-t <dir>` が後ろにあれば手前の語は行き先ではないので、`-t` は飛ばさない
# - _COPY_STOP: 引数の並びの終わり。文字列の終わり、コマンドの切れ目（`\x00`）、後ろに続く
#   リダイレクト（shellread が `cp a b 2 > x` `cp a b &> x` `cp a b {fd} > x` の形にそろえる）
# - _COPY_TAIL: 名前のあとに続く語の残りと、その終わり
# - _COPY_TERM: 名前がそこで語として終わり、それが最後の引数
# - _COPY_END: 区切りが続くか、そこで終わる。どちらも最後の引数
#
# 空白を語の終わりに数えないので、`cp .ccnavi /tmp/x` のように ccnavi ディレクトリから外へ
# コピーするだけの読みは通る。切れ目とリダイレクトまで含めるので、`cp /tmp/x .ccnavi && echo` や
# `cp /tmp/x .ccnavi > /dev/null` のように後ろに何か続けた形も止まる。
_COPY_OPTIONS = (
    rf"(?: (?:{_VALUED} (?:(?:[^- \x00][^ \x00]*)?|{_DIR_WORD})|{_DASH_NOT_DIR}))*(?: --)?"
)
_COPY_STOP = r"(?:$|\x00| (?:[0-9]+ |\{\w+\} )?&?[<>])"
_COPY_TERM = _COPY_OPTIONS + _COPY_STOP
_COPY_TAIL = r"[^ \x00]*" + _COPY_TERM
_COPY_END = r"(?:[\\/]" + _COPY_TAIL + r"|" + _COPY_TERM + r")"

# 行き先を、守るものが入っているディレクトリにした形（`cp /tmp/decisions.jsonl logs/`）。
# 行き先の表記に守るファイルの名前が出ないので、上の場所の表記ではヒットしない。
#
# 止めるのは、元の側に「そこへ置くと守るものと同じ名前になる」語があるときだけ
# （holder_regex）。行き先がディレクトリというだけで止めると、`cp /tmp/notes.txt logs/` のように
# 守らないファイルを置く操作まで止まる。元の名前は表記からは決まらないことがあるので、
# 決まらない語も同じ名前として数える（止まる側に倒す）。
#
#   - 名前が守る名前の語（`/tmp/decisions.jsonl`、後ろの区切りは何個でも）
#   - 名前が決まらない語。グロブ（`*` `?` `[`）、変数と置換（`$` と逆引用。shellread は
#     `$` を残す）、ブレース（`{`）。ブレース展開は書き直しを求める形として先に止まるが、
#     表記だけでも数える
#   - 中身をそのまま置く語。`.` と `..` で終わる元（`cp -r /tmp/d/. logs/`）は、d の中身が
#     行き先の直下に並ぶ
#   - 行き先をディレクトリと見ない選択肢（`-T` / `--no-target-directory`）。`cp -rT /tmp/d logs` は
#     d の中身を logs の直下に置く
#
# 動詞には mv も入れる。mv は書き込みの側で名指ししたところに適用するが、行き先がディレクトリ
# だけの形は名指ししないので、ここで拾う。
#
# 語の並びの読み方は _COPY_LAST / _COPY_TARGET と同じ。元の語は並びのどこにあってもよいので、
# 割り方は「どの語を元の語と見るか」の数だけある。ヒットしない長い並びで照合の手数がその数だけ
# 増えないように、最初に見つかった元の語で決め打ちする（`(?>...)`、後戻りしない括り）。
# 後ろの語でヒットするなら、前の語から読んでも同じ並びを通ってヒットするので、
# 決め打ちで外す形は無い。
# 元の語の終わり（空白）も括りの中に入れる。入れないと `decisions.jsonl.bak` の頭だけで決め打ちし、
# 後ろに並べた実際の名前を見落とす。
_HOLD_NAME = r"(^|\x00)(cp|ln|install|mv)\b"
_ARG = rf"(?:{_VALUED} {_DIR_WORD}|{_NOT_DIR})"
_TARGET_FLAG = rf"(?:-(?-i:{_FLAGS}*t)|--(?-i:t)[a-z-]*)[= ]?"
_NO_TARGET_FLAG = (
    rf"(?:-(?-i:{_FLAGS}*T{_FLAGS}*)(?:[^a-zA-Z \x00][^ \x00]*)?|--(?-i:no-t)[^ \x00]*)"
)
_HEAD = r"(?:[^- \x00][^ \x00]*[\\/]|[\\/])?"
_UNKNOWN = rf"(?:[^- \x00][^ \x00]*)?[*?\[{{$`][^ \x00]*|{_HEAD}\.\.?[\\/]*"
# 行き先のディレクトリの後ろ。区切りと `/.` は何個続いても同じディレクトリ。
_DIR_TAIL = r"(?:[\\/]\.?)*"


def under(directory: str) -> str:
    """ディレクトリの表記を、語の頭からでも置き場の下からでもヒットする行き先の形にする。

    `logs` なら `logs`・`./logs`・`/abs/logs` にヒットし、`mylogs` にはヒットしない。
    """
    return r"(?:[^ \x00]*[\\/])?" + directory


def holder_regex(directory: str, names: str, *, named_only: bool = False) -> str:
    """守るものが入っているディレクトリを行き先にした cp / ln / install / mv にヒットする式。

    directory は行き先の語全体に適用する表記（under で組むか、`\\.` のように語そのもの）、
    names は守るものの名前（区切りを含まない）。元の側に names の語か、名前の決まらない語が
    あるときだけヒットする（上の _HOLD_NAME の説明）。行き先は _COPY_LAST / _COPY_TARGET と同じく、
    `-t` の値か、選択肢でない最後の引数。

    named_only を立てると、元の側に names の語があるときだけヒットする（名前の決まらない語と、
    行き先をディレクトリと見ない選択肢にはヒットしない）。
    常にヒットする土台（`.git` の 1 本）で使う。
    土台は保護を切った設定でもヒットするので、`cp ../src/*.py .` のような日常のコピーを、守るものと
    関係の無い文面で止めないため。
    """
    source = rf"(?:{_HEAD}(?:{names})[\\/]*|{_UNKNOWN})"
    flag = rf"|{_NO_TARGET_FLAG}"
    if named_only:
        source = rf"(?:{_HEAD}(?:{names})[\\/]*)"
        flag = ""
    dest = directory + _DIR_TAIL
    # 最後の引数が行き先。元の語は `--` の手前（選択肢として読む並び）にも、後ろにもありうる。
    before = (
        rf"(?>(?: {_ARG})*? (?:{source}{flag}) )"
        rf"(?:{_ARG} )*(?:--(?: [^ \x00]*)* )?{dest}{_COPY_TERM}"
    )
    after = rf"(?: {_ARG})* --(?>(?: [^ \x00]*)*? {source} )(?:[^ \x00]* )*{dest}{_COPY_TERM}"
    # `-t` の値が行き先。元の語は `-t` の前にも後ろにもありうる。
    target_after = rf"(?>[^\x00]*? {source} )(?:[^\x00]* )?{_TARGET_FLAG}{dest}(?:[ \x00]|$)"
    target_before = rf"(?>[^\x00]*? {_TARGET_FLAG}{dest} )(?:[^\x00]* )?{source}(?:[ \x00]|$)"
    forms = "|".join((before, after, target_after, target_before))
    return _folded(rf"{_HOLD_NAME}(?:{forms})")


def copy_destination_regex(last_place: str, target_place: str) -> str:
    """cp / ln / install の行き先にヒットする式。

    last_place は最後の引数の語の頭から適用する表記（copy_last_place で組む）、target_place は
    `-t` の値に適用する表記で、値の語の中のどこから適用してもよい。
    """
    return rf"{_COPY_LAST}{last_place}|{_COPY_TARGET}{target_place}"


def copy_last_place(clause: str) -> str:
    """場所の表記を、最後の引数の語として適用する形にする。名前がそこで終わる形も、その下のものもヒットする。"""
    return _IN_WORD + clause + _COPY_END


# `.ccnavi/` は ccnavi ディレクトリの既定のパス。その下には各レイヤーの設定 3 本と、
# 配点が呼ぶスクリプトが入る。どちらも判定の中身そのものなので、ccnavi ディレクトリごと止める。
# 既定のパスをここに書いておくのは、ccnavi ディレクトリの名前を動かしていないワークスペースが、
# 設定の受け渡しに依らずに守られるようにするため。動かしてある場合は project_home_clause が足す。
#
# `.claude` の側は、その下の名前を絞ってある（`worktrees/` は守る対象ではない）。
# だから ccnavi ディレクトリと違って、名前がそこで終わる形は `_TERM` で閉じる。`_END` にすると
# `.claude/` に続くパス全部が入り、ワークツリーの片付けまで止まる。区切り 1 つで終わる形
# （`.claude/`）は `.claude` そのものなので適用する。
# 適用しないと `mv /tmp/settings.json .claude/` や
# `cp -t .claude/ /tmp/settings.json` のように、行き先をディレクトリにしただけで通る。
# チケットの sh は `.ccnavi/scripts/` にあるので、`.claude` の側で守るのは hook と設定ファイルだけ。
#
# `logs/` は記録と state の置き場（`logs/decisions.jsonl` と `logs/state/`）。どちらも判定が読むので
# 名前を絞って守る。ローテートした記録（`logs/decisions.<日時>.jsonl`、prune）も同じ形で守る。
# 判定は読まないが、「記録が無い = 動かなかった」を読む元で、シェルから消せると自分の呼び出しの
# 記録を消せる。消すのはセッションの開始と、ターミナルから打つ `ccnavi-user-prune.sh`
# （内部は `--prune`）だけ。
# `logs/` の下の git のラッパースクリプトの記録は、消しても判定に影響しないので守らない。
_PLACES = (
    r"\.claude(?:[\\/](hooks" + _END + r"|settings[\w.-]*\.json)|[\\/]?" + _TERM + r")",
    r"\.ccnavi" + _END,
    r"logs[\\/](decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state|archive)" + _END,
    r"ccnavi-git\.sh",
)
_COPY_PLACES = (
    _IN_WORD + r"\.claude(?:[\\/](hooks|settings)" + _COPY_TAIL + r"|[\\/]?" + _COPY_TERM + r")",
    # 行き先が ccnavi ディレクトリそのもの（`cp /tmp/x .ccnavi`）でも止める。
    _IN_WORD + r"\.ccnavi" + _COPY_END,
    _IN_WORD + r"logs[\\/](decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state|archive)" + _COPY_END,
    _IN_WORD + r"ccnavi-git\.sh" + _COPY_TAIL,
)
# 守るものが入っているディレクトリと、その中の守る名前（holder_regex）。既定の表記のぶん。
# `logs/` の中の記録と state の置き場と、閉じたチケットの退避（archive）。`logs/` の下の
# git のラッパースクリプトの記録は守らないので、名前で絞る。`.claude/` と `.ccnavi/` と
# `logs/state/` は、行き先がそのディレクトリなら名前を問わずに止まる（上の場所の表記）ので、
# ここには無い。
#
# ROOT_NAMES はワークスペースルートの直下に置く、守るものを含む名前。`cp -r /tmp/.ccnavi .` は
# ccnavi ディレクトリの全体を置き換える。行き先はルートを指す表記（`.` と、ルートの絶対パス）
# だけで見る（_moved_holders がルートの表記と組む）。`.` は居場所がルートでなくてもヒットするが、
# そこへ `.ccnavi` や `.claude` をコピーする用事は無い。
_HOLDERS = ((under(r"logs"), r"decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state|archive"),)
ROOT_NAMES = (r"\.ccnavi", r"\.claude", r"logs")


def shell_write_regex(
    bin_path: str = "",
    *extra_clauses: tuple[str, str, str],
    holders: tuple[tuple[str, str], ...] = (),
) -> str:
    """設定ファイルへシェルから書き込む形。実行ファイルのパスは設定で動くので、
    ここで組み立てる。

    実行ファイルを場所の一覧に足すのは、そこが判定器の実体だから。差し替えられると
    ルールを 1 行も変えずに判定そのものを入れ替えられる。しかも置き場は
    `.gitignore` の中にあることが多く、そうなると実行後チェックからも見えない。

    extra_clauses は設定で動く場所のパスで、（書き込み用, 最後の引数用, `-t` の値用）の
    3 つ組で来る。ccnavi ディレクトリ（project_home_clause）と共通レイヤーの 3 本
    （common_shell_clause）が来る。既定の名前は _PLACES に書いてあるので、ここで足すのは
    動かしてある場合のパスになる。レイヤーの設定は行き先の判定に使うので、書けるとエージェントが
    自分のルールを緩められる。空のパスは足さない。

    3 つに分けるのは、閉じ方が違うから。

    - 書き込み用は空白で閉じる。同じパスを cp / ln / install に足すと、元の側に出ただけでヒットする
    - 最後の引数用は引数の列の終わりで閉じる（_COPY_END / _COPY_TERM）
    - `-t` の値は後ろに元の引数が続くので、書き込み用の閉じ方を使う。ただし値の 1 語の中で
      ヒットするパスに限る。共通レイヤーの 3 本のパスは前の 1 文字（空白でもよい）から適用するので、
      `-t /tmp/out <共通レイヤーの 1 本>` の元の側にヒットしてしまう。
      `-t` の値はディレクトリなので、
      ファイルを指す共通レイヤーのパスは `-t` の側には足さない。
      `-t <共通レイヤーの入っているディレクトリ>` は
      holders の側で、元の名前と組で止める

    holders は行き先を守るものが入っているディレクトリにした形（holder_regex に渡す組）。
    既定のパスのぶん（_HOLDERS）と実行ファイルのぶん（binary_holders）はここで足すので、
    渡すのは設定で動く場所のぶん（guard_shell_regex）。
    """
    places = [*_PLACES]
    copy_places = [*_COPY_PLACES]
    target_places = [*_PLACES]
    binary = binary_clause(bin_path)
    triples = ((binary, binary_clause(bin_path, copy=True), binary), *extra_clauses)
    for clause, copy_clause, target_clause in triples:
        if clause:
            places.append(clause)
        if copy_clause:
            copy_places.append(copy_clause)
        if target_clause:
            target_places.append(target_clause)
    where = _folded("(" + "|".join(places) + ")")
    copy_where = _folded("(" + "|".join(copy_places) + ")")
    target_where = _folded("(" + "|".join(target_places) + ")")
    held = [holder_regex(d, n) for d, n in (*_HOLDERS, *binary_holders(bin_path), *holders)]
    copies = copy_destination_regex(copy_where, target_where)
    return "|".join((rf"{_WRITE_VERBS}{where}", copies, *held))


def _folded(clause: str) -> str:
    """場所のパスを、大文字小文字を区別しない形にする。どの機械でも同じ。

    大文字小文字を区別しない機械では `.Ccnavi/scripts/count.sh` は
    `.ccnavi/scripts/count.sh` そのもので、消せば実際のファイルも消える。区別すると、表記を
    1 文字変えるだけで ccnavi ディレクトリの中が書ける。
    区別する機械でも区別せずに適用する。ルールの glob とチケットの範囲はどの機械でも
    区別しないので、
    保護だけが機械でヒットの仕方を変えると、同じパスの扱いがルールと保護で食い違う。

    ここで囲むのは場所のパスだけだが、組み込みの保護も `rules._build` を通るので、
    式全体が `re.IGNORECASE` でヒットする（regex も glob と同じく大文字小文字を区別しない）。
    コマンドの名前（`rm` / `cp`）も結果として区別されなくなる。`RM` が走る保証は無いので
    保護が増えるわけではないが、`deny` が広がる側なので、そのままにしてある。区別が要るルールは
    `(?-i:...)` で囲む。
    """
    return f"(?i:{clause})"


def project_home_clause(project_home: str, *, copy: bool = False) -> str:
    """ccnavi ディレクトリのパスを、シェルの書き込みに適用する形に直す。

    ccnavi ディレクトリの下はすべて守る。レイヤーの設定 3 本も、配点が呼ぶスクリプトも、そこに入る。
    既定の名前（`.ccnavi`）は _PLACES が持っているので、ここが返すのは動かして
    ある場合のパス。区切りはどちらの表記にも適用し、名前がそこで終わる形（ccnavi ディレクトリごと
    消す・退かす）にも適用する。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ適用する形を返す。最後の引数の
    語の頭から適用し（_IN_WORD）、
    既定の名前のコピーする側（_COPY_PLACES）と同じ `_COPY_END` で閉じるので、
    `cp <ccnavi ディレクトリ>/config/project/rules.yml /tmp/x` のように
    外へコピーするだけの読みは通り、
    行き先が ccnavi ディレクトリそのものかその下なら止まる。
    """
    parts = [re.escape(p) for p in _home_name(project_home).split("/") if p]
    if not parts:
        return ""
    if copy:
        return _IN_WORD + r"[\\/]".join(parts) + _COPY_END
    return r"[\\/]".join(parts) + _END


def project_home_glob(project_home: str) -> str:
    """ccnavi ディレクトリの下を、名指しのツールで止める glob。`*/.ccnavi/*` の形。"""
    home = _home_name(project_home)
    return f"*/{home}/*" if home else ""


def _home_name(project_home: str) -> str:
    """ccnavi ディレクトリの名前。

    前後の区切りは取り除き、区切りを含むパスはそのまま 1 つの節にする。
    """
    return (project_home or "").replace("\\", "/").strip("/")


def binary_clause(bin_path: str, *, copy: bool = False) -> str:
    """実行ファイルのパスを、適用する形に直す。

    末尾の 2 要素だけを使う（PATH の実体だけは 3 要素。`bin/ccnavi` では別の道具にヒットしすぎる）。
    絶対で書かれても相対で書かれても同じ形にヒットし、
    ファイル名だけに絞ると、同じ名前の無関係なファイルまで拾う。
    区切りはどちらの表記にも適用する。ルールは 1 回書いてどの機械でも同じ意味で
    なければならない、という globmatch と同じ約束をここでも守る。

    指す先は振り分けの sh で、hook が実際に走らせるのは実体のほう（platformtag）。
    sh だけを守ると、実体を差し替えれば判定が入れ替わるので、組み立ての置き場にも適用する。
    どこを置き場と読むかは sh の名前だけで決め、platformtag.launched_executable と揃える。
    段の数など別の条件を足すと、守る場所とバックアップする場所が食い違う。

    - 名前が LAUNCHER_NAME なら、sh の末尾 2 要素（`scripts/ccnavi-launcher.sh`）と、
      その 1 つ上の親の下の `bin/<os>-<arch>/`。sh の隣の `<os>-<arch>/` は sh が探さない
      ので適用しない。親が無い浅いパスでは `bin/<os>-<arch>/` を親を問わずに適用する。判定に
      渡るパスは絶対パスなので、名指しのツールはそれでも止まる
    - それ以外の名前は実行ファイルそのもの。末尾 2 要素だけに適用する

    既定の置き場（`.ccnavi/`）は ccnavi ディレクトリを守るルールでも止まるが、ccnavi
    ディレクトリを動かしたときや、既定でないパスを指したときはここでしか止まらない。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ適用する形を返す。最後の引数の
    語の頭から適用し（_IN_WORD）、名前の後ろは引数の残り（_COPY_TAIL）で閉じ、組み立ての置き場の
    下も空白をまたがない。
    書き込み用の形は終わりを決めないので、`(?:[\\/][^\x00]*)?` が空白をまたいで
    `cp <置き場>/bin/<os>-<arch>/ccnavi /tmp/x` のような外へコピーするだけの読みにもヒットする。
    名指しのツール（BINARY_RULE_ID）は書き込み用の形に `$` を足して使う。
    """
    if not bin_path:
        return ""
    parts = [p for p in re.split(r"[\\/]", bin_path) if p and p not in (".", "..")]
    if not parts:
        return ""
    if parts[-1] == platformtag.LAUNCHER_NAME:
        sh = r"[\\/]".join(re.escape(p) for p in parts[-2:])
        home = re.escape(parts[-3]) + r"[\\/]" if len(parts) >= 3 else ""
        # PATH の ccnavi（bin/ に実体が無いとき sh が起動するもの）。末尾 3 要素で適用する。
        # 2 要素（`bin/ccnavi`）だと別のプロジェクトの `./bin/ccnavi` にまでヒットする。
        spellings = _path_spellings(bin_path)
        on_path = "".join(f"|{p}" for p in spellings)
        on_path_copy = "".join(f"|{p}{_COPY_TAIL}" for p in spellings)
        built = rf"{home}bin[\\/]{_BUILD_DIR}"
        if copy:
            return rf"{_IN_WORD}(?:{sh}{_COPY_TAIL}|{built}{_COPY_END}{on_path_copy})"
        return rf"(?:{sh}|{built}(?:[\\/][^\x00]*)?{on_path})"
    plain = r"[\\/]".join(re.escape(p) for p in parts[-2:])
    return _IN_WORD + plain + _COPY_TAIL if copy else plain


def _path_spellings(launcher: str = "") -> list[str]:
    """PATH の ccnavi を適用する形（末尾 3 要素）。書かれたパスと、リンクを解いた先の両方。

    置き場のディレクトリ（`~/.local/bin` 全体）は守らない。無関係な道具を入れる操作まで止まる。
    PATH に無ければ空。
    """
    exe = platformtag.path_executable(skip=launcher)
    if not exe:
        return []
    found = []
    for path in (exe, os.path.realpath(exe)):
        every = [p for p in re.split(r"[\\/]", path) if p and p not in (".", "..")]
        spelled = r"[\\/]".join(re.escape(p) for p in every[-3:])
        if len(every) <= 3:
            # 浅い実体（`/bin/ccnavi`）は、区切りで始まる形だけに適用する。
            # 付けないと、別のプロジェクトの `./bin/ccnavi` にもヒットする。
            spelled = r"(?<![\w.~-])[\\/]" + spelled
        if every and spelled not in found:
            found.append(spelled)
    return found


def binary_holders(bin_path: str) -> tuple[tuple[str, str], ...]:
    """実行ファイルが入っているディレクトリと、その中の名前（holder_regex に渡す組）。

    binary_clause と同じ末尾 2 要素で読む。PATH の実体のディレクトリは足さない
    （`~/.local/bin` 全体を守ると、無関係な道具の導入まで止まる）。
    `cp /tmp/ccnavi dist/` のように、行き先を実行ファイルの入っている
    ディレクトリにした形を止める。振り分けの sh なら、sh の入っている
    ディレクトリと、組み立ての置き場（`<親>/bin/` の下の `<os>-<arch>`、`<親>` の下の `bin`）。
    ディレクトリの表記が無い名前だけの表記（`ccnavi`）は、入っている場所が決まらないので足さない。
    """
    parts = [p for p in re.split(r"[\\/]", bin_path or "") if p and p not in (".", "..")]
    if len(parts) < 2:
        return ()
    found = [(under(re.escape(parts[-2])), re.escape(parts[-1]))]
    if parts[-1] == platformtag.LAUNCHER_NAME and len(parts) >= 3:
        home = re.escape(parts[-3])
        found.append((under(home + r"[\\/]bin"), _BUILD_DIR))
        found.append((under(home), "bin|" + re.escape(parts[-2])))
    return tuple(found)


# 機械ごとの組み立ての置き場。`bin/` の下に並ぶ。
_BUILD_DIR = r"(?:" + "|".join(platformtag.SYSTEMS) + r")-[a-z0-9_]+"

# 守る場所をコピーして直すための下書きの置き場（.ccnavi/docs/scratchpad.md）。
# ワークスペースルートからの相対。
HANDOFF = ("wip", "handoff")


def _handoff_place(root: str) -> re.Pattern[str]:
    """ツリー（ワークスペースルート、`.claude/worktrees/<名前>`、`projects/<名前>`、その中の
    ワークツリー）の直下の `wip/handoff/` の下にヒットする式。
    適用する先は fold して `/` 区切りにした
    絶対パス。"""
    base = pathcase.fold(os.path.realpath(root)).replace("\\", "/")
    return re.compile(
        re.escape(base)
        + r"(?:/projects/[^/]+)?(?:/\.claude/worktrees/[^/]+)?"
        + r"/"
        + "/".join(re.escape(part) for part in HANDOFF)
        + r"/"
    )


def _handoff_linked(subject: str, spelled: str) -> bool:
    """置き場の中の対象が、リンクを通る・ハードリンクでほかの名前とつながる・`..` を含むか。

    確かめられなければ（OSError）つながっているとみなす。
    """
    if ".." in spelled.split("/"):
        return True
    try:
        if pathcase.fold(os.path.realpath(subject)).replace("\\", "/") != spelled:
            return True
        return os.path.isfile(subject) and os.stat(subject).st_nlink > 1
    except OSError:
        return True


def handoff_write_exempt(root: str) -> Callable[[str], str] | None:
    """名指しのツール（Write / Edit）の保護から、下書きの置き場（`wip/handoff/`）の中を外す関数。

    守る場所のファイルを直すときは、置き場へコピーしてコピーを直す（.ccnavi/docs/scratchpad.md）。
    コピーは守る場所と同じ相対パス（`wip/handoff/.ccnavi/scripts/x.sh` など）に置くので、
    パスに `.ccnavi/` や `.claude/` が入っていて、守る場所の式にヒットする。コピーにはルールとしての
    効力が無く、ユーザが取り込むまで何も変わらないので、書けるようにする。

    外すのは、次をすべて満たすときだけ。1 つでも外れれば対象をそのまま返す。

    - ツリー（ワークスペースルート、`.claude/worktrees/<名前>`、`projects/<名前>`、
      その中のワークツリー）の直下の `wip/handoff/` の下。`docs/wip/handoff/` のように
      ツリーの直下でない綴りは外さない
    - 対象に `..` の段が無く、リンクを解いた実パスが綴りと同じ（途中にリンクが無い）
    - 既にあるファイルが、ハードリンクでほかの名前とつながっていない（つながった先が
      守る場所なら、上書きはその先も書き換える）

    root が空なら何も外さない。大文字小文字はどの機械でも区別しない。
    """
    if not root:
        return None
    pattern = _handoff_place(root)

    def exempt(subject: str) -> str:
        spelled = pathcase.fold(subject).replace("\\", "/")
        if not pattern.match(spelled) or _handoff_linked(subject, spelled):
            return subject
        return ""

    return exempt


def handoff_delete_path(root: str, path: str) -> bool:
    """削除の対象 1 つ（絶対パス。`..` は畳まない綴り）が、置き場の中で、つながっていないか。

    `handoff_write_exempt` と同じ条件（ツリー直下の `wip/handoff/` の下、`..` なし、
    リンクを通らない、ハードリンクでほかの名前とつながらない）。通すのは削除だけで、
    書き込みは今のとおり止める。
    """
    if not root or not os.path.isabs(path):
        return False
    spelled = pathcase.fold(path).replace("\\", "/")
    return bool(_handoff_place(root).match(spelled)) and not _handoff_linked(path, spelled)


# 削除の対象として読む語。シェルが展開しない文字だけ（`~` `$` `*` `{` `=` `!`・バックスラッシュ・
# 引用符・空白は、シェルが別のパスに直す）。素の rm とラッパーの rm が同じ検査を通る
# （`handoff_delete_word`）。
_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./:@+,%-]+")


def handoff_delete_word(root: str, base: str | None, word: str) -> bool:
    """削除の対象の語 1 つが、置き場の中の、つながっていないファイルか。

    語が許す文字だけで、絶対パスか、`base`（そのコマンドが居る場所）から解ける相対パスのとき、
    `handoff_delete_path` で決める。`base` が無ければ絶対パスだけ。
    """
    if not _PLAIN_WORD.fullmatch(word):
        return False
    if os.path.isabs(word):
        return handoff_delete_path(root, word)
    if not base:
        return False
    return handoff_delete_path(root, os.path.join(os.path.realpath(base), word))


def _delete_targets(words: list[str]) -> list[str]:
    """削除コマンド 1 本の語から、対象の語を取り出す。削除と読めない形・読み切れない形は空。"""
    k = 1 if words and _base(words[0]) in ("sh", "bash") else 0
    if k >= len(words):
        return []
    name = _base(words[k])
    if name == "rm":
        rest = words[k + 1 :]
    elif name in ("git", "ccnavi-git.sh") and k + 1 < len(words) and words[k + 1] == "rm":
        rest = words[k + 2 :]
    else:
        return []
    targets: list[str] = []
    dashes = False
    for word in rest:
        if not dashes and word == "--":
            dashes = True
        elif dashes or not word.startswith("-"):
            targets.append(word)
        elif word.startswith("--pathspec"):
            return []
    return targets


def handoff_delete_exempt(root: str) -> Callable[[str], str] | None:
    """シェルの保護から、下書きの置き場（`wip/handoff/`）の中だけを削除するコマンドを外す関数。

    不要になった下書きを消せないと、取り込みが「下書きがある」と断るので先へ進めない。
    削除のコマンド（`rm`・`git rm`・`ccnavi-git.sh rm`）の対象がすべて、`handoff_delete_word` で
    置き場の中と読めるときだけ、そのコマンドを対象から取り除く。
    1 つでも外れる語（守る場所そのもの・変数や置換を含む語・`>` など）があれば、そのコマンドは残す。
    ほかのコマンドは、そのまま残す。書き込み（`cp` `mv` やリダイレクト）は外さない。

    外すのは絶対パスの語だけ。この対象は `sh -c` や `find -execdir` の中身も別の層として含み、
    層の中の `cd` は見えないので、相対パスの行き先を決められない。相対パスは、居場所を追える
    `ccnavi-git.sh rm` の側（wrapguard）で外す。
    """
    if not root:
        return None

    def exempt(subject: str) -> str:
        commands = subject.split(shellread.SEP)
        kept = []
        for command in commands:
            targets = [] if shellread.WORD_SEP in command else _delete_targets(command.split(" "))
            if targets and all(handoff_delete_word(root, None, t) for t in targets):
                continue
            kept.append(command)
        return shellread.SEP.join(kept) if len(kept) < len(commands) else subject

    return exempt


def handoff_link_target(root: str) -> Callable[[str], str] | None:
    """名指しのツールで、置き場（`wip/handoff/`）の中のリンク・ハードリンクに書く形だけを残す関数。

    名指しのツールの保護のうち、`except` の式で置き場の中を外すものは、式がファイルを見ないので、
    守る場所とハードリンクでつながったコピーへの書き込みも外れる（`rules.yml` に利用者が書く
    ルールの `except` がこれにあたる）。組み込みの 4 本（`add_named_rules`）は
    `handoff_write_exempt` でつながったコピーを外さないが、利用者のルールには効かない。
    組み込みの 1 本（HANDOFF_LINK_RULE_ID）が
    この関数で、置き場の中のつながった対象だけを残して止める。つながっていないコピーと、置き場の外は
    空を返す（適用しない）。
    """
    if not root:
        return None
    pattern = _handoff_place(root)

    def target(subject: str) -> str:
        spelled = pathcase.fold(subject).replace("\\", "/")
        if pattern.match(spelled) and _handoff_linked(subject, spelled):
            return subject
        return ""

    return target


# リンクを作るコマンド。`ln`・`link` と、リンクを作る選択肢を付けた `cp`
# （`-l`・`-s`・`--link`・`--symbolic-link` と、その文字を含む短い選択肢の並び）。
_LINKERS = frozenset({"ln", "link", "gln"})
_LINK_PREFIX = frozenset(
    {"sudo", "env", "command", "exec", "nice", "nohup", "time", "busybox", "xargs", "timeout"}
)
_CP_LINK_OPTIONS = ("--link", "--symbolic-link")
_SEGMENTS = re.compile(r"[\x00;&|\n()`]+")


def _makes_link(words: list[str]) -> bool:
    k = 0
    while k < len(words) and (
        _ASSIGNMENT.match(words[k]) or _base(words[k]) in _LINK_PREFIX or words[k].startswith("-")
    ):
        k += 1
    if k >= len(words):
        return False
    name = _base(words[k])
    if name in _LINKERS:
        return True
    if name in ("cp", "gcp"):
        for word in words[k + 1 :]:
            option = word.partition("=")[0]
            # GNU の長い選択肢は省略できる（`--li` は `--link`、`--sym` は `--symbolic-link`）。
            if len(option) >= 3 and any(full.startswith(option) for full in _CP_LINK_OPTIONS):
                return True
            if re.fullmatch(r"-[A-Za-z]*[ls][A-Za-z]*", word):
                return True
    return False


def _base(word: str) -> str:
    name = word.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name[:-4] if name.endswith(".exe") else name


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")


def handoff_link_command(root: str, cwd: str = "") -> Callable[[str], str] | None:
    """シェルで、置き場（`wip/handoff/`）の中にリンクを作る形だけを残す関数。

    置き場に守る場所へのリンク（シンボリックリンクもハードリンクも）を置くと、次の 1 本で
    リンクの先へ書ける（コピーの例外は名指しのツールのパスで決めるため）。`ln`・`link`・リンクを
    作る `cp` があり、コマンド行に `handoff` の綴りがあるか、作業ディレクトリが置き場の中なら残す
    （止める）。行き先は変数や `cd` で組み立てられるので、読み分けず止める側に倒す。
    """
    if not root:
        return None
    pattern = _handoff_place(root)
    here = pathcase.fold(os.path.realpath(cwd)).replace("\\", "/") + "/" if cwd else ""
    inside = bool(here) and bool(pattern.match(here))

    def target(subject: str) -> str:
        text = subject.replace(shellread.WORD_SEP, " ")
        if not any(_makes_link(seg.split()) for seg in _SEGMENTS.split(text)):
            return ""
        if inside or "handoff" in text.casefold():
            return subject
        return ""

    return target


def guard_shell_regex(
    root: str, bin_path: str = "", project_home: str = "", common_files: tuple[str, ...] = ()
) -> str:
    """この設定で組んだ、シェルから書き込む形。

    実行前チェック（add_rules）と組み込みの既定（builtin）の両方がここから取る。既定の
    側だけモジュールを読んだ時点の空の設定で組むと、ccnavi ディレクトリや実行ファイルを
    動かしたワークスペースでは、ルールファイルが破損したときだけ動かした先への書き込みが
    止まらなくなる。2 か所で組むと、片方だけが弱い側になる。
    """
    home = project_home_clause(project_home)
    clauses = [(home, project_home_clause(project_home, copy=True), home)]
    clauses.extend(
        (common_shell_clause(root, path), common_shell_clause(root, path, copy=True), "")
        for path in common_files
    )
    return shell_write_regex(
        bin_path, *clauses, holders=_moved_holders(root, project_home, common_files)
    )


def _moved_holders(
    root: str, project_home: str, common_files: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    """設定で動く場所の、入っているディレクトリと名前の組（holder_regex に渡す）。

    - ccnavi ディレクトリと、ワークスペースルートの下に置いた共通レイヤーの 3 本。ルートからの相対の
      各段で、入っているディレクトリと名前の組を作る（`conf/x/rules.yml` なら `conf/x` と
      `rules.yml`、`conf` と `x`）。いちばん上の名前はワークスペースルートに置く名前に足す
    - ワークスペースルートの外に置いた共通レイヤーの 3 本。書かれた表記と行き着く先の、すぐ上の
      ディレクトリと名前の組だけ（その上は守る場所の外まで広がる）
    - ワークスペースルート。ROOT_NAMES と上で足した名前を、ルートを指す行き先で組む
    """
    found: list[tuple[str, str]] = []
    top = [*ROOT_NAMES]
    ranked = [_home_name(project_home)]
    for path in common_files:
        if not path:
            continue
        rel = selfguard_targets._inside(root, path) if root else ""
        if rel:
            ranked.append(rel)
            continue
        for name in sorted({path, os.path.realpath(path)}):
            directory, base = os.path.split(name)
            if directory and base:
                found.append((under(_spelled(directory)), re.escape(base)))
    # ccnavi ディレクトリの下は、行き先がそこなら名前を問わずに止まる（場所の表記）ので、
    # そこに置いた共通レイヤーの 3 本の組は作らない。いちばん上の名前だけ足す。
    homes = {_home_name(project_home).lower(), ".ccnavi"} - {""}
    for rel in ranked:
        parts = [p for p in re.split(r"[\\/]", rel) if p]
        if not parts:
            continue
        top.append(re.escape(parts[0]))
        if any("/".join(parts).lower().startswith(h + "/") for h in homes):
            continue
        found.extend(
            (under(r"[\\/]".join(re.escape(p) for p in parts[:k])), re.escape(parts[k]))
            for k in range(1, len(parts))
        )
    # ルートを指す行き先。`.` と、ルートの絶対パス（書かれた表記と行き着く先）。
    bases = sorted({os.path.realpath(root), os.path.abspath(root)}) if root else []
    where = "(?:" + "|".join([r"\.", *(_spelled(b) for b in bases)]) + ")"
    found.append((where, "|".join(dict.fromkeys(top))))
    # 共通レイヤーの 3 本は同じディレクトリに並ぶことが多い。同じ組は 1 つにする。
    return tuple(dict.fromkeys(found))


def common_layer_files(conf: settings.Settings) -> tuple[str, ...]:
    """共通レイヤーの 3 本。rules / phases / risk の順。"""
    return (conf.rules, conf.phases, conf.risk)


def common_shell_clause(root: str, path: str, *, copy: bool = False) -> str:
    """共通レイヤーの 1 本を、シェルの書き込みに適用する形に直す。

    既定の置き場（`.ccnavi/config/common/`）は _PLACES が持っているが、`--rules` / `--phases` /
    `--risk` のフラグは任意の場所を指せる。そこを名前で拾えないと、共通レイヤーを動かした
    ワークスペースでは `echo x > <その場所>` が通る。名指しのツール
    （`common_layer_regex`）は動かした先を追うので、片方だけ外すと、同じファイルが
    Write では止まってシェルでは通る形になる。

    env（`CCNAVI_RULES` など）は使われず、置き場を指せるのは診断のためのフラグだけ。
    置き場が完全には固定されていないので、この節も残す。

    ワークスペースルートの下ならその相対、外なら書かれたパスと行き着く先の両方で適用する。
    パスの前には名前の途中でないことを求める。`rules.yml` を直下に置いたワークスペースで、
    `myrules.yml` への書き込みまで止めないため。ルールの regex は後読みを受けない
    （rules._UNSUPPORTED）ので、前の 1 文字も含めて適用する形で書く。行き先の前には必ず 1 文字ある。
    shellread がリダイレクトを `> 行き先` にそろえ、コマンドの語は空白で区切られている。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ適用する形を返す。
    `_COPY_TERM` で閉じるので、`cp <共通レイヤーの 1 本> /tmp/x` のように元の側に出ただけの
    読みは通る。前の 1 文字は最後の引数の語の中に限る。語の頭なら前の文字は要らない
    （_COPY_LAST が手前の空白まで読んである）。前の 1 文字に空白を許すと、手前の語に戻って
    ヒットする。`-t` の値（ディレクトリ）には足さない（shell_write_regex）。
    """
    if not path:
        return ""
    rel = selfguard_targets._inside(root, path) if root else ""
    names = [rel] if rel else sorted({path, os.path.realpath(path)})
    spelled = [_spelled(name) for name in names if name]
    if not spelled:
        return ""
    named = "(?:" + "|".join(spelled) + ")"
    if copy:
        return rf"(?:{_IN_WORD}[^\w. \x00-])?" + named + _COPY_TERM
    return r"(?:^|[^\w.-])" + named + _TERM


def common_layer_regex(root: str, common_files: tuple[str, ...]) -> str:
    """共通レイヤーの 3 本を、名指しのツールに適用する形に直す。

    適用する先は解決済みの絶対パス。ワークスペースルートの下に在るなら、ワークスペースと、
    そこから切ったワークツリー（`.claude/worktrees/<名前>/`）の同じ相対に適用する。ワークツリー側の
    設定は統合で統合先の設定になる経路がある。ワークスペースルートより上で閉じないと、
    同じ名前のファイル（`tests/fixtures/rules.yml` など）まで止まる。
    """
    alternatives = []
    bases = sorted({os.path.realpath(root), os.path.abspath(root)}) if root else []
    worktrees = r"(?:[\\/]" + _spelled(tree.WORKTREES_DIR) + r"[\\/][^\\/]+)?"
    for path in common_files:
        if not path:
            continue
        rel = selfguard_targets._inside(root, path) if root else ""
        if rel:
            alternatives.extend(
                _spelled(base) + worktrees + r"[\\/]" + _spelled(rel) for base in bases
            )
        else:
            alternatives.extend(_spelled(p) for p in sorted({path, os.path.realpath(path)}))
    if not alternatives:
        return ""
    return _folded("^(?:" + "|".join(alternatives) + ")$")


# 記録と state の置き場の既定のパス（`_PLACES` の `logs/` の節と同じ場所）を、名指しのツールに
# 適用する形。適用する先は解決済みの絶対パスなので、末尾で閉じる。
_RECORDS_PLACES = r"[\\/]logs[\\/](?:decisions(?:\.[^\\/]*)?\.jsonl$|(?:state|archive)(?:[\\/]|$))"


def records_regex(log_path: str = "", state_dir: str = "") -> str:
    """記録と state の置き場を、名指しのツールに適用する形。

    シェルの書き込みの側（`_PLACES`）と同じ `logs/decisions*.jsonl` と `logs/state/` のパスに
    加えて、診断のフラグで動かした置き場（`--log` / `--state`）にも
    適用する。
    記録はいま書いている 1 本と、同じディレクトリのローテートした分（`<名前>.<日時><拡張子>`）。
    書かれたパスと行き着く先の両方で適用する。
    """
    alternatives = [_RECORDS_PLACES]
    if log_path:
        directory, base = os.path.split(log_path)
        stem, ext = os.path.splitext(base)
        for where in sorted({os.path.abspath(directory), os.path.realpath(directory)}):
            alternatives.append(
                "^"
                + _spelled(where)
                + r"[\\/]"
                + re.escape(stem)
                + r"(?:\.[^\\/]*)?"
                + re.escape(ext)
                + "$"
            )
    if state_dir:
        for where in sorted({os.path.abspath(state_dir), os.path.realpath(state_dir)}):
            alternatives.append("^" + _spelled(where) + r"(?:[\\/]|$)")
    return _folded("(?:" + "|".join(alternatives) + ")")


# git の管理ディレクトリ（`.git/` とその中身、ワークツリーの `.git` ファイル）を、名指しのツールに
# 適用する形。適用する先は解決済みの絶対パスなので、前は区切りで閉じる。`.gitignore` や `.github/`
# のように `.git` の後ろに名前が続くものにはヒットしない。
# 名前の後ろの `.` と空白は、Windows が名前を開くときに落とす（`.git.` も `.git ` も `.git`）。
GIT_DIR_PATH_REGEX = r"[\\/]\.git[. ]*(?:[\\/]|$)"

# シェルの側で `.git` の前に来てよいのは、語の頭（空白）、引用がつないだ空白、パスの区切り、
# `--opt=` の `=`。`repo.git/` や URL の中の `.git` にはヒットしない。
# 名前の後ろの `.` は名指しのツールと同じ理由で飛ばす。空白は引用がつないだ空白（_END）で閉じる。
_GIT_DIR_NAME = r"\.git\.*" + _END
# 語の中で `.git` の手前に来てよい前置き。区切りで終わる並びに限る。
_GIT_DIR_PREFIX = r"(?:[^ \x00\x01]*[\\/])?"
# 書き換える動詞。守る場所の `_VERB_WRITE` から `dd` を外し（`of=` の値だけを書き先とみる）、
# `<` の直後の語は読みなので外す（`tee /tmp/o < .git/config`・`patch -p1 < .git/x`）。
# `<` は shellread が前後に空白を置いた 1 語にそろえる。
_GIT_VERB_WRITE = (
    r"(^|\x00)(mv|rm|tee|truncate|patch|shred)\b(?:[^\x00]*[^<\x00])?[ \x01]"
    r"(?:[^ \x00\x01<]*[\\/=])?"
)
_GIT_DD_WRITE = r"(^|\x00)dd\b[^\x00]*[ \x01]of=(?:[^ \x00]*[\\/])?"
# `sed -i` の式（最初の選択肢でない語）を飛ばし、その後ろの語だけを書き先とみる。`-i` は式の
# 前（`sed -i s/a/b/ f`）にも後ろ（`sed -e s/a/b/ -i f`）にも置ける。`-e` で式を渡すと式の語は
# `-e` の値になり、最初の選択肢でない語は式そのものかファイルになる。どちらでも後ろの語は
# 書き先なので、止める側に倒れるだけ（`sed -i -e s/a/b/ -e s/c/.git/ f` は止まる）。
# 前置きと選択肢の並びは `(?>...)` で決め打ちし、ヒットしない長い並びで読み直さない。
_SED_SCRIPT = r"(?>(?: -[^ \x00]*)* [^- \x00][^ \x00]*)"
_SED_IN_PLACE = r"[ \x01](?:-[A-Za-z]*?i|--i)"
_GIT_SED_FILE = r"[^\x00]*?[ \x01]" + _GIT_DIR_PREFIX + _GIT_DIR_NAME
_GIT_SED_WRITE = (
    r"(^|\x00)sed\b(?:"
    rf"(?>(?: -[^ \x00]*)*?{_SED_IN_PLACE}[^ \x00]*(?: -[^ \x00]*)* [^- \x00][^ \x00]*)"
    rf"{_GIT_SED_FILE}"
    rf"|{_SED_SCRIPT}(?:(?>[^\x00]*?{_SED_IN_PLACE}){_GIT_SED_FILE}"
    rf"|(?>{_GIT_SED_FILE})[^\x00]*?{_SED_IN_PLACE}))"
)
# 開始点に `.git`（とその下）を置き、消す・コマンドを走らせる述語を持つ `find`。開始点は
# 選択肢（`-H` `-L` `-P`）のあとの、`-` `!` `(` で始まらない語の並び。`find . -path ./.git
# -prune -o … -delete` の `./.git` は述語の値なのでヒットしない。最初の `.git` の開始点で
# 決め打ちする。それより後ろの述語の並びは、最初のものの後ろに含まれる。
# `-exec` を持つ形は、後ろが読むだけの許可リストに無ければ読み切れない形
# （command-taken-as-code）になり、生の文字列で適用する。
# 許可リストの形でも `-exec` 自体にヒットする。
# 生の文字列で適用するときの切れ目は生の `|` `;` `&` 改行も数え、`.git` の前後の引用符も許す。
_GIT_FIND_WRITE = (
    r"(?:^|[\x00|;&\n]) *find(?: -[HLP])*"
    r"(?>(?: [^-!( \x00][^ \x00]*)*? (?:[^ \x00]*[\\/'\"])?\.git\.*(?:[\\/][^ \x00]*)?['\"]? )"
    r"(?:[^\x00]*[ \x01])?(?:-delete|-exec|-execdir|-ok|-okdir)\b"
)
# 前のコマンドが `.git` を名指しし、その出力を `xargs` が書き込む動詞に渡す形。`xargs` を含む
# コマンドは、後ろが許可リストに無ければ読み切れない形
# （shellread の command-taken-as-code）になり、生の文字列で適用するので、
# 切れ目は `\x00` のほかに生の `|` `;` 改行も数え、`.git` の前後には引用符も数える。
# コマンドの頭から最初の `.git` で決め打ちし、`.git` の数だけ尾を読み直さない。
_GIT_XARGS_WRITE = (
    r"(?:^|[\x00|;\n])"
    r"(?>[^\x00|;\n]*?[ \x01\\/='\"]\.git\.*(?:[\\/ \x00\x01'\"]|$))"
    r"[^\x00|;\n]*(?:\x00|\|) *xargs\b[^\x00|;\n]*[ \x01'\"]"
    r"(?:rm|mv|tee|dd|truncate|patch|shred|cp|ln|install)\b"
)


def git_dir_shell_regex() -> str:
    """`.git` ディレクトリへシェルから書き込む形。組み込みの土台（builtin）が持つ。

    リダイレクトと cp / ln / install の行き先は守る場所（`_PLACES`）と同じ部品で読む。
    書き換える動詞・`dd`・`sed -i` は、読むだけの語（`<` の後ろ、`dd` の `if=`、`sed` の式）を
    外した `.git` 用の形で読み、`find` の開始点と `xargs` に渡す前のコマンドも見る。
    場所の名前が出ただけでは止めない。`cat .git/config` も
    `cp .git/config /tmp/x` も読むだけなので通る。git のラッパー（`ccnavi-git.sh`）の中で行う
    `.git` への書き込みは、外側のコマンド文字列に `.git` が出ないのでヒットしない。

    守る場所の表記と違い、`.git` の前に区切りを求める。`.git` は URL や
    裸のリポジトリの名前（`repo.git`）にも出るので、前を閉じないと `sed -i` の置換式まで止まる。
    リダイレクトの行き先の語は、区切りで終わる前置きだけを読ませる（`_REDIRECT` の語の読みは
    前置きを何でも食うので、ここでは使わない）。`-t` の値は `_COPY_TARGET` が値の頭を食うので
    前を閉じられない。止める側に倒れるだけなので、そのままにする。

    いま居る場所（`.`）へ `.git` をコピーする形（`cp -r /tmp/x/.git .`）は holder_regex で止める。
    元の名前が決まらない語（`cp ../src/*.py .`）は数えない（named_only）。
    """
    # 前置きは `>` をまたがない。読みはリダイレクトを `> 行き先` にそろえるので、行き先の語に `>` は
    # 来ない。またがせると、読み切れなかった生の文字列（`>a>a>a…`）で `>` ごとに尾を読み直し、
    # 長さの 2 乗になる（REDIRECT_LIMIT の説明）。またがなければ、`>` ごとに読むのは次の `>`
    # までで、合わせて長さに比例する。上限（judge.SHELL_GUARD_RULE_IDS）に頼らずに済む。
    redirect = rf"(?:^|[^>|&])[|&]*>[>|&]* ?(?:[^ {_NOT_A_WORD}>|&][^ \x00>]*[\\/])?"
    writes = rf"(?:{redirect}|{_GIT_VERB_WRITE}|{_GIT_DD_WRITE})"
    last = r"(?:[^ \x00]*[\\/])?\.git\.*" + _COPY_END
    return "|".join(
        (
            _folded(writes + _GIT_DIR_NAME),
            _folded(_GIT_SED_WRITE),
            _folded(_GIT_FIND_WRITE),
            _folded(_GIT_XARGS_WRITE),
            copy_destination_regex(_folded(last), _folded(_GIT_DIR_NAME)),
            holder_regex(r"\.", r"\.git", named_only=True),
        )
    )


def _spelled(path: str) -> str:
    """パスを、区切りをどちらの表記でも適用する形にする。"""
    return r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", path))


# 足すルールの id。どれも rules.RESERVED_ID_PREFIX で始まり、ルールファイルからは
# 書けない。同じ名前のルールと重なることが無いので、ヒットした id を名指しされたユーザは
# 組み込みのルールだと分かる。
SHELL_RULE_ID = "builtin:guard-setting-files"
# シェルから書き込む形の 1 本を適用するツール（`add_rules`）。
SHELL_MATCH = "Bash"

# 生の文字列（読み切れなかった Bash と、読まない PowerShell）に、シェルから書き込む形の保護を
# 適用してよい `>` の個数の上限。
#
# 生の文字列にはリダイレクトの目印が無いので、_WRITE_VERBS の `>` は引用の中の `>` にもヒットし、
# ヒットするたびにその後ろの空白の無い語を読む。手数は `>` の個数 × 語の長さで、`>` を並べるほど
# 長さの 2 乗に近づく（`echo "` + `>a` を 4000 個で、この 1 本が約 2.7 秒、チケットの置き場を守る
# 1 本と合わせて 3 秒を超える）。上限の下では `>` 50 個 × 尾の長さの線形に収まる（`>a` 49 個の
# 後ろに空白の無い尾を続けて、2 本の照合が 100KB で約 0.5 秒、400KB で約 1.8 秒）。判定の期限は
# 3 秒で、超えると判定に達しなかったとして enable では block（終了コード 2）になり、理由も案内も
# 返せない（hook 自体の打ち切りは settings.json の timeout で 10 秒）。正規表現は後読みが
# 使えず、式の中で個数を数えて打ち切れないので、照合の前に数える。語の長さに上限を置くと、
# 長いパスの末尾の `.ccnavi/…` を見落として通るので、個数で切る。
#
# 超えたら照合を飛ばし、権限モードに依らず拒否にする（judge.decide_before）。期限を超えた
# block と同じく止める側に置いたまま、理由と書き直し方を付けて返すためのもの。確認には回さない。
# 確認に回すと、確認に答える者が居ないモードでは誰も答えないまま通り、居るモードでも保護を
# 適用していない呼び出しの判断をユーザに預けることになる。数えるのは `>` の文字数で、`>>` も
# `2>&1` も単純に足す。読み解けた形には掛けない。シェルの読みがリダイレクトを `> 行き先` に
# そろえてあり、引用の中の `>` にヒットしないので、この遅さは出ない。
REDIRECT_LIMIT = 50

# 生の文字列の長さの上限。`>` が 1 個でもあれば、その後ろの空白の無い尾を読むので、`>` を上限の
# 下の 49 個並べても、尾の長さの約 50 倍の手数になる（400KB で約 3.3 秒、100KB で約 0.9 秒）。
# 個数の上限だけでは尾の長さを止められないので、`>` を含む生の文字列の長さにも上限を置き、
# 超えたら同じ拒否にする（#241）。`>` の無い生の文字列には掛けない。
RAW_LENGTH_LIMIT = 100_000


def too_many_redirects(subject: str) -> bool:
    """生の文字列の `>` が REDIRECT_LIMIT を超えるか、`>` を含んで RAW_LENGTH_LIMIT を超えるか。"""
    count = subject.count(">")
    return count > REDIRECT_LIMIT or (count > 0 and len(subject) > RAW_LENGTH_LIMIT)


# 実行ファイルを名指しのツールから守るルールの id。Bash 側とは別に持つ。
# 適用する先が違う（あちらはコマンド文字列、こちらはパス）ので、1 件にまとめると
# どちらの読みでヒットしたのかが報告から消える。
BINARY_RULE_ID = "builtin:guard-binary"

COMMON_LAYER_RULE_ID = "builtin:guard-common-layer"

RECORDS_RULE_ID = "builtin:guard-records"

PROJECT_HOME_RULE_ID = "builtin:guard-project-home"

HANDOFF_LINK_RULE_ID = "builtin:guard-handoff-links"

HANDOFF_LN_RULE_ID = "builtin:guard-handoff-ln"


def add_rules(
    rule_set: rules.RuleSet,
    bin_path: str = "",
    project_home: str = "",
    root: str = "",
    common_files: tuple[str, ...] = (),
    records: tuple[str, str] = ("", ""),
    tool: str | None = None,
    cwd: str = "",
) -> None:
    """ガード自身を守るルールを、判定に足す。

    ルールファイルの外から足す。ここで守る対象をルールから導かないのと同じ
    理由で、止める側もルールに書かせない。書かせると、消せることになる。

    4 本ある。シェルから書き込む形、名指しのツールで実行ファイルを書く形、
    名指しのツールで ccnavi ディレクトリ（`.ccnavi/`）の下を書く形、名指しのツールで
    共通レイヤーの 3 本を書く形。実行ファイルと ccnavi ディレクトリは置き場が設定で動くので、
    ルールファイルにパスを固定できない。レイヤーの設定は行き先の判定に使うので、そのレイヤーの
    ルール自身に任せると、書けた時点で緩められる。

    hook・settings・scripts・共通の設定 3 本を名前で書くツールから守る 4 本は、ここでは足さない。
    別の関数（`add_named_rules`）が足す。ルールファイルが読めない間も、実行後の保護範囲
    （`events.watched_for`）にも、同じ関数から足すため。ルールファイルの `deny` に書かせると、
    書き換えられた時点で保護も一緒に消える。

    共通レイヤーの 3 本も同じ理由で組み込みに持つ。既定の置き場が ccnavi ディレクトリの下に
    あることに頼り、動かしたときはルールの 1 行に任せる形にすると、その 1 行は守られる
    ファイルそのものの中にあるので、消した・書き換えたルールファイルのもとでは通る。
    既定の置き場なら ccnavi ディレクトリを守る 1 本とも重なるが、共通レイヤーを示す
    こちらを先に出す。

    ルールファイルに何が書いてあっても足す。組み込みの名前（rules.RESERVED_ID_PREFIX）は
    ルールファイルに書けないので、同じ id の重なりは起きない（書けるとどうなるかは
    RESERVED_ID_PREFIX の側に書いてある）。

    `tool` は判定するツール名。渡せば、シェルから書き込む形の 1 本は、その名前にヒットする
    ときだけ組み立てる。式は大きく（守る先のパスを全部並べる）、組み立てとコンパイルが
    判定 1 回の時間の多くを占める。`match: Bash` にヒットしないツールでは
    `rules.Rule.matches` が式を見ずに外すので、足さなくても判定は同じになる。
    ヒットするかどうかは `rules.tool_matches` で決め、`Rule.matches` と同じ答えを引く。
    None なら（ツールを決めずに並べる呼び手）これまでどおり足す。

    処理フロー:
    1. シェルから書き込む形（`guard_shell_regex`）を、`tool` がヒットするときだけ足す
    2. 実行ファイルの書き込み（`binary_clause`）があれば足す
    3. ccnavi ディレクトリの下（`project_home_glob`）を足す
    4. `root` があれば、下書きの置き場へのリンク（名指しのツール・シェルの 2 本）を足す
    5. 記録の置き場（`records_regex`）と、共通レイヤーの 3 ファイルを 1 本の式にして足す
       （`common_layer_regex`）
    各 `_insert` は判定の先頭に挿すので、後に足したものほど先にヒットする。
    """
    if tool is None or rules.tool_matches(SHELL_MATCH, tool):
        _insert(
            rule_set,
            {
                "id": SHELL_RULE_ID,
                "match": SHELL_MATCH,
                "regex": guard_shell_regex(root, bin_path, project_home, common_files),
                # 置き場（`wip/handoff/`）へ cp で写す形も止める。写すのは ccnavi-stage.sh で、
                # その案内は文が持つ（ワークスペースルートの有無で分かれる）。
                **texts.spec(
                    "RULE_GUARD_SETTING_FILES", variants={"next": "root" if root else "noroot"}
                ),
            },
            root,
            # 下書きの置き場の中だけを消すコマンドは外す。書き込みは外さない。
            exempt=handoff_delete_exempt(root),
        )
    clause = binary_clause(bin_path)
    if clause:
        _insert(
            rule_set,
            {
                "id": BINARY_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                # 適用する先は解決済みの絶対パスなので、末尾で閉じる。
                "regex": clause + "$",
                **texts.spec("RULE_GUARD_BINARY"),
            },
            root,
        )
    home_glob = project_home_glob(project_home)
    if home_glob:
        _insert(
            rule_set,
            {
                "id": PROJECT_HOME_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                # ccnavi ディレクトリの下はすべて守る。レイヤーの設定 3 本だけを名指しすると、
                # 配点が呼ぶスクリプトが外れる。適用する先は解決済みの絶対パスなので、
                # どの ccnavi ディレクトリ（ワークスペース、プロジェクト、ワークツリー）にも
                # 同じ 1 本がヒットする。
                "glob": home_glob,
                **texts.spec("RULE_GUARD_PROJECT_HOME"),
            },
            root,
            exempt=handoff_write_exempt(root),
        )
    if root:
        _insert(
            rule_set,
            {
                "id": HANDOFF_LINK_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                # 適用する対象は関数が決める（置き場の中のつながった対象だけが残る）。
                "regex": ".",
                **texts.spec("RULE_GUARD_HANDOFF_LINKS"),
            },
            root,
            exempt=handoff_link_target(root),
        )
        # 式は 1 文字で組み立てが軽いので、ツールに依らず足す（適用する対象は関数が決める）。
        _insert(
            rule_set,
            {
                "id": HANDOFF_LN_RULE_ID,
                "match": "Bash",
                "regex": ".",
                **texts.spec("RULE_GUARD_HANDOFF_LN"),
            },
            root,
            exempt=handoff_link_command(root, cwd),
        )
    _insert(
        rule_set,
        {
            "id": RECORDS_RULE_ID,
            "match": "Write|Edit|NotebookEdit",
            "regex": records_regex(*records),
            **texts.spec("RULE_GUARD_RECORDS"),
        },
        root,
    )
    common = common_layer_regex(root, common_files)
    if common:
        # 先頭に挿すので、後に足したこちらが ccnavi ディレクトリの 1 本より先にヒットする。
        _insert(
            rule_set,
            {
                "id": COMMON_LAYER_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                "regex": common,
                **texts.spec("RULE_GUARD_COMMON_LAYER"),
            },
            root,
        )


HOOKS_RULE_ID = "builtin:guard-hooks"
SETTINGS_RULE_ID = "builtin:guard-settings"
SCRIPTS_RULE_ID = "builtin:guard-scripts"
CCNAVI_CONFIG_RULE_ID = "builtin:guard-ccnavi-config"
NAMED_RULE_IDS = (HOOKS_RULE_ID, SETTINGS_RULE_ID, SCRIPTS_RULE_ID, CCNAVI_CONFIG_RULE_ID)

_NAMED_MATCH = "Write|Edit|NotebookEdit"
_SETTINGS_REGEX = r"[\\/]\.claude[\\/]settings(\.local)?\.json$"


def _home_regex(project_home: str) -> str:
    """ccnavi ディレクトリの名前を、区切りを `[\\/]` にした regex の部品にする。"""
    parts = [re.escape(p) for p in _home_name(project_home).split("/") if p]
    return r"[\\/]".join(parts)


def add_named_rules(rule_set: rules.RuleSet, project_home: str = "", root: str = "") -> None:
    """hook・settings・scripts・共通の設定 3 本を、名前で書くツールから守る。

    対象のツールは Write / Edit / NotebookEdit。

    自己防衛の設定（`CCNAVI_GUARD_CORE_FILES`）が disable でないときに、呼び手が足す。
    ルールファイルの外から足すので、ルールを書き換えても消えない。ルールファイルが読めずに
    組み込みの既定で動いている間も、実行後の保護範囲（`events.watched_for`）にも使う。
    実行前の通常時は `add_rules` の後に呼ぶ（`_insert` は先頭に挿すので、この 4 本の専用の文面が
    `guard-project-home` / `guard-common-layer` より先に並ぶ）。`add_rules` の中身には触れない。

    下書きの置き場（各ツリー直下の `wip/handoff/`）の中は、守る場所のコピーなので
    `handoff_write_exempt` で外す。ハードリンクでつながったコピーは外さない。

    共通の設定 3 本（`builtin:guard-ccnavi-config`）は、ルートを固定せず、パスの末尾で適用する。
    `common_layer_regex` が追う範囲（設定で動かした置き場）と同じとは限らないので、仮定しない。
    ルールファイルが読めない間も外れない。直すときは `wip/handoff/.ccnavi/config/common/rules.yml`
    に下書きを置き、ユーザがターミナルの `ccnavi-user-handoff.sh` で取り込むか、手で直す。

    ccnavi ディレクトリの名前が空なら、それを使う 2 本（scripts と共通の設定）は足さない。

    処理フロー:
    1. 下書きの置き場の例外（`handoff_write_exempt`）と、直す手順の場合（ルートの有無）を用意する
    2. hook 本体と hook の登録（settings）の 2 本は、必ず名前で足す
    3. ccnavi ディレクトリの名前（`_home_regex`）があれば、scripts と共通の設定の 2 本を足す
    4. 4 本とも、`match` を `Write|Edit|NotebookEdit` にして `_insert` で足す
    """
    exempt = handoff_write_exempt(root)
    # 守る場所を直す手順の文は、ワークスペースルートがあるか（`root`）で分かれる。
    hint = "root" if root else "noroot"
    named: list[dict] = [
        {
            "id": HOOKS_RULE_ID,
            "glob": "*/.claude/hooks/*",
            **texts.spec("RULE_GUARD_HOOKS", variants={"next": hint}),
        },
        {
            "id": SETTINGS_RULE_ID,
            "regex": _SETTINGS_REGEX,
            **texts.spec("RULE_GUARD_SETTINGS", variants={"next": hint}),
        },
    ]
    home = _home_regex(project_home)
    if home:
        named += [
            {
                "id": SCRIPTS_RULE_ID,
                "glob": f"*/{_home_name(project_home)}/scripts/*",
                **texts.spec("RULE_GUARD_SCRIPTS", variants={"next": hint}),
            },
            {
                "id": CCNAVI_CONFIG_RULE_ID,
                "regex": rf"[\\/]{home}[\\/]config[\\/]common[\\/](rules|phases|risks)\.yml$",
                **texts.spec("RULE_GUARD_CCNAVI_CONFIG", variants={"next": hint}),
            },
        ]
    for raw in named:
        _insert(rule_set, {**raw, "match": _NAMED_MATCH}, root, exempt=exempt)


def _insert(
    rule_set: rules.RuleSet, raw: dict, root: str, exempt: Callable[[str], str] | None = None
) -> None:
    built, problems = rules.parse(
        {"version": rules.VERSION, "deny": [raw]}, root=root, builtin=True
    )
    if problems or not built.deny:
        # 組み立てられないのは、このファイルの書き損じ。判定を止める理由には
        # しない。止まると、直すための呼び出しごと止まる。ただ何も知らせずに外すと、保護が
        # 1 本欠けたことに誰も気づけないので、診断ログに残す。書くのは組み込みの id と
        # 問題の件数だけ（式には守る先のパスが入る）。root が無ければ CLAUDE_PROJECT_DIR。
        diaglog.get("ccnavi", root or None).warn(
            "組み込みの保護を組み立てられず外した", rule=raw.get("id", ""), problems=len(problems)
        )
        return
    built.deny[0].exempt = exempt
    rule_set.deny.insert(0, built.deny[0])
