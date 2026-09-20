"""テストの共通の置き場。"""

import atexit as _atexit
import os
import shutil as _shutil
import tempfile as _tempfile

from ccnavi import settings as _settings

# リポジトリの根。テストはグループのサブパッケージにあり、深さが揃わないのでここで 1 回だけ求める。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 共通層の 3 本の既定の綴り。ハーネスはここへ設定を置き、`--rules` / `--phases` /
# `--risk` は渡さない。3 つは診断（`--lint` / `--test` / `--explain`）でだけ効くので、
# hook の判定とチケット・レビューの副命令には届かない（ADR-0067）。
#
# 綴りは実行ファイルから引く。テスト側にもう 1 つ綴りを持つと、既定が動いたときに
# 2 つが黙ってずれる。既定の綴りそのものは tests/config/test_common_layer_place.py が
# 直に書いて見張る。
_COMMON_FILES = {
    "rules": _settings.DEFAULT_RULES,
    "phases": _settings.DEFAULT_PHASES,
    "risk": _settings.DEFAULT_RISK,
}


_FIXTURE_WORKSPACES: dict[str, str] = {}


def common_relpath(kind: str) -> str:
    """共通層のファイル（rules / phases / risk）の、ワークスペースルートからの相対の綴り。"""
    return _COMMON_FILES[kind]


def common_path(root: str, kind: str) -> str:
    """そのワークスペースルートの共通層のファイル（rules / phases / risk）の綴り。"""
    return os.path.join(root, common_relpath(kind))


def fixture_workspace(name: str = "rules.yml") -> str:
    """`tests/fixtures/<name>` を共通層のルールに据えたワークスペースルート。

    受入テストは `--rules` で見本のルールを指していたが、あれは診断でだけ効く
    （ADR-0067）。`--root` にここを渡して、共通層の既定の置き場から読ませる。

    リポジトリ自身をルートにしていたのをやめる利点もある。走った機械の
    `.ccnavi/common/rules.yml` が判定に混ざらない。

    組むのは見本ごとにプロセスで 1 度。中身は読むだけなので使い回してよい。
    """
    if name not in _FIXTURE_WORKSPACES:
        ws = _tempfile.mkdtemp(prefix="ccnavi-fixture-ws-")
        _atexit.register(_shutil.rmtree, ws, ignore_errors=True)
        target = common_path(ws, "rules")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        _shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", name), target)
        _FIXTURE_WORKSPACES[name] = ws
    return _FIXTURE_WORKSPACES[name]
