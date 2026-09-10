"""ccnavi の設定を解決する。

設定は環境変数で運ぶ。プロジェクトはそれを Claude Code の設定ファイルの env
ブロックに書く。エージェント側の設定スキーマが独自キーを拒むため、そこが唯一
開いている場所になる。

値はプロセスの環境から読み、設定ファイルからは読まない。この違いが効く。
hook が受け取る環境はセッション開始時に固定されるので、あとから設定ファイルを
直してもセッションを開き直すまで届かない。その古さは不便だが、同時に、
エージェントが自分を見張るものを緩めるのを防いでいる唯一の仕組みでもある。
設定ファイルは作業ツリーの中にあってエージェントが書けるので、そこから読んだ
値は次のツール呼び出しから効いてしまう。

例外は ccnavi 自身を開発しているときだけ。own_source_tree を参照。
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field

# ccnavi が読む環境変数。
MODE_ENV = "CCNAVI_MODE"
RULES_ENV = "CCNAVI_RULES"
LOG_ENV = "CCNAVI_LOG"
# STATE_ENV はセッションごとの控えの置き場。
STATE_ENV = "CCNAVI_STATE"
# 戻す働きは 2 つあり、守る対象の決まり方が違うので環境変数も分けてある。
#
# RESTORE_IF_DENY_ENV は、ルールが `deny` と宣言した場所を戻す。対象は
# ルールファイル次第で動くので、プロジェクトが書いたぶんだけ広がる。
# GUARD_CORE_FILES_ENV は、ccnavi 自身を成り立たせている設定ファイルを
# 戻す。対象は組み込みで固定されていて、ルールファイルには書かない。
#
# 分けた理由は selfguard.py の冒頭にある。片方だけを切れることが要る。
RESTORE_IF_DENY_ENV = "CCNAVI_RESTORE_IF_DENY"
GUARD_CORE_FILES_ENV = "CCNAVI_GUARD_CORE_FILES"
# GUARD_CLI_ENV は、人の判断の経路を守るか。enable（既定）なら、シェルから ccnavi の
# 実行ファイルに `--approve` `--reviewed` `ticket` `review` を付けた呼び出しを止め、
# `--approve` と `--reviewed` は標準入力が端末でなければ拒む。テストは disable にする。
GUARD_CLI_ENV = "CCNAVI_GUARD_CLI"
# BIN_ENV は ccnavi 自身の実行ファイル。判定器の実体なので、書き換えられると
# ルールを 1 行も変えずに判定を差し替えられる。既定は持たない。置き場は
# プロジェクトごとに違ううえ、間違った既定はそこに在る別のファイルを
# 守ることになる。hook の登録に書いた綴りをそのまま渡してもらう。
BIN_ENV = "CCNAVI_BIN_PATH"
# BIN_SUFFIXES は、書かれた綴りに無いときだけ継ぎ足して探す拡張子。
# PyInstaller は Windows でだけ `.exe` を付ける。build.py の側と対になる。
BIN_SUFFIXES = (".exe",)
# チケットによる範囲の制御が使う 2 つ。TICKETS_ENV は提案の置き場で、各作業ツリーの
# ルートからの相対。APPROVED_ENV は承認済みの写しの置き場で、ワークスペースルートからの相対。
# 判定が読むのは写しだけで、提案のほうは承認の画面と状態の同期しか読まない。
TICKETS_ENV = "CCNAVI_TICKETS"
APPROVED_ENV = "CCNAVI_APPROVED"
# PHASES_ENV はフェーズの種類の定義。ワークスペースルートからの相対。無ければ番号だけの挙動。
PHASES_ENV = "CCNAVI_PHASES"
# RISK_ENV は実績で測るリスクの配点。ワークスペースルートからの相対。無ければ組み込みの配点。
RISK_ENV = "CCNAVI_RISK"
# PROJECTS_ENV はプロジェクトの置き場（設計 §25）。ワークスペースルートからの相対。直下で `.git` を
# 持つディレクトリがプロジェクトになる。空文字にするとプロジェクトを数えない。
# PROJECT_RULES_ENV はプロジェクトごとのルールファイル。各 git プロジェクトルートからの相対。
PROJECTS_ENV = "CCNAVI_PROJECTS"
PROJECT_RULES_ENV = "CCNAVI_PROJECT_RULES"
# 以前の形（チケット 1 本と台帳 jsonl）の環境変数。もう効かない。指定されていたら
# --lint が言う。黙って無視すると、書いた人は効いていると思い続ける。
RETIRED_ENVS = ("CCNAVI_TICKET", "CCNAVI_LEDGER")

# own_project は ccnavi 自身のソースツリーを見分ける印。own_source_tree を参照。
OWN_PROJECT = "ccnavi"

# LOCAL_FILE は ccnavi 自身を開発しているときだけ読む上書き設定。
# コミットしないので、他のプロジェクトには存在せず、
# 直しても影響が及ぶのは道具を試している本人だけになる。
LOCAL_FILE = "ccnavi.settings.local.json"

# 既定の置き場。ワークスペースルートからの相対。
DEFAULT_LOG = os.path.join(".claude", "ccnavi", "log.jsonl")
DEFAULT_RULES = os.path.join(".claude", "ccnavi", "rules.yml")
# 控えはセッションごとの一時的な状態なので、記録とは分けて畳んでおく。
# 配る対象ではないし、消えても次の起動で取り直せる。
DEFAULT_STATE = os.path.join(".claude", "ccnavi", "state")
# 提案は各作業ツリーの `wip/tickets/` に置く。人が読み、人が承認するものなので、
# ガードの設定を畳んである場所ではなく、目に入る場所に出しておく。
# 区切りは "/" で持つ。作業ツリーのルートに継ぎ足すときに os の区切りへ直す。
DEFAULT_TICKETS = "wip/tickets"
# 写しは設定と同じ場所。そこはルールが Write / Edit を止め、組み込みの既定が
# シェル経由の書き込みを止めている。写しのために別の保護を足さずに済む。
DEFAULT_APPROVED = os.path.join(".claude", "ccnavi", "tickets")
# フェーズの種類は人が持つ設定なので、写しと同じ保護の内側に置く。
DEFAULT_PHASES = os.path.join(".claude", "ccnavi", "phases.yml")
# リスクの配点も人が持つ設定。エージェントが配点を書けると、自分のリスクを自分で決められる。
DEFAULT_RISK = os.path.join(".claude", "ccnavi", "risk.yml")
# プロジェクトの置き場。ワークスペースの直下に固定するのは、列挙が速いことと、
# 何がプロジェクトかで迷わないため。ワークスペースの `.gitignore` に入れる
# （プロジェクトは自分の git を持つ）。
DEFAULT_PROJECTS = "projects"
# プロジェクトのルールはプロジェクトの git で育てる。`.claude/` の下には置かない。
# プロジェクトに `.claude/` があると Claude Code がそこのスキルを読み、`--lint` が
# 迷い子として拾う。
DEFAULT_PROJECT_RULES = "config/rules.yml"


@dataclass
class Settings:
    """解決済みの設定。パスはすべて絶対にしてあるので、
    これ以降の処理が作業ディレクトリに依存しない。"""

    mode: str = ""
    # mode_declared_in_file は設定ファイルが求めたモード、
    # mode_from_environment はプロセスが渡されたモード。
    # 2 つを分けて持つことで、呼び手が「作業ツリーの中に書かれた値」と
    # 「セッションを起動した人が渡した値」を見分けられる。
    mode_declared_in_file: str = ""
    mode_from_environment: str = ""

    # live_files は上書き設定を読んだことを示す。
    # ccnavi 自身のソースツリーでしか立たない。
    live_files: bool = False

    log: str = ""
    rules: str = ""
    state: str = ""
    # 戻す働きの 2 つ。どちらも mode と同じ enable / dry-run / disable を取る。
    #
    # restore_if_deny は、ルールが `deny` と宣言した場所が副作用で変わったときに
    # git から戻すかどうか。対象がルールファイル次第で動くので、書き損じが
    # そのまま「頼んでいないファイル操作」になりうる。その懸念は残るが、
    # 戻さない既定は「宣言したのに守られない」を既定にすることでもあるので、
    # 既定は enable にしてある。切りたいプロジェクトは明示して切る。
    #
    # guard_core_files は、ccnavi 自身の設定ファイルを控えから戻すかどうか。
    # 対象は組み込みで固定なので、広がりようがない。
    restore_if_deny: str = ""
    guard_core_files: str = ""
    # guard_cli は人の判断の経路（承認・レビュー済みの受け入れ・状態の移動）を
    # エージェントの手から守るか。enable / disable。
    guard_cli: str = ""

    # bin は ccnavi 自身の実行ファイル。空なら守らない。指定されたときだけ
    # 対象に入るのは、綴りを推測して守ると、そこに在る別のファイルを
    # 「ccnavi の実体」として扱うことになるため。
    bin: str = ""

    # tickets は提案の置き場（各作業ツリーのルートからの相対、"/" 区切り）、
    # approved は承認済みの写しの置き場（絶対）。判定が読むのは approved だけ。
    # approved が空なら、チケットによる制御を使わない。
    tickets: str = ""
    approved: str = ""
    # phases はフェーズの種類の定義（絶対）。無ければフェーズは番号だけ。
    phases: str = ""
    # risk は実績で測るリスクの配点（絶対）。無ければ組み込みの配点。
    risk: str = ""
    # projects はプロジェクトの置き場（絶対）。空ならプロジェクトを数えず、この設定が
    # 入る前と同じに動く。project_rules は各プロジェクトのルールファイル
    # （git プロジェクトルートからの相対、"/" 区切り）。
    projects: str = ""
    project_rules: str = ""
    # retired は、もう効かない環境変数が指定されていたときの名前。--lint が言う。
    retired: list[str] = field(default_factory=list)


def load(root: str) -> tuple[Settings, list[str]]:
    """root にあるプロジェクトの設定を解決する。

    設定ファイルが無い、あるいは読めないことは失敗ではない。
    既定値だけで初回のチェックアウトが動かないと、
    ccnavi を入れるだけで設定ファイルの編集が必要になってしまう。
    """
    from_env = os.environ.get(MODE_ENV, "")
    settings = Settings(
        mode=from_env,
        mode_from_environment=from_env,
        log=os.path.join(root, DEFAULT_LOG),
        rules=os.path.join(root, DEFAULT_RULES),
        state=os.path.join(root, DEFAULT_STATE),
        restore_if_deny=os.environ.get(RESTORE_IF_DENY_ENV, ""),
        guard_core_files=os.environ.get(GUARD_CORE_FILES_ENV, ""),
        guard_cli=os.environ.get(GUARD_CLI_ENV, ""),
        tickets=DEFAULT_TICKETS,
        approved=os.path.join(root, DEFAULT_APPROVED),
        phases=os.path.join(root, DEFAULT_PHASES),
        risk=os.path.join(root, DEFAULT_RISK),
        projects=os.path.join(root, DEFAULT_PROJECTS),
        project_rules=DEFAULT_PROJECT_RULES,
        retired=[name for name in RETIRED_ENVS if name in os.environ],
    )
    if PROJECTS_ENV in os.environ:
        # 空文字は「プロジェクトを数えない」。
        settings.projects = _log_or_none(root, os.environ[PROJECTS_ENV])
    project_rules_env = os.environ.get(PROJECT_RULES_ENV, "")
    if project_rules_env:
        settings.project_rules = _relative(project_rules_env)
    phases_env = os.environ.get(PHASES_ENV, "")
    if phases_env:
        settings.phases = _resolve(root, phases_env)
    risk_env = os.environ.get(RISK_ENV, "")
    if risk_env:
        settings.risk = _resolve(root, risk_env)

    rules_env = os.environ.get(RULES_ENV, "")
    if rules_env:
        settings.rules = _resolve(root, rules_env)
    bin_env = os.environ.get(BIN_ENV, "")
    if bin_env:
        settings.bin = _resolve_bin(root, bin_env)
    if LOG_ENV in os.environ:
        settings.log = _log_or_none(root, os.environ[LOG_ENV])
    if STATE_ENV in os.environ:
        # 空文字は「控えを持たない」。診断のための実行が、走っている
        # セッションの控えを書き替えずに済むようにする。
        settings.state = _log_or_none(root, os.environ[STATE_ENV])
    tickets_env = os.environ.get(TICKETS_ENV, "")
    if tickets_env:
        settings.tickets = _relative(tickets_env)
    if APPROVED_ENV in os.environ:
        # 空文字は「写しを持たない」＝チケットによる制御を使わない。
        # 承認済みの範囲が無ければ範囲の制限は掛からないので、これは
        # チケットを置いていないのと同じ状態になる。
        settings.approved = _log_or_none(root, os.environ[APPROVED_ENV])

    if not own_source_tree(root):
        return settings, []

    # ccnavi 自身を触っている場合。ここでファイルを読むと、編集が次のセッション
    # ではなく次のツール呼び出しから効く。道具を自分自身に当てて試すには
    # これが要る。開発のための便宜であって境界ではない。効くのはここだけで、
    # off には手が届かないままにしてある。
    conf, problems = _read_local(root)
    if conf is None:
        return settings, problems
    settings.live_files = True

    if isinstance(conf.get("mode"), str):
        settings.mode_declared_in_file = conf["mode"]
        settings.mode = conf["mode"]
    if isinstance(conf.get("rules"), str) and conf["rules"]:
        settings.rules = _resolve(root, conf["rules"])
    if isinstance(conf.get("log"), str):
        settings.log = _log_or_none(root, conf["log"])
    if isinstance(conf.get("state"), str):
        settings.state = _log_or_none(root, conf["state"])
    if isinstance(conf.get("bin"), str) and conf["bin"]:
        settings.bin = _resolve_bin(root, conf["bin"])
    if isinstance(conf.get("restore_if_deny"), str):
        settings.restore_if_deny = conf["restore_if_deny"]
    if isinstance(conf.get("guard_core_files"), str):
        settings.guard_core_files = conf["guard_core_files"]
    if isinstance(conf.get("guard_cli"), str):
        settings.guard_cli = conf["guard_cli"]
    if isinstance(conf.get("tickets"), str) and conf["tickets"]:
        settings.tickets = _relative(conf["tickets"])
    if isinstance(conf.get("approved"), str):
        settings.approved = _log_or_none(root, conf["approved"])
    if isinstance(conf.get("phases"), str) and conf["phases"]:
        settings.phases = _resolve(root, conf["phases"])
    if isinstance(conf.get("risk"), str) and conf["risk"]:
        settings.risk = _resolve(root, conf["risk"])
    if isinstance(conf.get("projects"), str):
        settings.projects = _log_or_none(root, conf["projects"])
    if isinstance(conf.get("project_rules"), str) and conf["project_rules"]:
        settings.project_rules = _relative(conf["project_rules"])

    return settings, problems


def project_rules_path(conf: Settings, project_root: str) -> str:
    """このプロジェクトのルールファイルの絶対パス。"""
    return os.path.join(project_root, conf.project_rules.replace("/", os.sep))


def _relative(path: str) -> str:
    """提案の置き場の綴りを、作業ツリーのルートからの相対に揃える。

    絶対パスは受けない。作業ツリーごとに違うルートに継ぎ足すものなので、
    絶対で書かれた 1 か所を全ツリーが指すと、どのツリーの提案なのかが
    分からなくなる。絶対で来たら先頭の区切りだけ落として相対として読む。
    """
    return path.replace("\\", "/").strip("/")


def own_source_tree(root: str) -> bool:
    """root が ccnavi を開発しているチェックアウトかどうかを、
    そこにあるプロジェクト定義が名乗る名前で判断する。

    これは安全性の検査ではない。1 つのリポジトリを「道具を作っている場所」として
    印を付け、ルールを試す人がセッションを開き直さずに変更を見られるようにする
    だけのもの。他のプロジェクトは環境変数だけが設定の出所のままなので、
    そこでエージェントが設定ファイルを書き換えても、人がセッションを開き直すまで
    ガードには届かない。
    """
    try:
        with open(os.path.join(root, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return False
    project = data.get("project")
    return isinstance(project, dict) and project.get("name") == OWN_PROJECT


def _read_local(root: str) -> tuple[dict | None, list[str]]:
    """上書き設定を読む。

    壊れたファイルは報告して読み飛ばす。設定ファイルが壊れていることを理由に
    判定を拒むと、書き損じたカンマ 1 つでガードが消えることになる。
    """
    path = os.path.join(root, LOCAL_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, []
    except OSError as exc:
        return None, [f"{LOCAL_FILE} を読めないので無視した ({exc})"]

    try:
        conf = json.loads(raw)
    except ValueError as exc:
        return None, [f"{LOCAL_FILE} が JSON として読めないので無視した ({exc})"]
    if not isinstance(conf, dict):
        return None, [f"{LOCAL_FILE} がオブジェクトではないので無視した"]
    return conf, []


def _log_or_none(root: str, path: str) -> str:
    """明示的な空文字を「記録しない」として扱う。"""
    return _resolve(root, path) if path else ""


def _resolve(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def _resolve_bin(root: str, path: str) -> str:
    """実行ファイルの綴りを、この環境に在る形へ寄せる。

    書かれたとおりに在れば、それを使う。`.exe` まで書いてある設定が Windows で
    そのまま通るのはこの経路になる。無いときだけ、付くかもしれない拡張子を
    継ぎ足して探す。hook の登録に書いた `dist/ccnavi/ccnavi` の 1 行が、
    Windows では `ccnavi.exe` に、Linux ではそのまま当たる。PyInstaller が
    Windows でだけ `.exe` を付けるので、3 つの環境で同じ 1 行を使うと、
    設定の綴りとファイルの綴りがここでずれる。

    プラットフォームで分けない。WSL から Windows 側の置き場を指す形があり、
    そこでも守れるほうがよい。継ぎ足した綴りは在るものだけを採るので、
    推測で別のファイルを実体として扱うことにはならない。

    どちらも無ければ書かれたまま返す。ここで空に落とすと、綴りを間違えた設定が
    「実行ファイルを守らない設定」と見分けられなくなる。無いことは selfguard が
    missing と言い、綴りを確かめるよう促す。
    """
    full = _resolve(root, path)
    if os.path.exists(full):
        return full
    for suffix in BIN_SUFFIXES:
        if not full.endswith(suffix) and os.path.exists(full + suffix):
            return full + suffix
    return full
