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
import os
import re
import shutil
from dataclasses import dataclass
from typing import TextIO

from . import gitstate, rules

# 設定の値。mode と同じ語彙にしてある。覚えるものを増やさないため。
ENABLE = "enable"
DRY_RUN = "dry-run"
DISABLE = "disable"
SETTINGS = (ENABLE, DRY_RUN, DISABLE)

# 控えの置き場。state の下に畳む。セッションごとに分けるのは、控えが
# 「このセッションの直前の断面」でしかないから。別のセッションが取った断面で
# 戻すと、こちらが一度も見ていない内容へ書き換えることになる。
BACKUP_DIR = "selfguard"

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
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

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

_PLACES = (r"\.claude[\\/]((ccnavi|hooks|scripts)[\\/]|settings[\w.-]*\.json)", r"ccnavi-git\.sh")
_COPY_PLACES = (r"\.claude[\\/](ccnavi|hooks|scripts|settings)", r"ccnavi-git\.sh")


def shell_write_regex(bin_path: str = "") -> str:
    """設定ファイルへシェルから書き込む形。実行ファイルの綴りは設定で動くので、
    ここで組み立てる。

    実行ファイルを場所の一覧に足すのは、そこが判定器の実体だから。差し替えられると
    ルールを 1 行も変えずに判定そのものを入れ替えられる。しかも置き場は
    `.gitignore` の中にあることが多く、そうなると実行後の監視からも見えない。
    """
    places = [*_PLACES]
    copy_places = [*_COPY_PLACES]
    clause = binary_clause(bin_path)
    if clause:
        places.append(clause)
        copy_places.append(clause)
    where = "(" + "|".join(places) + ")"
    copy_where = "(" + "|".join(copy_places) + ")"
    return rf"{_WRITE_VERBS}{where}|{_COPY_VERBS}{copy_where}[^ \x00]*($|\x00)"


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


@dataclass
class Outcome:
    """対象 1 つについて、この 1 回で何をしたか。"""

    target: Target
    action: str = ""
    detail: str = ""


def resolve(stderr: TextIO, flag: str, declared: str, name: str) -> str:
    """設定の値を解決する。読めない値は enable に倒す。

    mode の解決が読めない値を enable へ倒すのと同じ向き。倒れた先が
    「守る」側になる。書き損じた 1 語で守りが消えるより、書き損じた 1 語で
    守りが残るほうがよい。戻す動きはファイルに触るが、触る先は組み込みで
    固定された 3 つだけで、しかも戻す先はこちらが取った直前の断面になる。
    """
    value = (flag or declared or "").strip().lower()
    if not value:
        return ENABLE
    if value in SETTINGS:
        return value
    stderr.write(
        f"ccnavi: {name}={value!r} is not a setting; using {ENABLE}. "
        f"Valid values are {', '.join(SETTINGS)}\n"
    )
    return ENABLE


def add_rules(rule_set: rules.RuleSet, bin_path: str = "") -> None:
    """ガード自身を守るルールを、判定に足す。

    ルールファイルの外から足す。この面が守る対象をルールから導かないのと同じ
    理由で、止める側もルールに書かせない。書かせると、消せることになる。

    2 本ある。シェルから書き込む形と、名指しのツールで実行ファイルを書く形。
    設定ファイルを名指しのツールから守るぶんはプロジェクトのルールに任せる。
    そこは `deny` に 1 行書けば済み、書いたことが読める場所に残る。実行ファイルは
    置き場が設定で動くので、ルールファイルに綴りを固定できない。

    同じ id が既にあるなら足さない。プロジェクトが自分で書いているなら、
    書いたとおりに効いているほうがよい。組み込みが黙って重ねると、当たった
    ルールを名指しされた人が、ルールファイルを見ても見つけられなくなる。
    """
    _insert(
        rule_set,
        {
            "id": SHELL_RULE_ID,
            "match": "Bash",
            "regex": shell_write_regex(bin_path),
            "message": SHELL_MESSAGE,
        },
    )
    clause = binary_clause(bin_path)
    if clause:
        _insert(
            rule_set,
            {
                "id": BINARY_RULE_ID,
                "match": "Write|Edit|MultiEdit|NotebookEdit",
                # 当てる先は解決済みの絶対パスなので、末尾で閉じる。
                "regex": clause + "$",
                "message": BINARY_MESSAGE,
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


def targets(root: str, rules_path: str, bin_path: str = "") -> list[Target]:
    """守る対象を組み立てる。

    ルールファイルと実行ファイルは設定で動くので、解決済みの綴りを受け取る。
    空なら、その設定を持たないということなので、対象からも外れる。
    """
    found = [
        Target(key=key, path=os.path.realpath(os.path.join(root, rel)), label=rel)
        for key, rel in _SETTINGS_FILES
    ]
    if rules_path:
        full = os.path.realpath(rules_path)
        found.append(Target(key="rules", path=full, label=_relative(root, full)))
    if bin_path:
        full = os.path.realpath(bin_path)
        found.append(Target(key="bin", path=full, label=_relative(root, full), heavy=True))
    return found


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
    """セッションが始まったとき。大きい対象をここで 1 度だけ控える。

    実行ファイルがこれにあたる。ツール呼び出しのたびに数十 MB を写すわけには
    いかないので、写すのはセッションに 1 度。そのあいだに作り直されたものは
    控えと食い違うが、作り直しは人が起こす作業なので、食い違いは報告に出て
    人の目に触れる。黙って新しいほうを控え直すと、差し替えと作り直しが
    同じ見た目になる。
    """
    if setting == DISABLE or not state_dir:
        return []

    outcomes = []
    for target in found:
        if not target.heavy:
            continue
        if not os.path.exists(target.path):
            outcomes.append(
                Outcome(
                    target,
                    ACTION_MISSING,
                    "実行ファイルが見つからない。CCNAVI_BIN_PATH の綴りを確かめること",
                )
            )
            continue
        if setting != ENABLE:
            outcomes.append(Outcome(target, ACTION_WOULD, "控えを取るはずだった"))
            continue
        failed = _copy(target.path, _backup_path(state_dir, session, target))
        if failed:
            outcomes.append(Outcome(target, ACTION_FAILED, f"控えを取れない: {failed}"))
    return outcomes


def _check_heavy(setting: str, state_dir: str, session: str, target: Target) -> Outcome | None:
    """大きい対象を、大きさと更新時刻で突き合わせる。

    中身は読まない。控えは更新時刻ごと写してあるので、差し替えられれば
    どちらかが必ず動く。同じ大きさで同じ時刻に作った別物までは見分けられないが、
    そこを見分けるには毎回全部を読むことになり、実行前の判定に張った期限を
    設定ファイル 1 つのために使い切ることになる。

    控えが無いときは黙る。セッション開始のイベントに登録していない、あるいは
    実行ファイルを指していない設定がこれで、事件ではない。登録の漏れは
    `--lint` が言う。
    """
    backup = _backup_path(state_dir, session, target)
    if not os.path.exists(backup):
        return None
    if _same_shape(backup, target.path):
        return None
    if setting != ENABLE:
        return Outcome(target, ACTION_WOULD, "控えから戻すはずだった（今回は触っていない）")
    failed = _copy(backup, target.path)
    if failed:
        return Outcome(target, ACTION_FAILED, f"戻せない: {failed}")
    return Outcome(target, ACTION_RESTORED, "セッション開始の時点の実行ファイルに戻した")


def _same_shape(a: str, b: str) -> bool:
    """大きさと更新時刻が同じかどうか。

    秒未満を落として比べる。控えは更新時刻ごと写すが、写した先の
    ファイルシステムがそこまでの精度を持たないことがある。
    """
    try:
        left, right = os.stat(a), os.stat(b)
    except OSError:
        return False
    return left.st_size == right.st_size and int(left.st_mtime) == int(right.st_mtime)


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
    top = gitstate.top_level(root)
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
    top = gitstate.top_level(root)
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


def _backup_path(state_dir: str, session: str, target: Target) -> str:
    safe = _UNSAFE.sub("_", session or "no-session")
    return os.path.join(state_dir, BACKUP_DIR, safe, target.key)


def _read_backup(state_dir: str, session: str, target: Target) -> bytes | None:
    return _read(_backup_path(state_dir, session, target))


def _write_backup(state_dir: str, session: str, target: Target, content: bytes) -> str:
    return _write(_backup_path(state_dir, session, target), content)


def _read(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。

    バイト列で扱う。改行を変換すると、控えから戻したファイルが元と 1 バイト
    違うものになる。Windows と Linux で同じ控えを取るために、ここは解釈しない。
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _write(path: str, content: bytes) -> str:
    """中身をそのまま書く。書けたら空文字、駄目なら理由を返す。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
    except OSError as exc:
        return f"{exc}"
    return ""


def _relative(base: str, path: str) -> str:
    """報告と git に渡すための、base からの相対。
    別のドライブに在るなど、相対にできないものは絶対のまま返す。"""
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path
