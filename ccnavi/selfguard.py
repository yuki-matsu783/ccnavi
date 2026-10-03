"""ccnavi 自身を成り立たせている設定ファイルを、実行前にバックアップし、実行後に戻す。

## 守る対象をルールファイルに書かない

ルールに書いた守りは、ルールを消せば一緒に消える。実行後の監視は保護領域を
ルールファイルの `deny` と `ask` から導くので、ルールファイル自身をそこで守ると、
書き換えられた時点で「何を守るか」の一覧ごと失われる。`deny` を空にされた形も、
壊れて組み込みの既定に戻った形も、どちらも保護領域は 0 件になり、監視は何も
検知しない。守りの根拠が、守られる対象の中に置いてあることが原因になる。

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
ccnavi ディレクトリの下のファイル数は定まらない。
呼び出しのたびに全部を読んで比べることになり、実行前の判定に設けた期限に影響する。
数が定まっているのは設定 3 本のほうで、そこがコアとコアでないものの境になる。

## ワークツリー側の設定も同じ扱い

上のファイルは追跡されているので、`.claude/worktrees/<名前>/` の中にも同じものが入る。
このワークツリー側の設定は、今の時点では誰にも読まれない。hook の登録を読むのはセッションを
起こしたワークスペースルートの側で、判定が読む層も元リポジトリに checkout されている版
（REQ-MLT-04）だから、ワークツリーの中の `settings.json` や `.ccnavi/config/rules.yml` を
書き換えても、その場では何も変わらない。

それでも守るのは、ワークツリー側の設定が統合先へ入る経路があるから。ワークツリーで書き換えて
ブランチを統合すれば、そのまま統合先の hook の登録とルールになる。実行前の `deny` は
「今の 1 回」を止めるが、統合は別の日の別のセッションが行うので、そこには届かない。
しかもワークスペースルートの側と違って、ワークツリーは `.gitignore` の中にあり、実行後の監視が読む
git の変更一覧にも出てこない。止める仕組みも気づく仕組みも無いまま、時間差で反映される経路になる。

守る場所は増えるが、守る対象の種類は増えていない。上の一式が、それぞれの
ワークツリーにもう 1 つずつ在るというだけ。ワークツリー側の設定を探す先は元リポジトリで決まる。
プロジェクトから切ったワークツリーには、そのプロジェクトが追跡しているファイル
だけが入り、表記は元リポジトリからの相対になる。

実行ファイルだけはワークツリー側に持たない。置き場が `.gitignore` の中にあり、統合で
統合先へ入る経路が無い。

## なぜバックアップを実行前に取るか

実行後に git へ聞く形だと、戻す先が「コミット済みの内容」になる。利用者が
まだコミットしていない編集を持っていると、それを消す。守るための仕組みが、
守るはずの人の編集を消してはいけない。バックアップなら戻す先が「そのツール呼び出しの
直前」になるので、人の書きかけはそのまま残る。

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
見分けられない。人の書きかけが消えているかもしれない、という違いになる。

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
うちに反映される。`PostToolUse` の登録を消されると、その次の呼び出しから実行後の監視は
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

from . import diaglog, fsio, gitstate, platformtag, rules, settings, shellread, tree
from .modes import DISABLE, DRY_RUN, ENABLE

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

# セッションの側に置く参照の表記。実体のハッシュと、バックアップした時点の大きさと
# 更新時刻が入る。
REF_SUFFIX = ".ref"

# バックアップを残す日数。これより長く触られていないセッションのバックアップは落とす。
# バックアップが役に立つのはそれを取ったセッションの中だけなので、短くてよい。
# 中断して翌日に開き直した形までは拾える幅にしてある。
KEEP_DAYS = 3

# 実体を写すときの一時名。写している途中で落ちたものを、次のセッションが
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
# 前半の括弧が書き込む表記で、後ろに続く場所と組で当たる。場所の名前が出ただけでは
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
#   3. sed だけは `-i` が付いた形に絞る。`sed -n 1,20p` はただの読み。
#
# 元と行き先がある cp / ln / install は組が違うので後ろに分けてある。見るのは
# 行き先の側だけで、行き先は最後の引数なので、コマンドの終わりに来た形に絞る。
_NOT_A_WORD = re.escape(shellread.SEP) + re.escape(shellread.WORD_SEP)
_WRITE_VERBS = (
    rf"(>[>|&]* ?[^ {_NOT_A_WORD}]*"
    r"|(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*"
    r"|(^|\x00)sed\b[^\x00]*-i[^\x00]*)"
)
_COPY_VERBS = r"(^|\x00)(cp|ln|install)\b[^\x00]*"

# 名前がそこで終わる形。空白とコマンドの切れ目（`\x00`）を語の終わりとして数える。
# `[\\/]` だけで閉じていると、区切りが続かない表記が止まらずに通る。`rm -rf .ccnavi` も
# `mv .ccnavi .ccnavi.bak` も、ccnavi ディレクトリごと消す・退かす形なので、下のファイルを 1 本ずつ
# 書き換えるのと同じだけ守りが消える（敵対的レビュー A-3）。
# 語の中の目印も終わりに数える。数えないと、`rm ".ccnavi x"` のように引用がつないだ
# 表記が通る。
_TERM = rf"(?:[ {_NOT_A_WORD}]|$)"
# 区切りが続く形と、そこで終わる形の両方。`.ccnavi/config/x` にも `.ccnavi` にも
# 当たり、`.ccnavixyz` のような別名には当たらない。
_END = rf"(?:[\\/ {_NOT_A_WORD}]|$)"
# 元と行き先がある cp / ln / install のための終わり。空白とコマンドの切れ目を
# 数えない。あちらは行き先（最後の引数）だけを見る形で、後ろに `[^ \x00]*($|\x00)`
# が続く。空白を数えると `cp .ccnavi /tmp/x` のように ccnavi ディレクトリから外へ写すだけの読みが
# 止まり、`\x00` を数えるとその切れ目に先に当たってしまい、後ろの当てが外れる。
_COPY_TERM = r"$"
_COPY_END = r"(?:[\\/]|$)"

# `.ccnavi/` は ccnavi ディレクトリの既定の表記（設計 11.2）。その下には各層の設定 3 本と、
# 配点が呼ぶスクリプトが入る。どちらも判定の中身そのものなので、ccnavi ディレクトリごと止める。
# 既定の表記をここに書いておくのは、ccnavi ディレクトリの名前を動かしていないワークスペースが、
# 設定の受け渡しに依らずに守られるようにするため。動かしてある場合は project_home_clause が足す。
#
# `.claude` の側は、その下の名前を絞ってある（`worktrees/` は守る対象ではない）。
# だから ccnavi ディレクトリと違って、名前がそこで終わる形は `_TERM` で閉じる。`_END` にすると
# `.claude/` に続く表記全部が入り、ワークツリーの片付けまで止まる。
# チケットの sh は `.ccnavi/scripts/` にあるので、`.claude` の側で守るのは hook と設定ファイルだけ。
#
# `logs/` は記録と状態ディレクトリ（`logs/decisions.jsonl` と `logs/state/`）。
# どちらも判定が読むので
# 名前を絞って守る。ローテートした記録（`logs/decisions.<日時>.jsonl`、prune）も同じ表記で守る。
# 判定は読まないが、「記録が無い = 動かなかった」を読む元で、シェルから消せると自分の呼び出しの
# 記録を消せる。消すのはセッションの開始と、端末から打つ `ccnavi --prune` だけ。
# `logs/` の下の git のラッパースクリプトの記録は、消しても判定に影響しないので守らない。
_PLACES = (
    r"\.claude(?:[\\/](hooks" + _END + r"|settings[\w.-]*\.json)|" + _TERM + r")",
    r"\.ccnavi" + _END,
    r"logs[\\/](decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state)" + _END,
    r"ccnavi-git\.sh",
)
_COPY_PLACES = (
    r"\.claude(?:[\\/](hooks|settings)|" + _COPY_TERM + r")",
    # 行き先が ccnavi ディレクトリそのもの（`cp /tmp/x .ccnavi`）でも止める。
    r"\.ccnavi" + _COPY_END,
    r"logs[\\/](decisions(?:\.[^\s\\/\x00]*)?\.jsonl|state)" + _COPY_END,
    r"ccnavi-git\.sh",
)


def shell_write_regex(bin_path: str = "", *extra_clauses: str) -> str:
    """設定ファイルへシェルから書き込む形。実行ファイルの表記は設定で動くので、
    ここで組み立てる。

    実行ファイルを場所の一覧に足すのは、そこが判定器の実体だから。差し替えられると
    ルールを 1 行も変えずに判定そのものを入れ替えられる。しかも置き場は
    `.gitignore` の中にあることが多く、そうなると実行後の監視からも見えない。

    extra_clauses は設定で動く場所の表記。ccnavi ディレクトリ（project_home_clause）と
    共通層の 3 本（common_shell_clause）が来る。既定の名前は _PLACES に書いてあるので、
    ここで足すのは動かしてある場合の表記になる。層の設定は行き先の判定に使うので、
    書けるとエージェントが自分のルールを緩められる。
    """
    places = [*_PLACES]
    copy_places = [*_COPY_PLACES]
    for clause in (binary_clause(bin_path), *extra_clauses):
        if clause:
            places.append(clause)
            copy_places.append(clause)
    where = _folded("(" + "|".join(places) + ")")
    copy_where = _folded("(" + "|".join(copy_places) + ")")
    return rf"{_WRITE_VERBS}{where}|{_COPY_VERBS}{copy_where}[^ \x00]*($|\x00)"


def _folded(clause: str) -> str:
    """場所の表記を、大文字小文字を区別しない形にする。どの機械でも同じ。

    大文字小文字を区別しない機械では `.Ccnavi/scripts/count.sh` は
    `.ccnavi/scripts/count.sh` そのもので、消せば本物が消える。区別すると、表記を
    1 文字変えるだけで ccnavi ディレクトリの中が書ける。
    区別する機械でも区別せずに当てる。ルールの glob とチケットの範囲はどの機械でも
    区別しないので、
    守りだけが機械で当たり方を変えると、同じ表記の扱いがルールと守りで食い違う。

    ここで囲むのは場所の表記だけだが、組み込みの守りも `rules._build` を通るので、
    式全体が `re.IGNORECASE` で当たる（ADR-0051）。コマンドの名前（`rm` / `cp`）も
    結果として区別されなくなる。`RM` が走る保証は無いので守りが増えるわけではないが、
    `deny` が広がる側なので、そのままにしてある。区別が要るルールは `(?-i:...)` で囲む。
    """
    return f"(?i:{clause})"


def project_home_clause(project_home: str) -> str:
    """ccnavi ディレクトリの表記を、シェルの書き込みに当てる形に直す（設計 11.6）。

    ccnavi ディレクトリの下は丸ごと守る。層の設定 3 本も、配点が呼ぶスクリプトも、そこに入る。
    既定の名前（`.ccnavi`）は _PLACES が持っているので、ここが返すのは動かして
    ある場合の表記。区切りはどちらの表記にも当て、名前がそこで終わる形（ccnavi ディレクトリごと
    消す・退かす）にも当てる。

    返す 1 本は書き込む側と写す側の両方に足される。写す側だけは既定の名前が
    `_COPY_END`（空白を数えない）で閉じているので、動かしてある ccnavi ディレクトリのほうが
    `cp <ccnavi ディレクトリ> <外>` まで止める、というぶんだけ広い。広い側が deny なので
    食い違う向きは安全だが、表記を揃えるなら足し方を 2 つに分けることになる。
    """
    parts = [re.escape(p) for p in _home_name(project_home).split("/") if p]
    if not parts:
        return ""
    return r"[\\/]".join(parts) + _END


def project_home_glob(project_home: str) -> str:
    """ccnavi ディレクトリの下を、名指しのツールで止める glob。`*/.ccnavi/*` の形。"""
    home = _home_name(project_home)
    return f"*/{home}/*" if home else ""


def _home_name(project_home: str) -> str:
    """ccnavi ディレクトリの名前。

    前後の区切りは落とし、区切りを含む表記はそのまま 1 つの節にする。
    """
    return (project_home or "").replace("\\", "/").strip("/")


def binary_clause(bin_path: str) -> str:
    """実行ファイルの表記を、当てる形に直す。

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
      ので当てない。親が無い浅い表記では `bin/<os>-<arch>/` を親を問わずに当てる。判定に
      渡る表記は絶対パスなので、名指しのツールはそれでも止まる
    - それ以外の名前は実行ファイルそのもの。末尾 2 要素だけに当てる

    既定の置き場（`.ccnavi/`）は ccnavi ディレクトリを守るルールでも止まるが、ccnavi
    ディレクトリを動かしたときや、既定でない表記を指したときはここでしか止まらない。
    """
    if not bin_path:
        return ""
    parts = [p for p in re.split(r"[\\/]", bin_path) if p and p not in (".", "..")]
    if not parts:
        return ""
    if parts[-1] == platformtag.LAUNCHER_NAME:
        sh = r"[\\/]".join(re.escape(p) for p in parts[-2:])
        home = re.escape(parts[-3]) + r"[\\/]" if len(parts) >= 3 else ""
        return rf"(?:{sh}|{home}bin[\\/]{_BUILD_DIR}(?:[\\/][^\x00]*)?)"
    return r"[\\/]".join(re.escape(p) for p in parts[-2:])


# 機械ごとの組み立ての置き場。`bin/` の下に並ぶ。
_BUILD_DIR = r"(?:" + "|".join(platformtag.SYSTEMS) + r")-[a-z0-9_]+"


def guard_shell_regex(
    root: str, bin_path: str = "", project_home: str = "", common_files: tuple[str, ...] = ()
) -> str:
    """この設定で組んだ、シェルから書き込む形。

    実行前の判定（add_rules）と組み込みの既定（builtin）の両方がここから取る。既定の
    側だけモジュールを読んだ時点の空の設定で組むと、ccnavi ディレクトリや実行ファイルを
    動かしたワークスペースでは、ルールファイルが壊れたときだけ動かした先への書き込みが
    止まらなくなる。2 か所で組むと、片方だけが弱い側になる。
    """
    clauses = [project_home_clause(project_home)]
    clauses.extend(common_shell_clause(root, path) for path in common_files)
    return shell_write_regex(bin_path, *clauses)


def common_layer_files(conf: settings.Settings) -> tuple[str, ...]:
    """共通層の 3 本。rules / phases / risk の順。"""
    return (conf.rules, conf.phases, conf.risk)


def common_shell_clause(root: str, path: str) -> str:
    """共通層の 1 本を、シェルの書き込みに当てる形に直す。

    既定の置き場（`.ccnavi/common/`）は _PLACES が持っているが、`--rules` / `--phases` /
    `--risk` のフラグは任意の場所を指せる。そこを名前で拾えないと、共通層を動かした
    ワークスペースでは `echo x > <その場所>` が通る。名指しのツール
    （`common_layer_regex`）は動かした先を追うので、片方だけ外すと、同じファイルが
    Write では止まってシェルでは通る形になる。

    env（`CCNAVI_RULES` など）は使われず、置き場を指せるのは診断のためのフラグだけ
    （ADR-0052）。置き場が完全には固定されていないので、この節も残す。

    ワークスペースルートの下ならその相対、外なら書かれた表記と行き着く先の両方で当てる。
    表記の前には名前の途中でないことを求める。`rules.yml` を直下に置いたワークスペースで、
    `myrules.yml` への書き込みまで止めないため。ルールの regex は後読みを受けない
    （rules._UNSUPPORTED）ので、前の 1 文字も含めて当てる形で書く。行き先の前には必ず 1 文字ある。
    shellread がリダイレクトを `> 行き先` にそろえ、コマンドの語は空白で区切られている。
    """
    if not path:
        return ""
    rel = _inside(root, path) if root else ""
    names = [rel] if rel else sorted({path, os.path.realpath(path)})
    spelled = [_spelled(name) for name in names if name]
    if not spelled:
        return ""
    return r"(?:^|[^\w.-])(?:" + "|".join(spelled) + ")" + _TERM


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


# 記録と状態ディレクトリの既定の表記（`_PLACES` の `logs/` の節と同じ場所）を、名指しのツールに
# 当てる形。当てる先は解決済みの絶対パスなので、末尾で閉じる。
_RECORDS_PLACES = r"[\\/]logs[\\/](?:decisions(?:\.[^\\/]*)?\.jsonl$|state(?:[\\/]|$))"


def records_regex(log_path: str = "", state_dir: str = "") -> str:
    """記録と状態ディレクトリを、名指しのツールに当てる形（設計 11.6、ADR-0089）。

    シェルの書き込みの側（`_PLACES`）と同じ `logs/decisions*.jsonl` と `logs/state/` の表記に
    加えて、設定で動かした置き場（`--log` / `--state`、`CCNAVI_LOG` / `CCNAVI_STATE`）にも
    当てる。
    記録はいま書いている 1 本と、同じディレクトリのローテートした分（`<名前>.<日時><拡張子>`）。
    書かれた表記と行き着く先の両方で当てる。
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
# 書けない。同じ名前のルールと重なることが無いので、当たった id を名指しされた人は
# 組み込みのルールだと分かる。
SHELL_RULE_ID = "builtin-guard-setting-files"

SHELL_MESSAGE = (
    "ガード自身の設定と hook を、シェルからの書き込みで変えようとしています。"
    "Write / Edit でも拒否される場所です。表記を変えても同じ場所なので、変更が要る"
    "理由を伝えて利用者に依頼してください。読むだけなら cat や grep はそのまま通ります。"
)

# 実行ファイルを名指しのツールから守るルールの id。Bash 側とは別に持つ。
# 当てる先が違う（あちらはコマンド文字列、こちらはパス）ので、1 件にまとめると
# どちらの読みで当たったのかが報告から消える。
BINARY_RULE_ID = "builtin-guard-binary"

BINARY_MESSAGE = (
    "ccnavi 自身の実行ファイルです。ここが差し替わると、ルールを 1 行も変えずに"
    "判定そのものを入れ替えられます。作り直しが要るなら、何をなぜ変えたいのかを"
    "伝えて利用者に依頼してください。"
)

COMMON_LAYER_RULE_ID = "builtin-guard-common-layer"

COMMON_LAYER_MESSAGE = (
    "ccnavi の共通層の設定（ルール・フェーズの種類・リスクの配点）です。どのツリーの判定にも"
    "効くので、エージェントが書き換えると自分の判定を緩められます。変更が要るなら、下書きを"
    "検証したうえで何をなぜ変えたいのかを伝えて利用者に依頼してください（/ccnavi-config）。"
    "読むだけなら止まりません。"
)

RECORDS_RULE_ID = "builtin-guard-records"

RECORDS_MESSAGE = (
    "ccnavi の記録と状態ディレクトリ（logs/decisions*.jsonl と logs/state/）です。判定が読み、"
    "「ccnavi が何を判定したか」を後から確かめる元なので、エージェントは書き換えません。"
    "シェルからの書き込みでも拒否される場所です。読むだけなら止まりません。"
)

PROJECT_HOME_RULE_ID = "builtin-guard-project-home"

PROJECT_HOME_MESSAGE = (
    "層の設定の置き場です。ここに入っているルール・フェーズの種類・リスクの配点が"
    "このツリーへの判定を決めるので、エージェントが書き換えると自分の判定を緩め"
    "られます。承認済みチケット・フェーズのマーカー・子のフロー（approved/ の下）も"
    "ここにあり、書くのは人です。変更が要るなら、何をなぜ変えたいのかを伝えて利用者に"
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
    # label は報告に出す表記。root からの相対で、人が探せる形。
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
    # spelled は、リンクを解く前の表記（ワークツリー側の設定だけ持つ）。着手が写した分かを
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
    「守る」側になる。書き損じた 1 語で守りが消えるより、書き損じた 1 語で
    守りが残るほうがよい。戻す動きはファイルに触るが、触る先は組み込みで
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
) -> None:
    """ガード自身を守るルールを、判定に足す。

    ルールファイルの外から足す。ここで守る対象をルールから導かないのと同じ
    理由で、止める側もルールに書かせない。書かせると、消せることになる。

    4 本ある。シェルから書き込む形、名指しのツールで実行ファイルを書く形、
    名指しのツールで ccnavi ディレクトリ（`.ccnavi/`）の下を書く形、名指しのツールで
    共通層の 3 本を書く形。hook の登録（`.claude/settings*.json`）を名指しのツールから
    守るぶんはワークスペースのルールに任せる。そこは `deny` に 1 行書けば済み、書いたことが
    読める場所に残る。実行ファイルと ccnavi ディレクトリは置き場が設定で動くので、
    ルールファイルに表記を固定できない。層の設定は行き先の判定に使うので、その層の
    ルール自身に任せると、書けた時点で緩められる（REQ-MLT-08）。

    共通層の 3 本も同じ理由で組み込みに持つ。既定の置き場が ccnavi ディレクトリの下に
    あることに頼り、動かしたときはルールの 1 行に任せる形にすると、その 1 行は守られる
    ファイルそのものの中にあるので、消した・書き換えたルールファイルのもとでは通る。
    既定の置き場なら ccnavi ディレクトリを守る 1 本とも重なるが、共通層を名乗る
    こちらを先に出す。

    ルールファイルに何が書いてあっても足す。組み込みの名前（rules.RESERVED_ID_PREFIX）は
    ルールファイルに書けないので、同じ id の重なりは起きない（書けるとどうなるかは
    RESERVED_ID_PREFIX の側に書いてある）。
    """
    _insert(
        rule_set,
        {
            "id": SHELL_RULE_ID,
            "match": "Bash",
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
        # しない。止まると、直すための呼び出しごと止まる。ただ黙って外すと、守りが
        # 1 本欠けたことに誰も気づけないので、診断ログに残す。書くのは組み込みの id と
        # 問題の件数だけ（表記には守る先のパスが入る）。root が無ければ CLAUDE_PROJECT_DIR。
        diaglog.get("ccnavi", root or None).warn(
            "組み込みの守りを組み立てられず外した", rule=raw.get("id", ""), problems=len(problems)
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

    ルールファイルと実行ファイルは設定で動くので、解決済みの表記を受け取る。
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

    決めるのは層の種別（`settings.ORIGIN_*`）で、層の名前の表記ではない。層の名前で
    比べると、`projects/common/` の 3 本が共通層と同じ key（`rules` / `phases` /
    `risk`）になり、`_places` の重複の排除でその層の 3 本がバックアップと復元の対象から
    丸ごと落ちる。プロジェクトが名前を 1 つ選ぶだけで守りが外れることになる。

    予約名のプロジェクトは置き場をつける（`rules:projects/self`）。`projects/self/`
    の 3 本を素の `rules:self` にすると、こんどはワークスペース自身の層と
    ぶつかって、先に積んだほうだけが残る。層の名前に予約してある表記は層の名前の側で
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
    ワークツリー側の設定が無ければ対象から落ちるだけで、別の場所を守りに行くことにはならない。
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
    ディレクトリを守りに行かないため。git は起こさないので、呼び出しごとに
    通っても外部プロセスは増えない。

    元リポジトリをつけて列挙する。ワークツリーはワークスペースからもプロジェクトからも
    切れて、中に入っているワークツリー側の設定は元リポジトリが追跡しているものだけになる。
    lib から切ったツリーに `.claude/settings.json` は無いし、lib の層の表記は
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
    利用者がエディタで直している最中の 1 文字が、エージェントが何か 1 つ
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
            # 数十 MB を写すと、判定を待たせるために置いた仕組みが、判定より
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
    人の書きかけが残っているかどうかが変わるので。

    written は、この呼び出しが名指しのツール（REPAIR_TOOLS）で書いた先の解決済みの
    パス。組み込みの既定を使っている間の修復だけは戻さない（_left_as_repair）。

    synced は、ワークツリー側の設定の変更が、着手のときに共通層でプロジェクトの層を
    上書きしたものかを答える（`configsync.is_synced_write`）。そう読めるものは戻さない。
    戻すと着手が写した中身が同じ呼び出しの中で消え、最初のレビューで知らせるマーカーだけが残る。
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
                # 無かったところに現れた。消しはしない。人が置くこともある
                # ファイルで、消すほうが取り返しが付かない。言うだけにする。
                outcomes.append(
                    Outcome(
                        target,
                        ACTION_MISSING,
                        "無かったところに設定ファイルが現れた。"
                        "hook の登録が増えているかもしれないので、中身を人に見てもらってください",
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
    たびに数十 MB を写すわけにはいかないので、写すのはセッションに 1 度。そのあいだに
    作り直されたものはバックアップと食い違うが、作り直しは人が起こす作業なので、食い違いは
    報告に出て人の目に触れる。何も出さずに新しいほうをバックアップし直すと、差し替えと作り直しが
    同じ見た目になる。

    小さいほうも一緒にバックアップする。実行前のバックアップが始まるのは最初のツール呼び出しからで、
    それより前に設定ファイルを消されると、バックアップを持たないまま実行後の監視に入る。
    セッションの開始そのものが、いちばん早く取れる断面になる。

    バックアップするだけで、戻さない。`dry-run` でもバックアップするのは、
    バックアップすることが誰の書きかけも
    消さない側の操作だから。ここを `enable` に限ると、切り替えた最初のセッションが
    戻す先を持たないまま走る。設定は変えた時点から反映したい。何を戻すかは
    実行前と実行後が `setting` を見て決める。

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
    置き場は必ず新しい。件数の上限で切ると、長く人の返事を待っているだけの
    セッションが、他が増えたという理由でバックアップを失う。

    自分の置き場は日付を見ずに残す。始まったばかりで中身が無い、あるいは
    時計がずれている環境で、自分のバックアップを自分で消してしまわないため。

    掃除に失敗してもセッションは始める。バックアップが消せないことは、バックアップが取れない
    ことより軽い。ここで止めると、ディスクの都合で守りが使えなくなる。
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
    """大きい対象をバックアップする。写せたら空文字、駄目なら理由を返す。

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
    見分けられないが、そこを見分けるには毎回全部を読むことになり、実行前の
    判定に設けた期限を設定ファイル 1 つのために使い切ることになる。

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
    # バックアップした時点の時刻に戻す。実体はコピーなので、写した時刻をそのまま置くと
    # 次の突き合わせが毎回食い違ったまま報告されつづける。
    with contextlib.suppress(OSError):
        os.utime(target.path, (mtime, mtime))
    return Outcome(target, ACTION_RESTORED, "セッション開始の時点の実行ファイルに戻した")


def _same_shape(path: str, size: int, mtime: int) -> bool:
    """バックアップした時点の大きさと更新時刻に、現物が一致するか。

    秒未満を落として比べる。バックアップは更新時刻ごと写すが、写した先の
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
    """別の名前へ写してから置き換える。写せたら空文字、駄目なら理由を返す。

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
    """更新時刻ごと写す。写せたら空文字、駄目なら理由を返す。"""
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
        # 戻さなかった回。「次も戻る」と言うと嘘になる。代わりに、人が中身を見る
        # 用件を渡す。ルールが読めない間の書き込みは、どれだけ緩めたかを誰も判定していない。
        lines.append(
            "読めなかった共通層のルールファイルへの修復は戻していません。"
            "直した中身が意図どおりかを、ユーザが確かめてください。"
        )
    if any(outcome.action != ACTION_LEFT for outcome in outcomes):
        lines.append(
            "変更が要るなら、何をなぜ変えたいのかを利用者に伝えて依頼してください。"
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

    base も行き着く先まで解く。path（Target.path）は解いた表記で来るので、base が
    リンクを含むまま（macOS の /var → /private/var など）だと、相対が base の外へ
    `../` で回り込み、git に渡す表記が別の場所を指して戻せなくなる。"""
    try:
        return os.path.relpath(path, os.path.realpath(base))
    except ValueError:
        return path
