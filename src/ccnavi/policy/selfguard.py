"""ccnavi 自身を成り立たせている設定ファイルを、実行前にバックアップし、実行後に戻す。

守る対象の一覧は `selfguard_targets`、シェルからの書き込みを止める組み込みのルールは
`selfguard_shell` に分けてある。どちらも selfguard を読まない。

## 守る対象をルールファイルに書かない

ルールに書いた保護は、ルールを消せば一緒に消える。実行後チェックは保護領域を
ルールファイルの `deny` と `ask` から導くので、ルールファイル自身をそこで守ると、
書き換えられた時点で「何を守るか」の一覧ごと失われる。`deny` を空にされた形も、
破損して組み込みの既定に戻った形も、どちらも保護領域は 0 件になり、実行後チェックは何も
検知しない。保護の根拠が、守られる対象の中に置いてあることが原因になる。

だからこの一式は、ルールファイルの外に、組み込みで持つ（設計 11.6）。

    <root>/.claude/settings.json        hook の登録そのもの
    <root>/.claude/settings.local.json  同上。個人の上書き
    共通レイヤーの 3 本                       <root>/.ccnavi/common/{rules,phases,risks}.yml
    自身のレイヤーの 3 本                     <root>/.ccnavi/config/{rules,phases,risks}.yml
    プロジェクトのレイヤーの 3 本
    projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml

守る場所はレイヤーの数だけ増えるが、守る対象の種類は「設定 3 本」の 1 種のまま。
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
起こしたワークスペースルートの側で、判定が読むレイヤーも元リポジトリに checkout されている版
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

基本はバックアップで、git はバックアップが無いときだけ使う。無いのは 2 通りある。

  1. 実行前に、対象そのものが無かった。バックアップする中身が無いので、まず git から
     戻して、戻ったものをバックアップする。バックアップできなければ、次の実行後に戻す先が無い。
  2. 実行後に、バックアップが読めなかった。バックアップの置き場ごと消されたときがこれ。
     戻す先を失っているので、git のコミット済みの内容を使う。

どちらも「基本の手段が使えないので、劣るほうを使った」と報告に書く。それを
書かないと、戻した先が直前の断面なのかコミット済みの内容なのかを、受け取った側が
見分けられない。ユーザの書きかけが消えているかもしれない、という違いになる。

## バックアップを溜めない

バックアップはセッションごとに分かれる。何もしないと、走らせたセッションの数だけ
残りつづける。小さい設定ファイルは上書きなので増えないが、実行ファイルは
1 セッションにつき 1 本ぶんまるまる残り、そこが積み重なる。

2 つで抑える。

  1. 実体はセッションの側に置かない。中身のハッシュで名前を付けた 1 本を
     `store/` に置き、セッションは参照だけ持つ。同じビルドで何セッション
     走っても実体は 1 本のまま。
  2. 古いバックアップはセッション開始で削除する。生きているセッションは呼び出しの
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
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitstate
from ..infra.modes import DISABLE, DRY_RUN, ENABLE
from . import rules, selfguard_targets

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

# バックアップを残す日数。これより長く触られていないセッションのバックアップは削除する。
# バックアップが役に立つのはそれを取ったセッションの中だけなので、短くてよい。
# 中断して翌日に開き直した形までは拾える幅にしてある。
KEEP_DAYS = 3

# 実体をコピーするときの一時名。コピーの途中で中断されたものを、次のセッションが
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

# 共通レイヤーのルールファイルに付けるバックアップの key（_places）。
COMMON_RULES_KEY = "rules"

# 名指しのツールで書くもの。修復として戻さない判断は、この経路で書いた先にだけ掛ける。
REPAIR_TOOLS = ("Write", "Edit", "NotebookEdit")


@dataclass
class Outcome:
    """対象 1 つについて、この 1 回で何をしたか。"""

    target: selfguard_targets.Target
    action: str = ""
    detail: str = ""


def resolve(
    stderr: TextIO, flag: str, declared: str, name: str, allowed: tuple[str, ...] = SETTINGS
) -> str:
    """設定の値を解決する。読めない値は enable として扱う。

    mode の解決が読めない値を enable として扱うのと同じ向き。行き着く先が
    「守る」側になる。書き損じた 1 語で保護が消えるより、書き損じた 1 語で
    保護が残るほうがよい。戻す動きはファイルを書き換えるが、書き換える先は組み込みで
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


def before(
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[selfguard_targets.Target],
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
            # 言うべき 1 行が見落とされる。記録には残る。
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
    found: list[selfguard_targets.Target],
    written: str,
    synced: Callable[..., bool],
) -> list[Outcome]:
    """実行後。バックアップと比べて、変わっていれば戻す。

    バックアップが読めなければ git のコミット済みの内容を使う。使ったことは
    報告に書く。戻した先が直前の断面なのかコミット済みの内容なのかで、
    ユーザの書きかけが残っているかどうかが変わるので。

    written は、この呼び出しが名指しのツール（REPAIR_TOOLS）で書いた先の解決済みの
    パス。組み込みの既定を使っている間の修復だけは戻さない（_left_as_repair）。

    synced は、ワークツリー側の設定の変更が、着手のときに共通レイヤーをミラーした書き込みかを
    答える（`configsync.is_synced_write`。第 2 引数は変更後の中身で、None は消えたこと）。
    そう読めるものは戻さない。戻すと、着手がミラーした中身が同じ呼び出しの中で消える。
    ワークツリー側の設定に限るのは、ミラーを書くのが親のワークツリーだけだから。
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

        if target.copy and synced(target.spelled or target.path, now):
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
    # 何も起きていない回は除く。ACTION_KEPT はここまでの経路で「調べたが
    # 変わっていなかった」を伝えるための値で、報告に出す用件ではない。
    return [o for o in outcomes if o.action != ACTION_KEPT]


def _left_as_repair(
    target: selfguard_targets.Target, written: str, saved: bytes | None, now: bytes | None
) -> bool:
    """組み込みの既定を使っている間の修復か。そうなら戻さない（REQ-PRE-06）。

    共通レイヤーのルールファイルが読めないと、組み込みの既定は Write / Edit による修復を通す。
    ところが実行前に取るバックアップは破損した中身なので、そのまま戻すと、直した結果が同じ呼び出しの
    中で消える。「Write / Edit で直せ」という案内と実際が食い違う。

    戻さないのは次の 4 つが揃ったときだけ。
      1. 対象が共通レイヤーのルールファイルそのもの。組み込みの既定に戻る原因になるのはこの
         1 本だけで、ワークツリー側の設定や他のレイヤーの設定は読めなくても既定に戻らない
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
    found: list[selfguard_targets.Target],
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

    古いバックアップを削除するのもここ。セッションに 1 度しか来ない場所が他に無く、
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
    """古いバックアップを削除する。

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
    """どのセッションからも参照されなくなった実体を削除する。

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


def _save_heavy(state_dir: str, session: str, target: selfguard_targets.Target) -> str:
    """大きい対象をバックアップする。コピーできたら空文字、駄目なら理由を返す。

    実体は中身のハッシュで名前を付けて `store/` に 1 本だけ置き、セッションの
    側には参照を置く。同じビルドのまま何セッション走っても、コピーは 1 本で済む。

    参照には、バックアップした時点の大きさと更新時刻も書く。照合はこの数字と
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
    # 最後に使った時刻にする。掃除はこれを見て、使われなくなった実体を削除する。
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


def _check_heavy(
    setting: str, state_dir: str, session: str, target: selfguard_targets.Target
) -> Outcome | None:
    """大きい対象を、大きさと更新時刻で照合する。

    中身は読まない。バックアップした時点の大きさと更新時刻を参照に書いてあるので、
    差し替えられればどちらかが必ず動く。同じ大きさで同じ時刻に作った別のものまでは
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
    # 次の照合で毎回一致しないまま報告されつづける。
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
    たびの照合は、参照に書いた大きさと更新時刻だけで済ませる。
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

    途中で中断されたコピーを、中身の揃ったバックアップとして次のセッションに拾わせないため。
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
            "読めなかった共通レイヤーのルールファイルへの修復は戻していません。"
            "直した中身が意図どおりかを、ユーザが確かめてください。"
        )
    if any(outcome.action != ACTION_LEFT for outcome in outcomes):
        lines.append(
            "変更が要るなら、何をなぜ変えたいのかをユーザに伝えて依頼してください。"
            "自分で書き換えると、次の呼び出しで同じように戻ります。"
        )
    return "\n".join(lines)


def _recover_missing(
    setting: str, root: str, target: selfguard_targets.Target, saved: bytes | None
) -> Outcome:
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
    top = selfguard_targets._top(root, target)
    failed = gitstate.restore_committed(
        top, selfguard_targets._relative(top, target.path).replace(os.sep, "/")
    )
    if failed:
        return Outcome(target, ACTION_MISSING, f"対象が無く、git からも戻せない: {failed}")
    return Outcome(target, ACTION_RESTORED_GIT, "対象が無かったので、コミット済みの内容から戻した")


def _fall_back_to_git(
    setting: str, root: str, target: selfguard_targets.Target, now: bytes | None
) -> Outcome:
    """バックアップが無いまま実行後に来た。git に頼る。

    git が「変わっていない」と言うなら何もしない。バックアップが取れなかっただけで
    誰も触っていない、という回がここに来るので、そこで毎回 git restore を
    打つと、何も起きていない呼び出しがファイルを書き換えることになる。
    """
    top = selfguard_targets._top(root, target)
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

    failed = gitstate.restore_committed(
        top, selfguard_targets._relative(top, target.path).replace(os.sep, "/")
    )
    if failed:
        return Outcome(target, ACTION_FAILED, f"バックアップが無く、git からも戻せない: {failed}")
    return Outcome(
        target,
        ACTION_RESTORED_GIT,
        "バックアップが無いのでコミット済みの内容へ戻した。"
        "コミットしていない編集があったなら、それは残っていない",
    )


def _absent_path(state_dir: str, session: str, target: selfguard_targets.Target) -> str:
    """「このファイルは置かれていない」というマーカーの置き場。

    マーカーを持つのは、無いことを毎回 git に確かめに行かないため。設定ファイルを
    置いていないプロジェクトでは、無いことがそのプロジェクトの正常な状態になる。
    そこで呼び出しのたびに外部プロセスを起こすと、何も起きていない作業が
    いちばん重くなる。
    """
    return _backup_path(state_dir, session, target) + ".absent"


def _absent_noted(state_dir: str, session: str, target: selfguard_targets.Target) -> bool:
    return os.path.exists(_absent_path(state_dir, session, target))


def _note_absent(state_dir: str, session: str, target: selfguard_targets.Target) -> None:
    _write(_absent_path(state_dir, session, target), b"")


def _clear_absent(state_dir: str, session: str, target: selfguard_targets.Target) -> None:
    """マーカーを消す。無かったはずのものが現れたら、次からは普通にバックアップする。"""
    with contextlib.suppress(OSError):
        os.remove(_absent_path(state_dir, session, target))


def _safe(session: str) -> str:
    """セッション識別子から作る、置き場の名前。

    そのまま名前になるので、区切り文字が混じった値でファイルを別の場所へ
    書かせない。
    """
    return fsio.safe_name(session or "no-session", limit=None)


def _backup_path(state_dir: str, session: str, target: selfguard_targets.Target) -> str:
    """バックアップの置き場。key はそのままでは名前にしない。

    レイヤーの key には `:` が入る（`rules:self`、`phases:lib`）。Windows でその表記の
    ファイルを開くと、同じ名前のファイルの代替データストリームに書くことになり、
    バックアップが在るのに読めない形になる。名前に使える字へ置き換えてから置く。

    切り詰めない。ワークツリー側の設定の key は末尾にワークツリーの名前の digest を持っていて、
    そこを削ると別のワークツリーのバックアップと同じ名前になる。長さで書けなくなるなら、
    書けなかったことが報告に出るほうがよい。
    """
    name = fsio.safe_name(target.key, limit=None)
    return os.path.join(state_dir, BACKUP_DIR, _safe(session), name)


def _ref_path(state_dir: str, session: str, target: selfguard_targets.Target) -> str:
    """大きい対象の参照の置き場。実体は持たず、どれを指しているかだけを持つ。"""
    return _backup_path(state_dir, session, target) + REF_SUFFIX


def _store_path(state_dir: str, digest: str) -> str:
    """実体の置き場。セッションをまたいで共有するので、セッションの下には分けない。"""
    return os.path.join(state_dir, BACKUP_DIR, STORE_DIR, digest)


def _read_backup(state_dir: str, session: str, target: selfguard_targets.Target) -> bytes | None:
    return _read(_backup_path(state_dir, session, target))


def _write_backup(
    state_dir: str, session: str, target: selfguard_targets.Target, content: bytes
) -> str:
    return _write(_backup_path(state_dir, session, target), content)


def _read(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。バイト列で扱う理由は fsio.read_bytes を見よ。"""
    return fsio.read_bytes(path)


def _write(path: str, content: bytes) -> str:
    """中身をそのまま書く。書けたら空文字、駄目なら理由を返す。"""
    return fsio.write_bytes(path, content)
