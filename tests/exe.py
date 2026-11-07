"""テストが起こす ccnavi の実行ファイル（Go）を 1 か所で用意する。

`CCNAVI_TEST_EXE` があればそれを使う（配る物そのものを測る）。無ければ `go/` を
`go build` して、一時置き場の `ccnavi-test-exe/<ソースの指紋>/` に置く。指紋は `go/` の
ソース（`_test.go` を除く .go と、埋め込む txt・json、go.mod・go.sum）の中身と Go の版・
OS・CPU から作るので、ソースが同じなら回し直しても組み直さない。

組み立てた後は `CCNAVI_TEST_EXE` に入れる。テストが起こす子のプロセス
（`python -m tests` が起こす各モジュール）が、指紋を数え直さずに同じものを使う。

同時に走るプロセスが同じ指紋を組んでも、出来た物は置き場の中で名前を差し替えて置くので、
組みかけのファイルを起こすことは無い。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

from tests import ROOT

GO = os.path.join(ROOT, "go")
NAME = "ccnavi.exe" if os.name == "nt" else "ccnavi"
_SOURCE_SUFFIXES = (".go", ".txt", ".json", ".jsonl", ".mod", ".sum")

_built: str | None = None


def path() -> str:
    """ccnavi の実行ファイルのパス。初めて呼ばれたときに用意する。"""
    global _built
    if _built is None:
        _built = os.environ.get("CCNAVI_TEST_EXE", "") or _build()
        os.environ["CCNAVI_TEST_EXE"] = _built
    return _built


def sh_path() -> str:
    """sh に書くための実行ファイルのパス。`'…'` で囲んで使う。

    区切りを `/` にそろえる。Git Bash は `C:/…` をそのまま起こせるが、`\\` は sh が食う。
    """
    return path().replace("\\", "/")


def _fingerprint() -> str:
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(GO):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(_SOURCE_SUFFIXES) or name.endswith("_test.go"):
                continue
            file = os.path.join(base, name)
            digest.update(os.path.relpath(file, GO).replace(os.sep, "/").encode("utf-8"))
            with open(file, "rb") as f:
                digest.update(f.read())
    try:
        go = subprocess.run(
            ["go", "env", "GOVERSION", "GOOS", "GOARCH"],
            cwd=GO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except FileNotFoundError:
        raise RuntimeError(
            "go が見つかりません。Go を入れるか、CCNAVI_TEST_EXE に実行ファイルを渡してください"
        ) from None
    digest.update(go.encode("utf-8"))
    return digest.hexdigest()[:16]


def _build() -> str:
    base = os.path.join(tempfile.gettempdir(), "ccnavi-test-exe")
    place = os.path.join(base, _fingerprint())
    target = os.path.join(place, NAME)
    if os.path.isfile(target):
        return target
    _drop_stale(base)
    os.makedirs(place, exist_ok=True)
    partial = os.path.join(place, f".{os.getpid()}.part{'.exe' if os.name == 'nt' else ''}")
    done = subprocess.run(
        ["go", "build", "-o", partial, "./cmd/ccnavi"],
        cwd=GO,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        sys.stderr.write(done.stdout + done.stderr)
        raise RuntimeError(f"go build に失敗しました（{GO}）")
    try:
        os.replace(partial, target)
    except OSError:
        # Windows は、別のプロセスが先に置いて起こしている物を差し替えられない。中身は同じ。
        if not os.path.isfile(target):
            raise
        os.remove(partial)
    return target


def _drop_stale(base: str) -> None:
    """ソースを直すたびに増える組み立て済みの物のうち、1 日より前に組んだものを消す。

    別のプロセスがいま使っている物を消さないよう、新しいものは残す。
    """
    if not os.path.isdir(base):
        return
    limit = time.time() - 86400
    for name in os.listdir(base):
        place = os.path.join(base, name)
        with contextlib.suppress(OSError):
            if os.path.getmtime(place) < limit:
                shutil.rmtree(place)
