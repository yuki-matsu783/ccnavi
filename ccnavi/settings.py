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
from dataclasses import dataclass

# ccnavi が読む環境変数。
MODE_ENV = "CCNAVI_MODE"
RULES_ENV = "CCNAVI_RULES"
LOG_ENV = "CCNAVI_LOG"

# own_project は ccnavi 自身のソースツリーを見分ける印。own_source_tree を参照。
OWN_PROJECT = "ccnavi"

# LOCAL_FILE は ccnavi 自身を開発しているときだけ読む上書き設定。
# コミットしないので、他のプロジェクトには存在せず、
# 直しても影響が及ぶのは道具を試している本人だけになる。
LOCAL_FILE = "ccnavi.settings.local.json"

# 既定の置き場。プロジェクト根からの相対。
DEFAULT_LOG = os.path.join(".claude", "ccnavi", "log.jsonl")
DEFAULT_RULES = os.path.join(".claude", "ccnavi", "rules.json")


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
    )

    rules_env = os.environ.get(RULES_ENV, "")
    if rules_env:
        settings.rules = _resolve(root, rules_env)
    if LOG_ENV in os.environ:
        settings.log = _log_or_none(root, os.environ[LOG_ENV])

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

    return settings, problems


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
