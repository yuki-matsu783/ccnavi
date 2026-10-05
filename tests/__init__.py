"""テストの共通の置き場。"""

import atexit as _atexit
import glob as _glob
import os
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile

# リポジトリの根。テストはグループのサブパッケージにあり、深さが揃わないのでここで 1 回だけ求める。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ccnavi パッケージの置き場（src レイアウト）。パッケージとして入れていない Python で回しても
# このツリーのソースを読むよう、先頭に足す。子プロセスで `-m ccnavi` を起こすときは
# PYTHONPATH に渡す。
SRC = os.path.join(ROOT, "src")
if SRC not in _sys.path:
    _sys.path.insert(0, SRC)

from ccnavi.infra import settings as _settings  # noqa: E402

# 共通レイヤーの 3 本の既定のパス。ハーネスはここへ設定を置き、`--rules` / `--phases` /
# `--risk` は渡さない。3 つは診断（`--lint` / `--test` / `--explain`）でだけ有効なので、
# hook の判定とチケット・レビューの副命令には届かない。
#
# パスは実行ファイルから引く。テスト側にもう 1 つパスを持つと、既定が動いたときに
# 2 つが気づかないうちに食い違う。既定のパスそのものは tests/config/test_common_layer_place.py が
# 直に書いて確かめる。
_COMMON_FILES = {
    "rules": _settings.DEFAULT_RULES,
    "phases": _settings.DEFAULT_PHASES,
    "risk": _settings.DEFAULT_RISK,
}


def _block_host_git_config() -> dict[str, str]:
    """走った機械の git の設定を、テストが起こす git から締め出す。

    git は `~/.gitconfig` と `/etc/gitconfig` を読む。そこに何が入っているかは
    機械ごとに違うので、締め出さないとテストの結果が「誰の機械で走らせたか」で
    変わる。2 つとも実際に問題が起きている。

    - `init.defaultBranch = main` を持つ機械では `tests/guard/test_post.py` が落ちる。
      あのテストは `git init`（`-b` 無し）が `master` を作る前提で `checkout master`
      する。既定を動かしている機械では `master` が無い
    - `commit.gpgsign = true` を持つ機械では commit 1 回が 8 ミリ秒から 90 ミリ秒に
      なる。テストは 1,000 回以上 commit するので、署名の有無だけで全体が倍になる

    どちらも「テストが緩む」ではなく「テストの答えが機械で変わる」問題で、
    同じチケットがどの環境でも同じ場所で止まるという設計（regex も大文字小文字を
    区別せずに当て、機械で答えを割らない。ticket.py）と
    合わない。

    空の設定ではなく `init.defaultBranch` を書いた設定を指すのは、git 自身の
    既定に頼らないため。git は既定を `master` から動かすと予告し続けており、
    書いておかないと git を上げた日にテストの前提が気づかないうちに変わる。値は、今の
    テストが前提にしている `master` に固定する。

    `/dev/null` を指さないのは Windows に無いため。本物の空ファイルなら 4 環境で同じ。
    `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` は git 2.32 以降。読めているかは
    `tests/core/test_git_env.py` が確かめるので、古い git では気づかれないまま通ることはなく落ちる。
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


# テストが起こす git に見せる環境。`tests/` を import した時点で反映される。
#
# ここで `os.environ` に入れるのは、テストが git を起こす経路が 1 つではないため。
# 各テストの `git()` ヘルパ（13 か所ある）だけでなく、検査対象の sh
# （`ccnavi-git.sh` など）も、ccnavi 自身（`src/ccnavi/infra/gitcmd.py`）も git を起こす。
# 引数に `-c` を足す形では、自分が直に起こす分しか防げない。
GIT_ENV = _block_host_git_config()
os.environ.update(GIT_ENV)


_FIXTURE_WORKSPACES: dict[str, str] = {}


def common_relpath(kind: str) -> str:
    """共通レイヤーのファイル（rules / phases / risk）の、ワークスペースルートからの相対パス。"""
    return _COMMON_FILES[kind]


def common_path(root: str, kind: str) -> str:
    """そのワークスペースルートの共通レイヤーのファイル（rules / phases / risk）のパス。"""
    return os.path.join(root, common_relpath(kind))


def fixture_workspace(name: str = "rules.yml") -> str:
    """`tests/fixtures/<name>` を共通レイヤーのルールに据えたワークスペースルート。

    `--rules` は診断でだけ有効なので、見本のルールを指すのには使わない。
    `--root` にここを渡して、共通レイヤーの既定の置き場から読ませる。

    リポジトリ自身をルートにしないので、走った機械の `.ccnavi/common/rules.yml` が
    判定に入り込まない。

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


# 保護済み sh の置き場。
SH_SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")


def common_sh(scripts_dir: str = SH_SCRIPTS) -> tuple[str, ...]:
    """保護済み sh が起動して最初に `.` で読む共通部のファイル名。

    入口の `ccnavi-common.sh` と、入口が同じディレクトリから読む部品（`ccnavi-common-*.sh`）。
    sh を使い捨ての木へ写すテストは、名前を並べずにこれで全部を一緒に写す。
    部品は入口が `$0` のディレクトリから読むので、1 本でも欠けると sh は起動の段で落ちる。
    共通部が 1 本のままでも、部品に分かれていても同じ書き方で済む。
    """
    names = tuple(
        sorted(
            os.path.basename(path)
            for path in _glob.glob(os.path.join(scripts_dir, "ccnavi-common*.sh"))
        )
    )
    if "ccnavi-common.sh" not in names:
        raise FileNotFoundError(os.path.join(scripts_dir, "ccnavi-common.sh"))
    return names
