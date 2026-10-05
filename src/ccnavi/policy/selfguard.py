"""ccnavi 自身を成り立たせている設定ファイルを、実行前にバックアップし、実行後に戻す。

## 守る対象をルールファイルに書かない

ルールに書いた保護は、ルールを消せば一緒に消える。実行後チェックは保護領域を
ルールファイルの `deny` と `ask` から導くので、ルールファイル自身をそこで守ると、
書き換えられた時点で「何を守るか」の一覧ごと失われる。`deny` を空にされた形も、
壊れて組み込みの既定に戻った形も、どちらも保護領域は 0 件になり、実行後チェックは何も
検知しない。保護の根拠が、守られる対象の中に置いてあることが原因になる。

だからこの一式は、ルールファイルの外に、組み込みで持つ（設計 11.6）。

    <root>/.claude/settings.json        hook の登録そのもの
    <root>/.claude/settings.local.json  同上。個人の上書き
    共通層の 3 本                       <root>/.ccnavi/common/{rules,phases,risks}.yml
    自身の層の 3 本                     <root>/.ccnavi/config/{rules,phases,risks}.yml
    プロジェクトの層の 3 本             projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml

守る場所は層の数だけ増えるが、守る対象の種類は「設定 3 本」の 1 種のまま。
rules.yml は何を止めるかを、phases.yml はレビューが要るかを、risks.yml は点が
幾つかを決める。3 本とも判定の中身そのもので、ここが書けると、エージェントが
自分のルールとレビューの要否とリスクの配点を緩められ、次の呼び出しから判定に使われる。
ファイル単位で見るので、無いものは対象から外れる（REQ-SLF-03）。

数を増やさない。ここに足したものは、プロジェクトが宣言を書かなくても、
そして宣言を消しても守られる。それは強い扱いなので、ccnavi が動くこと自体が
懸かっているものに限る。それ以外は `deny` に書いて、ルール由来の保護
（`CCNAVI_RESTORE_IF_DENY`）に任せる。切りたいプロジェクトが切れる側に置く。

## `.ccnavi/scripts/` はコアに入れない

ccnavi ディレクトリの下には、配点が呼ぶスクリプトも入る。そちらはコアにしない。スクリプトは
risks.yml から `script:` で参照されて初めて使われ、参照が無ければ何もしない。その
risks.yml がここで守られているので、新しい参照を足すことはできない。残る経路は
「既に参照されているスクリプトの中身を書き換える」だけで、そこは組み込みの deny
（`*/.ccnavi/*`、add_rules）とルール由来の保護に任せる。止まり、止めきれなくても戻る。

コアのバックアップと復元まで持たせない理由はもう 1 つある。
ccnavi ディレクトリの下のファイル数は定まらない。呼び出しのたびに全部を読んで比べることになり、
実行前チェックに設けた期限に影響する。数が定まっているのは設定 3 本のほうで、
そこがコアとコアでないものの境になる。

## ワークツリー側の設定も同じ扱い

上のファイルは追跡されているので、`.claude/worktrees/<名前>/` の中にも同じものが入る。
このワークツリー側の設定は、今の時点では誰にも読まれない。hook の登録を読むのはセッションを
起こしたワークスペースルートの側で、判定が読む層も元リポジトリに checkout されている版
（REQ-MLT-04）だから、ワークツリーの中の `settings.json` や `.ccnavi/config/rules.yml` を
書き換えても、その場では何も変わらない。

それでも守るのは、ワークツリー側の設定が統合先へ入る経路があるから。ワークツリーで書き換えて
ブランチを統合すれば、そのまま統合先の hook の登録とルールになる。実行前の `deny` は
「今の 1 回」を止めるが、統合は別の日の別のセッションが行うので、そこには届かない。
しかもワークスペースルートの側と違って、ワークツリーは `.gitignore` の中にあり、実行後チェックが読む
git の変更一覧にも出てこない。止める仕組みも気づく仕組みも無いまま、時間差で反映される経路になる。

守る場所は増えるが、守る対象の種類は増えていない。上の一式が、それぞれの
ワークツリーにもう 1 つずつ在るというだけ。ワークツリー側の設定を探す先は元リポジトリで決まる。
プロジェクトから切ったワークツリーには、そのプロジェクトが追跡しているファイル
だけが入り、パスは元リポジトリからの相対になる。

実行ファイルだけはワークツリー側に持たない。置き場が `.gitignore` の中にあり、統合で
統合先へ入る経路が無い。

## なぜバックアップを実行前に取るか

実行後に git へ聞く形だと、戻す先が「コミット済みの内容」になる。ユーザが
まだコミットしていない編集を持っていると、それを消す。守るための仕組みが、
守るはずのユーザの編集を消してはいけない。バックアップなら戻す先が「そのツール呼び出しの
直前」になるので、ユーザの書きかけはそのまま残る。

届く範囲も広い。git が無い場所、リポジトリの外、`.gitignore` の中。
判定器の実体も設定ファイルも、git から見えない場所に置かれることがある。

## git は縮退

バックアップが本筋で、git はバックアップが無いときだけ使う。無いのは 2 通りある。

  1. 実行前に、対象そのものが無かった。バックアップする中身が無いので、まず git から
     戻して、戻ったものをバックアップする。バックアップできなければ、次の実行後に戻す先が無い。
  2. 実行後に、バックアップが読めなかった。バックアップの置き場ごと消されたときがこれ。
     戻す先を失っているので、git のコミット済みの内容を使う。

どちらも「本筋が使えないので、劣るほうを使った」と報告に書く。それを
書かないと、戻した先が直前の断面なのかコミット済みの内容なのかを、受け取った側が
見分けられない。ユーザの書きかけが消えているかもしれない、という違いになる。

## バックアップを溜めない

バックアップはセッションごとに分かれる。何もしないと、走らせたセッションの数だけ
残りつづける。小さい設定ファイルは上書きなので増えないが、実行ファイルは
1 セッションにつき丸ごと 1 本で、そこが積み重なる。

2 つで抑える。

  1. 実体はセッションの側に置かない。中身のハッシュで名前を付けた 1 本を
     `store/` に置き、セッションは参照だけ持つ。同じビルドで何セッション
     走っても実体は 1 本のまま。
  2. 古いバックアップはセッション開始で落とす。生きているセッションは呼び出しの
     たびにバックアップを書き直すので、更新時刻が新しいものは巻き添えにならない。
     件数の上限ではなく日付で切るのはここが理由で、長く何もしていないだけの
     生きたセッションを、数が増えたという理由で消したくない。

## 実行後だけでは足りない

hook の登録は設定ファイルの file watcher が拾っていて、書き換えは同じセッションの
うちに反映される。`PostToolUse` の登録を消されると、その次の呼び出しから実行後チェックは
動かない。実際に動かして確かめてある。だから実行前にも手を入れて、対象が消えていれば
そこで戻す。

実行前は消せない。消せるとすれば `PreToolUse` の登録ごとで、そのときは ccnavi が
一切動かない。hook の登録を hook 自身で守ることはできないので、そこは仕組みの外。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitstate, platformtag, settings, shellread, tree
from ..infra.modes import DISABLE, DRY_RUN, ENABLE
from ..records import diaglog
from . import rules

# 設定の値。mode と同じ 3 語。定義は modes にあり、ここは借りているだけ。
SETTINGS = (ENABLE, DRY_RUN, DISABLE)
# 戻す働きを持たない切り替えの環境変数の値。dry-run が無い。
#
# 「止めずに報告する」は、止めたあとに何が起きたかを見せられる働き（deny の場所を
# 戻す、コアファイルをバックアップから戻す）があって初めて意味を持つ。チケットの承認の経路は
# その形を持たない。通せば承認が済んでしまい、済んだものは報告では戻らない。
# 止めずに報告するだけの状態を作れないので、書ける値を 2 つに絞る。
GATE_SETTINGS = (ENABLE, DISABLE)

# バックアップの置き場。state の下にまとめる。セッションごとに分けるのは、バックアップが
# 「このセッションの直前の断面」でしかないから。別のセッションが取った断面で
# 戻すと、こちらが一度も見ていない内容へ書き換えることになる。
BACKUP_DIR = "selfguard"

# 大きい対象の実体の置き場。セッションの下ではなくバックアップの直下に置き、中身の
# ハッシュで名前を付ける。断面が「このセッションのもの」であることは参照の側が
# 担うので、実体まで分ける必要がない。同じ中身を何本も持たない。
STORE_DIR = "store"

# セッションの側に置く参照ファイルの拡張子。実体のハッシュと、バックアップした時点の大きさと
# 更新時刻が入る。
REF_SUFFIX = ".ref"

# バックアップを残す日数。これより長く触られていないセッションのバックアップは落とす。
# バックアップが役に立つのはそれを取ったセッションの中だけなので、短くてよい。
# 中断して翌日に開き直した形までは拾える幅にしてある。
KEEP_DAYS = 3

# 実体をコピーするときの一時名。コピーしている途中で落ちたものを、次のセッションが
# 中身の揃ったバックアップとして拾わないように、名前を分けてから置き換える。
_PART_SUFFIX = ".part"

# 何が起きたかの名前。報告と記録に出る。
ACTION_KEPT = "kept"  # 変わっていない
ACTION_SAVED = "saved"  # バックアップを取った
ACTION_RESTORED = "restored"  # バックアップから戻した
ACTION_RESTORED_GIT = "restored-from-git"  # バックアップが無く、git から戻した
ACTION_MISSING = "missing"  # 対象が無く、戻す先も無い
ACTION_FAILED = "failed"  # 戻そうとして駄目だった
ACTION_WOULD = "would-restore"  # dry-run。戻す代わりに言うだけ
ACTION_LEFT = "left-as-repair"  # 読めないルールファイルへの修復なので戻さなかった

# 共通層のルールファイルに付けるバックアップの key（_places）。
COMMON_RULES_KEY = "rules"

# 名指しのツールで書くもの。修復として戻さない判断は、この経路で書いた先にだけ掛ける。
REPAIR_TOOLS = ("Write", "Edit", "NotebookEdit")

# 守る対象。root からの相対で書く。rules は設定で動くので、ここには無い。
_SETTINGS_FILES = (
    ("settings", os.path.join(".claude", "settings.json")),
    ("settings-local", os.path.join(".claude", "settings.local.json")),
)

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
#      行き先の語は、語の中の目印（下の 2 の WORD_SEP）を頭にも終わりにも置かせない。頭に
#      置かせると引用の中の `> 場所` が書き込み先に見える（引用の中の `>` は両側に目印が付くので、
#      `>` のすぐ後ろが目印になる）。語の途中の目印は、いくつ並んでも通す。引用の中の
#      空白は 1 つにつき目印 1 つ、演算子の文字（`(` `)` `&` `;` など）は両側に目印 1 つずつ
#      になるので、`"a  b/.ccnavi/..."` や `"New folder (2)/.ccnavi/..."` は目印が 2 つ続く。
#      照合の手数を増やさないため、3 つ決めてある。
#        - 当て始めは `>` の並び（`[>|&]` の続き）の頭だけ。並びの途中の `>` から当て直すと、
#          `>` が n 個続く入力で当て始めが n 通りに増え、それぞれが行き先の語を読み直す。
#          後読みはルールの regex で使えない（rules._UNSUPPORTED）ので、手前の 1 文字
#          （`[>|&]` でない文字か文字列の頭）ごと当てる
#        - 行き先の語は `>` `|` `&` で始めない。shellread は演算子のあとに必ず空白を置くので、
#          語の頭にこの文字は来ない。並びと語が同じ文字を取り合うと、割り方が並びの長さの
#          ぶんだけ増える
#        - 語は「頭の 1 文字、目印も含めた途中、終わりの 1 文字」の形で書き、目印ごとに
#          区切った繰り返しにしない。どちらも同じ語に当たるが、繰り返しの形は語の終わりを
#          探し直すたびに繰り返しの出入りを試すので、手数が倍ほどになる
#   2. 名指ししたところを必ず書き換えるコマンド。`\x00` はコマンドの切れ目に
#      shellread が置く目印で、`(^|\x00)` はコマンドの先頭を意味する。
#      語の中の切れ目（引用がつないだ空白、語の中の演算子の両側）は別の目印
#      `shellread.WORD_SEP` なので、`[^\x00]*` は同じコマンドの中を丸ごと指す。
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
# 1 のリダイレクトと行き先の語。手数の約束（当て始めと語の頭）はテストが形で確かめるので、
# 名前を分けてある。
_REDIRECT = (
    rf"(?:^|[^>|&])[|&]*>[>|&]* ?"
    rf"(?:[^ {_NOT_A_WORD}>|&](?:[^ \x00]*[^ {_NOT_A_WORD}])?)?"
)
_WRITE_VERBS = (
    rf"({_REDIRECT}"
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
        rel = _inside(root, path) if root else ""
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
    rel = _inside(root, path) if root else ""
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
        rel = _inside(root, path) if root else ""
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

# 読み切れなかったコマンドの生の文字列に、シェルから書き込む形の 1 本を当ててよい `>` の個数の上限。
#
# 生の文字列にはリダイレクトの目印が無いので、_WRITE_VERBS の `>` は引用の中の `>` にも当たり、
# 当たるたびにその後ろの空白の無い語を読む。手数は `>` の個数 × 語の長さで、長さの 2 乗で遅くなる
# （`echo "` + `>a` を 4000 個で約 0.8 秒）。判定の期限は 3 秒で、超えると止める判定を出せずに
# 確認へ落ちる。正規表現は後読みが使えず、式の中で個数を数えて打ち切れないので、照合の前に数える。
# 語の長さに上限を置くと、長いパスの末尾の `.ccnavi/…` を見落として通るので、個数で切る。
#
# 超えたら照合を飛ばして通さず、確認に回す（judge.decide_before）。数えるのは `>` の文字数で、
# `>>` も `2>&1` も単純に足す。読み解けた形には掛けない。シェルの読みがリダイレクトを
# `> 行き先` にそろえてあり、引用の中の `>` に当たらないので、この遅さは出ない。
REDIRECT_LIMIT = 50

REDIRECT_LIMIT_MESSAGE = (
    f"コマンドに '>' が {REDIRECT_LIMIT} 個を超えて含まれていて、保護の照合を安全に終えられません。"
    "長い内容は Write / Edit ツールでファイルに書くか、コマンドを分けて実行してください。"
)


def too_many_redirects(subject: str) -> bool:
    """生の文字列の `>` が REDIRECT_LIMIT を超えるか。"""
    return subject.count(">") > REDIRECT_LIMIT


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


@dataclass
class Target:
    """守る対象 1 つ。"""

    # key はバックアップのファイル名。対象ごとに固定してある。パスから作ると、
    # 設定でルールファイルの場所を変えただけでバックアップが別名になり、
    # 直前の断面を見失う。
    key: str = ""
    # path は行き着く先まで解いた絶対パス。
    path: str = ""
    # label は報告に出すパス。root からの相対で、ユーザが探せる形。
    label: str = ""
    # heavy は、中身を毎回読むには大きすぎる対象。実行ファイルがこれで、
    # PyInstaller が作るものは数十 MB になる。呼び出しのたびに読むと、
    # 判定に設けた期限に影響する。バックアップはセッション開始で 1 度だけ取り、
    # 突き合わせは大きさと更新時刻で行う。
    heavy: bool = False
    # top は、この対象を git から戻すときに渡すルート。空なら root の側から戻す。
    # 持つのは 2 通り。ワークツリー側の設定と、プロジェクトの層の設定。ワークスペースルートの git に
    # `.claude/worktrees/...` を聞いても、そこは `.gitignore` の中なので何も持っていない。
    # `projects/...` も同じで、プロジェクトは自分の git を持つ。持っているのは
    # そのワークツリー自身の git と、そのプロジェクト自身の git。
    top: str = ""
    # copy は、これがワークツリー側の設定であること。報告の文面がここで分かれる。
    # ワークツリー側の設定は今の時点では誰も読まないので、「何も起きていないのに
    # 戻された」と読まれる。統合で反映される経路であることを言わないと、同じ操作が繰り返される。
    copy: bool = False
    # spelled は、リンクを解く前のパス（ワークツリー側の設定だけ持つ）。着手がコピーした分かを
    # 答えさせるときに渡す。解いた先で答えると、リンクに差し替えた形が指す先の中身で外れる。
    spelled: str = ""


@dataclass
class Outcome:
    """対象 1 つについて、この 1 回で何をしたか。"""

    target: Target
    action: str = ""
    detail: str = ""


def resolve(
    stderr: TextIO, flag: str, declared: str, name: str, allowed: tuple[str, ...] = SETTINGS
) -> str:
    """設定の値を解決する。読めない値は enable として扱う。

    mode の解決が読めない値を enable として扱うのと同じ向き。行き着く先が
    「守る」側になる。書き損じた 1 語で保護が消えるより、書き損じた 1 語で
    保護が残るほうがよい。戻す動きはファイルに触るが、触る先は組み込みで
    固定された 3 つだけで、しかも戻す先はこちらが取った直前の断面になる。

    `allowed` を絞ると、そこに無い語も「読めない値」として扱う。dry-run を
    持たない切り替えの環境変数（GATE_SETTINGS）に dry-run と書かれた設定が、止めているのに
    止めていないように読める形で残らないようにする。
    """
    value = (flag or declared or "").strip().lower()
    if not value:
        return ENABLE
    if value in allowed:
        return value
    stderr.write(
        f"ccnavi: {name}={value!r} is not a setting; using {ENABLE}. "
        f"Valid values are {', '.join(allowed)}\n"
    )
    return ENABLE


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


def targets(
    root: str,
    rules_path: str,
    bin_path: str = "",
    layers: list[settings.LayerFile] = (),
    projects_dir: str = "",
) -> list[Target]:
    """守る対象を組み立てる（設計 11.6）。

    ルールファイルと実行ファイルは設定で動くので、解決済みのパスを受け取る。
    空なら、その設定を持たないということなので、対象からも外れる。

    layers は `settings.LayerFile`（層の種別, 層の名前, kind, そのファイル）のリスト
    （`ruleload.layer_files`）。kind は rules / phases / risk。共通層は phases と
    risk の 2 本で来る。rules は `rules_path` が渡していて、両方から並べると同じ
    ファイルを 2 度守ることになる。

    バックアップの key は層ごとに分ける。共通層は kind そのまま（`rules` / `phases` /
    `risk`）、それ以外は `rules:self` / `phases:lib` の形。key はそのままバックアップの
    名前になるので、層が違えば別の断面として残り、取り違えが起きない。
    """
    places = _places(root, projects_dir, rules_path, layers)
    found = [
        Target(key=key, path=path, label=_relative(root, path), top=_home_top(root, home))
        for key, home, path in places
    ]
    if bin_path:
        full = os.path.realpath(bin_path)
        found.append(Target(key="bin", path=full, label=_relative(root, full), heavy=True))
        # 指す先が振り分けの sh なら、hook が実際に走らせるのは `../bin/` の実体。
        # そちらもバックアップする。探す先は binary_clause が守る置き場と同じ条件で決まる。
        # 見つからなければ足さない。組み立てが無いことは sh が起動の時に言う。
        launched = platformtag.launched_executable(bin_path)
        if launched:
            real = os.path.realpath(launched)
            found.append(
                Target(key="bin-launched", path=real, label=_relative(root, real), heavy=True)
            )
    found.extend(_worktree_copies(root, projects_dir, places))
    return found


def _places(
    root: str,
    projects_dir: str,
    rules_path: str,
    layers: list[settings.LayerFile],
) -> list[tuple[str, str, str]]:
    """守る対象の (バックアップの key, 追跡している git プロジェクトルート, 絶対パス)。

    git プロジェクトルートを一緒に持つのは 2 つの用が在るから。git から戻すときに
    どの git に聞くか（プロジェクトの層はそのプロジェクト自身の git）と、ワークツリー側の
    設定をどこからの相対で組むか（元リポジトリから）。

    同じ key が二度来たら後ろを捨てる。バックアップの名前が key で決まるので、重なったまま
    並べると、2 つの対象が同じバックアップを書き換え合う。
    """
    found = [(key, root, os.path.join(root, rel)) for key, rel in _SETTINGS_FILES]
    if rules_path:
        found.append(("rules", root, rules_path))
    for origin, layer, kind, path in layers:
        if not path:
            continue
        found.append(
            (
                _layer_key(origin, layer, kind),
                _layer_home(root, projects_dir, origin, layer),
                path,
            )
        )

    seen = set()
    places = []
    for key, home, path in found:
        if key in seen:
            continue
        seen.add(key)
        places.append((key, home, os.path.realpath(path)))
    return places


def _layer_key(origin: str, layer: str, kind: str) -> str:
    """バックアップの key。共通層は kind そのまま、それ以外は `<kind>:<層>`。

    決めるのは層の種別（`settings.ORIGIN_*`）で、名札の表記ではない。名札で
    比べると、`projects/common/` の 3 本が共通層と同じ key（`rules` / `phases` /
    `risk`）になり、`_places` の重複の排除でその層の 3 本がバックアップと復元の対象から
    丸ごと落ちる。プロジェクトが名前を 1 つ選ぶだけで保護が外れることになる。

    予約名のプロジェクトは置き場をつける（`rules:projects/self`）。`projects/self/`
    の 3 本を素の `rules:self` にすると、こんどはワークスペース自身の層と
    ぶつかって、先に積んだほうだけが残る。名札に予約してある表記は名札の側で
    使い、プロジェクトの側は別の表記にする。
    """
    if origin == settings.ORIGIN_COMMON:
        return kind
    if origin == settings.ORIGIN_PROJECT and settings.is_reserved_layer_name(layer):
        return f"{kind}:{settings.PROJECT_KEY_HOME}{layer}"
    return f"{kind}:{layer}"


def _layer_home(root: str, projects_dir: str, origin: str, layer: str) -> str:
    """その層の設定を追跡している git プロジェクトルート。

    共通層と自身の層はワークスペースルート、プロジェクトの層はそのプロジェクト。
    ここも層の名前では決めない。`projects/common/` の設定はそのプロジェクトの git が
    追跡しているので、層の名前で共通層と同じに扱うと、ワークスペースの git に戻し方を
    聞きに行くことになる。

    名前を引けないものはワークスペースルートとして扱う。そこから切ったワークツリーに
    ワークツリー側の設定が無ければ対象から落ちるだけで、別の場所を保護に行くことにはならない。
    """
    if origin != settings.ORIGIN_PROJECT:
        return root
    return tree.project_root(projects_dir, layer) or root


def _home_top(root: str, home: str) -> str:
    """git から戻すときに渡すルート。ワークスペースルートなら空（root の側から戻す）。"""
    if not home or os.path.realpath(home) == os.path.realpath(root):
        return ""
    return home


def _worktree_copies(
    root: str, projects_dir: str, places: list[tuple[str, str, str]]
) -> list[Target]:
    """ワークツリー側の設定。root の下と同じ設定ファイルが、ワークツリーの中にもある。

    なぜ守るかは冒頭の「ワークツリー側の設定も同じ扱い」に書いた。ここでは
    対象の組み立て方だけ。

    ワークツリーの一覧は `tree.worktrees` から取る。`.claude/worktrees/` の下に
    在るだけではワークツリーと呼ばず、`.git` ファイルと元リポジトリの登録の相互参照が
    両向きに揃ったものだけを数える。参照実装のコピーのような、ただの
    ディレクトリを保護に行かないため。git は起こさないので、呼び出しごとに
    通っても外部プロセスは増えない。

    元リポジトリをつけて列挙する。ワークツリーはワークスペースからもプロジェクトからも
    切れて、中に入っているワークツリー側の設定は元リポジトリが追跡しているものだけになる。
    lib から切ったツリーに `.claude/settings.json` は無いし、lib の層のパスは
    `projects/lib/.ccnavi/config/rules.yml` ではなく `.ccnavi/config/rules.yml`。
    ワークスペースルートからの相対で組むと、どちらの向きにも当たらない。

    設定の置き場は設定で動く。元リポジトリの外を指しているなら、ワークツリーの中に
    対応するものは無いので、そこは対象から落ちる。
    """
    by_home: dict[str, list[tuple[str, str]]] = {}
    for key, home, path in places:
        rel = _inside(home, path)
        if rel:
            by_home.setdefault(os.path.realpath(home), []).append((key, rel))

    copies = []
    for work in tree.worktrees(root, projects_dir):
        home = tree.project_root(projects_dir, work.project) if work.project else root
        for key, rel in by_home.get(os.path.realpath(home), ()):
            full = os.path.realpath(os.path.join(work.root, rel))
            copies.append(
                Target(
                    key=_copy_key(key, work.name),
                    path=full,
                    label=_relative(root, full),
                    top=work.root,
                    copy=True,
                    spelled=os.path.join(work.root, rel),
                )
            )
    return copies


def _copy_key(key: str, name: str) -> str:
    """ワークツリー側の設定のバックアップの名前。

    key はそのままファイル名になるので、ワークツリーの名前を素で混ぜると、
    区切り文字の入った名前でバックアップが別の場所へ書かれる。使えない字を置き換えた表記だけにすると、
    今度は `a/b` と `a_b` が同じ名前になって、別のワークツリーのバックアップを互いに
    書き戻すことになる。中身が入れ替わるので、取り違えは実際に問題になる。
    置き換えた表記に元の名前の digest をつけて、読めることと衝突しないことの
    両方を取る。
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{key}.{fsio.safe_name(name, 40)}-{digest}"


def _inside(root: str, path: str) -> str:
    """root の下に在るなら root からの相対、外に在るなら空文字。"""
    if not path:
        return ""
    rel = _relative(root, os.path.realpath(path))
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return ""
    return rel


def _top(root: str, target: Target) -> str:
    """この対象を git から戻すときに渡す、ワークツリーのルート。"""
    return target.top or gitstate.top_level(root)


def before(
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
) -> list[Outcome]:
    """実行前。対象の断面をバックアップする。消されていれば戻してからバックアップする。

    ここでは内容の照合をしない。バックアップと違うことを理由に実行前に書き戻すと、
    ユーザがエディタで直している最中の 1 文字が、エージェントが何か 1 つ
    ツールを呼んだだけで消える。設定ファイルを直しているときこそ、
    エージェントは並行して動いている。

    見るのは「在るか」だけ。ただし「無い」には 2 通りある。

      * 前は在ったのに今は無い。消された。バックアップから戻す。
      * 最初から無い。`settings.local.json` を置いていないプロジェクトが
        これで、普通の状態になる。ここで毎回 git を呼ぶと、何も起きていない
        呼び出しが毎回外部プロセスを起こす。無いことを 1 度バックアップして、以後は何も出さない。

    2 つを分けるのがバックアップの有無。バックアップが在るのに対象が無いなら、バックアップを取った
    あとに消えたということになる。
    """
    if setting == DISABLE or not state_dir:
        return []

    outcomes = []
    for target in found:
        if target.heavy:
            # 大きい対象はセッション開始で 1 度だけバックアップする。呼び出しのたびに
            # 数十 MB をコピーすると、判定を待たせるために置いた仕組みが、判定より
            # 重くなる。
            continue
        content = _read(target.path)
        if content is not None:
            _clear_absent(state_dir, session, target)
            failed = _write_backup(state_dir, session, target, content)
            if failed:
                outcomes.append(Outcome(target, ACTION_FAILED, f"バックアップを書けない: {failed}"))
            # バックアップを取れた回は報告に出さない。毎回の呼び出しで 3 行増えると、本当に
            # 言うべき 1 行がその中に埋もれる。記録には残る。
            continue

        if _absent_noted(state_dir, session, target):
            # 最初から無いと分かっている。何も言わない。
            continue

        saved = _read_backup(state_dir, session, target)
        outcome = _recover_missing(setting, root, target, saved)
        content = _read(target.path)
        if content is None:
            # 戻せなかった。バックアップも git も持っていないなら、そもそも
            # 置かれていないファイルなので、マーカーを残して次から何も出さない。
            if saved is None:
                _note_absent(state_dir, session, target)
                continue
            outcomes.append(outcome)
            continue
        outcomes.append(outcome)
        _write_backup(state_dir, session, target, content)
    return outcomes


def after(
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
    written: str,
    synced: Callable[..., bool],
) -> list[Outcome]:
    """実行後。バックアップと突き合わせて、変わっていれば戻す。

    バックアップが読めなければ git のコミット済みの内容を使う。使ったことは
    報告に書く。戻した先が直前の断面なのかコミット済みの内容なのかで、
    ユーザの書きかけが残っているかどうかが変わるので。

    written は、この呼び出しが名指しのツール（REPAIR_TOOLS）で書いた先の解決済みの
    パス。組み込みの既定を使っている間の修復だけは戻さない（_left_as_repair）。

    synced は、ワークツリー側の設定の変更が、着手のときに共通層でプロジェクトの層を
    上書きしたものかを答える（`configsync.is_synced_write`）。そう読めるものは戻さない。
    戻すと着手がコピーした中身が同じ呼び出しの中で消え、最初のレビューで知らせる上書きの記録だけが残る。
    ワークツリー側の設定に限るのは、上書きするのが親のワークツリーだけだから。
    """
    if setting == DISABLE or not state_dir:
        return []

    outcomes = []
    for target in found:
        if target.heavy:
            heavy = _check_heavy(setting, state_dir, session, target)
            if heavy is not None:
                outcomes.append(heavy)
            continue

        saved = _read_backup(state_dir, session, target)
        now = _read(target.path)

        if saved is not None and now == saved:
            continue

        if target.copy and now is not None and synced(target.spelled or target.path):
            continue

        if _left_as_repair(target, written, saved, now):
            outcomes.append(
                Outcome(
                    target,
                    ACTION_LEFT,
                    "ルールファイルが読めない間に Write / Edit で直したので、戻さなかった",
                )
            )
            continue

        if saved is None:
            if now is None:
                # 対象もバックアップも無い。置いていないファイルなので何も言わない。
                # 実行前がここにマーカーを残しているが、マーカーが読めない場合でも
                # 「無いものが無いまま」を異常として扱わない。
                continue
            if _absent_noted(state_dir, session, target):
                # 無かったところに現れた。消しはしない。ユーザが置くこともある
                # ファイルで、消すほうが取り返しが付かない。言うだけにする。
                outcomes.append(
                    Outcome(
                        target,
                        ACTION_MISSING,
                        "無かったところに設定ファイルが現れた。"
                        "hook の登録が増えているかもしれないので、"
                        "中身をユーザに見てもらってください",
                    )
                )
                continue
            # バックアップが無いまま中身が在る。実行前にバックアップを取れなかった回。
            outcomes.append(_fall_back_to_git(setting, root, target, now))
            continue

        if setting != ENABLE:
            outcomes.append(
                Outcome(target, ACTION_WOULD, "本来ならバックアップから戻す（今回は触っていない）")
            )
            continue

        failed = _write(target.path, saved)
        if failed:
            outcomes.append(Outcome(target, ACTION_FAILED, f"戻せない: {failed}"))
        else:
            outcomes.append(
                Outcome(target, ACTION_RESTORED, "このツール呼び出しの直前の内容に戻した")
            )
    # 何も起きていない回は落とす。ACTION_KEPT はここまでの経路で「調べたが
    # 変わっていなかった」を伝えるための値で、報告に出す用件ではない。
    return [o for o in outcomes if o.action != ACTION_KEPT]


def _left_as_repair(target: Target, written: str, saved: bytes | None, now: bytes | None) -> bool:
    """組み込みの既定を使っている間の修復か。そうなら戻さない（REQ-PRE-06）。

    共通層のルールファイルが読めないと、組み込みの既定は Write / Edit による修復を通す。
    ところが実行前に取るバックアップは壊れた中身なので、そのまま戻すと、直した結果が同じ呼び出しの
    中で消える。「Write / Edit で直せ」という案内と実際が食い違う。

    戻さないのは次の 4 つが揃ったときだけ。
      1. 対象が共通層のルールファイルそのもの。組み込みの既定に戻る原因になるのはこの
         1 本だけで、ワークツリー側の設定や他の層の設定は読めなくても既定に戻らない
      2. この呼び出しが名指しのツールでそこを書いた。シェルからの書き込みは既定でも止める
         経路なので、ここでも戻す
      3. バックアップ（この呼び出しの直前の中身）がルールファイルとして読めない。実行後の中身で
         決めると、読める版を壊した書き込み（dry-run の deny は止めない）がそのまま残り、
         壊すことが戻されない経路になる
      4. 今もファイルが在る。消した呼び出しは修復ではない
    """
    if not written or target.copy or target.key != COMMON_RULES_KEY:
        return False
    if saved is None or now is None:
        return False
    if os.path.realpath(written) != target.path:
        return False
    return not rules.readable(saved)


def at_start(
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
) -> list[Outcome]:
    """セッションが始まったとき。対象をすべてバックアップする。

    大きい対象をバックアップするのはここだけ。実行ファイルがこれにあたり、ツール呼び出しの
    たびに数十 MB をコピーするわけにはいかないので、コピーするのはセッションに 1 度。そのあいだに
    作り直されたものはバックアップと食い違うが、作り直しはユーザが起こす作業なので、食い違いは
    報告に出てユーザの目に触れる。何も出さずに新しいほうをバックアップし直すと、差し替えと作り直しが
    同じ見た目になる。

    小さいほうも一緒にバックアップする。実行前のバックアップが始まるのは最初のツール呼び出しからで、
    それより前に設定ファイルを消されると、バックアップを持たないまま実行後チェックに入る。
    セッションの開始そのものが、いちばん早く取れる断面になる。

    バックアップするだけで、戻さない。`dry-run` でもバックアップするのは、
    バックアップすることが誰の書きかけも消さない側の操作だから。ここを `enable` に限ると、
    切り替えた最初のセッションが戻す先を持たないまま走る。設定は変えた時点から反映したい。
    何を戻すかは実行前と実行後が `setting` を見て決める。

    古いバックアップを落とすのもここ。セッションに 1 度しか来ない場所が他に無く、
    ツール呼び出しのたびに置き場を数えると、判定を待たせるために置いた仕組みが
    判定より重くなる。
    """
    if setting == DISABLE or not state_dir:
        return []

    sweep(state_dir, session)

    outcomes = []
    for target in found:
        if target.heavy:
            if not os.path.exists(target.path):
                outcomes.append(
                    Outcome(
                        target,
                        ACTION_MISSING,
                        "実行ファイルが見つからない。CCNAVI_BIN_PATH のパスを確かめてください",
                    )
                )
                continue
            failed = _save_heavy(state_dir, session, target)
            if failed:
                outcomes.append(Outcome(target, ACTION_FAILED, f"バックアップを取れない: {failed}"))
            continue

        content = _read(target.path)
        if content is None:
            # 最初から無い。`settings.local.json` を置いていない形がこれで、
            # 異常ではない。無いことのマーカーは実行前の側が残す。開始の時点では
            # まだ「消された」と「置いていない」を見分ける手がかりが無い。
            continue
        _clear_absent(state_dir, session, target)
        failed = _write_backup(state_dir, session, target, content)
        if failed:
            outcomes.append(Outcome(target, ACTION_FAILED, f"バックアップを書けない: {failed}"))
    return outcomes


def sweep(state_dir: str, session: str) -> None:
    """古いバックアップを落とす。

    セッション開始で 1 度だけ呼ぶ。バックアップが役に立つのはそれを取ったセッションの
    中だけなので、しばらく触られていないものは持っていても戻す先にならない。

    生きているセッションを巻き添えにしないために、見るのは更新時刻にする。
    実行前のバックアップは呼び出しのたびに書き直されるので、動いているセッションの
    置き場は必ず新しい。件数の上限で切ると、長くユーザの返事を待っているだけの
    セッションが、他が増えたという理由でバックアップを失う。

    自分の置き場は日付を見ずに残す。始まったばかりで中身が無い、あるいは
    時計がずれている環境で、自分のバックアップを自分で消してしまわないため。

    掃除に失敗してもセッションは始める。バックアップが消せないことは、バックアップが取れない
    ことより軽い。ここで止めると、ディスクの都合で保護が使えなくなる。
    """
    if not state_dir:
        return
    root = os.path.join(state_dir, BACKUP_DIR)
    try:
        names = os.listdir(root)
    except OSError:
        return

    cutoff = time.time() - KEEP_DAYS * 86400
    mine = _safe(session)
    kept = []
    for name in names:
        path = os.path.join(root, name)
        if name in (mine, STORE_DIR) or not os.path.isdir(path):
            kept.append(name)
            continue
        if _touched(path) >= cutoff:
            kept.append(name)
            continue
        shutil.rmtree(path, ignore_errors=True)
    _sweep_store(root, kept, cutoff)


def _touched(path: str) -> float:
    """置き場がいちばん最後に触られた時刻。

    ディレクトリの更新時刻だけでは足りない。中身を書き換えても、名前が
    増えなければ親は動かないファイルシステムがある。
    """
    newest = 0.0
    try:
        names = os.listdir(path)
    except OSError:
        return newest
    for entry in (path, *(os.path.join(path, name) for name in names)):
        try:
            newest = max(newest, os.stat(entry).st_mtime)
        except OSError:
            continue
    return newest


def _sweep_store(root: str, kept: list[str], cutoff: float) -> None:
    """どのセッションからも参照されなくなった実体を落とす。

    2 つの条件が揃ったものだけを消す。残ったセッションの参照に出てこないことと、
    最後に使われてから日が経っていること。参照だけを見ると、セッション開始が
    まだ来ていない置き場の実体を消しうる。時刻だけを見ると、何日も続いている
    セッションが、自分が戻す先を失う。
    """
    store = os.path.join(root, STORE_DIR)
    try:
        entries = os.listdir(store)
    except OSError:
        return

    alive = set()
    for name in kept:
        directory = os.path.join(root, name)
        try:
            files = os.listdir(directory)
        except OSError:
            continue
        for found in files:
            if not found.endswith(REF_SUFFIX):
                continue
            ref = _parse_ref(_read(os.path.join(directory, found)))
            if ref:
                alive.add(ref[0])

    for name in entries:
        if name in alive:
            continue
        path = os.path.join(store, name)
        try:
            if os.stat(path).st_mtime >= cutoff:
                continue
        except OSError:
            continue
        with contextlib.suppress(OSError):
            os.remove(path)


def _save_heavy(state_dir: str, session: str, target: Target) -> str:
    """大きい対象をバックアップする。コピーできたら空文字、駄目なら理由を返す。

    実体は中身のハッシュで名前を付けて `store/` に 1 本だけ置き、セッションの
    側には参照を置く。同じビルドのまま何セッション走っても、コピーは 1 本で済む。

    参照には、バックアップした時点の大きさと更新時刻も書く。突き合わせはこの数字と
    現物を比べる形になるので、実体の側の時刻をあとから触っても判定は動かない。
    """
    try:
        shape = os.stat(target.path)
    except OSError as exc:
        return f"{exc}"
    digest = _digest(target.path)
    if not digest:
        return f"読めない: {target.path}"

    entry = _store_path(state_dir, digest)
    if not os.path.exists(entry):
        failed = _place(target.path, entry)
        if failed:
            return failed
    # 最後に使った時刻にする。掃除はこれを見て、使われなくなった実体を落とす。
    with contextlib.suppress(OSError):
        os.utime(entry, None)

    ref = f"{digest} {shape.st_size} {int(shape.st_mtime)}\n".encode()
    failed = _write(_ref_path(state_dir, session, target), ref)
    if failed:
        return failed
    # セッションごとに実体を持っていたころのバックアップ。参照に置き換わったので要らない。
    with contextlib.suppress(OSError):
        os.remove(_backup_path(state_dir, session, target))
    return ""


def _check_heavy(setting: str, state_dir: str, session: str, target: Target) -> Outcome | None:
    """大きい対象を、大きさと更新時刻で突き合わせる。

    中身は読まない。バックアップした時点の大きさと更新時刻を参照に書いてあるので、
    差し替えられればどちらかが必ず動く。同じ大きさで同じ時刻に作った別物までは
    見分けられないが、そこを見分けるには毎回全部を読むことになり、実行前
    チェックに設けた期限を設定ファイル 1 つのために使い切ることになる。

    バックアップが無いときは何も出さない。セッション開始のイベントに登録していない、あるいは
    実行ファイルを指していない設定がこれで、異常ではない。登録の漏れは
    `--lint` が言う。
    """
    ref = _parse_ref(_read(_ref_path(state_dir, session, target)))
    if ref is None:
        return None
    digest, size, mtime = ref
    if _same_shape(target.path, size, mtime):
        return None
    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "本来ならバックアップから戻す（今回は触っていない）")

    entry = _store_path(state_dir, digest)
    if not os.path.exists(entry):
        return Outcome(
            target,
            ACTION_FAILED,
            "バックアップの実体が見つからないので戻せない。"
            "ccnavi を作り直して、セッションを開き直してください",
        )
    failed = _place(entry, target.path)
    if failed:
        return Outcome(target, ACTION_FAILED, f"戻せない: {failed}")
    # バックアップした時点の時刻に戻す。実体はコピーなので、コピーした時刻をそのまま置くと
    # 次の突き合わせが毎回食い違ったまま報告されつづける。
    with contextlib.suppress(OSError):
        os.utime(target.path, (mtime, mtime))
    return Outcome(target, ACTION_RESTORED, "セッション開始の時点の実行ファイルに戻した")


def _same_shape(path: str, size: int, mtime: int) -> bool:
    """バックアップした時点の大きさと更新時刻に、現物が一致するか。

    秒未満を落として比べる。バックアップは更新時刻ごとコピーするが、コピーした先の
    ファイルシステムがそこまでの精度を持たないことがある。
    """
    try:
        found = os.stat(path)
    except OSError:
        return False
    return found.st_size == size and int(found.st_mtime) == mtime


def _digest(path: str) -> str:
    """中身のハッシュ。読めなければ空文字。

    まるごと読むが、これを呼ぶのはセッション開始の 1 度だけ。ツール呼び出しの
    たびの突き合わせは、参照に書いた大きさと更新時刻だけで済ませる。
    """
    found = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                found.update(chunk)
    except OSError:
        return ""
    return found.hexdigest()


def _parse_ref(raw: bytes | None) -> tuple[str, int, int] | None:
    """参照を読み解く。読めない形は「バックアップが無い」として扱う。"""
    if not raw:
        return None
    parts = raw.decode("utf-8", "replace").split()
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _place(source: str, destination: str) -> str:
    """別の名前へコピーしてから置き換える。コピーできたら空文字、駄目なら理由を返す。

    途中で落ちたコピーを、中身の揃ったバックアップとして次のセッションに拾わせないため。
    """
    part = destination + _PART_SUFFIX
    failed = _copy(source, part)
    if failed:
        with contextlib.suppress(OSError):
            os.remove(part)
        return failed
    try:
        os.replace(part, destination)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.remove(part)
        return f"{exc}"
    return ""


def _copy(source: str, target: str) -> str:
    """更新時刻ごとコピーする。コピーできたら空文字、駄目なら理由を返す。"""
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        return f"{exc}"
    return ""


def report(outcomes: list[Outcome]) -> str:
    """モデルに返す文。何が起きたかと、次に何をすべきかを書く。"""
    if not outcomes:
        return ""
    lines = [
        "[ccnavi] ccnavi 自身の設定ファイルが変更されました。"
        "ここはルールファイルの外で守っている場所で、判定と hook の登録は"
        "このファイルで決まります。",
    ]
    for outcome in outcomes:
        lines.append(f"  {outcome.target.label}: {outcome.action} — {outcome.detail}")
    if any(outcome.target.copy for outcome in outcomes):
        # ワークツリー側の設定が混じっている。今の時点の判定は変わらないので、
        # 「何も起きていないのに戻された」と読まれる。時間差で反映される経路であることを
        # 言わないと、次に同じ操作が繰り返される。
        lines.append(
            "ワークツリー側の設定は、その場では誰も読みませんが、統合すれば統合先の "
            "hook の登録とルールになります。"
        )
    if any(outcome.action == ACTION_LEFT for outcome in outcomes):
        # 戻さなかった回。「次も戻る」と言うと嘘になる。代わりに、ユーザが中身を見る
        # 用件を渡す。ルールが読めない間の書き込みは、どれだけ緩めたかを誰も判定していない。
        lines.append(
            "読めなかった共通層のルールファイルへの修復は戻していません。"
            "直した中身が意図どおりかを、ユーザが確かめてください。"
        )
    if any(outcome.action != ACTION_LEFT for outcome in outcomes):
        lines.append(
            "変更が要るなら、何をなぜ変えたいのかをユーザに伝えて依頼してください。"
            "自分で書き換えると、次の呼び出しで同じように戻ります。"
        )
    return "\n".join(lines)


def _recover_missing(setting: str, root: str, target: Target, saved: bytes | None) -> Outcome:
    """実行前に対象が無かった。バックアップがあればバックアップから、無ければ git から戻す。

    バックアップを先に見る。バックアップは「このセッションで最後に見た内容」で、git が持つのは
    「最後にコミットされた内容」。消えたものを戻すなら、近いほうから戻す。
    """
    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "対象が無い。本来なら戻す")
    if saved is not None:
        failed = _write(target.path, saved)
        if failed:
            return Outcome(target, ACTION_FAILED, f"消えた対象を戻せない: {failed}")
        return Outcome(target, ACTION_RESTORED, "消えていたので、直前の内容に戻した")
    top = _top(root, target)
    failed = gitstate.restore_committed(top, _relative(top, target.path).replace(os.sep, "/"))
    if failed:
        return Outcome(target, ACTION_MISSING, f"対象が無く、git からも戻せない: {failed}")
    return Outcome(target, ACTION_RESTORED_GIT, "対象が無かったので、コミット済みの内容から戻した")


def _fall_back_to_git(setting: str, root: str, target: Target, now: bytes | None) -> Outcome:
    """バックアップが無いまま実行後に来た。git に頼る。

    git が「変わっていない」と言うなら何もしない。バックアップが取れなかっただけで
    誰も触っていない、という回がここに来るので、そこで毎回 git restore を
    打つと、何も起きていない呼び出しがファイルに触ることになる。
    """
    top = _top(root, target)
    changes, unreadable = gitstate.read(top)
    if unreadable:
        if now is None:
            return Outcome(
                target, ACTION_MISSING, f"対象もバックアップも無く、git も読めない: {unreadable}"
            )
        return Outcome(target, ACTION_FAILED, f"バックアップが無く、git も読めない: {unreadable}")

    dirty = [c for c in changes if os.path.realpath(c.full) == target.path]
    if not dirty and now is not None:
        return Outcome(target, ACTION_KEPT, "バックアップは無いが、コミット済みの内容と同じ")

    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "バックアップが無い。本来なら git から戻す")

    failed = gitstate.restore_committed(top, _relative(top, target.path).replace(os.sep, "/"))
    if failed:
        return Outcome(target, ACTION_FAILED, f"バックアップが無く、git からも戻せない: {failed}")
    return Outcome(
        target,
        ACTION_RESTORED_GIT,
        "バックアップが無いのでコミット済みの内容へ戻した。"
        "コミットしていない編集があったなら、それは残っていない",
    )


def _absent_path(state_dir: str, session: str, target: Target) -> str:
    """「このファイルは置かれていない」というマーカーの置き場。

    マーカーを持つのは、無いことを毎回 git に確かめに行かないため。設定ファイルを
    置いていないプロジェクトでは、無いことがそのプロジェクトの正常な状態になる。
    そこで呼び出しのたびに外部プロセスを起こすと、何も起きていない作業が
    いちばん重くなる。
    """
    return _backup_path(state_dir, session, target) + ".absent"


def _absent_noted(state_dir: str, session: str, target: Target) -> bool:
    return os.path.exists(_absent_path(state_dir, session, target))


def _note_absent(state_dir: str, session: str, target: Target) -> None:
    _write(_absent_path(state_dir, session, target), b"")


def _clear_absent(state_dir: str, session: str, target: Target) -> None:
    """マーカーを消す。無かったはずのものが現れたら、次からは普通にバックアップする。"""
    with contextlib.suppress(OSError):
        os.remove(_absent_path(state_dir, session, target))


def _safe(session: str) -> str:
    """セッション識別子から作る、置き場の名前。

    そのまま名前になるので、区切り文字が混じった値でファイルを別の場所へ
    書かせない。
    """
    return fsio.safe_name(session or "no-session", limit=None)


def _backup_path(state_dir: str, session: str, target: Target) -> str:
    """バックアップの置き場。key はそのままでは名前にしない。

    層の key には `:` が入る（`rules:self`、`phases:lib`）。Windows でその表記の
    ファイルを開くと、同じ名前のファイルの代替データストリームに書くことになり、
    バックアップが在るのに読めない形になる。名前に使える字へ置き換えてから置く。

    切り詰めない。ワークツリー側の設定の key は末尾にワークツリーの名前の digest を持っていて、
    そこを落とすと別のワークツリーのバックアップと同じ名前になる。長さで落ちるなら、
    書けなかったことが報告に出るほうがよい。
    """
    name = fsio.safe_name(target.key, limit=None)
    return os.path.join(state_dir, BACKUP_DIR, _safe(session), name)


def _ref_path(state_dir: str, session: str, target: Target) -> str:
    """大きい対象の参照の置き場。実体は持たず、どれを指しているかだけを持つ。"""
    return _backup_path(state_dir, session, target) + REF_SUFFIX


def _store_path(state_dir: str, digest: str) -> str:
    """実体の置き場。セッションをまたいで共有するので、セッションの下には分けない。"""
    return os.path.join(state_dir, BACKUP_DIR, STORE_DIR, digest)


def _read_backup(state_dir: str, session: str, target: Target) -> bytes | None:
    return _read(_backup_path(state_dir, session, target))


def _write_backup(state_dir: str, session: str, target: Target, content: bytes) -> str:
    return _write(_backup_path(state_dir, session, target), content)


def _read(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。バイト列で扱う理由は fsio.read_bytes を見よ。"""
    return fsio.read_bytes(path)


def _write(path: str, content: bytes) -> str:
    """中身をそのまま書く。書けたら空文字、駄目なら理由を返す。"""
    return fsio.write_bytes(path, content)


def _relative(base: str, path: str) -> str:
    """報告と git に渡すための、base からの相対。
    別のドライブに在るなど、相対にできないものは絶対のまま返す。

    base も行き着く先まで解く。path（Target.path）は解いたパスで来るので、base が
    リンクを含むまま（macOS の /var → /private/var など）だと、相対が base の外へ
    `../` で回り込み、git に渡すパスが別の場所を指して戻せなくなる。"""
    try:
        return os.path.relpath(path, os.path.realpath(base))
    except ValueError:
        return path
