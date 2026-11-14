"""回す前の準備と後片付け（段 3）。

setup のファイル、回す前の検査、setup.host の相手の用意と記録。
"""

import contextlib
from pathlib import Path

from tests.scenarios import hostenv, hostsnap, repo, schema
from tests.scenarios.common import Refusal, log, write_state


def check_before_run(scn, root, top):
    """回す前（確認文より前）に確かめられる setup の誤りを、Refusal で返す。何も変えない。
    追記の対象は reset のあとにもう一度確かめる（base の中身で変わりうるため）。"""
    setup = scn.get("setup") or {}
    for rel, v in (setup.get("files") or {}).items():
        if schema.file_spec(v)[1]:
            repo.resolve_append(rel, root, top)
    base = (setup.get("reset") or {}).get("base")
    if base and repo.git(["cat-file", "-e", f"{base}^{{commit}}"], cwd=top).returncode != 0:
        raise Refusal(
            f"setup.reset.base {base} が対象 {top} にコミットとして無い",
            "base を、対象にあるコミットの 40 桁の SHA に直して打ち直す",
            "対象には何もしていない",
        )
    if schema.scenario_host(scn) == hostenv.DOCKER:
        try:
            hostenv.check_docker_gitlab()
        except (RuntimeError, OSError) as exc:
            raise Refusal(
                f"docker-gitlab の GitLab を使えない: {exc}",
                "`uv run python tests/manual/gitlab/docker_gitlab.py up` で立て"
                "（動いていれば `status` で確かめる。.agents/docs/gitlab-manual.md）、打ち直す",
                "対象にも GitLab にも何もしていない",
            ) from None


def setup_clashes(scn, root):
    """setup.files のパスのうち、回せない原因になるもの。
    作る形（文字列）は、すでにあるもの（後片付けで消してしまうため）。
    追記の形（append: true）は、まだ無いもの（追記は対象にある管理下のファイルにだけ使う）。"""
    files = (scn.get("setup") or {}).get("files") or {}
    return [
        rel for rel, v in files.items() if (Path(root) / rel).exists() != schema.file_spec(v)[1]
    ]


def make_files(scn, root, toplevel=None):
    """setup のファイルを作り、追記する。(作ったファイル, 作ったディレクトリ) を返す。
    作る形は、既にあるパスには触らない（setup_clashes で先に確かめてある）。
    ディレクトリは、この関数が新しく作ったものだけ。追記は作ったものに数えない
    （戻すのは setup.reset。消すと管理下のファイルを削除してしまう）。
    追記の対象は、先に全部確かめてから（toplevel は判定を通した対象）、書き始める。"""
    files, dirs, appends = [], [], {}
    entries = {
        rel: schema.file_spec(v) for rel, v in ((scn.get("setup") or {}).get("files") or {}).items()
    }
    for rel, (body, append) in entries.items():
        if append:
            if toplevel is None:
                raise Refusal(
                    "追記の対象を決められない（対象の判定を通っていない）", "setup を直す"
                )
            appends[repo.resolve_append(rel, root, toplevel)] = body
    try:
        for rel, (body, append) in entries.items():
            if append:
                continue
            p = Path(root) / rel
            missing = []
            d = p.parent
            while d != Path(root) and not d.exists():
                missing.append(d)
                d = d.parent
            for d in reversed(missing):
                d.mkdir()
                dirs.append(str(d.relative_to(root)))
            p.write_text(body, encoding="utf-8")
            files.append(rel)
        for real, body in appends.items():
            with open(real, "a", encoding="utf-8", newline="") as f:
                f.write(body)
    except BaseException:
        remove_files(files, dirs, root)  # 途中で失敗したとき、ここまでに作ったものを残さない
        raise
    return files, dirs


def remove_files(made, made_dirs, root):
    for rel in made:
        p = Path(root) / rel
        if p.is_file():
            p.unlink()
    for rel in sorted(made_dirs, key=len, reverse=True):  # 深いものから。空のときだけ消す
        with contextlib.suppress(OSError):
            (Path(root) / rel).rmdir()


def host_env(st):
    """claude に渡す環境。setup.host が real 以外のときだけ作る（real は None）。"""
    host = st.get("host")
    return hostenv.step_env(host) if host and not host.get("down") else None


def teardown_host(st, root):
    """模擬を止め、スタブの置き場を消す（何度呼んでもよい）。docker-gitlab のプロジェクトを
    消せなかったとき（手作業が要る）は True。"""
    if st.get("host"):
        return bool(hostenv.teardown(root, st["host"], repo.git))
    return False


def start_host(scn, st, out, root, top):
    """setup.host が bare / fake-gitlab / docker-gitlab なら、相手を用意して state に残す。"""
    if not hostenv.uses_stub_git(schema.scenario_host(scn)):
        return
    try:
        # prepare が途中で失敗すると辞書は返らない。作ったものは prepare 自身が消す
        st["host"] = hostenv.prepare(root, top, scn["setup"], repo.git, scn["id"])
    except RuntimeError as exc:
        raise Refusal(
            f"setup.host の相手を用意できなかった: {exc}",
            f"対象 {top} の origin を確かめて打ち直す",
            "対象は後始末で戻す",
        ) from None
    write_state(out, st)


def snapshot_host(out, st, idx, root):
    """setup.host が fake-gitlab / docker-gitlab のとき、step の終わりの相手側の状態を
    `host-<step>.json` に残す。取れなくても例外にせず、シナリオを止めない（失敗は記録に残る）。"""
    host = st.get("host")
    if not host or host.get("down") or host.get("kind") not in hostenv.snapshot_hosts():
        return
    try:
        result = hostsnap.take(root, host, idx + 1, out)
    except Exception as exc:  # 記録の失敗でシナリオを止めない
        result = {"step": idx + 1, "file": None, "ok": False, "errors": 1}
        log(root).error("相手側の状態の記録で例外", step=idx + 1, err=type(exc).__name__)
    st.setdefault("host_snapshots", []).append(result)
    write_state(out, st)
