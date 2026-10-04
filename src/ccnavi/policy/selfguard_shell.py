"""守る対象へのシェルからの書き込みを止める組み込みのルール。正規表現の組み立てと `add_rules`。

selfguard から分けた。selfguard を読まない。
"""

from __future__ import annotations

import os
import re

from ..infra import platformtag, settings, shellread, tree
from ..records import diaglog
from . import rules, selfguard_targets

# 設定ファイルへシェルから書き込む形。戻す側と対になる、止める側。
#
# 戻せるだけでは足りない。実行後に戻すまでの間、書き換わった設定がそのまま
# 反映されている時間がある。hook の登録は同じセッションのうちに反映されるので、その間に
# 実行後のイベントごと消されると、戻す機会が来ない。だから同じ場所を実行前にも
# 止める。止めるほうが本筋で、戻すほうは止めきれなかったぶんの受け皿になる。
#
# 前半の括弧が書き込みの形で、後ろに続く場所と組で当たる。場所の名前が出ただけでは
# 止めない。`cat .ccnavi/common/rules.yml` も `git add <パス>` も、中身を書かない。
# 名前で止める形にすると、いちばんガードを直したいときにいちばん多く止められる。
#
#   1. リダイレクトの行き先。`>` `>>` `>|` `&>` はどれも `>` を含み、
#      shellread が `> 行き先` の形にそろえてから渡す。
#   2. 名指ししたところを必ず書き換えるコマンド。`\x00` はコマンドの切れ目に
#      shellread が置く目印で、`(^|\x00)` はコマンドの先頭を意味する。
#      語の中の切れ目（引用がつないだ空白、語の中の演算子の両側）は別の目印
#      `shellread.WORD_SEP` なので、`[^\x00]*` は同じコマンドの中を丸ごと指す。
#      1 のリダイレクトの行き先だけは、語の中の目印まで含めて当てると引用の中の `> 場所` が
#      書き込み先に見えるので、そちらも除外する。
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
# 実行前チェックはルールの regex で当てる（rules._build を通る、組み込みのルールの 1 本）。
# 語のリストを Python で解くと、ルールの外に判定の経路がもう 1 本できて、組み込みの既定
# （builtin）と `--lint` の見本（check_rules）から見えなくなる。shellread が語を空白で、
# コマンドを `\x00` で、引用がつないだ空白を WORD_SEP で区切って渡すので、語の並びは
# regex でも読める。2 つの枝は「または」で並ぶので、読み違えて両方を見る形は止まる側に倒れる。
# 読み違えて両方を見落とす形が無いように、語の分け方を _COPY_TARGET と _COPY_LAST で揃える。
_NOT_A_WORD = re.escape(shellread.SEP) + re.escape(shellread.WORD_SEP)
_WRITE_VERBS = (
    rf"(>[>|&]* ?[^ {_NOT_A_WORD}]*"
    r"|(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*"
    r"|(^|\x00)sed\b[^\x00]*[ \x01](-[A-Za-z]*i|--i)[^\x00]*)"
)
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
# 語の並びの割り方は 1 通りに決まる。割り方が何通りもあると、当たらない長い並び
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
# 値の後ろには元の引数が続くので、場所は書き込み用の表記（空白で閉じる）で当てる。
_COPY_TARGET = rf"{_COPY_VERBS} (?:-(?-i:{_FLAGS}*t)|--(?-i:t)[a-z-]*)[= ]?[^ \x00]*"
# 最後の引数の手前まで。`-t` を持たない語の並びと、最後の引数の前の空白。値を取る選択肢の
# 値に出た `-t`（`-S -t`）は選択肢ではない。`--` のあとは選択肢を読まないので、何が来てもよい。
# 空白までで止め、最後の引数の中は場所の表記（_COPY_PLACES など）が語の頭から当てる。
# 場所の表記が語の途中から始められると、共通層の表記のように前の 1 文字から当てる形が
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
# 書き換えるのと同じだけ保護が消える（敵対的レビュー A-3）。
# 語の中の目印も終わりに数える。数えないと、`rm ".ccnavi x"` のように引用がつないだ
# パスが通る。
_TERM = rf"(?:[ {_NOT_A_WORD}]|$)"
# 区切りが続く形と、そこで終わる形の両方。`.ccnavi/config/x` にも `.ccnavi` にも
# 当たり、`.ccnavixyz` のような別名には当たらない。
_END = rf"(?:[\\/ {_NOT_A_WORD}]|$)"
# 元と行き先がある cp / ln / install のための終わり。行き先（最後の引数）だけに当てるので、
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
# 写すだけの読みは通る。切れ目とリダイレクトまで含めるので、`cp /tmp/x .ccnavi && echo` や
# `cp /tmp/x .ccnavi > /dev/null` のように後ろに何か続けた形も止まる。
_COPY_OPTIONS = (
    rf"(?: (?:{_VALUED} (?:(?:[^- \x00][^ \x00]*)?|{_DIR_WORD})|{_DASH_NOT_DIR}))*(?: --)?"
)
_COPY_STOP = r"(?:$|\x00| (?:[0-9]+ |\{\w+\} )?&?[<>])"
_COPY_TERM = _COPY_OPTIONS + _COPY_STOP
_COPY_TAIL = r"[^ \x00]*" + _COPY_TERM
_COPY_END = r"(?:[\\/]" + _COPY_TAIL + r"|" + _COPY_TERM + r")"

# 行き先を、守るものが入っているディレクトリにした形（`cp /tmp/decisions.jsonl logs/`）。
# 行き先の表記に守るファイルの名前が出ないので、上の場所の表記では当たらない。
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
# 動詞には mv も入れる。mv は書き込みの側で名指ししたところに当てるが、行き先がディレクトリ
# だけの形は名指ししないので、ここで拾う。
#
# 語の並びの読み方は _COPY_LAST / _COPY_TARGET と同じ。元の語は並びのどこにあってもよいので、
# 割り方は「どの語を元の語と見るか」の数だけある。当たらない長い並びで照合の手数がその数だけ
# 増えないように、最初に見つかった元の語で決め打ちする（`(?>...)`、後戻りしない括り）。
# 後ろの語で当たるなら、前の語から読んでも同じ並びを通って当たるので、決め打ちで外す形は無い。
# 元の語の終わり（空白）も括りの中に入れる。入れないと `decisions.jsonl.bak` の頭だけで決め打ちし、
# 後ろに並べた本物の名前を見落とす。
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
    """ディレクトリの表記を、語の頭からでも置き場の下からでも当たる行き先の形にする。

    `logs` なら `logs`・`./logs`・`/abs/logs` に当たり、`mylogs` には当たらない。
    """
    return r"(?:[^ \x00]*[\\/])?" + directory


def holder_regex(directory: str, names: str) -> str:
    """守るものが入っているディレクトリを行き先にした cp / ln / install / mv に当たる式。

    directory は行き先の語全体に当てる表記（under で組むか、`\\.` のように語そのもの）、
    names は守るものの名前（区切りを含まない）。元の側に names の語か、名前の決まらない語が
    あるときだけ当たる（上の _HOLD_NAME の説明）。行き先は _COPY_LAST / _COPY_TARGET と同じく、
    `-t` の値か、選択肢でない最後の引数。
    """
    source = rf"(?:{_HEAD}(?:{names})[\\/]*|{_UNKNOWN})"
    dest = directory + _DIR_TAIL
    # 最後の引数が行き先。元の語は `--` の手前（選択肢として読む並び）にも、後ろにもありうる。
    before = (
        rf"(?>(?: {_ARG})*? (?:{source}|{_NO_TARGET_FLAG}) )"
        rf"(?:{_ARG} )*(?:--(?: [^ \x00]*)* )?{dest}{_COPY_TERM}"
    )
    after = rf"(?: {_ARG})* --(?>(?: [^ \x00]*)*? {source} )(?:[^ \x00]* )*{dest}{_COPY_TERM}"
    # `-t` の値が行き先。元の語は `-t` の前にも後ろにもありうる。
    target_after = rf"(?>[^\x00]*? {source} )(?:[^\x00]* )?{_TARGET_FLAG}{dest}(?:[ \x00]|$)"
    target_before = rf"(?>[^\x00]*? {_TARGET_FLAG}{dest} )(?:[^\x00]* )?{source}(?:[ \x00]|$)"
    forms = "|".join((before, after, target_after, target_before))
    return _folded(rf"{_HOLD_NAME}(?:{forms})")


def copy_destination_regex(last_place: str, target_place: str) -> str:
    """cp / ln / install の行き先に当たる式。

    last_place は最後の引数の語の頭から当てる表記（copy_last_place で組む）、target_place は
    `-t` の値に当てる表記で、値の語の中のどこから当ててもよい。
    """
    return rf"{_COPY_LAST}{last_place}|{_COPY_TARGET}{target_place}"


def copy_last_place(clause: str) -> str:
    """場所の表記を、最後の引数の語として当てる形にする。名前がそこで終わる形も、下も当たる。"""
    return _IN_WORD + clause + _COPY_END


# `.ccnavi/` は ccnavi ディレクトリの既定のパス（設計 11.2）。その下には各層の設定 3 本と、
# 配点が呼ぶスクリプトが入る。どちらも判定の中身そのものなので、ccnavi ディレクトリごと止める。
# 既定のパスをここに書いておくのは、ccnavi ディレクトリの名前を動かしていないワークスペースが、
# 設定の受け渡しに依らずに守られるようにするため。動かしてある場合は project_home_clause が足す。
#
# `.claude` の側は、その下の名前を絞ってある（`worktrees/` は守る対象ではない）。
# だから ccnavi ディレクトリと違って、名前がそこで終わる形は `_TERM` で閉じる。`_END` にすると
# `.claude/` に続くパス全部が入り、ワークツリーの片付けまで止まる。区切り 1 つで終わる形
# （`.claude/`）は `.claude` そのものなので当てる。当てないと `mv /tmp/settings.json .claude/` や
# `cp -t .claude/ /tmp/settings.json` のように、行き先をディレクトリにしただけで通る。
# チケットの sh は `.ccnavi/scripts/` にあるので、`.claude` の側で守るのは hook と設定ファイルだけ。
#
# `logs/` は記録と state の置き場（`logs/decisions.jsonl` と `logs/state/`）。どちらも判定が読むので
# 名前を絞って守る。ローテートした記録（`logs/decisions.<日時>.jsonl`、prune）も同じ形で守る。
# 判定は読まないが、「記録が無い = 動かなかった」を読む元で、シェルから消せると自分の呼び出しの
# 記録を消せる。消すのはセッションの開始と、端末から打つ `ccnavi --prune` だけ。
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
# ccnavi ディレクトリを丸ごと置き換える。行き先はルートを指す表記（`.` と、ルートの絶対パス）
# だけで見る（_moved_holders がルートの表記と組む）。`.` は居場所がルートでなくても当たるが、
# そこへ `.ccnavi` や `.claude` を写す用事は無い。
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
    3 つ組で来る。ccnavi ディレクトリ（project_home_clause）と共通層の 3 本
    （common_shell_clause）が来る。既定の名前は _PLACES に書いてあるので、ここで足すのは
    動かしてある場合のパスになる。層の設定は行き先の判定に使うので、書けるとエージェントが
    自分のルールを緩められる。空のパスは足さない。

    3 つに分けるのは、閉じ方が違うから。

    - 書き込み用は空白で閉じる。同じパスを cp / ln / install に足すと、元の側に出ただけで当たる
    - 最後の引数用は引数の列の終わりで閉じる（_COPY_END / _COPY_TERM）
    - `-t` の値は後ろに元の引数が続くので、書き込み用の閉じ方を使う。ただし値の 1 語の中で
      当たるパスに限る。共通層の 3 本のパスは前の 1 文字（空白でもよい）から当てるので、
      `-t /tmp/out <共通層の 1 本>` の元の側に当たってしまう。`-t` の値はディレクトリなので、
      ファイルを指す共通層のパスは `-t` の側には足さない。`-t <共通層の入っているディレクトリ>` は
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
    `.ccnavi/scripts/count.sh` そのもので、消せば本物が消える。区別すると、表記を
    1 文字変えるだけで ccnavi ディレクトリの中が書ける。
    区別する機械でも区別せずに当てる。ルールの glob とチケットの範囲はどの機械でも
    区別しないので、
    保護だけが機械で当たり方を変えると、同じパスの扱いがルールと保護で食い違う。

    ここで囲むのは場所のパスだけだが、組み込みの保護も `rules._build` を通るので、
    式全体が `re.IGNORECASE` で当たる（regex も glob と同じく大文字小文字を区別しない）。
    コマンドの名前（`rm` / `cp`）も結果として区別されなくなる。`RM` が走る保証は無いので
    保護が増えるわけではないが、`deny` が広がる側なので、そのままにしてある。区別が要るルールは
    `(?-i:...)` で囲む。
    """
    return f"(?i:{clause})"


def project_home_clause(project_home: str, *, copy: bool = False) -> str:
    """ccnavi ディレクトリのパスを、シェルの書き込みに当てる形に直す（設計 11.6）。

    ccnavi ディレクトリの下は丸ごと守る。層の設定 3 本も、配点が呼ぶスクリプトも、そこに入る。
    既定の名前（`.ccnavi`）は _PLACES が持っているので、ここが返すのは動かして
    ある場合のパス。区切りはどちらの表記にも当て、名前がそこで終わる形（ccnavi ディレクトリごと
    消す・退かす）にも当てる。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ当てる形を返す。最後の引数の
    語の頭から当て（_IN_WORD）、既定の名前の写す側（_COPY_PLACES）と同じ `_COPY_END` で閉じるので、
    `cp <ccnavi ディレクトリ>/config/rules.yml /tmp/x` のように外へ写すだけの読みは通り、
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

    前後の区切りは落とし、区切りを含むパスはそのまま 1 つの節にする。
    """
    return (project_home or "").replace("\\", "/").strip("/")


def binary_clause(bin_path: str, *, copy: bool = False) -> str:
    """実行ファイルのパスを、当てる形に直す。

    末尾の 2 要素だけを使う。絶対で書かれても相対で書かれても同じ形に当たり、
    ファイル名だけに絞ると、同じ名前の無関係なファイルまで拾う。
    区切りはどちらの表記にも当てる。ルールは 1 回書いてどの機械でも同じ意味で
    なければならない、という globmatch と同じ約束をここでも守る。

    指す先は振り分けの sh で、hook が実際に走らせるのは実体のほう（platformtag）。
    sh だけを守ると、実体を差し替えれば判定が入れ替わるので、組み立ての置き場にも当てる。
    どこを置き場と読むかは sh の名前だけで決め、platformtag.launched_executable と揃える。
    段の数など別の条件を足すと、守る場所とバックアップする場所が食い違う。

    - 名前が LAUNCHER_NAME なら、sh の末尾 2 要素（`scripts/ccnavi-launcher.sh`）と、
      その 1 つ上の親の下の `bin/<os>-<arch>/`。sh の隣の `<os>-<arch>/` は sh が探さない
      ので当てない。親が無い浅いパスでは `bin/<os>-<arch>/` を親を問わずに当てる。判定に
      渡るパスは絶対パスなので、名指しのツールはそれでも止まる
    - それ以外の名前は実行ファイルそのもの。末尾 2 要素だけに当てる

    既定の置き場（`.ccnavi/`）は ccnavi ディレクトリを守るルールでも止まるが、ccnavi
    ディレクトリを動かしたときや、既定でないパスを指したときはここでしか止まらない。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ当てる形を返す。最後の引数の
    語の頭から当て（_IN_WORD）、名前の後ろは引数の残り（_COPY_TAIL）で閉じ、組み立ての置き場の
    下も空白をまたがない。
    書き込み用の形は終わりを決めないので、`(?:[\\/][^\x00]*)?` が空白をまたいで
    `cp <置き場>/bin/<os>-<arch>/ccnavi /tmp/x` のような外へコピーするだけの読みにも当たる。
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
        if copy:
            return rf"{_IN_WORD}(?:{sh}{_COPY_TAIL}|{home}bin[\\/]{_BUILD_DIR}{_COPY_END})"
        return rf"(?:{sh}|{home}bin[\\/]{_BUILD_DIR}(?:[\\/][^\x00]*)?)"
    plain = r"[\\/]".join(re.escape(p) for p in parts[-2:])
    return _IN_WORD + plain + _COPY_TAIL if copy else plain


def binary_holders(bin_path: str) -> tuple[tuple[str, str], ...]:
    """実行ファイルが入っているディレクトリと、その中の名前（holder_regex に渡す組）。

    binary_clause と同じ末尾 2 要素で読む。`cp /tmp/ccnavi dist/` のように、行き先を
    実行ファイルの入っているディレクトリにした形を止める。振り分けの sh なら、sh の入っている
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


def guard_shell_regex(
    root: str, bin_path: str = "", project_home: str = "", common_files: tuple[str, ...] = ()
) -> str:
    """この設定で組んだ、シェルから書き込む形。

    実行前チェック（add_rules）と組み込みの既定（builtin）の両方がここから取る。既定の
    側だけモジュールを読んだ時点の空の設定で組むと、ccnavi ディレクトリや実行ファイルを
    動かしたワークスペースでは、ルールファイルが壊れたときだけ動かした先への書き込みが
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

    - ccnavi ディレクトリと、ワークスペースルートの下に置いた共通層の 3 本。ルートからの相対の
      各段で、入っているディレクトリと名前の組を作る（`conf/x/rules.yml` なら `conf/x` と
      `rules.yml`、`conf` と `x`）。いちばん上の名前はワークスペースルートに置く名前に足す
    - ワークスペースルートの外に置いた共通層の 3 本。書かれた表記と行き着く先の、すぐ上の
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
    # そこに置いた共通層の 3 本の組は作らない。いちばん上の名前だけ足す。
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
    # 共通層の 3 本は同じディレクトリに並ぶことが多い。同じ組は 1 つにする。
    return tuple(dict.fromkeys(found))


def common_layer_files(conf: settings.Settings) -> tuple[str, ...]:
    """共通層の 3 本。rules / phases / risk の順。"""
    return (conf.rules, conf.phases, conf.risk)


def common_shell_clause(root: str, path: str, *, copy: bool = False) -> str:
    """共通層の 1 本を、シェルの書き込みに当てる形に直す。

    既定の置き場（`.ccnavi/common/`）は _PLACES が持っているが、`--rules` / `--phases` /
    `--risk` のフラグは任意の場所を指せる。そこを名前で拾えないと、共通層を動かした
    ワークスペースでは `echo x > <その場所>` が通る。名指しのツール
    （`common_layer_regex`）は動かした先を追うので、片方だけ外すと、同じファイルが
    Write では止まってシェルでは通る形になる。

    env（`CCNAVI_RULES` など）は使われず、置き場を指せるのは診断のためのフラグだけ。
    置き場が完全には固定されていないので、この節も残す。

    ワークスペースルートの下ならその相対、外なら書かれたパスと行き着く先の両方で当てる。
    パスの前には名前の途中でないことを求める。`rules.yml` を直下に置いたワークスペースで、
    `myrules.yml` への書き込みまで止めないため。ルールの regex は後読みを受けない
    （rules._UNSUPPORTED）ので、前の 1 文字も含めて当てる形で書く。行き先の前には必ず 1 文字ある。
    shellread がリダイレクトを `> 行き先` にそろえ、コマンドの語は空白で区切られている。

    copy を立てると、cp / ln / install の行き先（最後の引数）にだけ当てる形を返す。
    `_COPY_TERM` で閉じるので、`cp <共通層の 1 本> /tmp/x` のように元の側に出ただけの
    読みは通る。前の 1 文字は最後の引数の語の中に限る。語の頭なら前の文字は要らない
    （_COPY_LAST が手前の空白まで読んである）。前の 1 文字に空白を許すと、手前の語に戻って
    当たる。`-t` の値（ディレクトリ）には足さない（shell_write_regex）。
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
    """共通層の 3 本を、名指しのツールに当てる形に直す（設計 11.6）。

    当てる先は解決済みの絶対パス。ワークスペースルートの下に在るなら、ワークスペースと、
    そこから切ったワークツリー（`.claude/worktrees/<名前>/`）の同じ相対に当てる。ワークツリー側の
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
# 当てる形。当てる先は解決済みの絶対パスなので、末尾で閉じる。
_RECORDS_PLACES = r"[\\/]logs[\\/](?:decisions(?:\.[^\\/]*)?\.jsonl$|(?:state|archive)(?:[\\/]|$))"


def records_regex(log_path: str = "", state_dir: str = "") -> str:
    """記録と state の置き場を、名指しのツールに当てる形（設計 11.6）。

    シェルの書き込みの側（`_PLACES`）と同じ `logs/decisions*.jsonl` と `logs/state/` のパスに
    加えて、診断のフラグで動かした置き場（`--log` / `--state`。env では動かない）にも
    当てる。
    記録はいま書いている 1 本と、同じディレクトリのローテートした分（`<名前>.<日時><拡張子>`）。
    書かれたパスと行き着く先の両方で当てる。
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


def _spelled(path: str) -> str:
    """パスを、区切りをどちらの表記でも当てる形にする。"""
    return r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", path))


# 足すルールの id。どれも rules.RESERVED_ID_PREFIX で始まり、ルールファイルからは
# 書けない。同じ名前のルールと重なることが無いので、当たった id を名指しされたユーザは
# 組み込みのルールだと分かる。
SHELL_RULE_ID = "builtin-guard-setting-files"
# シェルから書き込む形の 1 本を当てるツール（`add_rules`）。
SHELL_MATCH = "Bash"

SHELL_MESSAGE = (
    "ガード自身の設定と hook を、シェルからの書き込みで変えようとしています。"
    "Write / Edit でも拒否される場所です。表記を変えても同じ場所なので、変更が要る"
    "理由を伝えてユーザに依頼してください。読むだけなら cat や grep はそのまま通ります。"
)

# 実行ファイルを名指しのツールから守るルールの id。Bash 側とは別に持つ。
# 当てる先が違う（あちらはコマンド文字列、こちらはパス）ので、1 件にまとめると
# どちらの読みで当たったのかが報告から消える。
BINARY_RULE_ID = "builtin-guard-binary"

BINARY_MESSAGE = (
    "ccnavi 自身の実行ファイルです。ここが差し替わると、ルールを 1 行も変えずに"
    "判定そのものを入れ替えられます。作り直しが要るなら、何をなぜ変えたいのかを"
    "伝えてユーザに依頼してください。"
)

COMMON_LAYER_RULE_ID = "builtin-guard-common-layer"

COMMON_LAYER_MESSAGE = (
    "ccnavi の共通層の設定（ルール・フェーズの種類・リスクの配点）です。どのツリーの判定にも"
    "効くので、エージェントが書き換えると自分の判定を緩められます。変更が要るなら、下書きを"
    "検証したうえで何をなぜ変えたいのかを伝えてユーザに依頼してください（/ccnavi-config）。"
    "読むだけなら止まりません。"
)

RECORDS_RULE_ID = "builtin-guard-records"

RECORDS_MESSAGE = (
    "ccnavi の記録と state の置き場（logs/decisions*.jsonl と logs/state/）か、閉じたチケットの"
    "退避（logs/archive/）です。判定が読み、「ccnavi が何を判定したか」・「何が閉じたか」を"
    "後から確かめる元なので、エージェントは書き換えません。"
    "シェルからの書き込みでも拒否される場所です。読むだけなら止まりません。"
)

PROJECT_HOME_RULE_ID = "builtin-guard-project-home"

PROJECT_HOME_MESSAGE = (
    "層の設定の置き場です。ここに入っているルール・フェーズの種類・リスクの配点が"
    "このツリーへの判定を決めるので、エージェントが書き換えると自分の判定を緩め"
    "られます。承認済みチケット・フェーズのマーカー・子のフロー（approved/ の下）も"
    "ここにあり、書くのはユーザです。変更が要るなら、何をなぜ変えたいのかを伝えてユーザに"
    "依頼してください。読むだけなら止まりません。"
)


def add_rules(
    rule_set: rules.RuleSet,
    bin_path: str = "",
    project_home: str = "",
    root: str = "",
    common_files: tuple[str, ...] = (),
    records: tuple[str, str] = ("", ""),
    tool: str | None = None,
) -> None:
    """ガード自身を守るルールを、判定に足す。

    ルールファイルの外から足す。ここで守る対象をルールから導かないのと同じ
    理由で、止める側もルールに書かせない。書かせると、消せることになる。

    4 本ある。シェルから書き込む形、名指しのツールで実行ファイルを書く形、
    名指しのツールで ccnavi ディレクトリ（`.ccnavi/`）の下を書く形、名指しのツールで
    共通層の 3 本を書く形。hook の登録（`.claude/settings*.json`）を名指しのツールから
    守るぶんはワークスペースのルールに任せる。そこは `deny` に 1 行書けば済み、書いたことが
    読める場所に残る。実行ファイルと ccnavi ディレクトリは置き場が設定で動くので、
    ルールファイルにパスを固定できない。層の設定は行き先の判定に使うので、その層の
    ルール自身に任せると、書けた時点で緩められる（REQ-MLT-08）。

    共通層の 3 本も同じ理由で組み込みに持つ。既定の置き場が ccnavi ディレクトリの下に
    あることに頼り、動かしたときはルールの 1 行に任せる形にすると、その 1 行は守られる
    ファイルそのものの中にあるので、消した・書き換えたルールファイルのもとでは通る。
    既定の置き場なら ccnavi ディレクトリを守る 1 本とも重なるが、共通層を名乗る
    こちらを先に出す。

    ルールファイルに何が書いてあっても足す。組み込みの名前（rules.RESERVED_ID_PREFIX）は
    ルールファイルに書けないので、同じ id の重なりは起きない（書けるとどうなるかは
    RESERVED_ID_PREFIX の側に書いてある）。

    `tool` は判定するツール名。渡せば、シェルから書き込む形の 1 本は、その名前に当たる
    ときだけ組み立てる。式は大きく（守る先のパスを全部並べる）、組み立てとコンパイルが
    判定 1 回の時間の多くを占める。`match: Bash` に当たらないツールでは
    `rules.Rule.matches` が式を見ずに外すので、足さなくても判定は同じになる。
    当たるかどうかは `rules.tool_matches` で決め、`Rule.matches` と同じ答えを引く。
    None なら（ツールを決めずに並べる呼び手）これまでどおり足す。
    """
    if tool is None or rules.tool_matches(SHELL_MATCH, tool):
        _insert(
            rule_set,
            {
                "id": SHELL_RULE_ID,
                "match": SHELL_MATCH,
                "regex": guard_shell_regex(root, bin_path, project_home, common_files),
                "message": SHELL_MESSAGE,
            },
            root,
        )
    clause = binary_clause(bin_path)
    if clause:
        _insert(
            rule_set,
            {
                "id": BINARY_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                # 当てる先は解決済みの絶対パスなので、末尾で閉じる。
                "regex": clause + "$",
                "message": BINARY_MESSAGE,
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
                # ccnavi ディレクトリの下は丸ごと。層の設定 3 本だけを名指しすると、配点が呼ぶ
                # スクリプトが外れる。当てる先は解決済みの絶対パスなので、どの ccnavi ディレクトリ
                # （ワークスペース、プロジェクト、ワークツリー）にも同じ 1 本が当たる。
                "glob": home_glob,
                "message": PROJECT_HOME_MESSAGE,
            },
            root,
        )
    _insert(
        rule_set,
        {
            "id": RECORDS_RULE_ID,
            "match": "Write|Edit|NotebookEdit",
            "regex": records_regex(*records),
            "message": RECORDS_MESSAGE,
        },
        root,
    )
    common = common_layer_regex(root, common_files)
    if common:
        # 先頭に挿すので、後に足したこちらが ccnavi ディレクトリの 1 本より先に当たる。
        _insert(
            rule_set,
            {
                "id": COMMON_LAYER_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                "regex": common,
                "message": COMMON_LAYER_MESSAGE,
            },
            root,
        )


def _insert(rule_set: rules.RuleSet, raw: dict, root: str) -> None:
    built, problems = rules.parse({"version": rules.VERSION, "deny": [raw]}, builtin=True)
    if problems or not built.deny:
        # 組み立てられないのは、このファイルの書き損じ。判定を止める理由には
        # しない。止まると、直すための呼び出しごと止まる。ただ黙って外すと、保護が
        # 1 本欠けたことに誰も気づけないので、診断ログに残す。書くのは組み込みの id と
        # 問題の件数だけ（式には守る先のパスが入る）。root が無ければ CLAUDE_PROJECT_DIR。
        diaglog.get("ccnavi", root or None).warn(
            "組み込みの保護を組み立てられず外した", rule=raw.get("id", ""), problems=len(problems)
        )
        return
    rule_set.deny.insert(0, built.deny[0])
