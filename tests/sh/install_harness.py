"""導入の sh（ccnavi-doctor.sh・ccnavi-install.sh・ccnavi-package.sh）のテストが使う道具立て。

道具を隔離する。偽物を PATH の先頭に置くだけでは、後ろの本物が見つかってしまう。テストごとに一時の
bin ディレクトリを作り、要る道具だけを本物へのリンクで置き、PATH をそのディレクトリだけにする。
glab・jq・curl・git・setup・uv は、引数を記録する偽物にできる。
記録は `calls.log` に `<道具> <引数>` で 1 行ずつ。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from tests import ROOT

SHELL = shutil.which("sh")
REAL_GIT = shutil.which("git")
REAL_JQ = shutil.which("jq")

# 導入の sh が使う、ふつうの道具。ここに無いものは PATH に無い。
BASE_TOOLS = [
    *("sh", "sed", "grep", "awk", "cat", "mkdir", "cp", "rm", "tr", "head", "tail"),
    *("dirname", "basename", "uname", "date", "sleep", "kill", "cut", "ps", "cksum"),
    *("chmod", "ls", "wc", "find", "sort", "mktemp"),
]


def have_tools() -> bool:
    return bool(SHELL and REAL_GIT and REAL_JQ and shutil.which("ps"))


def write_exec(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, 0o755)


def snapshot(root: str) -> dict:
    """ディレクトリの中身・権限・拡張属性・リンク先。配布元が変わっていないことを比べるのに使う。"""
    seen: dict = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(dirs + files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            info = os.lstat(path)
            entry = [stat.S_IMODE(info.st_mode)]
            if stat.S_ISLNK(info.st_mode):
                entry.append(os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                with open(path, "rb") as f:
                    entry.append(f.read())
            if hasattr(os, "listxattr"):
                with contextlib.suppress(OSError):
                    entry.append(tuple(sorted(os.listxattr(path, follow_symlinks=False))))
            seen[rel] = tuple(entry)
    return seen


class Env(unittest.TestCase):
    """隔離した PATH と、記録つきの偽物を持つテストの土台。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = os.path.realpath(self.tmp.name)
        self.bin = os.path.join(self.tmpdir, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.tmpdir, "calls.log")
        self.home = os.path.join(self.tmpdir, "home")
        os.makedirs(self.home)
        self.ctl = os.path.join(self.tmpdir, "ctl")
        os.makedirs(self.ctl)
        for name in BASE_TOOLS:
            self.link(name)

    # ---- 道具

    def link(self, name: str) -> None:
        real = shutil.which(name)
        if real is None:
            return
        dst = os.path.join(self.bin, name)
        if os.path.lexists(dst):
            os.remove(dst)
        os.symlink(real, dst)

    def unlink(self, name: str) -> None:
        dst = os.path.join(self.bin, name)
        if os.path.lexists(dst):
            os.remove(dst)

    def fake(self, name: str, body: str) -> str:
        """引数を calls.log に記録する偽物を置く。`body` は記録のあとに実行する sh。"""
        dst = os.path.join(self.bin, name)
        if os.path.lexists(dst):
            os.remove(dst)
        write_exec(dst, f'#!/bin/sh\nprintf \'%s %s\\n\' {name} "$*" >> "{self.calls}"\n{body}\n')
        return dst

    def ctl_set(self, key: str, value: str) -> None:
        with open(os.path.join(self.ctl, key), "w", encoding="utf-8") as f:
            f.write(value)

    def ctl_ref(self, key: str) -> str:
        return os.path.join(self.ctl, key)

    def fake_git(self) -> None:
        """ふつうの git に委ねるが、通信するものだけ偽物にする。挙動は ctl の値で決める。"""
        refused = "fatal: unable to access 'https://u:tok@host/g/p.git/': Could not resolve host"
        body = f"""case "$*" in
*ls-remote*)
	mode=$(cat "{self.ctl_ref("ls")}" 2>/dev/null || echo ok)
	case "$mode" in
	ok) printf 'abc\\tHEAD\\n'; exit 0 ;;
	empty) exit 2 ;;
	fail) printf '%s\\n' "{refused}" >&2; exit 128 ;;
	sleep) sleep "$(cat "{self.ctl_ref("ls_sleep")}")" & wait; exit 0 ;;
	esac
	;;
*pull*)
	exit 0
	;;
esac
exec "{REAL_GIT}" "$@"
"""
        self.fake("git", body)
        self.ctl_set("ls", "ok")

    def fake_glab(self, credential: bool = True, api_ok: bool = True) -> None:
        sub = "git-credential" if credential else "login"
        text = f"Available commands:\\n  {sub}"
        body = f"""case "$*" in
"auth --help") printf '{text}\\n'; exit 0 ;;
"--version") printf 'glab 1.52.0 (2025-01-01)\\n'; exit 0 ;;
api*) exit {0 if api_ok else 1} ;;
esac
exit 0
"""
        self.fake("glab", body)

    def fake_curl(self, mode: str = "ok") -> None:
        """curl の応答を ctl の `curl` で切り替える（ok / 401 / 404 / timeout / unreach）。"""
        body = f"""mode=$(cat "{self.ctl_ref("curl")}" 2>/dev/null || echo ok)
case "$mode" in
ok) printf '{{"id":1}}'; exit 0 ;;
401) printf 'curl: (22) The requested URL returned error: 401\\n' >&2; exit 22 ;;
404) printf 'curl: (22) The requested URL returned error: 404\\n' >&2; exit 22 ;;
timeout) printf 'curl: (28) Operation timed out\\n' >&2; exit 28 ;;
unreach) printf 'curl: (7) Failed to connect to host\\n' >&2; exit 7 ;;
esac
"""
        self.fake("curl", body)
        self.ctl_set("curl", mode)

    def env(self, **extra: str) -> dict:
        env = {
            "PATH": self.bin,
            "HOME": self.home,
            "XDG_CACHE_HOME": os.path.join(self.home, ".cache"),
            "CCNAVI_DOCTOR_GLAB_CANDIDATES": "",
            "CCNAVI_DOCTOR_JQ_CANDIDATES": "",
            "LANG": "C.UTF-8",
        }
        env.update(extra)
        return env

    # ---- 作業場

    def git_repo(self, path: str, origin: str | None = None) -> str:
        os.makedirs(path, exist_ok=True)
        subprocess.run([REAL_GIT, "init", "-q", path], check=True)
        if origin:
            subprocess.run([REAL_GIT, "-C", path, "remote", "add", "origin", origin], check=True)
        return path

    def run_sh(
        self, args: list[str], env: dict | None = None, cwd: str | None = None, timeout: int = 60
    ):
        return subprocess.run(
            [os.path.join(self.bin, "sh"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env if env is not None else self.env(),
            cwd=cwd,
            timeout=timeout,
            check=False,
        )

    def calls_text(self) -> str:
        if not os.path.exists(self.calls):
            return ""
        with open(self.calls, encoding="utf-8") as f:
            return f.read()

    # ---- 残りのプロセス

    @staticmethod
    def running(needle: str) -> list[str]:
        out = subprocess.run(
            ["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=False
        ).stdout
        return [ln for ln in out.splitlines() if needle in ln and "ps -eo" not in ln]

    def wait_gone(self, needle: str, seconds: float = 5.0) -> list[str]:
        end = time.time() + seconds
        while time.time() < end:
            if not self.running(needle):
                return []
            time.sleep(0.1)
        return self.running(needle)


def script(name: str) -> str:
    return os.path.join(ROOT, "scripts", name)


def count_next(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.startswith("次: "))
