"""使い捨てのリポジトリの事前状態・戻し・排他（段 2）。

setup.git / setup.reset / setup.files の追記が触れる唯一の対象を扱う。
"""

import contextlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ccnavi.infra import pathcase
from tests.scenarios import hostenv, schema, workspace
from tests.scenarios.common import DISPOSABLE_REL, RUNNER, Refusal, log

LOCK_NAME = "ccnavi-scenario.lock"  # 対象の .git/ の中。git clean で消えず、status も汚さない


LEFT_NAME = "ccnavi-scenario.left"  # 戻さずに残した回の印（中身は出力の置き場）


def _git_env():
    """cwd で対象を決めさせるため、git の向き先を変える環境変数を外す。"""
    drop = (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CONFIG_PARAMETERS",
    )
    return {k: v for k, v in os.environ.items() if k not in drop}


def git(args, cwd, input=None, env=None):
    """env は、この git にだけ足す環境（docker-gitlab のトークン）。"""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        env={**_git_env(), **(env or {})},
        input=input,
        stdin=subprocess.DEVNULL if input is None else None,
    )


def _toplevel(path):
    """path が属するリポジトリの toplevel の実体。リポジトリでない・path が無いときは None。"""
    try:
        r = git(["rev-parse", "--show-toplevel"], cwd=path)
    except OSError:
        return None
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def _common_dir(path):
    """リンクされたワークツリーも同じになる、リポジトリ共通の .git の実体（比べる形）。"""
    r = git(["rev-parse", "--git-common-dir"], cwd=path)
    return pathcase.fold(os.path.realpath(os.path.join(path, r.stdout.strip())))


def _origin(top):
    r = git(["remote", "get-url", "origin"], cwd=top)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return pathcase.fold(r.stdout.strip().rstrip("/").removesuffix(".git"))


def _inside(path, top):
    top = pathcase.fold(top).rstrip(os.sep)
    return pathcase.fold(path).startswith(top + os.sep)


def require_disposable_repo(root, target, allowed=None):
    """target が使い捨てのリポジトリだと確かめ、その toplevel（実体）を返す。違えば Refusal。

    setup.git・setup.reset・setup.files の追記は、すべてこれを通る。比べるのは toplevel の実体で、
    ccnavi 本体（root）と同じもの、origin が本体と同じものは断る。許可されるのは allowed
    （既定は root/projects/test-repo）の実体だけ。どちらかに origin が無いときは origin を比べない。
    """
    allowed = Path(allowed) if allowed is not None else Path(root) / DISPOSABLE_REL
    use = "setup.git・setup.reset・setup.files の追記は、使い捨てのリポジトリにだけ使える"
    top = _toplevel(target)
    if top is None:
        raise Refusal(
            f"対象 {target} は git のリポジトリではない（または無い）",
            f"{DISPOSABLE_REL} を用意してから打ち直す",
            use,
        )
    body = os.path.realpath(root)
    if pathcase.fold(top) == pathcase.fold(body):
        raise Refusal(
            f"対象が ccnavi 本体（ワークスペースルート）になっている: {top}",
            f"対象を使い捨てのリポジトリ（{DISPOSABLE_REL}）にして打ち直す",
            use,
        )
    if _common_dir(top) == _common_dir(body):
        raise Refusal(
            f"対象が ccnavi 本体と同じリポジトリのワークツリーになっている: {top}",
            f"本体とは別のリポジトリを {DISPOSABLE_REL} に置いて打ち直す",
            use,
        )
    mine, theirs = _origin(top), _origin(body)
    if mine is not None and mine == theirs:
        raise Refusal(
            f"対象の origin が ccnavi 本体の origin と同じ: {top}",
            f"本体とは別のリポジトリを {DISPOSABLE_REL} に置いて打ち直す",
            use,
        )
    want = os.path.realpath(allowed)
    if pathcase.fold(top) != pathcase.fold(want):
        raise Refusal(
            f"対象の実体 {top} が、許可された {want} と違う",
            f"対象を {DISPOSABLE_REL} にして打ち直す",
            use,
        )
    return top


def _git_dir(top):
    r = git(["rev-parse", "--absolute-git-dir"], cwd=top)
    if r.returncode != 0:
        raise RuntimeError(f".git の場所を取れない: {top}: {r.stderr.strip()}")
    return Path(os.path.realpath(r.stdout.strip()))


def _first_line(text):
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def target_git(root, top, args, level="info", env=None):
    """対象で git を実行する（cwd は判定を通した toplevel）。コマンドと結果を診断ログに残す。
    level は、reset のように何度も呼ぶときだけ debug にする（ループの中で INFO を書かない）。"""
    r = git(args, cwd=top, env=env)
    getattr(log(root), level)(
        "対象の git を実行した",
        cmd=shlex.join(["git", *args]),
        rc=r.returncode,
        out_len=len(r.stdout),
        err=_first_line(r.stderr) if r.returncode else "",
    )
    return r


def run_setup_git(root, target, commands, allowed=None, host=None):
    """setup.git のコマンドを、対象の toplevel を cwd にして順に実行する（shell=False）。
    各コマンドのあと ccnavi 本体の repo_snapshot が変わっていたら止める。
    host（setup.host の辞書）があれば、本物の origin への通信を bare へ向ける -c を足す。"""
    top = require_disposable_repo(root, target, allowed)
    before = workspace.repo_snapshot(root)
    via = hostenv.git_via(host) if host else []
    env = hostenv.git_env(host) if host else None
    for argv in commands:
        try:
            schema.check_git_argv(argv)
        except ValueError as exc:
            raise Refusal(f"setup.git を実行しない: {exc}", "シナリオの setup.git を直す") from None
        r = target_git(root, top, [*via, *argv[1:]], env=env)
        if r.returncode != 0:
            raise Refusal(
                f"setup.git が失敗した: {shlex.join(argv)}（終了コード {r.returncode}）",
                "原因を直して打ち直す",
                f"git の出力: {_first_line(r.stderr)}",
                "対象は後始末で戻す（setup.reset があるとき）",
            )
        if workspace.repo_snapshot(root) != before:
            raise Refusal(
                f"setup.git の実行で ccnavi 本体の HEAD・ブランチ・ワークツリーが変わった: "
                f"{shlex.join(argv)}",
                "ccnavi 本体の状態を手で確かめて戻し、シナリオの setup.git を直す",
                "回し続けない",
            )


CCNAVI_STATE_DIRS = ("ccnavi-direct", "ccnavi-integrate", "ccnavi-integrate-done")

CCNAVI_REF_PREFIX = "refs/ccnavi"


def _git_common_path(top):
    """対象の git の共通ディレクトリ（リンクされたワークツリーでも本体の .git）の実体。"""
    r = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=top)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"git の共通ディレクトリを取れない: {top}: {r.stderr.strip()}")
    return Path(os.path.realpath(r.stdout.strip()))


def _clear_ccnavi_state(root, top, run):
    """ccnavi 自身が対象の git に書く状態（直接作業の記録・取り込みの marker・専用の ref）を消す。
    top は require_disposable_repo を通したものだけ。消すのは共通ディレクトリ直下の名指しの 3 つと
    refs/ccnavi の下だけで、logs/ など .gitignore 済みのものは触らない。"""
    common_dir = _git_common_path(top)
    for name in CCNAVI_STATE_DIRS:
        path = common_dir / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for ref in run("for-each-ref", "--format=%(refname)", CCNAVI_REF_PREFIX).split():
        run("update-ref", "-d", ref)


def _clear_archive(root, top):
    """前の回の ready が logs/archive/<プロジェクト>/ へ退避した親子を消す。
    残すと、同じ識別子の親が「一度は送った形跡」になり、新しい origin への push のゲートが止まる。
    置き場はワークスペースルートの logs/ の下（git の外）で、そのプロジェクトの分だけを消す。
    リンクは辿らず、リンク自体を外す。"""
    path = Path(root) / "logs" / "archive" / Path(top).name
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def reset_repo(root, target, base, allowed=None):
    """対象を base へ戻す。順序: ワークツリーを消す → prune → ccnavi の記録・marker・ref を消す →
    logs/archive/<プロジェクト>/ を消す → main を base に合わせて切り替える →
    main 以外のローカルブランチを消す → タグを消す → stash を空にする → reset --hard → clean -fd。
    ccnavi の状態は、ワークツリーを消して prune したあとに消す。記録は一時ワークツリーや
    ブランチを指すので、ワークツリーが消せずに止まった回で、記録だけ先に消さない。
    clean に -x は付けない（.gitignore 済みを消さない）。refs/remotes は触らない。"""
    top = require_disposable_repo(root, target, allowed)

    def run(*args):
        r = target_git(root, top, list(args), level="debug")
        if r.returncode != 0:
            log(root).error("対象を戻せなかった", target=top, base=base, cmd=" ".join(args[:3]))
            raise Refusal(
                f"対象を戻す途中で失敗した: git {' '.join(args[:3])}（終了コード {r.returncode}）",
                f"{top} を手で {base} に戻す",
                f"git の出力: {_first_line(r.stderr)}",
            )
        return r.stdout

    run("cat-file", "-e", f"{base}^{{commit}}")
    paths = [
        line[len("worktree ") :]
        for line in run("worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    keep = {pathcase.fold(os.path.realpath(p)) for p in (paths[:1] + [top])}
    for p in paths:
        if pathcase.fold(os.path.realpath(p)) not in keep:
            run("worktree", "remove", "--force", p)
    run("worktree", "prune")
    _clear_ccnavi_state(root, top, run)
    _clear_archive(root, top)
    run("checkout", "-f", "-B", "main", base)
    for name in run("for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        if name != "main":
            run("branch", "-D", name)
    tags = run("tag", "-l").split()
    for i in range(0, len(tags), 100):
        run("tag", "-d", *tags[i : i + 100])
    run("stash", "clear")
    run("reset", "--hard", base)
    run("clean", "-fd")
    log(root).info("対象を base に戻した", target=top, base=base)


def _pid_alive(pid):
    """その PID のプロセスが生きているか。判定できない（権限など）ときは生きていると見なす。
    Windows の os.kill はプロセスを終わらせてしまうので、OpenProcess で見る。"""
    if os.name == "nt":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            k32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: その PID は無い
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _lock_pid(lock):
    try:
        pid = int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def acquire_target(top, out, resuming=False):
    """対象を排他で使い始める。ロックのパスを返す。使えなければ Refusal。

    ロックは対象の .git/ の中（O_CREAT|O_EXCL。中身は PID）。あれば PID が生きているかを見て、
    生きている・判定できないなら断り、死んでいれば奪う。戻さずに残した回の印があれば、
    その回を続けるとき（resuming で出力の置き場が同じ）以外は断る。"""
    git_dir = _git_dir(top)
    left = git_dir / LEFT_NAME
    if left.exists():
        who = left.read_text(encoding="utf-8").strip()
        same = bool(who) and (
            pathcase.fold(os.path.realpath(who)) == pathcase.fold(os.path.realpath(out))
        )
        if not (resuming and same):
            raise Refusal(
                f"前の回が対象を戻さずに残している: {who}",
                f"続けるなら `{RUNNER} next {who}`、やめるなら `{RUNNER} restore {who}`",
                "戻せずに残した回も同じ。restore で戻す",
            )
    lock = git_dir / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pid = _lock_pid(lock)
            if pid is None or _pid_alive(pid):
                raise Refusal(
                    f"同じ対象を別の回が使っている（PID {pid or '読めない'}）: {lock}",
                    "その回が終わってから打ち直す",
                    "ロックの中身が PID として読めないときは、使っている回が無いことを"
                    "確かめてからファイルを消す",
                ) from None
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()  # 持ち主が死んでいる。奪う
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return str(lock)
    raise Refusal(f"ロックを奪えなかった: {lock}", "少し待って打ち直す")


def release_target(st, root, hold=False):
    """対象を放す。例外は出さない。戻せなかったときは文面を出し、その文面を返す。

    hold: 人間の操作で止めた回。base へ戻さず、ロックだけ放して残し印を書く（続きは next）。
    それ以外: base へ戻し、ロックと残し印を放す。戻せなければ残し印を書き、次の回を断らせる。"""
    tgt = st.get("target")
    if not tgt or st.get("released"):
        return None
    top, base, out = tgt["path"], tgt.get("base"), st.get("out", "")
    err = None
    try:
        git_dir = _git_dir(top)
        left = git_dir / LEFT_NAME
        if not hold and base:
            try:
                reset_repo(root, top, base)
            except Exception as exc:  # 戻せない原因は何でも、手作業の案内にする
                err = str(exc) or repr(exc)
        if hold or err:
            left.write_text(os.path.realpath(out) if out else out, encoding="utf-8")
        else:
            with contextlib.suppress(FileNotFoundError):
                left.unlink()
        with contextlib.suppress(FileNotFoundError):
            (git_dir / LOCK_NAME).unlink()
    except Exception as exc:
        err = err or repr(exc)
    if hold:
        st["held"] = True
        return None
    if err:
        text = str(
            Refusal(
                f"対象 {top} を {base} に戻せなかった。手作業で戻す必要がある",
                f"{top} を手で {base} に戻し、`{RUNNER} restore {out}` で印を外す",
                f"原因: {_first_line(err)}",
                "戻すまで、次の回は始められない",
            )
        )
        print(text)
        return text
    st["released"] = True
    return None


def resolve_append(rel, root, toplevel):
    """追記の対象（ワークスペースルートからの相対）を実体にして返す。
    toplevel の中にある、git 管理下のファイルだけ。.. ・絶対パス・外へ出るリンクは断る。"""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise Refusal(
            f"追記のパスは、ワークスペースルートからの相対パスで .. を含まない形にする: {rel}",
            "setup.files のパスを直す",
        )
    real = os.path.realpath(Path(root) / p)
    if not _inside(real, toplevel):
        raise Refusal(
            f"追記のパスが対象 {toplevel} の外にある（外へ出るリンクを含む）: {rel}",
            "setup.files のパスを、対象の中のファイルにする",
        )
    if not os.path.isfile(real):
        raise Refusal(
            f"追記の対象のファイルが無い: {rel}",
            "追記は対象にすでにあるファイルにだけ使える。パスを直すか、作って消す形（文字列）にする",
        )
    rel_in_top = os.path.relpath(real, toplevel)
    r = git(["--literal-pathspecs", "ls-files", "--error-unmatch", "--", rel_in_top], cwd=toplevel)
    if r.returncode != 0:
        raise Refusal(
            f"追記の対象が git 管理下にない: {rel}",
            "追記は git 管理下のファイルにだけ使える。パスを直す",
        )
    return real
