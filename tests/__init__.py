"""テストの共通の置き場。"""

import atexit as _atexit
import contextlib as _contextlib
import glob as _glob
import os
import shutil as _shutil
import subprocess as _subprocess
import tempfile as _tempfile
import unittest as _unittest

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
_COMMON_FILES = {
    kind: os.path.join(_PROJECT_HOME, "config", "common", name)
    for kind, name in _LAYER_FILE_NAMES.items()
}


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


_FIXTURE_WORKSPACES: dict[str, str] = {}


def common_relpath(kind: str) -> str:
    """共通レイヤーのファイル（rules / phases / risk）の、ワークスペースルートからの相対パス。"""
    return _COMMON_FILES[kind]


def common_path(root: str, kind: str) -> str:
    """そのワークスペースルートの共通レイヤーのファイル（rules / phases / risk）のパス。"""
    return os.path.join(root, common_relpath(kind))


def config_path(root: str, kind: str) -> str:
    """ワークスペースルートの config（自身のレイヤー）のファイル（rules / phases / risk）のパス。

    フェーズ定義は共通レイヤーと config の両方に置け、判定は 2 本の和を読む。
    """
    return os.path.join(root, _PROJECT_HOME, "config", "project", _LAYER_FILE_NAMES[kind])


def fixture_workspace(name: str = "rules.yml") -> str:
    """`tests/fixtures/<name>` を共通レイヤーのルールに据えたワークスペースルート。

    `--rules` は診断でだけ有効なので、見本のルールを指すのには使わない。
    `--root` にここを渡して、共通レイヤーの既定の置き場から読ませる。

    リポジトリ自身をルートにしないので、走った機械の `.ccnavi/config/common/rules.yml` が
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


def can_symlink() -> bool:
    """シンボリックリンクを実際に作れるか。Windows は特権が無いと作れない。

    OS の名前では決めず、使い捨ての置き場で 1 本作って確かめる。作れない環境では、
    リンクを辿らないことを見るテストは成り立たないので、`requires_symlink` で skip する。
    """
    with _tempfile.TemporaryDirectory(prefix="ccnavi-symlink-probe-") as tmp:
        try:
            os.symlink(tmp, os.path.join(tmp, "link"))
        except (OSError, NotImplementedError):
            return False
    return True


def can_case_sensitive() -> bool:
    """大文字小文字だけが違う名前を、別のファイルとして並べて作れるか。

    macOS（既定）と Windows のファイルシステムは区別しないので、`a` と `A` が同じ 1 本になる。
    OS の名前では決めず、使い捨ての置き場で実際に 2 本作って確かめる。
    """
    with _tempfile.TemporaryDirectory(prefix="ccnavi-case-probe-") as tmp:
        try:
            for name in ("a", "A"):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write(name)
            return len(os.listdir(tmp)) == 2
        except OSError:
            return False


def can_pass_argument(size: int) -> bool:
    """sh から jq へ、`size` 字の引数を渡せるか。実際に渡して確かめる。

    取ってきた状態は `jq --argjson` の引数で渡すので、1 引数の上限（Windows は約 32,000 字）を
    超える場面は、上限のある環境では取れない。sh か jq が無ければ、確かめずに True を返す
    （そちらは別の skip が受ける）。
    """
    sh = _shutil.which("sh") or _shutil.which("bash")
    if sh is None or _shutil.which("jq") is None:
        return True
    done = _subprocess.run(
        [sh, "-c", "a=$(cat); jq -n --arg a \"$a\" '$a|length'"],
        input="x" * size,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0 and done.stdout.strip() == str(size)


def live_sh_pid(test) -> int:
    """sh の `kill -0` から生きて見えるプロセスの pid。テストごとに 1 つ起こして使い回す。

    ロックの持ち主を「生きているプロセス」にしたいテストは、自分の `os.getpid()` を書いていた。
    Windows の Python の pid は Git Bash（MSYS）の pid の表に無く、sh からは死んで見える。
    sh 自身に起こさせた sleep の pid（sh の `$$`）なら、どこでも sh から生きて見える。
    """
    pid = getattr(test, "_live_sh_pid", None)
    if pid is None:
        sh = _shutil.which("sh") or _shutil.which("bash")
        proc = _subprocess.Popen(
            [sh, "-c", "echo $$; exec sleep 600"],
            stdout=_subprocess.PIPE,
            stderr=_subprocess.DEVNULL,
            text=True,
        )
        test.addCleanup(proc.wait)
        test.addCleanup(proc.kill)
        pid = int(proc.stdout.readline())
        test._live_sh_pid = pid
    return pid


requires_symlink = _unittest.skipUnless(can_symlink(), "シンボリックリンクを作れない")


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


def put_ccnavi_md(root: str) -> str:
    """作業ディレクトリに、このリポジトリの `.ccnavi/CCNAVI.md` をコピーする。

    セッション開始で渡す作業の決まりの原本。
    """
    target = os.path.join(root, ".ccnavi", "CCNAVI.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # 保護対象の下書き（wip/handoff/）があれば、取り込んだ後の姿として下書きを読む。
    draft = os.path.join(ROOT, "wip", "handoff", ".ccnavi", "CCNAVI.md")
    source = draft if os.path.isfile(draft) else os.path.join(ROOT, ".ccnavi", "CCNAVI.md")
    _shutil.copyfile(source, target)
    return target


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


# 文面に置くパスを引用せずに書ける字（実行ファイルの案内と同じ決め方）。
_BARE_PATH = r"[^\s'\"\\$`!*?\[\]{}()<>|&;#~]+"
_SPECIAL_IN_DOUBLE_QUOTES = r'["\\$`!]'


def _quoted(path: str) -> str:
    """要るときだけ引用する。まず `"…"`、`"` の中でも意味を持つ字があるときは `'…'`。"""
    import re
    import shlex

    if re.fullmatch(_BARE_PATH, path):
        return path
    if not re.search(_SPECIAL_IN_DOUBLE_QUOTES, path):
        return f'"{path}"'
    return shlex.quote(path)


def script_command(root: str, name: str) -> str:
    """文面が案内する `.ccnavi/scripts/` の sh の打ち方（`sh <ルートの実パス>/.ccnavi/scripts/<name>`）。

    区切りは `/`。空白やシェルの記号を含むときだけ引用する。実行ファイルの書き方と食い違えば、
    案内の文を比べるテストが落ちる。
    """
    base = os.path.realpath(root).replace("\\", "/").rstrip("/")
    return f"sh {_quoted(f'{base}/{_PROJECT_HOME}/scripts/{name}')}"


def shell_path(path: str) -> str:
    """文面が `cd <パス> && …` の形で案内するパス。区切りは Windows のパスだけ `/` に揃える。"""
    import re

    if os.name == "nt" or re.match(r"[A-Za-z]:\\", path):
        path = path.replace("\\", "/")
    return _quoted(path)
