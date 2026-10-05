"""ccnavi の設定を解決する。

設定は環境変数で渡す。プロジェクトはそれを Claude Code の設定ファイルの env
ブロックに書く。エージェント側の設定スキーマが独自キーを拒むため、そこが独自の値を書ける
唯一の場所になる。

値はプロセスの環境から読み、設定ファイルからは読まない。この違いに意味がある。
hook が受け取る環境はセッション開始時に固定されるので、あとから設定ファイルを
直してもセッションを開き直すまで反映されない。その古さは不便だが、同時に、
エージェントが自分を監視するものを緩めるのを防いでいる唯一の仕組みでもある。
設定ファイルは作業ツリーの中にあってエージェントが書けるので、そこから読んだ
値は次のツール呼び出しから反映されてしまう。

例外は ccnavi 自身を開発しているときだけ。own_source_tree を参照。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from typing import NamedTuple

# ccnavi が読む環境変数。
#
# 共通レイヤーの 3 本（ルール・フェーズ定義・リスクの配点）はここに無い。置き場は
# `.ccnavi/common/` に固定で、env では動かない。3 つのレイヤーのうち共通レイヤーだけが別の決まり方を
# していた非対称を無くしたもの。診断のためにここを動かすには `--rules` /
# `--phases` / `--risk` のフラグを使う。hook は引数を渡さずに起動するので、
# 判定の入口は固定される。
MODE_ENV = "CCNAVI_MODE"
# 置き場（記録・state・提案・承認済みチケット・プロジェクト・ccnavi ディレクトリ）も env では
# 動かない。既定に固定で、下の DEFAULT_* がそれ。診断のために動かす道は `--log` /
# `--state` / `--tickets` / `--approved` / `--projects` / `--project-home` のフラグだけで、
# cli_args._override が重ねる。
#
# 戻す働きは 2 つあり、守る対象の決まり方が違うので環境変数も分けてある。
#
# RESTORE_IF_DENY_ENV は、ルールが `deny` と宣言した場所を戻す。対象は
# ルールファイル次第で動くので、プロジェクトが書いたぶんだけ広がる。
# 戻すのは `deny` だけで、`ask` と承認済みチケットの範囲外は報告に留める
# （post._restorable）。実行後チェックが見る範囲（post_findings._guarding）より狭い。
# GUARD_CORE_FILES_ENV は、ccnavi 自身を成り立たせている設定ファイルを
# 戻す。対象は組み込みで固定されていて、ルールファイルには書かない。
#
# 分けた理由は selfguard.py の冒頭にある。片方だけを切れることが要る。
RESTORE_IF_DENY_ENV = "CCNAVI_RESTORE_IF_DENY"
GUARD_CORE_FILES_ENV = "CCNAVI_GUARD_CORE_FILES"
# GUARD_TICKET_APPROVAL_ENV は、チケットの承認の経路を守るか。enable（既定）なら、
# シェルから ccnavi の実行ファイルに `--agree` `--reviewed` `--close-early` `ticket` `review` を
# 付けた呼び出しを止め、この 3 つのフラグは標準入力が端末でなければ拒む。
# テストは disable にする。
#
# 守る手段（CLI から打つ形）ではなく守る対象で名前を付ける。
# 切りたいユーザが何を切ることになるのかを、名前から読めるようにする。
GUARD_TICKET_APPROVAL_ENV = "CCNAVI_GUARD_TICKET_APPROVAL"
# GUARD_UNWATCHED_ENV は、ユーザにも classifier にも確認できないモード
# （dontAsk・bypassPermissions）で、ルールがどこも言及しない呼び出しを止めるか。
# enable（既定）/ disable の 2 値。
#
# enable なら通さない。そこで確認を返しても答える者が居ないので、通せば
# 「誰も見ないまま通った」になる。disable なら判定を返さず、そのモードの
# 取り決めに委ねる。ccnavi はルールに書かれたものだけを止める道具になる。
#
# この切り替えの環境変数に dry-run は無い。止めずに報告する段は CCNAVI_MODE=dry-run が持つ。
GUARD_UNWATCHED_ENV = "CCNAVI_GUARD_UNWATCHED"
# BIN_ENV は ccnavi 自身の実行ファイル。判定器の実体なので、書き換えられると
# ルールを 1 行も変えずに判定を差し替えられる。既定は持たない。置き場は
# プロジェクトごとに違ううえ、間違った既定はそこに在る別のファイルを
# 守ることになる。hook の登録に書いたパスをそのまま渡してもらう。
BIN_ENV = "CCNAVI_BIN_PATH"
# BIN_SUFFIXES は、書かれたパスに無いときだけ継ぎ足して探す拡張子。
# PyInstaller は Windows でだけ `.exe` を付ける。build.py の側と対になる。
BIN_SUFFIXES = (".exe",)
# TICKET_CONTROL_ENV は、チケット制御を使うか。enable（既定）/ disable の 2 値。
# チケット制御は、提案を承認して承認済みチケットを作り、その範囲・フェーズの HITL ポイント・
# サブエージェントの制限を判定に掛ける働き全体。全体ルールは全プロジェクトが使うが、
# チケットまで使うかはプロジェクトが決めるので、その宣言をここに置く。
# 置き場のパス（承認済みチケットの置き場）とは分けてある。パスが在ることと機能を切ることは別の話。
TICKET_CONTROL_ENV = "CCNAVI_TICKET_CONTROL"
# DENY_REPEAT_ENV は、同じ理由で同じ呼び出しを何回止めたら「言い換えずに相談せよ」と
# つけるか（repeat）。既定は 3。判定は変わらず、文面とユーザへの報告が変わるだけ。
DENY_REPEAT_ENV = "CCNAVI_DENY_REPEAT"
# INTEGRATION_ENV は統合先の名前。リポジトリには置かず、未設定ならホストのデフォルトブランチ。
# `done/` とレイヤーと置き場のパスを読むブランチで、親のブランチはここから切る。
# **ccnavi はこの環境変数を読まない。** 読むのは sh（`ccnavi-sync.sh`）で、sh が環境変数か
# `.claude/settings.local.json` の `env` から決め、要る所へ `--integration-branch` で渡す。
# settings.local.json の `env` は Claude Code が起こしたプロセスにしか渡らないので、ユーザが端末で
# 打つ sh のために、その値だけを `sync paths` が読んで返す（integration_local）。
INTEGRATION_ENV = "CCNAVI_INTEGRATION_BRANCH"
# 個人の上書き設定。Claude Code が `env` を起こしたプロセスに渡す。
LOCAL_CLAUDE_SETTINGS = os.path.join(".claude", "settings.local.json")
# 共有の設定。`env` に書いた値は、Claude Code が起こしたプロセスに渡る。
SHARED_CLAUDE_SETTINGS = os.path.join(".claude", "settings.json")
# BRANCH_PREFIXES_ENV は親の識別子の先頭の語のリスト。カンマか空白で区切る
# （`feature,hotfix,fix`）。無ければ DEFAULT_BRANCH_PREFIXES。ユーザがファイルを書かなくても
# 既定のリストで足り、リストを変えたい人だけが settings.json（か settings.local.json）の
# `env` に書く。
BRANCH_PREFIXES_ENV = "CCNAVI_BRANCH_PREFIXES"
# 既定の先頭の語。`release` は統合先や保護されたブランチの名前（`release-*`）に当たるので入れない。
DEFAULT_BRANCH_PREFIXES = ("feature", "hotfix", "fix", "bugfix", "chore", "refactor", "docs")
# 先頭の語に使えない名前（`ticket_ids.RESERVED_BRANCH_IDS` と同じリスト）。
_RESERVED_PREFIXES = ("main", "master", "develop", "release")
_PREFIX = re.compile(r"^[a-z][a-z0-9]*\Z")

# own_project は ccnavi 自身のソースツリーを見分ける目印。own_source_tree を参照。
OWN_PROJECT = "ccnavi"

# LOCAL_FILE は ccnavi 自身を開発しているときだけ読む上書き設定。
# コミットしないので、他のプロジェクトには存在せず、
# 直しても影響が及ぶのは道具を試している本人だけになる。
LOCAL_FILE = "ccnavi.settings.local.json"

# 既定の置き場。ワークスペースルートからの相対。
#
# ユーザが持つ設定（共通レイヤーの 3 本）は ccnavi ディレクトリの下の `.ccnavi/common/`、
# 実行のたびに書かれる記録と state は `logs/` に置く。
# `.claude/` には Claude Code 自身のもの（settings.json・hooks・skills・worktrees）だけを残す。
#
# 置き場はどれもここに固定で、env でも上書き設定ファイルでも動かない。
# 診断のために別の場所を指すのはフラグ（`--rules` / `--log` など）だけで、
# hook は引数を渡さずに起動するから、判定の入口はここから動かない。
# 判定の記録のファイル名は `decisions.jsonl`。
DEFAULT_LOG = os.path.join("logs", "decisions.jsonl")
DEFAULT_RULES = os.path.join(".ccnavi", "common", "rules.yml")
# state はセッションごとの一時的な状態なので、記録とは分けてまとめておく。
# 配る対象ではないし、消えても次の起動で取り直せる。
DEFAULT_STATE = os.path.join("logs", "state")
# 提案は各作業ツリーの `wip/proposals/` に置く。ユーザが読み、ユーザが承認するものなので、
# ガードの設定をまとめてある場所ではなく、目に入る場所に出しておく。
# 区切りは "/" で持つ。作業ツリーのルートに継ぎ足すときに os の区切りへ直す。
DEFAULT_TICKETS = "wip/proposals"
# 承認済みチケットは ccnavi ディレクトリ（`.ccnavi/`）の下。そこは組み込みが丸ごと止めているので、
# 別の保護を足さずに済む。ワークスペースの 1 か所ではなくツリーごとに置くのは、
# 承認をプロジェクトの git で共有するため。承認したユーザの機械にだけ在る形だと、A が承認して
# B の機械で作業する流れが成り立たない（設計 9.2）。区切りは "/" で持ち、ツリーの
# ルートに継ぎ足すときに os の区切りへ直す。
# 下に `doing/`（作業中）と `done/`（閉じた）と `phases/`（マーカー）が並ぶ。
DEFAULT_APPROVED = ".ccnavi/approved"
# フェーズ定義はユーザが持つ設定なので、承認済みチケットと同じ保護の内側に置く。
DEFAULT_PHASES = os.path.join(".ccnavi", "common", "phases.yml")
# リスクの配点もユーザが持つ設定。エージェントが配点を書けると、自分のリスクを自分で決められる。
DEFAULT_RISK = os.path.join(".ccnavi", "common", "risks.yml")
# プロジェクトの置き場。ワークスペースの直下に固定するのは、列挙が速いことと、
# 何がプロジェクトかで迷わないため。ワークスペースの `.gitignore` に入れる
# （プロジェクトは自分の git を持つ）。
DEFAULT_PROJECTS = "projects"
# ccnavi ディレクトリ。プロジェクトの設定はプロジェクトの git で育てるので、`.claude/` の下には
# 置かない（プロジェクトに `.claude/` があると Claude Code がそこのスキルを読み、
# `--lint` が「ワークツリーでもワークスペースルートでもないのに `.claude/` を持つ」と
# 警告する）。`config/` でもなく `.ccnavi/` にするのは、3 本とスクリプトを 1 つの
# ディレクトリにまとめて、組み込みの deny を `*/.ccnavi/*` の 1 行で済ませるため
# （設計 11.2）。
DEFAULT_PROJECT_HOME = ".ccnavi"
# 引用せずにシェルへ渡せるパス。空白とシェルの記号を含まない。
_BARE_PATH = re.compile(r"[^\s'\"\\$`!*?\[\]{}()<>|&;#~]+")
# 二重引用符の中でも意味を持つ文字。
_SPECIAL_IN_DOUBLE_QUOTES = re.compile(r'["\\$`!]')


def script_command(root: str, name: str) -> str:
    """文面で案内する `.ccnavi/scripts/` の sh のパス。ワークスペースルートから書く。

    スクリプトはワークスペースにしか無く、プロジェクトから切ったワークツリーでは相対の
    `sh .ccnavi/scripts/...` が届かない。パスはルールの `{root}`（rules.root_glob）と揃え、
    区切りは `/` にそろえる（Git Bash は `C:/...` を読める）。

    空白やシェルの記号を含むときだけ引用する。引用しないと sh が単語に分け、止めている間の例外と
    サブエージェントの禁止（`\\S*ccnavi-...`）にも当たらない。引用すれば shellread が中の空白を
    区切りと別の目印にするので、どちらにも当たる。文面は案内を `'...'` で囲むので、引用は
    まず `"..."` にし、`"` の中でも意味を持つ文字があるときだけ単引用符にする。
    """
    base = os.path.realpath(root).replace("\\", "/").rstrip("/")
    return f"sh {_quoted(f'{base}/{DEFAULT_PROJECT_HOME}/scripts/{name}')}"


def _quoted(path: str) -> str:
    """文面に置くパスの表記。引用が要るときだけ引用する（引用の決め方は script_command）。"""
    if _BARE_PATH.fullmatch(path):
        return path
    if not _SPECIAL_IN_DOUBLE_QUOTES.search(path):
        return f'"{path}"'
    return shlex.quote(path)


def bin_command(bin_path: str) -> str:
    """文面で案内する ccnavi 自身のパス。設定（CCNAVI_BIN_PATH）が指すものを打てる形にする。

    案内したパスをそのまま打てないと、案内は「そういうものが在るらしい」で終わる。
    hook が起動しているのと同じパスを案内するのが、いちばん確かめようがある。

    設定が無ければ `ccnavi`。PATH に置いたユーザはそれで打てるし、置いていないユーザには
    パスを尋ねる手掛かりになる。`.sh` は `sh` を頭に付ける（起動役はシェルの
    スクリプト。ccnavi-launcher.sh がこれにあたる）。
    """
    if not bin_path:
        return "ccnavi"
    path = bin_path.replace("\\", "/")
    # 名前の大小は区別しない。Windows と macOS の既定のファイルシステムでは `LAUNCHER.SH` も
    # 同じシェルのスクリプトで、区別すると `sh` の付かない打てないパスを案内することになる。
    return f"sh {_quoted(path)}" if path.lower().endswith(".sh") else _quoted(path)


# ccnavi ディレクトリの下の固定のパス。レイヤーはこの形でしか置けない。
LAYER_CONFIG_DIR = "config"
# 共通レイヤーとそのミラーの置き場（ccnavi ディレクトリからの相対）。
COMMON_DIR = "common"
# レイヤーが持てる設定。3 本は独立に無くてよい。
KIND_RULES = "rules"
KIND_PHASES = "phases"
KIND_RISK = "risk"
LAYER_KINDS = (KIND_RULES, KIND_PHASES, KIND_RISK)
# レイヤーの設定のファイル名。kind は記録と `--explain --json` の鍵の表記なので、
# ファイル名とは別に持つ。
LAYER_FILE_NAMES = {KIND_RULES: "rules.yml", KIND_PHASES: "phases.yml", KIND_RISK: "risks.yml"}
# レイヤーの名前。記録の `source` と id の前置きに使う表記（設計 11.4）。ruleload が
# 別名で持っているが、実体はここに置く。phases と risk の合成は phase / risk が
# 行い、そこは ruleload を import できない（ruleload が phase を import する）。
LAYER_COMMON = "common"
LAYER_SELF = "self"
# レイヤーの名札に予約してある表記。プロジェクトはこの名前を使えない。
RESERVED_LAYER_NAMES = (LAYER_COMMON, LAYER_SELF)
# 予約名のプロジェクトのバックアップの key につける前置き。名札の側（`rules:self`）と
# プロジェクトの側を分ける（_layer_key）。
PROJECT_KEY_HOME = "projects/"
# ミラーのバックアップの key につける前置き（`rules:mirror/lib`）。
MIRROR_KEY_HOME = "mirror/"

# レイヤーの種別。そのレイヤーがどこから来たかを、名札の表記とは別に持つ（設計 11.4）。
#
# 名札の表記では種別を決められない。`projects/common/` の名札は `common` だが
# 共通レイヤーではないし、`projects/self/` の名札は `self` だがワークスペース自身のレイヤー
# ではない。`layer == LAYER_COMMON` のような文字列比較で種別を決めると、
# プロジェクトが名前を 1 つ選ぶだけで、共通レイヤーと同じ扱いを受けられてしまう。
ORIGIN_COMMON = "common-layer"
ORIGIN_SELF = "self-layer"
ORIGIN_PROJECT = "project-layer"
# プロジェクトの `.ccnavi/common/`（共通レイヤーのミラー。設計 11.12）。ワークスペースの中では
# 判定に読まれないが、守る対象には入る。
ORIGIN_MIRROR = "mirror-layer"


class LayerFile(NamedTuple):
    """レイヤー 1 つの設定ファイル。守る対象（selfguard）へ渡す形（`ruleload.layer_files`）。

    `origin` はレイヤーの種別（ORIGIN_*）、`layer` は名札（`common` / `self` /
    プロジェクトの名前）、`kind` は rules / phases / risk、`path` はそのパス。
    種別をつけるのは、受け取る側が名札の文字列比較をしなくて済むようにするため。
    """

    origin: str
    layer: str
    kind: str
    path: str


def is_reserved_layer_name(name: str) -> bool:
    """その名前がレイヤーの名前に予約してあるか（`common` / `self`、設計 11.4）。

    予約の判断はここ 1 か所だけで持つ。ruleload（レイヤーを数える・行き先のレイヤーを引く）、
    lint（名指しする）、approval（`project:` を承認しない）、phase / risk
    （レイヤーの phases / risk を足さない）が同じ答えを引く。片側でしか予約を
    見ていないと、数えないレイヤーの名前で別のレイヤーの判定を引ける。

    名前の大文字小文字は問わない。`projects/Self/` を数えると、そのレイヤーの id が
    `Self:schema` になり、記録を読むユーザが `self:schema`（ワークスペース自身のレイヤー）と
    取り違える。機械が表記を区別するかどうかとは別の話なので、どの機械でも
    大文字小文字を区別せずに扱う。`--lint` が error で名指しする（lint_places._projects）。
    """
    folded = (name or "").casefold()
    return any(folded == reserved.casefold() for reserved in RESERVED_LAYER_NAMES)


def approved_dir(conf: Settings, tree_root: str) -> str:
    """このツリーの承認済みチケットの置き場（絶対）。

    承認済みチケットとマーカーはそのツリーの git が追跡し、
    親チケットのブランチに乗って他の機械へ届く（設計 9.2）。
    だから置き場はワークスペースの 1 か所ではなく、ツリーごとに解く。
    """
    return os.path.join(tree_root, (conf.approved or DEFAULT_APPROVED).replace("/", os.sep))


def layer_script_home(conf: Settings) -> str:
    """自身のレイヤーとプロジェクトのレイヤーの `script:` に書ける唯一のパス（設計 11.4.2）。

    形は `<ccnavi ディレクトリ>/scripts/` で、"/" 区切り。
    解く基準はそのレイヤーの git プロジェクトルート。

    共通レイヤー（と単体 clone でそれになるミラー）だけは `.ccnavi/common/scripts/`
    （risk.SCRIPT_HOMES。解く基準は、そのとき共通レイヤーを持つルート）。
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
    # 「セッションを起動したユーザが渡した値」を見分けられる。
    mode_declared_in_file: str = ""
    mode_from_environment: str = ""

    # live_files は上書き設定を読んだことを示す。
    # ccnavi 自身のソースツリーでしか真にならない。
    live_files: bool = False

    log: str = ""
    rules: str = ""
    state: str = ""
    # 同じ理由の拒否を何回目から名指しするか。書かれたままの値で、読むのは repeat.threshold。
    deny_repeat: str = ""
    # 戻す働きの 2 つ。どちらも mode と同じ enable / dry-run / disable を取る。
    #
    # restore_if_deny は、ルールが `deny` と宣言した場所が副作用で変わったときに
    # git から戻すかどうか。対象がルールファイル次第で動くので、書き損じが
    # そのまま「頼んでいないファイル操作」になりうる。その懸念は残るが、
    # 戻さない既定は「宣言したのに守られない」を既定にすることでもあるので、
    # 既定は enable にしてある。切りたいプロジェクトは明示して切る。
    #
    # guard_core_files は、ccnavi 自身の設定ファイルをバックアップから戻すかどうか。
    # 対象は組み込みで固定なので、広がりようがない。
    restore_if_deny: str = ""
    guard_core_files: str = ""
    # guard_ticket_approval はチケットの承認の経路（承認・レビュー済みの受け入れ・
    # 状態の移動）をエージェントの手から守るか。enable / disable の 2 つだけを取る。
    #
    # guard_ticket_approval_declared は、解決する前にユーザが書いた値。判定はこれを
    # 読まない。読むのは --lint で、dry-run のように「書けるつもりで書かれたが
    # この切り替えの環境変数には無い値」を名指しするために要る。解決した値だけを持っていると、
    # 書いたユーザの思い違いが enable として扱われた時点で消える。
    guard_ticket_approval: str = ""
    guard_ticket_approval_declared: str = ""

    # guard_unwatched は、確認できる者が居ないモードで未宣言の呼び出しを止めるか。
    # enable / disable の 2 つだけを取る。解決は cli が selfguard.resolve で行い、
    # 読めない値は enable になる。空は enable と同じに読む。
    guard_unwatched: str = ""

    # bin は ccnavi 自身の実行ファイル。空なら守らない。指定されたときだけ
    # 対象に入るのは、パスを推測して守ると、そこに在る別のファイルを
    # 「ccnavi の実体」として扱うことになるため。
    bin: str = ""

    # ticket_control はチケット制御を使うか。enable / disable の 2 つだけを取る。
    # 解決は cli が selfguard.resolve で行い、読めない値は enable として扱う。
    # ticket_control_declared は解決する前にユーザが書いた値で、--lint がそれを名指しする。
    ticket_control: str = ""
    ticket_control_declared: str = ""

    # tickets は提案の置き場（各作業ツリーのルートからの相対、"/" 区切り）、
    # approved は承認済みチケットの置き場（各ツリーのルートからの相対、"/" 区切り）。
    # 絶対で 1 か所を指さないのは、そのツリーの git に乗って他の PC に届くから。判定が読むのは
    # approved だけ。
    # チケット制御を使うかは ticket_control が決める。approved はパスでしかない。
    tickets: str = ""
    approved: str = ""
    # phases はフェーズ定義（絶対）。無ければフェーズは番号だけ。
    phases: str = ""
    # risk は実績で測るリスクの配点（絶対）。無ければ組み込みの配点。
    risk: str = ""
    # projects はプロジェクトの置き場（絶対）。既定に固定で、空になるのは診断のフラグ
    # （`--projects ""`）で渡したときだけ。そのときはプロジェクトを数えず、ワークスペース
    # 自身だけで動く。project_home は ccnavi ディレクトリ（git プロジェクトルートからの相対、
    # "/" 区切り）。自身のレイヤーとプロジェクトのレイヤーの両方に使われる。
    projects: str = ""
    project_home: str = ""
    # project_rules_files は、名前で指したプロジェクトのルールファイルの差し替え
    # （名前 → 絶対パス）。`--project-rules-file <名前>=<パス>` が入れる。診断（--test /
    # --test-samples / --lint / --explain）だけが使い、hook からの判定では空のまま。
    # VS Code 拡張が、編集中のプロジェクトのルールを保存せずに試すために使う。
    project_rules_files: dict[str, str] = field(default_factory=dict)
    # project_phases_files は同じ差し替えをレイヤーのフェーズ定義に対して行う（名前 → 絶対パス）。
    # `--project-phases-file <名前>=<パス>` が入れる。名前は `self` かプロジェクトの名前で、
    # 共通レイヤーの定義は今までどおり `--phases` で差し替える。VS Code 拡張のフェーズ管理画面が、
    # 編集中のレイヤーの定義を保存せずに検証するために使う。
    project_phases_files: dict[str, str] = field(default_factory=dict)
    # branch_prefixes は親の識別子の先頭の語のリスト（`branch_prefixes`）。
    # branch_prefixes_rejected は環境変数に書かれていたが使えない語（lint が warn で名指しする）。
    branch_prefixes: tuple = DEFAULT_BRANCH_PREFIXES
    branch_prefixes_rejected: tuple = ()
    # integration_branch は統合先の名前。環境変数からは読まず、`--integration-branch` で
    # 渡されたときだけ入る。いまは識別子の予約（統合先と同じ名前を使わせない）の検査が読む。
    integration_branch: str = ""

    @property
    def tickets_enabled(self) -> bool:
        """チケット制御が有効か。

        判定・実行後チェック・診断はこれで分岐する。approved の真偽で分岐しない。
        解決前（空）は enable と同じに読む。読めない値は解決で enable になるので、
        ここで disable と読めるのは disable と書かれたときだけになる。
        """
        return self.ticket_control != TICKET_CONTROL_DISABLE


# ticket_control の値。modes / selfguard と同じ表記だが、settings は両方より下に
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
        guard_unwatched=os.environ.get(GUARD_UNWATCHED_ENV, ""),
        deny_repeat=os.environ.get(DENY_REPEAT_ENV, ""),
        ticket_control=os.environ.get(TICKET_CONTROL_ENV, ""),
        ticket_control_declared=os.environ.get(TICKET_CONTROL_ENV, ""),
        tickets=DEFAULT_TICKETS,
        approved=DEFAULT_APPROVED,
        phases=os.path.join(root, DEFAULT_PHASES),
        risk=os.path.join(root, DEFAULT_RISK),
        projects=os.path.join(root, DEFAULT_PROJECTS),
        project_home=DEFAULT_PROJECT_HOME,
    )
    # 環境変数と上書き設定ファイルで重ねる欄と、その読み方。空文字は「指定しなかった」と読む。
    #
    # 置き場（共通レイヤーの 3 本、記録・state・提案・承認済みチケット・プロジェクト・ccnavi
    # ディレクトリ）はこの表に無い。env でも上書き設定ファイルでも動かず、既定のまま。
    # 動かせるのはフラグだけで、そちらは cli_args._override が重ねる。
    # 残る `bin` は置き場ではなく、hook が起動する実行ファイルの指定で、既定を持たない。
    overrides = (("bin", BIN_ENV, _resolve_bin),)
    for name, env, read in overrides:
        if os.environ.get(env):
            setattr(settings, name, read(root, os.environ[env]))
    settings.branch_prefixes, settings.branch_prefixes_rejected = branch_prefixes(root)

    if not own_source_tree(root):
        return settings, []

    # ccnavi 自身を触っている場合。ここでファイルを読むと、編集が次のセッション
    # ではなく次のツール呼び出しから反映される。道具を自分自身に当てて試すには
    # これが要る。開発のための便宜であって境界ではない。反映されるのはここだけで、
    # このファイルに `disable` と書いてもモードの解決で無視され、判定は止まらない。
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
    # 書かれた値をそのまま残す。上書き設定ファイルは ccnavi 自身を開発している
    # ときだけ読むものだが、そこに dry-run と書いたユーザにも --lint から同じことを言う。
    for name in ("guard_ticket_approval", "ticket_control"):
        if isinstance(conf.get(name), str):
            setattr(settings, f"{name}_declared", conf[name])
    for name, _, read in overrides:
        value = conf.get(name)
        if isinstance(value, str) and value:
            setattr(settings, name, read(root, value))

    return settings, problems


def layer_path(conf: Settings, home_root: str, kind: str, layer: str = "") -> str:
    """レイヤーの設定ファイルの絶対パス。判定と診断が読む先（設計 11.2）。

    `home_root` はそのレイヤーの git プロジェクトルート。自身のレイヤーならワークスペースルート、
    プロジェクトのレイヤーならその git プロジェクトルートを渡す。3 種とも同じ形なので、
    rules だけの経路を別に持たない。

    `--project-rules-file` / `--project-phases-file` で名前が差し替えられていれば、rules / phases に
    限ってそのパス。差し替えは診断のためのもので、risk には当てはまらない。守る対象（selfguard）は
    差し替えを見ない `layer_real_path` を使う。
    """
    swaps = {KIND_RULES: conf.project_rules_files, KIND_PHASES: conf.project_phases_files}.get(kind)
    if swaps:
        override = swaps.get(layer or _layer_name(home_root))
        if override:
            return override
    return layer_real_path(conf, home_root, kind)


def layer_real_path(conf: Settings, home_root: str, kind: str) -> str:
    """レイヤーの設定ファイルが本来ある場所。差し替えを見ない。"""
    home = (conf.project_home or DEFAULT_PROJECT_HOME).replace("/", os.sep)
    return os.path.join(home_root, home, LAYER_CONFIG_DIR, LAYER_FILE_NAMES[kind])


def mirror_real_path(conf: Settings, home_root: str, kind: str) -> str:
    """プロジェクトの `.ccnavi/common/`（共通レイヤーのミラー）の設定ファイルの場所。"""
    home = (conf.project_home or DEFAULT_PROJECT_HOME).replace("/", os.sep)
    return os.path.join(home_root, home, COMMON_DIR, LAYER_FILE_NAMES[kind])


def _layer_name(home_root: str) -> str:
    """差し替えを引くときの名前。プロジェクトのレイヤーは置き場の下のディレクトリ名。"""
    return os.path.basename(os.path.normpath(home_root)) if home_root else ""


def own_source_tree(root: str) -> bool:
    """root が ccnavi を開発しているチェックアウトかどうかを、
    そこにあるプロジェクト定義に書かれた名前で判断する。

    これは安全性の検査ではない。1 つのリポジトリを「道具を作っている場所」として
    目印を付け、ルールを試すユーザがセッションを開き直さずに変更を見られるようにする
    だけのもの。他のプロジェクトは環境変数だけが設定の出所のままなので、
    そこでエージェントが設定ファイルを書き換えても、ユーザがセッションを開き直すまで
    ガードには反映されない。
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


def _resolve(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def _resolve_bin(root: str, path: str) -> str:
    """実行ファイルのパスを、この環境に在る形へ直す。

    書かれたとおりに在れば、それを使う。`.exe` まで書いてある設定が Windows で
    そのまま通るのはこの経路になる。無いときだけ、付くかもしれない拡張子を
    継ぎ足して探す。hook の登録に書いた `dist/ccnavi/ccnavi` の 1 行が、
    Windows では `ccnavi.exe` に、Linux ではそのまま当たる。PyInstaller が
    Windows でだけ `.exe` を付けるので、3 つの環境で同じ 1 行を使うと、
    設定のパスとファイルのパスがここで食い違う。

    プラットフォームで分けない。WSL から Windows 側の置き場を指す形があり、
    そこでも守れるほうがよい。継ぎ足したパスは在るものだけを採るので、
    推測で別のファイルを実体として扱うことにはならない。

    どちらも無ければ書かれたまま返す。ここで空にすると、パスを間違えた設定が
    「実行ファイルを守らない設定」と見分けられなくなる。無いことは selfguard が
    missing と言い、パスを確かめるよう促す。
    """
    full = _resolve(root, path)
    if os.path.exists(full):
        return full
    for suffix in BIN_SUFFIXES:
        if not full.endswith(suffix) and os.path.exists(full + suffix):
            return full + suffix
    return full


def integration_local(root: str) -> str:
    """`.claude/settings.local.json` の `env` に書かれた統合先の名前。

    環境変数は読まない。sh が環境変数を先に見て、空のときにこれを使う。ユーザが端末で打つ sh には
    settings.local.json の `env` が渡らないので、JSON を読む役をここが持つ（sh は jq を使わない）。
    ファイルが無い・読めない・値が文字列でないときは空を返す（既定の統合先に落ちる）。
    """
    try:
        with open(os.path.join(root, LOCAL_CLAUDE_SETTINGS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    env = data.get("env") if isinstance(data, dict) else None
    value = env.get(INTEGRATION_ENV) if isinstance(env, dict) else None
    if not isinstance(value, str):
        return ""
    # 1 行で返す契約（sh は jq を使わず 1 行 1 項目で読む）。改行を含む値は使えないので空に落とす。
    return "" if ("\n" in value or "\r" in value) else value.strip()


def integration_recorded(state: str) -> str:
    """`ccnavi-sync.sh` が統合先の取り込み結果に書いた統合先の名前。

    `<state の置き場>/sync/self/integration/head` の `branch` の行（1 行 1 項目）。
    取り込み結果が無い・読めない・途中にシンボリックリンクがあるときは空（既定の予約だけになる）。
    環境変数は読まない。取り込み結果の中のリンクは辿らない（コピーするときに落としてあり、
    読む側でも辿らない決まり）。
    """
    if not state:
        return ""
    path = state
    for part in ("sync", "self", "integration", "head"):
        path = os.path.join(path, part)
        if os.path.islink(path):
            return ""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.rstrip("\r\n").partition(" ")
                if key == "branch":
                    return value.strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return ""


def is_branch_prefix(word: str) -> bool:
    """親の識別子の先頭の語に使える形か（英小文字で始まる英小文字と数字。予約の名前でない）。"""
    return bool(_PREFIX.match(word or "")) and word not in _RESERVED_PREFIXES


def parse_branch_prefixes(text: str) -> tuple[tuple, tuple]:
    """先頭の語のリストを読む。答えは（使うリスト, 使えない語）。使える語が無ければ既定のリスト。"""
    words: list[str] = []
    rejected: list[str] = []
    for word in re.split(r"[\s,]+", text or ""):
        if not word:
            continue
        if is_branch_prefix(word):
            if word not in words:
                words.append(word)
        else:
            rejected.append(word)
    return (tuple(words) or DEFAULT_BRANCH_PREFIXES), tuple(rejected)


def branch_prefixes(root: str) -> tuple[tuple, tuple]:
    """親の識別子の先頭の語のリスト。答えは（使うリスト, 使えない語）。

    環境変数 `CCNAVI_BRANCH_PREFIXES` を先に見る（Claude Code が settings.json の `env` を渡す）。
    無ければ、ユーザが端末で打つときのために `.claude/settings.local.json`、`.claude/settings.json`
    の `env` の順に読む。どこにも無ければ既定のリスト。
    """
    if BRANCH_PREFIXES_ENV in os.environ:
        return parse_branch_prefixes(os.environ[BRANCH_PREFIXES_ENV])
    for rel in (LOCAL_CLAUDE_SETTINGS, SHARED_CLAUDE_SETTINGS):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        env = data.get("env") if isinstance(data, dict) else None
        value = env.get(BRANCH_PREFIXES_ENV) if isinstance(env, dict) else None
        if isinstance(value, str):
            return parse_branch_prefixes(value)
    return DEFAULT_BRANCH_PREFIXES, ()
