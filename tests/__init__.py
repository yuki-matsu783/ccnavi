"""テストの共通の置き場。"""

import atexit as _atexit
import contextlib as _contextlib
import glob as _glob
import os
import shutil as _shutil
import tempfile as _tempfile

# リポジトリの根。テストはグループのサブパッケージにあり、深さが揃わないのでここで 1 回だけ求める。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 使い捨ての置き場は、シンボリックリンクを解いた実パスにそろえる。macOS の一時置き場は
# `/var/...` だが実体は `/private/var/...` で、git や realpath は後者を返す。置き場の
# 文字列をそのまま比べるテストが、機械の違いだけで食い違う。
# 他の OS では実パスと同じで、何も変わらない。
_tempfile.tempdir = os.path.realpath(_tempfile.gettempdir())


def _drop_ccnavi_from_path() -> None:
    """走らせた人の PATH にある ccnavi を、テストから見えなくする。

    振り分けの sh と自己防衛は、bin/ に実体が無いと PATH の ccnavi を使う。PATH に ccnavi を
    通している機械では、「実体が無い」ことを見るテストの答えが、どの機械で走らせるかで変わる。
    PATH を使うテストは、自分で置き場を作って PATH に足す。

    ccnavi があるディレクトリは、ccnavi 以外の中身（`uv` や `git` など）を残すため、
    それらへのリンクだけを並べた使い捨てのディレクトリに置き換える。
    """
    kept = []
    for place in os.environ.get("PATH", "").split(os.pathsep):
        names = ("ccnavi", "ccnavi.exe")
        if not place or not any(os.path.isfile(os.path.join(place, n)) for n in names):
            kept.append(place)
            continue
        shim = _tempfile.mkdtemp(prefix="ccnavi-path-shim-")
        _atexit.register(_shutil.rmtree, shim, ignore_errors=True)
        for entry in os.listdir(place):
            if entry in names:
                continue
            with _contextlib.suppress(OSError):
                os.symlink(os.path.join(place, entry), os.path.join(shim, entry))
        kept.append(shim)
    os.environ["PATH"] = os.pathsep.join(kept)


_drop_ccnavi_from_path()

# 共通レイヤーの 3 本の既定のパス。ハーネスはここへ設定を置き、`--rules` / `--phases` /
# `--risk` は渡さない。3 つは診断（`--lint` / `--test` / `--explain`）でだけ有効なので、
# hook の判定とチケット・レビューのサブコマンドには届かない。
#
# 置き場の名前はテストの側に持つ。既定が動いたら、ここを置いた木で実行ファイルが設定を
# 読まなくなり、設定を前提にしたテストが落ちて気づく。
_PROJECT_HOME = ".ccnavi"
_LAYER_FILE_NAMES = {"rules": "rules.yml", "phases": "phases.yml", "risk": "risks.yml"}


def _block_host_git_config() -> dict[str, str]:
    """走った機械の git の設定を、テストが起こす git から締め出す。

    git は `~/.gitconfig` と `/etc/gitconfig` を読む。そこに何が入っているかは
    機械ごとに違うので、締め出さないとテストの結果が「誰の機械で走らせたか」で
    変わる。2 つとも実際に問題が起きている。

    - `init.defaultBranch = main` を持つ機械では `tests/guard/test_post.py` が失敗する。
      あのテストは `git init`（`-b` 無し）が `master` を作る前提で `checkout master`
      する。既定を動かしている機械では `master` が無い
    - `commit.gpgsign = true` を持つ機械では commit 1 回が 8 ミリ秒から 90 ミリ秒に
      なる。テストは 1,000 回以上 commit するので、署名の有無だけで全体が倍になる

    どちらも「テストが緩む」ではなく「テストの答えが機械で変わる」問題で、
    同じチケットがどの環境でも同じ場所で止まるという設計（regex も大文字小文字を
    区別せずに適用し、機械で答えを割らない。go/internal/tickets/model/ticket）と
    合わない。

    空の設定ではなく `init.defaultBranch` を書いた設定を指すのは、git 自身の
    既定に頼らないため。git は既定を `master` から動かすと予告し続けており、
    書いておかないと git を上げた日にテストの前提が気づかないうちに変わる。値は、今の
    テストが前提にしている `master` に固定する。

    もう 1 つ、グローバルの無視ファイル（`$XDG_CONFIG_HOME/git/ignore`、既定は
    `~/.config/git/ignore`）も機械ごとに違う。これは `GIT_CONFIG_GLOBAL` では塞がらず、
    `core.excludesFile` を書かないかぎり git が読む。Claude Code は
    `**/.claude/settings.local.json` をここへ書くので、入っている機械ではテストのリポジトリの
    `git add -A` がその設定ファイルを拾わず、追跡されない（Linux の CI には無いので、
    #607 まで macOS の開発機でだけ `tests.guard.test_named_guards_post` が 27 件落ちていた）。
    空のファイルを指して塞ぐ。

    `/dev/null` を指さないのは Windows に無いため。実際の空ファイルなら 4 環境で同じ。
    `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` は git 2.32 以降。読めているかは
    `tests/core/test_git_env.py` が確かめるので、古い git では
    気づかれないまま通ることはなく失敗する。
    """
    home = _tempfile.mkdtemp(prefix="ccnavi-gitconfig-")
    _atexit.register(_shutil.rmtree, home, ignore_errors=True)
    path = os.path.join(home, "config")
    excludes = os.path.join(home, "excludes")
    open(excludes, "w", encoding="utf-8").close()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "[init]\n\tdefaultBranch = master\n"
            f"[core]\n\texcludesFile = {excludes.replace(os.sep, '/')}\n"
        )
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
# （`ccnavi-git.sh` など）も、ccnavi 自身（`go/internal/infra/git`）も git を起こす。
# 引数に `-c` を足す形では、自分が直に起こす分しか防げない。
GIT_ENV = _block_host_git_config()
os.environ.update(GIT_ENV)

# Git for Windows の sh（MSYS）は、ネイティブの exe に渡す引数のうち `/` を含み `:` で
# 区切られたものをパスの一覧とみなして Windows 形式に書き換える
# （`refs/remotes/origin/main:.claude/x` が `refs\remotes\origin\main;.claude\x` になり、
# git が object を解けない）。sh の中で `git cat-file -e <ref>:<path>` を打つ検査対象の sh が
# テストの中では本来の動きをするよう、書き換えを止める。POSIX では何も起きない環境変数。
#
# 全部を止める（`*`）と、sh が `/tmp/...` で渡すパスを git が読めなくなる（書き換えは
# 必要な場面もある）。止めるのは、`<ref>:<path>` の形になる ref の頭（`refs/`・`origin/`）だけ。
os.environ.setdefault("MSYS2_ARG_CONV_EXCL", "refs/;origin/")


def config_path(root: str, kind: str) -> str:
    """ワークスペースルートの config（自身のレイヤー）のファイル（rules / phases / risk）のパス。

    フェーズ定義は共通レイヤーと config の両方に置け、判定は 2 本の和を読む。
    """
    return os.path.join(root, _PROJECT_HOME, "config", "project", _LAYER_FILE_NAMES[kind])


# 保護済み sh の置き場。
SH_SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")


def common_sh(scripts_dir: str = SH_SCRIPTS) -> tuple[str, ...]:
    """保護済み sh が起動して最初に `.` で読む共通部のファイル名。

    入口の `ccnavi-common.sh` と、入口が同じディレクトリから読む部品（`ccnavi-common-*.sh`）。
    sh を使い捨ての木へコピーするテストは、名前を並べずにこれで全部を一緒にコピーする。
    部品は入口が `$0` のディレクトリから読むので、1 本でも欠けると sh は起動の段階で失敗する。
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


# このリポジトリの文面ファイルの置き場。
MESSAGES = os.path.join(ROOT, ".ccnavi", "messages")


def put_messages(root: str) -> str:
    """作業ディレクトリに、このリポジトリの文面ファイル（`.ccnavi/messages/`）をコピーする。

    文面は文面ファイルが持つので、文を見るテストの使い捨てのワークスペースには要る。
    無くても判定は変わらず、文が短い既定の文に落ちる。
    """
    target = os.path.join(root, ".ccnavi", "messages")
    _shutil.copytree(MESSAGES, target, dirs_exist_ok=True)
    return target
