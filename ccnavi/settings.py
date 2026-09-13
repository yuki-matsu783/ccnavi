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
from typing import NamedTuple

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
# GUARD_TICKET_APPROVAL_ENV は、チケットの承認の経路を守るか。enable（既定）なら、
# シェルから ccnavi の実行ファイルに `--approve` `--reviewed` `ticket` `review` を付けた
# 呼び出しを止め、`--approve` と `--reviewed` は標準入力が端末でなければ拒む。
# テストは disable にする。
#
# 守る対象で名乗る。以前は CCNAVI_GUARD_CLI といって、守る手段（CLI から打つ形）の
# ほうを名前にしていた。切りたい人が何を切ることになるのかが、名前から読めなかった。
GUARD_TICKET_APPROVAL_ENV = "CCNAVI_GUARD_TICKET_APPROVAL"
# BIN_ENV は ccnavi 自身の実行ファイル。判定器の実体なので、書き換えられると
# ルールを 1 行も変えずに判定を差し替えられる。既定は持たない。置き場は
# プロジェクトごとに違ううえ、間違った既定はそこに在る別のファイルを
# 守ることになる。hook の登録に書いた綴りをそのまま渡してもらう。
BIN_ENV = "CCNAVI_BIN_PATH"
# BIN_SUFFIXES は、書かれた綴りに無いときだけ継ぎ足して探す拡張子。
# PyInstaller は Windows でだけ `.exe` を付ける。build.py の側と対になる。
BIN_SUFFIXES = (".exe",)
# TICKET_CONTROL_ENV は、チケット制御を使うか。enable（既定）/ disable の 2 値。
# チケット制御は、提案を承認して承認済みチケットを作り、その範囲・フェーズのゲート・
# サブエージェントの制限を判定に掛ける働き全体。全体ルールは全プロジェクトが使うが、
# チケットまで使うかはプロジェクトが決めるので、その宣言をここに置く。
# 以前は APPROVED_ENV を空文字にすることがこの宣言を兼ねていた。置き場のパスが
# 空であることと機能を切ることは別の話なので、名前を分けた。
TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL"
# チケット制御が使う置き場 2 つ。TICKETS_ENV は提案の置き場で、各作業ツリーの
# ルートからの相対。APPROVED_ENV は承認済みチケットの置き場で、ワークスペースルートからの相対。
# 判定が読むのは承認済みチケットだけで、提案のほうは承認の画面と状態の同期しか読まない。
TICKETS_ENV = "CCNAVI_TICKETS"
APPROVED_ENV = "CCNAVI_APPROVED"
# PHASES_ENV はフェーズの種類の定義。ワークスペースルートからの相対。無ければ番号だけの挙動。
PHASES_ENV = "CCNAVI_PHASES"
# RISK_ENV は実績で測るリスクの配点。ワークスペースルートからの相対。無ければ組み込みの配点。
RISK_ENV = "CCNAVI_RISK"
# PROJECTS_ENV はプロジェクトの置き場（設計 §25）。ワークスペースルートからの相対。直下で `.git` を
# 持つディレクトリがプロジェクトになる。空文字にするとプロジェクトを数えない。
# PROJECT_HOME_ENV は層の傘（設計 §25.2）。各 git プロジェクトルートからの相対で、
# その下の `config/{rules,phases,risk}.yml` が層の 3 本になる。自身の層
# （ワークスペースルートの下）とプロジェクトの層の両方に同じ値が効く。
# 動かせるのは傘の名前だけで、`config/` と 3 本のファイル名は固定。
PROJECTS_ENV = "CCNAVI_PROJECTS"
PROJECT_HOME_ENV = "CCNAVI_PROJECT_HOME"
# もう効かない環境変数。指定されていたら --lint が言う。黙って無視すると、書いた人は
# 効いていると思い続ける。
#
# 前の 2 つは以前の形（チケット 1 本と台帳 jsonl）のもの。CCNAVI_GUARD_CLI は
# CCNAVI_GUARD_TICKET_APPROVAL に改名した。旧名で disable と書いてあった設定は、
# 読まれなくなった時点で既定の enable に戻る――守りが消える向きには倒れない――が、
# 切ったつもりの人には止まる理由が分からないので、名前を挙げて知らせる。
RETIRED_ENVS = (
    "CCNAVI_TICKET",
    "CCNAVI_LEDGER",
    "CCNAVI_GUARD_CLI",
    # 層の置き場が 3 本まとめて `CCNAVI_PROJECT_HOME` の下に移った（設計 §25.2）。
    # 旧の綴り（`config/rules.yml`）はもう読まない。
    "CCNAVI_PROJECT_RULES",
)

# 旧のプロジェクトのルールの置き場。読まないが、まだそこに置いてあるワークスペースに
# --lint が「あるが読まない」と言うために覚えておく（設計 §25.12）。
OLD_PROJECT_RULES = "config/rules.yml"

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
# 承認済みチケットは設定と同じ場所。そこはルールが Write / Edit を止め、組み込みの既定が
# シェル経由の書き込みを止めている。承認済みチケットのために別の保護を足さずに済む。
DEFAULT_APPROVED = os.path.join(".claude", "ccnavi", "tickets")
# フェーズの種類は人が持つ設定なので、承認済みチケットと同じ保護の内側に置く。
DEFAULT_PHASES = os.path.join(".claude", "ccnavi", "phases.yml")
# リスクの配点も人が持つ設定。エージェントが配点を書けると、自分のリスクを自分で決められる。
DEFAULT_RISK = os.path.join(".claude", "ccnavi", "risk.yml")
# プロジェクトの置き場。ワークスペースの直下に固定するのは、列挙が速いことと、
# 何がプロジェクトかで迷わないため。ワークスペースの `.gitignore` に入れる
# （プロジェクトは自分の git を持つ）。
DEFAULT_PROJECTS = "projects"
# 層の傘。プロジェクトの設定はプロジェクトの git で育てるので、`.claude/` の下には
# 置かない（プロジェクトに `.claude/` があると Claude Code がそこのスキルを読み、
# `--lint` が迷い子として拾う）。`config/` でもなく `.ccnavi/` にするのは、3 本と
# スクリプトを 1 つの傘にまとめて、組み込みの deny を `*/.ccnavi/*` の 1 行で
# 済ませるため（設計 §25.2）。
DEFAULT_PROJECT_HOME = ".ccnavi"
# 傘の下の固定の綴り。層はこの形でしか置けない。
LAYER_CONFIG_DIR = "config"
# 層が持てる設定。3 本は独立に無くてよい。
KIND_RULES = "rules"
KIND_PHASES = "phases"
KIND_RISK = "risk"
LAYER_KINDS = (KIND_RULES, KIND_PHASES, KIND_RISK)
# 層の名前。記録の `source` と id の前置きに使う綴り（設計 §25.4）。ruleload が
# 別名で持っているが、実体はここに置く。phases と risk の合成は phase / risk が
# 行い、そこは ruleload を import できない（ruleload が phase を import する）。
LAYER_COMMON = "common"
LAYER_SELF = "self"
# 層の名札に予約してある綴り。プロジェクトはこの名前を名乗れない。
RESERVED_LAYER_NAMES = (LAYER_COMMON, LAYER_SELF)
# 予約名のプロジェクトの控えの key に添える前置き。名札の側（`rules:self`）と
# プロジェクトの側を分ける（_layer_key）。
PROJECT_KEY_HOME = "projects/"

# 層の種別。その層がどこから来たかを、名札の綴りとは別に持つ（設計 §25.4）。
#
# 名札の綴りでは種別を決められない。`projects/common/` は `common` を名乗るが
# 共通層ではないし、`projects/self/` は `self` を名乗るがワークスペース自身の層
# ではない。`layer == LAYER_COMMON` のような文字列比較で種別を決めると、
# プロジェクトが名前を 1 つ選ぶだけで、共通層と同じ扱いに滑り込める。
ORIGIN_COMMON = "common-layer"
ORIGIN_SELF = "self-layer"
ORIGIN_PROJECT = "project-layer"


class LayerFile(NamedTuple):
    """層 1 つの設定ファイル。守る対象（selfguard）へ渡す形（`ruleload.layer_files`）。

    `origin` は層の種別（ORIGIN_*）、`layer` は名札（`common` / `self` /
    プロジェクトの名前）、`kind` は rules / phases / risk、`path` はその綴り。
    種別を添えるのは、受け取る側が名札の文字列比較をしなくて済むようにするため。
    """

    origin: str
    layer: str
    kind: str
    path: str


def is_reserved_layer_name(name: str) -> bool:
    """その名前が層の名札に予約してあるか（`common` / `self`、設計 §25.4）。

    予約の判断はここ 1 か所だけで持つ。ruleload（層を数える・行き先の層を引く）、
    lint（名指しする）、approval（`project:` を承認しない）、phase / risk
    （層の phases / risk を足さない）が同じ答えを引く。片側にしか予約が
    掛かっていないと、数えない層の名前で別の層の判定を引ける。

    綴りの大文字小文字は問わない。`projects/Self/` を数えると、その層の id が
    `Self:schema` になり、記録を読む人が `self:schema`（ワークスペース自身の層）と
    取り違える。機械が綴りを区別するかどうかとは別の話なので、どの機械でも
    同じに畳む。`--lint` が error で名指しする（lint._projects）。
    """
    folded = (name or "").casefold()
    return any(folded == reserved.casefold() for reserved in RESERVED_LAYER_NAMES)


def layer_script_home(conf: Settings) -> str:
    """各層の `script:` に書ける唯一の綴り（`<傘>/scripts/`、"/" 区切り、設計 §25.4.2）。

    共通層だけは今までどおり `.claude/ccnavi/` と `.claude/scripts/`（risk.SCRIPT_HOMES）。
    たがいの側は指せない。プロジェクトの `.ccnavi/` はそのプロジェクトだけで閉じる。
    """
    home = (conf.project_home or DEFAULT_PROJECT_HOME).replace("\\", "/").strip("/")
    return f"{home}/scripts/"


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
    # guard_ticket_approval はチケットの承認の経路（承認・レビュー済みの受け入れ・
    # 状態の移動）をエージェントの手から守るか。enable / disable の 2 つだけを取る。
    #
    # guard_ticket_approval_declared は、解決する前に人が書いた綴り。判定はこれを
    # 読まない。読むのは --lint で、dry-run のように「書けるつもりで書かれたが
    # この門には無い値」を名指しするために要る。解決した値だけを持っていると、
    # 書いた人の思い違いが enable に倒れた時点で消える。
    guard_ticket_approval: str = ""
    guard_ticket_approval_declared: str = ""

    # bin は ccnavi 自身の実行ファイル。空なら守らない。指定されたときだけ
    # 対象に入るのは、綴りを推測して守ると、そこに在る別のファイルを
    # 「ccnavi の実体」として扱うことになるため。
    bin: str = ""

    # ticket_control はチケット制御を使うか。enable / disable の 2 つだけを取る。
    # 解決は cli が selfguard.resolve で行い、読めない値は enable に倒す。
    # ticket_control_declared は解決する前に人が書いた綴りで、--lint がそれを名指しする。
    ticket_control: str = ""
    ticket_control_declared: str = ""

    # tickets は提案の置き場（各作業ツリーのルートからの相対、"/" 区切り）、
    # approved は承認済みチケットの置き場（絶対）。判定が読むのは approved だけ。
    # チケット制御を使うかは ticket_control が決める。approved はパスでしかない。
    # approved_blank は、置き場を空文字で指定されたこと。以前はそれが「使わない」の
    # 宣言だったので、--lint が今の書き方を案内する。
    tickets: str = ""
    approved: str = ""
    approved_blank: bool = False
    # phases はフェーズの種類の定義（絶対）。無ければフェーズは番号だけ。
    phases: str = ""
    # risk は実績で測るリスクの配点（絶対）。無ければ組み込みの配点。
    risk: str = ""
    # projects はプロジェクトの置き場（絶対）。空ならプロジェクトを数えず、この設定が
    # 入る前と同じに動く。project_home は層の傘（git プロジェクトルートからの相対、
    # "/" 区切り）。自身の層とプロジェクトの層の両方に効く。
    projects: str = ""
    project_home: str = ""
    # project_rules_files は、名前で指したプロジェクトのルールファイルの差し替え
    # （名前 → 絶対パス）。`--project-rules-file <名前>=<パス>` が入れる。診断（--test /
    # --test-samples / --lint / --explain）だけが使い、hook からの判定では空のまま。
    # VS Code 拡張が、編集中のプロジェクトのルールを保存せずに試すために使う。
    project_rules_files: dict[str, str] = field(default_factory=dict)
    # retired は、もう効かない環境変数が指定されていたときの名前。--lint が言う。
    retired: list[str] = field(default_factory=list)

    @property
    def tickets_enabled(self) -> bool:
        """チケット制御が効いているか。

        判定・監視・診断はこれで分岐する。approved の真偽で分岐しない。
        解決前（空）は enable と同じに読む。読めない値は解決で enable に倒れるので、
        ここで disable と読めるのは disable と書かれたときだけになる。
        """
        return self.ticket_control != TICKET_CONTROL_DISABLE


# ticket_control の値。modes / selfguard と同じ綴りだが、settings は両方より下に
# あるので、ここに持つ。
TICKET_CONTROL_ENABLE = "enable"
TICKET_CONTROL_DISABLE = "disable"


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
        guard_ticket_approval=os.environ.get(GUARD_TICKET_APPROVAL_ENV, ""),
        guard_ticket_approval_declared=os.environ.get(GUARD_TICKET_APPROVAL_ENV, ""),
        ticket_control=os.environ.get(TICKET_CONTROL_ENV, ""),
        ticket_control_declared=os.environ.get(TICKET_CONTROL_ENV, ""),
        tickets=DEFAULT_TICKETS,
        approved_blank=APPROVED_ENV in os.environ and os.environ[APPROVED_ENV] == "",
        approved=os.path.join(root, DEFAULT_APPROVED),
        phases=os.path.join(root, DEFAULT_PHASES),
        risk=os.path.join(root, DEFAULT_RISK),
        projects=os.path.join(root, DEFAULT_PROJECTS),
        project_home=DEFAULT_PROJECT_HOME,
        retired=[name for name in RETIRED_ENVS if name in os.environ],
    )
    # 環境変数と上書き設定ファイルで重ねる欄。読み方と、空文字を「指定した」と読むか。
    # 空文字を受ける欄は、「記録しない」「控えを持たない」「プロジェクトを数えない」を
    # 言えるようにしてある。承認済みチケットの置き場は空文字を受けない。チケット制御を切るのは
    # TICKET_CONTROL_ENV の仕事で、置き場を空にしても既定の置き場のまま動く。
    overrides = (
        ("projects", PROJECTS_ENV, _log_or_none, True),
        ("project_home", PROJECT_HOME_ENV, _relative, False),
        ("phases", PHASES_ENV, _resolve, False),
        ("risk", RISK_ENV, _resolve, False),
        ("rules", RULES_ENV, _resolve, False),
        ("bin", BIN_ENV, _resolve_bin, False),
        ("log", LOG_ENV, _log_or_none, True),
        ("state", STATE_ENV, _log_or_none, True),
        ("tickets", TICKETS_ENV, _relative, False),
        ("approved", APPROVED_ENV, _resolve, False),
    )
    for name, env, read, accepts_empty in overrides:
        if env in os.environ and (accepts_empty or os.environ[env]):
            setattr(settings, name, read(root, os.environ[env]))

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
    for name in ("restore_if_deny", "guard_core_files", "guard_ticket_approval", "ticket_control"):
        if isinstance(conf.get(name), str):
            setattr(settings, name, conf[name])
    # 書かれた綴りをそのまま控える。上書き設定ファイルは ccnavi 自身を開発している
    # ときだけ読むものだが、そこに dry-run と書いた人にも --lint から同じことを言う。
    for name in ("guard_ticket_approval", "ticket_control"):
        if isinstance(conf.get(name), str):
            setattr(settings, f"{name}_declared", conf[name])
    if conf.get("approved") == "":
        settings.approved_blank = True
    for name, _, read, accepts_empty in overrides:
        value = conf.get(name)
        if isinstance(value, str) and (accepts_empty or value):
            setattr(settings, name, read(root, value))

    return settings, problems


def layer_path(conf: Settings, home_root: str, kind: str, layer: str = "") -> str:
    """層の設定ファイルの絶対パス。判定と診断が読む先（設計 §25.2）。

    `home_root` はその層の git プロジェクトルート。自身の層ならワークスペースルート、
    プロジェクトの層ならその git プロジェクトルートを渡す。3 種とも同じ形なので、
    rules だけの経路を別に持たない。

    `--project-rules-file` で名前が差し替えられていれば、rules に限ってそのパス。
    差し替えは診断のためのもので、phases と risk には効かない。守る対象（selfguard）は
    差し替えを見ない `layer_real_path` を使う。
    """
    if kind == KIND_RULES:
        override = conf.project_rules_files.get(layer or _layer_name(home_root))
        if override:
            return override
    return layer_real_path(conf, home_root, kind)


def layer_real_path(conf: Settings, home_root: str, kind: str) -> str:
    """層の設定ファイルが本来ある場所。差し替えを見ない。"""
    home = (conf.project_home or DEFAULT_PROJECT_HOME).replace("/", os.sep)
    return os.path.join(home_root, home, LAYER_CONFIG_DIR, f"{kind}.yml")


def _layer_name(home_root: str) -> str:
    """差し替えを引くときの名前。プロジェクトの層は置き場の下のディレクトリ名。"""
    return os.path.basename(os.path.normpath(home_root)) if home_root else ""


def _relative(root: str, path: str) -> str:
    """提案の置き場の綴りを、作業ツリーのルートからの相対に揃える。

    絶対パスは受けない。作業ツリーごとに違うルートに継ぎ足すものなので、
    絶対で書かれた 1 か所を全ツリーが指すと、どのツリーの提案なのかが
    分からなくなる。絶対で来たら先頭の区切りだけ落として相対として読む。
    root は使わない。他の読み方と並べて表に置けるように、引数の形だけ揃えてある。
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
