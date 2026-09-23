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


def _block_host_git_config() -> dict[str, str]:
    """走った機械の git の設定を、テストが起こす git から締め出す。

    git は `~/.gitconfig` と `/etc/gitconfig` を読む。そこに何が入っているかは
    機械ごとに違うので、締め出さないとテストの結果が「誰の機械で走らせたか」で
    変わる。実害は 2 つとも出ている。

    - `init.defaultBranch = main` を持つ機械では `tests/guard/test_post.py` が落ちる。
      あのテストは `git init`（`-b` 無し）が `master` を作る前提で `checkout master`
      する。既定を動かしている機械では `master` が無い
    - `commit.gpgsign = true` を持つ機械では commit 1 回が 8 ミリ秒から 90 ミリ秒に
      なる。テストは 1,000 回以上 commit するので、署名の有無だけで全体が倍になる

    どちらも「テストが緩む」ではなく「テストの答えが機械で変わる」問題で、
    同じチケットがどの環境でも同じ場所で止まるという設計（ADR-0051、ticket.py）と
    合わない。

    空の設定ではなく `init.defaultBranch` を書いた設定を指すのは、git 自身の
    既定に寄りかからないため。git は既定を `master` から動かすと予告し続けており、
    書いておかないと git を上げた日にテストの前提が黙って変わる。値は、今の
    テストが前提にしている `master` に固定する。

    `/dev/null` を指さないのは Windows に無いため。本物の空ファイルなら 4 環境で同じ。
    `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` は git 2.32 以降。読めているかは
    `tests/core/test_git_env.py` が見張るので、古い git では黙って通らずに落ちる。
    """
    home = _tempfile.mkdtemp(prefix="ccnavi-gitconfig-")
    _atexit.register(_shutil.rmtree, home, ignore_errors=True)
    path = os.path.join(home, "config")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("[init]\n\tdefaultBranch = master\n")
    return {
        "GIT_CONFIG_GLOBAL": path,
        "GIT_CONFIG_SYSTEM": path,
        # 2.32 より前の git は GIT_CONFIG_SYSTEM を知らない。こちらは昔からある。
        "GIT_CONFIG_NOSYSTEM": "1",
    }


# テストが起こす git に見せる環境。`tests/` を import した時点で効く。
#
# ここで `os.environ` に入れるのは、テストが git を起こす道が 1 つではないため。
# 各テストの `git()` ヘルパ（13 か所ある）だけでなく、検査対象の sh
# （`ccnavi-git.sh` など）も、ccnavi 自身（`ccnavi/gitcmd.py`）も git を起こす。
# 引数に `-c` を足す形では、自分が直に起こす分しか塞げない。
GIT_ENV = _block_host_git_config()
os.environ.update(GIT_ENV)


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
