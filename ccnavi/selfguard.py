"""ccnavi 自身を成り立たせている設定ファイルを、実行前に控え、実行後に戻す。

## 守る対象をルールファイルに書かない

ルールに書いた守りは、ルールを消せば一緒に消える。実行後の監視は保護領域を
ルールファイルの `deny` と `ask` から導くので、ルールファイル自身をそこで守ると、
書き換えられた瞬間に「何を守るか」の一覧ごと失われる。`deny` を空にされた形も、
壊れて組み込みの既定に落ちた形も、どちらも保護領域は 0 件になり、監視は何も
検知しない。守りの根拠が、守られる対象の中に置いてあることが原因になる。

だからこの 3 つだけは、ルールファイルの外に、組み込みで持つ。

    <root>/.claude/settings.json        hook の登録そのもの
    <root>/.claude/settings.local.json  同上。個人の上書き
    CCNAVI_RULES が指すファイル          判定の中身そのもの

数を増やさない。ここに足したものは、プロジェクトが宣言を書かなくても、
そして宣言を消しても守られる。それは強い扱いなので、ccnavi が動くこと自体が
懸かっているものに限る。それ以外は `deny` に書いて、ルール由来の保護
（`CCNAVI_RESTORE_IF_DENY`）に任せる。切りたいプロジェクトが切れる側に置く。

## 作業ツリーの中の写しも同じ扱い

この 3 つは追跡されているので、`.claude/worktrees/<名前>/` の中にも複製が入る。
写しは今この瞬間には誰にも読まれない。hook の登録を読むのはセッションを起こした
ワークスペースルートの側で、判定のルートも `CLAUDE_PROJECT_DIR` に留まるから、作業ツリーの
中の `settings.json` を書き換えても、その場では何も変わらない。

それでも守るのは、写しが main へ入る道を持っているから。作業ツリーで書き換えて
ブランチを統合すれば、そのまま main の hook の登録とルールになる。実行前の `deny` は
「今の 1 回」を止めるが、統合は別の日の別のセッションが行うので、そこには届かない。
しかも main の側と違って、作業ツリーは `.gitignore` の中にあり、実行後の監視が読む
git の変更一覧にも出てこない。止める側も気づく側も無いまま、時間差で効く道になる。

守る場所は増えるが、守る対象の種類は増えていない。上の 3 つが、それぞれの
作業ツリーにもう 1 つずつ在るというだけ。プロジェクトごとのルールファイルも
同じで、root の下に在れば同じ道を持つ。

実行ファイルだけは写しを持たない。置き場が `.gitignore` の中にあり、統合で
main へ入る道が無い。

## なぜ控えを実行前に取るか

実行後に git へ聞く形だと、戻す先が「コミット済みの内容」になる。利用者が
まだコミットしていない編集を持っていると、それを消す。守るための仕組みが、
守るはずの人の手を消してはいけない。控えなら戻す先が「そのツール呼び出しの
直前」になるので、人の書きかけはそのまま残る。

届く範囲も広い。git が無い場所、リポジトリの外、`.gitignore` の中。
判定器の実体も設定ファイルも、git から見えない場所に置かれることがある。

## git はフォールバック

控えが本筋で、git は控えが無いときだけ使う。無いのは 2 通りある。

  1. 実行前に、対象そのものが無かった。控える中身が無いので、まず git から
     戻して、戻ったものを控える。控えられなければ、次の実行後に戻す先が無い。
  2. 実行後に、控えが読めなかった。控えの置き場ごと消されたときがこれ。
     戻す先を失っているので、git のコミット済みの内容へ落とす。

どちらも「本筋が使えないので、劣るほうへ落ちた」と報告に書く。落ちたことを
黙ると、戻した先が直前の断面なのかコミット済みの内容なのかを、受け取った側が
見分けられない。人の書きかけが消えているかもしれない、という違いになる。

## 控えを溜めない

控えはセッションごとに分かれる。何もしないと、走らせたセッションの数だけ
残りつづける。小さい設定ファイルは上書きなので増えないが、実行ファイルは
1 セッションにつき丸ごと 1 本で、そこが効いてくる。

2 つで抑える。

  1. 実体はセッションの側に置かない。中身のハッシュで名前を付けた 1 本を
     `store/` に置き、セッションは参照だけ持つ。同じビルドで何セッション
     走っても実体は 1 本のまま。
  2. 古い控えはセッション開始で落とす。生きているセッションは呼び出しの
     たびに控えを書き直すので、更新時刻が新しいものは巻き添えにならない。
     件数の上限ではなく日付で切るのはここが理由で、長く黙っているだけの
     生きたセッションを、数が増えたという理由で消したくない。

## 実行後だけでは足りない

hook の登録は設定ファイルの file watcher が拾っていて、書き換えは同じセッションの
うちに効く。`PostToolUse` の登録を消されると、その次の呼び出しから実行後の監視は
動かない。実測で確かめてある。だから実行前にも手を入れて、対象が消えていれば
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
from dataclasses import dataclass
from typing import TextIO

from . import fsio, gitstate, rules, tree
from .modes import DISABLE, DRY_RUN, ENABLE

# 設定の値。mode と同じ 3 語。定義は modes にあり、ここは借りているだけ。
SETTINGS = (ENABLE, DRY_RUN, DISABLE)
# 戻す働きを持たない門の値。dry-run が無い。
#
# 「止めずに報告する」は、止めたあとに何が起きたかを見せられる働き――deny の場所を
# 戻す、中核ファイルを控えから戻す――があって初めて意味を持つ。チケットの承認の経路は
# その形を持たない。通せば承認が済んでしまい、済んだものは報告では戻らない。
# 半分開けた状態を作れないので、書ける値を 2 つに絞る。
GATE_SETTINGS = (ENABLE, DISABLE)

# 控えの置き場。state の下に畳む。セッションごとに分けるのは、控えが
# 「このセッションの直前の断面」でしかないから。別のセッションが取った断面で
# 戻すと、こちらが一度も見ていない内容へ書き換えることになる。
BACKUP_DIR = "selfguard"

# 大きい対象の実体の置き場。セッションの下ではなく控えの直下に置き、中身の
# ハッシュで名前を付ける。断面が「このセッションのもの」であることは参照の側が
# 担うので、実体まで分ける必要がない。同じ中身を何本も持たない。
STORE_DIR = "store"

# セッションの側に置く参照の綴り。実体のハッシュと、控えた時点の大きさと
# 更新時刻が入る。
REF_SUFFIX = ".ref"

# 控えを残す日数。これより長く触られていないセッションの控えは落とす。
# 控えが役に立つのはそれを取ったセッションの中だけなので、短くてよい。
# 中断して翌日に開き直した形までは拾える幅にしてある。
KEEP_DAYS = 3

# 実体を写すときの一時名。写している途中で落ちたものを、次のセッションが
# 中身の揃った控えとして拾わないように、名前を分けてから置き換える。
_PART_SUFFIX = ".part"

# 何が起きたかの名前。報告と記録に出る。
ACTION_KEPT = "kept"  # 変わっていない
ACTION_SAVED = "saved"  # 控えを取った
ACTION_RESTORED = "restored"  # 控えから戻した
ACTION_RESTORED_GIT = "restored-from-git"  # 控えが無く、git から戻した
ACTION_MISSING = "missing"  # 対象が無く、戻す先も無い
ACTION_FAILED = "failed"  # 戻そうとして駄目だった
ACTION_WOULD = "would-restore"  # dry-run。戻す代わりに言うだけ

# 控えのファイル名に使える文字。セッション識別子はそのまま名前になるので、
# 区切り文字が混じった値でファイルを別の場所へ書かせない。

# 守る対象。root からの相対で書く。rules は設定で動くので、ここには無い。
_SETTINGS_FILES = (
    ("settings", os.path.join(".claude", "settings.json")),
    ("settings-local", os.path.join(".claude", "settings.local.json")),
)

# 設定ファイルへシェルから書き込む形。戻す側と対になる、止める側。
#
# 戻せるだけでは足りない。実行後に戻すまでの間、書き換わった設定がそのまま
# 効いている時間がある。hook の登録は同じセッションのうちに効くので、その間に
# 実行後のイベントごと消されると、戻す機会が来ない。だから同じ場所を実行前にも
# 止める。止めるほうが本筋で、戻すほうは止めきれなかったぶんの受け皿になる。
#
# 前半の括弧が書き込む綴りで、後ろに続く場所と組で当たる。場所の名前が出ただけでは
# 止めない。`cat .claude/ccnavi/rules.yml` も `git add <パス>` も、中身を書かない。
# 名前で止める形にすると、いちばんガードを直したいときにいちばん強く効く。
#
#   1. リダイレクトの行き先。`>` `>>` `>|` `&>` はどれも `>` を含み、
#      shellread が `> 行き先` の形に均してから渡してくる。
#   2. 名指ししたところを必ず書き換えるコマンド。`\x00` はコマンドの切れ目に
#      shellread が置く印で、`(^|\x00)` はコマンドの先頭を意味する。
#   3. sed だけは `-i` が付いた形に絞る。`sed -n 1,20p` はただの読み。
#
# 元と行き先がある cp / ln / install は組が違うので後ろに分けてある。見るのは
# 行き先の側だけで、行き先は最後の引数なので、コマンドの終わりに来た形に絞る。
_WRITE_VERBS = (
    r"(>[>|&]* ?[^ \x00]*"
    r"|(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*"
    r"|(^|\x00)sed\b[^\x00]*-i[^\x00]*)"
)
_COPY_VERBS = r"(^|\x00)(cp|ln|install)\b[^\x00]*"

#
# `.ccnavi/` は層の傘（設計 §25.2）。その下には各層の設定 3 本と、配点が呼ぶ
# スクリプトが入る。どちらも判定の中身そのものなので、傘ごと止める。既定の綴りを
# ここに書いておくのは、傘の名前を動かしていないワークスペースが、設定の受け渡しに
# 依らずに守られるようにするため。動かしてある場合は project_home_clause が足す。
_PLACES = (
    r"\.claude[\\/]((ccnavi|hooks|scripts)[\\/]|settings[\w.-]*\.json)",
    r"\.ccnavi[\\/]",
    r"ccnavi-git\.sh",
)
_COPY_PLACES = (
    r"\.claude[\\/](ccnavi|hooks|scripts|settings)",
    r"\.ccnavi[\\/]",
    r"ccnavi-git\.sh",
)


def shell_write_regex(bin_path: str = "", extra_clause: str = "") -> str:
    """設定ファイルへシェルから書き込む形。実行ファイルの綴りは設定で動くので、
    ここで組み立てる。

    実行ファイルを場所の一覧に足すのは、そこが判定器の実体だから。差し替えられると
    ルールを 1 行も変えずに判定そのものを入れ替えられる。しかも置き場は
    `.gitignore` の中にあることが多く、そうなると実行後の監視からも見えない。

    extra_clause は層の傘の綴り（project_home_clause）。既定の名前は _PLACES に
    書いてあるので、ここで足すのは傘を動かしてある場合の綴りになる。層の設定は
    行き先の判定に使うので、書けるとエージェントが自分のルールを緩められる。
    """
    places = [*_PLACES]
    copy_places = [*_COPY_PLACES]
    for clause in (binary_clause(bin_path), extra_clause):
        if clause:
            places.append(clause)
            copy_places.append(clause)
    where = "(" + "|".join(places) + ")"
    copy_where = "(" + "|".join(copy_places) + ")"
    return rf"{_WRITE_VERBS}{where}|{_COPY_VERBS}{copy_where}[^ \x00]*($|\x00)"


def project_home_clause(project_home: str) -> str:
    """層の傘の綴りを、シェルの書き込みに当てる形に直す（設計 §25.6）。

    傘の下は丸ごと守る。層の設定 3 本も、配点が呼ぶスクリプトも、そこに入る。
    既定の名前（`.ccnavi`）は _PLACES が持っているので、ここが返すのは動かして
    ある場合の綴り。区切りはどちらの綴りにも当てる。
    """
    parts = [re.escape(p) for p in _home_name(project_home).split("/") if p]
    if not parts:
        return ""
    return r"[\\/]".join(parts) + r"[\\/]"


def project_home_glob(project_home: str) -> str:
    """層の傘の下を、名指しのツールで止める glob。`*/.ccnavi/*` の形。"""
    home = _home_name(project_home)
    return f"*/{home}/*" if home else ""


def _home_name(project_home: str) -> str:
    """傘の名前。前後の区切りは落とし、区切りを含む綴りはそのまま 1 つの節にする。"""
    return (project_home or "").replace("\\", "/").strip("/")


def binary_clause(bin_path: str) -> str:
    """実行ファイルの綴りを、当てる形に直す。

    末尾の 2 要素だけを使う。絶対で書かれても相対で書かれても同じ形に当たり、
    ファイル名だけに絞ると、同じ名前の無関係なファイルまで拾う。
    区切りはどちらの綴りにも当てる。ルールは 1 回書いてどの機械でも同じ意味で
    なければならない、という globmatch と同じ約束をここでも守る。
    """
    if not bin_path:
        return ""
    parts = [p for p in re.split(r"[\\/]", bin_path) if p and p not in (".", "..")]
    if not parts:
        return ""
    return r"[\\/]".join(re.escape(p) for p in parts[-2:])


SHELL_WRITE_REGEX = shell_write_regex()

# 足すルールの id。プロジェクトが同じ名前で書いていれば、そちらを優先して
# 足さない。組み込みが黙って上書きすると、ルールファイルを読んだ人が
# 「ここに書いてあるとおりに効いている」と読めなくなる。
SHELL_RULE_ID = "builtin-guard-setting-files"

SHELL_MESSAGE = (
    "ガード自身の設定と hook を、シェルからの書き込みで変えようとしています。"
    "Write / Edit でも拒否される場所です。綴りを変えても同じ場所なので、変更が要る"
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

PROJECT_HOME_RULE_ID = "builtin-guard-project-home"

PROJECT_HOME_MESSAGE = (
    "層の設定の置き場です。ここに入っているルール・フェーズの種類・リスクの配点が"
    "このツリーへの判定を決めるので、エージェントが書き換えると自分の判定を緩め"
    "られます。変更が要るなら、何をなぜ変えたいのかを伝えて利用者に依頼してください。"
    "読むだけなら止まりません。"
)


@dataclass
class Target:
    """守る対象 1 つ。"""

    # key は控えのファイル名。対象ごとに固定してある。パスから作ると、
    # 設定でルールファイルの場所を変えただけで控えが別名になり、
    # 直前の断面を見失う。
    key: str = ""
    # path は行き着く先まで解いた絶対パス。
    path: str = ""
    # label は報告に出す綴り。root からの相対で、人が探せる形。
    label: str = ""
    # heavy は、中身を毎回読むには大きすぎる対象。実行ファイルがこれで、
    # PyInstaller が作るものは数十 MB になる。呼び出しのたびに読むと、
    # 判定に張った期限に効く。控えはセッション開始で 1 度だけ取り、
    # 突き合わせは大きさと更新時刻で行う。
    heavy: bool = False
    # top は、この対象を git から戻すときに渡す作業ツリーのルート。作業ツリーの
    # 中の写しだけが持つ。空なら root の側から戻す。main の git に
    # `.claude/worktrees/...` を聞いても、そこは `.gitignore` の中なので
    # 何も持っていない。写しを持っているのは、その作業ツリー自身の git。
    top: str = ""


@dataclass
class Outcome:
    """対象 1 つについて、この 1 回で何をしたか。"""

    target: Target
    action: str = ""
    detail: str = ""


def resolve(
    stderr: TextIO, flag: str, declared: str, name: str, allowed: tuple[str, ...] = SETTINGS
) -> str:
    """設定の値を解決する。読めない値は enable に倒す。

    mode の解決が読めない値を enable へ倒すのと同じ向き。倒れた先が
    「守る」側になる。書き損じた 1 語で守りが消えるより、書き損じた 1 語で
    守りが残るほうがよい。戻す動きはファイルに触るが、触る先は組み込みで
    固定された 3 つだけで、しかも戻す先はこちらが取った直前の断面になる。

    `allowed` を絞ると、そこに無い語も「読めない値」として扱う。dry-run を
    持たない門（GATE_SETTINGS）に dry-run と書かれた設定が、止めているのに
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


def add_rules(rule_set: rules.RuleSet, bin_path: str = "", project_home: str = "") -> None:
    """ガード自身を守るルールを、判定に足す。

    ルールファイルの外から足す。この面が守る対象をルールから導かないのと同じ
    理由で、止める側もルールに書かせない。書かせると、消せることになる。

    3 本ある。シェルから書き込む形、名指しのツールで実行ファイルを書く形、
    名指しのツールで層の傘（`.ccnavi/`）の下を書く形。ワークスペースの設定
    ファイルを名指しのツールから守るぶんはワークスペースのルールに任せる。
    そこは `deny` に 1 行書けば済み、書いたことが読める場所に残る。実行ファイルと
    層の傘は置き場が設定で動くので、ルールファイルに綴りを固定できない。
    層の設定は行き先の判定に使うので、その層のルール自身に任せると、書けた瞬間に
    緩められる（REQ-MLT-08）。

    同じ id が既にあるなら足さない。プロジェクトが自分で書いているなら、
    書いたとおりに効いているほうがよい。組み込みが黙って重ねると、当たった
    ルールを名指しされた人が、ルールファイルを見ても見つけられなくなる。
    """
    _insert(
        rule_set,
        {
            "id": SHELL_RULE_ID,
            "match": "Bash",
            "regex": shell_write_regex(bin_path, project_home_clause(project_home)),
            "message": SHELL_MESSAGE,
        },
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
        )
    home_glob = project_home_glob(project_home)
    if home_glob:
        _insert(
            rule_set,
            {
                "id": PROJECT_HOME_RULE_ID,
                "match": "Write|Edit|NotebookEdit",
                # 傘の下は丸ごと。層の設定 3 本だけを名指しすると、配点が呼ぶ
                # スクリプトが外れる。当てる先は解決済みの絶対パスなので、
                # どの層の傘（ワークスペース、プロジェクト、作業ツリー）にも同じ 1 本が当たる。
                "glob": home_glob,
                "message": PROJECT_HOME_MESSAGE,
            },
        )


def _insert(rule_set: rules.RuleSet, raw: dict) -> None:
    if any(rule.id == raw["id"] for rule in rule_set.deny):
        return
    built, problems = rules.parse({"version": rules.VERSION, "deny": [raw]})
    if problems or not built.deny:
        # 組み立てられないのは、このファイルの書き損じ。判定を止める理由には
        # しない。止まると、直すための呼び出しごと止まる。
        return
    rule_set.deny.insert(0, built.deny[0])


def targets(
    root: str,
    rules_path: str,
    bin_path: str = "",
    project_rules: list[tuple[str, str]] = (),
) -> list[Target]:
    """守る対象を組み立てる。

    ルールファイルと実行ファイルは設定で動くので、解決済みの綴りを受け取る。
    空なら、その設定を持たないということなので、対象からも外れる。
    project_rules は (プロジェクトの名前, そのルールファイル) の並び（REQ-MLT-08）。

    渡ってくるのは今のところプロジェクトの層の rules だけ（ruleload.layer_files）。
    自身の層と phases / risk、プロジェクトから切った作業ツリーの写しを足すのは
    設計 §25.6 の回で、そこで引数を層ごとの 3 本（層, kind, パス）に広げる。
    控えの key と写しの並べ方が一緒に変わるので、置き場の変更とは別の回にしてある。
    """
    found = [
        Target(key=key, path=os.path.realpath(os.path.join(root, rel)), label=rel)
        for key, rel in _SETTINGS_FILES
    ]
    if rules_path:
        full = os.path.realpath(rules_path)
        found.append(Target(key="rules", path=full, label=_relative(root, full)))
    for name, path in project_rules:
        full = os.path.realpath(path)
        found.append(Target(key=f"rules:{name}", path=full, label=_relative(root, full)))
    if bin_path:
        full = os.path.realpath(bin_path)
        found.append(Target(key="bin", path=full, label=_relative(root, full), heavy=True))
    found.extend(_worktree_copies(root, rules_path, project_rules))
    return found


def _worktree_copies(
    root: str, rules_path: str, project_rules: list[tuple[str, str]] = ()
) -> list[Target]:
    """作業ツリーの中にある、同じ設定ファイルの写し。

    なぜ守るかは冒頭の「作業ツリーの中の写しも同じ扱い」に書いた。ここでは
    対象の組み立て方だけ。

    作業ツリーの一覧は `tree.worktrees` から取る。`.claude/worktrees/` の下に
    在るだけでは作業ツリーと呼ばず、`.git` ファイルと main の登録の相互参照が
    両向きに揃ったものだけを数える。参照実装の写しのような、ただの
    ディレクトリを守りに行かないため。git は起こさないので、呼び出しごとに
    通っても外部プロセスは増えない。

    ルールファイルの置き場は設定で動く。root の外を指しているなら、作業ツリーの
    中に対応する写しは無いので、そこは対象から落ちる。プロジェクトごとの
    ルールファイルも同じ扱いで、root の下に在るぶんだけ写しを守る。
    """
    places = [*_SETTINGS_FILES]
    inside = _inside(root, rules_path)
    if inside:
        places.append(("rules", inside))
    for name, path in project_rules:
        rel = _inside(root, path)
        if rel:
            places.append((f"rules:{name}", rel))

    copies = []
    for work in tree.worktrees(root):
        for key, rel in places:
            full = os.path.realpath(os.path.join(work.root, rel))
            copies.append(
                Target(
                    key=_copy_key(key, work.name),
                    path=full,
                    label=_relative(root, full),
                    top=work.root,
                )
            )
    return copies


def _copy_key(key: str, name: str) -> str:
    """写しの控えの名前。

    key はそのままファイル名になるので、作業ツリーの名前を素で混ぜると、
    区切り文字の入った名前で控えが別の場所へ書かれる。潰した綴りだけにすると、
    今度は `a/b` と `a_b` が同じ名前に落ちて、別の作業ツリーの控えを互いに
    書き戻すことになる。中身が入れ替わるので、取り違えは実害になる。
    潰した綴りに元の名前の digest を添えて、読めることと衝突しないことの
    両方を取る。
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"{key}.{fsio.safe_name(name, 40)}-{digest}"


def _inside(root: str, path: str) -> str:
    """root の下に在るなら root からの相対、外に在るなら空文字。"""
    if not path:
        return ""
    rel = _relative(os.path.realpath(root), os.path.realpath(path))
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return ""
    return rel


def _top(root: str, target: Target) -> str:
    """この対象を git から戻すときに渡す、作業ツリーのルート。"""
    return target.top or gitstate.top_level(root)


def before(
    stderr: TextIO,
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
) -> list[Outcome]:
    """実行前。対象の断面を控える。消されていれば戻してから控える。

    ここでは内容の照合をしない。控えと違うことを理由に実行前に書き戻すと、
    利用者がエディタで直している最中の 1 文字が、エージェントが何か 1 つ
    ツールを呼んだだけで消える。設定ファイルを直しているときこそ、
    エージェントは並行して動いている。

    見るのは「在るか」だけ。ただし「無い」には 2 通りある。

      * 前は在ったのに今は無い。消された。控えから戻す。
      * 最初から無い。`settings.local.json` を置いていないプロジェクトが
        これで、普通の状態になる。ここで毎回 git を叩くと、何も起きていない
        呼び出しが毎回外部プロセスを起こす。無いことを 1 度控えて、以後は黙る。

    2 つを分けるのが控えの有無。控えが在るのに対象が無いなら、控えを取った
    あとに消えたということになる。
    """
    if setting == DISABLE or not state_dir:
        return []

    outcomes = []
    for target in found:
        if target.heavy:
            # 大きい対象はセッション開始で 1 度だけ控える。呼び出しのたびに
            # 数十 MB を写すと、判定を待たせるために置いた仕組みが、判定より
            # 重くなる。
            continue
        content = _read(target.path)
        if content is not None:
            _clear_absent(state_dir, session, target)
            failed = _write_backup(state_dir, session, target, content)
            if failed:
                outcomes.append(Outcome(target, ACTION_FAILED, f"控えを書けない: {failed}"))
            # 控えを取れた回は黙る。毎回の呼び出しで 3 行増えると、本当に
            # 言うべき 1 行がその中に埋もれる。記録には残る。
            continue

        if _absent_noted(state_dir, session, target):
            # 最初から無いと分かっている。何も言わない。
            continue

        saved = _read_backup(state_dir, session, target)
        outcome = _recover_missing(setting, root, target, saved)
        content = _read(target.path)
        if content is None:
            # 戻せなかった。控えも git も持っていないなら、そもそも
            # 置かれていないファイルなので、印を残して次から黙る。
            if saved is None:
                _note_absent(state_dir, session, target)
                continue
            outcomes.append(outcome)
            continue
        outcomes.append(outcome)
        _write_backup(state_dir, session, target, content)
    return outcomes


def after(
    stderr: TextIO,
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
) -> list[Outcome]:
    """実行後。控えと突き合わせて、変わっていれば戻す。

    控えが読めなければ git のコミット済みの内容へ落ちる。落ちたことは
    報告に書く。戻した先が直前の断面なのかコミット済みの内容なのかで、
    人の書きかけが残っているかどうかが変わるので。
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

        if saved is None:
            if now is None:
                # 対象も控えも無い。置いていないファイルなので何も言わない。
                # 実行前がここに印を残しているが、印が読めない場合でも
                # 「無いものが無いまま」を事件として扱わない。
                continue
            if _absent_noted(state_dir, session, target):
                # 無かったところに現れた。消しはしない。人が置くこともある
                # ファイルで、消すほうが取り返しが付かない。言うだけにする。
                outcomes.append(
                    Outcome(
                        target,
                        ACTION_MISSING,
                        "無かったところに設定ファイルが現れた。"
                        "hook の登録が増えているかもしれないので、中身を人が見ること",
                    )
                )
                continue
            # 控えが無いまま中身が在る。実行前に控えを取れなかった回。
            outcomes.append(_fall_back_to_git(setting, root, target, now))
            continue

        if setting != ENABLE:
            outcomes.append(
                Outcome(target, ACTION_WOULD, "控えから戻すはずだった（今回は触っていない）")
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
    # 変わっていなかった」を運ぶための値で、報告に出す用件ではない。
    return [o for o in outcomes if o.action != ACTION_KEPT]


def at_start(
    setting: str,
    state_dir: str,
    session: str,
    root: str,
    found: list[Target],
) -> list[Outcome]:
    """セッションが始まったとき。対象をすべて控える。

    大きい対象を控えるのはここだけ。実行ファイルがこれにあたり、ツール呼び出しの
    たびに数十 MB を写すわけにはいかないので、写すのはセッションに 1 度。そのあいだに
    作り直されたものは控えと食い違うが、作り直しは人が起こす作業なので、食い違いは
    報告に出て人の目に触れる。黙って新しいほうを控え直すと、差し替えと作り直しが
    同じ見た目になる。

    小さいほうも一緒に控える。実行前の控えが始まるのは最初のツール呼び出しからで、
    それより前に設定ファイルを消されると、控えを持たないまま実行後の監視に入る。
    セッションの開始そのものが、いちばん早く取れる断面になる。

    控えるだけで、戻さない。`dry-run` でも控えるのは、控えることが誰の書きかけも
    消さない側の操作だから。ここを `enable` に限ると、切り替えた最初のセッションが
    戻す先を持たないまま走る。設定を変えた瞬間から効いてほしい。何を戻すかは
    実行前と実行後が `setting` を見て決める。

    古い控えを落とすのもここ。セッションに 1 度しか来ない場所が他に無く、
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
                        "実行ファイルが見つからない。CCNAVI_BIN_PATH の綴りを確かめること",
                    )
                )
                continue
            failed = _save_heavy(state_dir, session, target)
            if failed:
                outcomes.append(Outcome(target, ACTION_FAILED, f"控えを取れない: {failed}"))
            continue

        content = _read(target.path)
        if content is None:
            # 最初から無い。`settings.local.json` を置いていない形がこれで、
            # 事件ではない。無いことの印は実行前の側が残す。開始の時点では
            # まだ「消された」と「置いていない」を見分ける手がかりが無い。
            continue
        _clear_absent(state_dir, session, target)
        failed = _write_backup(state_dir, session, target, content)
        if failed:
            outcomes.append(Outcome(target, ACTION_FAILED, f"控えを書けない: {failed}"))
    return outcomes


def sweep(state_dir: str, session: str, keep_days: int = KEEP_DAYS) -> None:
    """古い控えを落とす。

    セッション開始で 1 度だけ呼ぶ。控えが役に立つのはそれを取ったセッションの
    中だけなので、しばらく触られていないものは持っていても戻す先にならない。

    生きているセッションを巻き添えにしないために、見るのは更新時刻にする。
    実行前の控えは呼び出しのたびに書き直されるので、動いているセッションの
    置き場は必ず新しい。件数の上限で切ると、長く人の返事を待っているだけの
    セッションが、他が増えたという理由で控えを失う。

    自分の置き場は日付を見ずに残す。始まったばかりで中身が無い、あるいは
    時計がずれている環境で、自分の控えを自分で消してしまわないため。

    掃除に失敗してもセッションは始める。控えが消せないことは、控えが取れない
    ことより軽い。ここで止めると、ディスクの都合で守りが働かなくなる。
    """
    if not state_dir:
        return
    root = os.path.join(state_dir, BACKUP_DIR)
    try:
        names = os.listdir(root)
    except OSError:
        return

    cutoff = time.time() - keep_days * 86400
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
    """大きい対象を控える。写せたら空文字、駄目なら理由を返す。

    実体は中身のハッシュで名前を付けて `store/` に 1 本だけ置き、セッションの
    側には参照を置く。同じビルドのまま何セッション走っても、写しは 1 本で済む。

    参照には、控えた時点の大きさと更新時刻も書く。突き合わせはこの数字と
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
    # セッションごとに実体を持っていたころの控え。参照に置き換わったので要らない。
    with contextlib.suppress(OSError):
        os.remove(_backup_path(state_dir, session, target))
    return ""


def _check_heavy(setting: str, state_dir: str, session: str, target: Target) -> Outcome | None:
    """大きい対象を、大きさと更新時刻で突き合わせる。

    中身は読まない。控えた時点の大きさと更新時刻を参照に書いてあるので、
    差し替えられればどちらかが必ず動く。同じ大きさで同じ時刻に作った別物までは
    見分けられないが、そこを見分けるには毎回全部を読むことになり、実行前の
    判定に張った期限を設定ファイル 1 つのために使い切ることになる。

    控えが無いときは黙る。セッション開始のイベントに登録していない、あるいは
    実行ファイルを指していない設定がこれで、事件ではない。登録の漏れは
    `--lint` が言う。
    """
    ref = _parse_ref(_read(_ref_path(state_dir, session, target)))
    if ref is None:
        return None
    digest, size, mtime = ref
    if _same_shape(target.path, size, mtime):
        return None
    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "控えから戻すはずだった（今回は触っていない）")

    entry = _store_path(state_dir, digest)
    if not os.path.exists(entry):
        return Outcome(
            target,
            ACTION_FAILED,
            "控えの実体が見つからないので戻せない。ccnavi を作り直して、セッションを開き直すこと",
        )
    failed = _place(entry, target.path)
    if failed:
        return Outcome(target, ACTION_FAILED, f"戻せない: {failed}")
    # 控えた時点の時刻に戻す。実体は写しなので、写した時刻をそのまま置くと
    # 次の突き合わせが毎回ずれたまま鳴りつづける。
    with contextlib.suppress(OSError):
        os.utime(target.path, (mtime, mtime))
    return Outcome(target, ACTION_RESTORED, "セッション開始の時点の実行ファイルに戻した")


def _same_shape(path: str, size: int, mtime: int) -> bool:
    """控えた時点の大きさと更新時刻に、現物が一致するか。

    秒未満を落として比べる。控えは更新時刻ごと写すが、写した先の
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
    """参照を読み解く。読めない形は「控えが無い」として扱う。"""
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

    途中で落ちた写しを、中身の揃った控えとして次のセッションに拾わせないため。
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
        "[ccnavi] ccnavi 自身の設定ファイルに手が入りました。"
        "ここはルールファイルの外で守られている場所で、判定と hook の登録が"
        "そのまま懸かっています。",
    ]
    for outcome in outcomes:
        lines.append(f"  {outcome.target.label}: {outcome.action} — {outcome.detail}")
    if any(outcome.target.top for outcome in outcomes):
        # 作業ツリーの中の写しが混じっている。今この瞬間の判定は変わらないので、
        # 「何も起きていないのに戻された」と読まれる。時間差で効く道であることを
        # 言わないと、次に同じ手が繰り返される。
        lines.append(
            "作業ツリーの中の写しは、その場では誰も読みませんが、統合すれば main の "
            "hook の登録とルールになります。"
        )
    lines.append(
        "変更が要るなら、何をなぜ変えたいのかを利用者に伝えて依頼してください。"
        "自分で書き換えると、次の呼び出しで同じように戻ります。"
    )
    return "\n".join(lines)


def _recover_missing(setting: str, root: str, target: Target, saved: bytes | None) -> Outcome:
    """実行前に対象が無かった。控えがあれば控えから、無ければ git から戻す。

    控えを先に見る。控えは「このセッションで最後に見た内容」で、git が持つのは
    「最後にコミットされた内容」。消えたものを戻すなら、近いほうから戻す。
    """
    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "対象が無い。戻すはずだった")
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
    """控えが無いまま実行後に来た。git に頼る。

    git が「変わっていない」と言うなら何もしない。控えが取れなかっただけで
    誰も触っていない、という回がここに来るので、そこで毎回 git restore を
    打つと、何も起きていない呼び出しがファイルに触ることになる。
    """
    top = _top(root, target)
    changes, unreadable = gitstate.read(top)
    if unreadable:
        if now is None:
            return Outcome(
                target, ACTION_MISSING, f"対象も控えも無く、git も読めない: {unreadable}"
            )
        return Outcome(target, ACTION_FAILED, f"控えが無く、git も読めない: {unreadable}")

    dirty = [c for c in changes if os.path.realpath(c.full) == target.path]
    if not dirty and now is not None:
        return Outcome(target, ACTION_KEPT, "控えは無いが、コミット済みの内容と同じ")

    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "控えが無い。git から戻すはずだった")

    failed = gitstate.restore_committed(top, _relative(top, target.path).replace(os.sep, "/"))
    if failed:
        return Outcome(target, ACTION_FAILED, f"控えが無く、git からも戻せない: {failed}")
    return Outcome(
        target,
        ACTION_RESTORED_GIT,
        "控えが無いのでコミット済みの内容へ戻した。"
        "コミットしていない編集があったなら、それは残っていない",
    )


def _absent_path(state_dir: str, session: str, target: Target) -> str:
    """「このファイルは置かれていない」という印の置き場。

    印を持つのは、無いことを毎回 git に確かめに行かないため。設定ファイルを
    置いていないプロジェクトでは、無いことがそのプロジェクトの正常な姿になる。
    そこで呼び出しのたびに外部プロセスを起こすと、何も起きていない作業が
    いちばん重くなる。
    """
    return _backup_path(state_dir, session, target) + ".absent"


def _absent_noted(state_dir: str, session: str, target: Target) -> bool:
    return os.path.exists(_absent_path(state_dir, session, target))


def _note_absent(state_dir: str, session: str, target: Target) -> None:
    _write(_absent_path(state_dir, session, target), b"")


def _clear_absent(state_dir: str, session: str, target: Target) -> None:
    """印を消す。無かったはずのものが現れたら、次からは普通に控える。"""
    with contextlib.suppress(OSError):
        os.remove(_absent_path(state_dir, session, target))


def _safe(session: str) -> str:
    """セッション識別子から作る、置き場の名前。

    そのまま名前になるので、区切り文字が混じった値でファイルを別の場所へ
    書かせない。
    """
    return fsio.safe_name(session or "no-session", limit=None)


def _backup_path(state_dir: str, session: str, target: Target) -> str:
    return os.path.join(state_dir, BACKUP_DIR, _safe(session), target.key)


def _ref_path(state_dir: str, session: str, target: Target) -> str:
    """大きい対象の参照の置き場。実体は持たず、どれを指しているかだけを持つ。"""
    return _backup_path(state_dir, session, target) + REF_SUFFIX


def _store_path(state_dir: str, digest: str) -> str:
    """実体の置き場。セッションをまたいで共有するので、下には畳まない。"""
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
    別のドライブに在るなど、相対にできないものは絶対のまま返す。"""
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path
