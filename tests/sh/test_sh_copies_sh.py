"""実行ファイルを起こさずに動く sh が持つ規則の写しと、実行ファイルの一致。

`ccnavi-fetch.sh`・`ccnavi-user-push-approved.sh` は、実行ファイルの入れ替え中でも
取ってくる・送るために、次の写しを sh に持つ。実行ファイルの側は go test が同じ表で見る。

- 置き場の名前（`ccnavi-common.sh` の `ccnavi_worktrees_dir` など。`tests/fixtures/layout.json`）
- 「origin にその ref が無い」の見分け（`ccnavi_no_remote_ref`。
  `tests/fixtures/no-remote-ref.json`）
- 置き場の変更のパスの読みと、書きかけの一時ファイル（`ccnavi_porcelain_paths`・`ccnavi_drop_temp`。
  `tests/fixtures/approved-carry.json`）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
SHELL = shutil.which("sh")


def table(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def run(script: str, *args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(
        [SHELL, "-c", '. "$0"; ' + script, COMMON, *args],
        input=stdin,
        capture_output=True,
        check=False,
    )


@unittest.skipUnless(SHELL, "sh が無い")
class ShCopiesTest(unittest.TestCase):
    def test_layout(self):
        want = table("layout.json")
        done = run(
            'printf "%s\\n" "$ccnavi_worktrees_dir" "$ccnavi_projects_dir" '
            '"$ccnavi_approved_dir" "$ccnavi_proposals_dir"'
        )
        got = done.stdout.decode("utf-8").splitlines()
        self.assertEqual(
            [want["worktrees"], want["projects"], want["approved"], want["proposals"]], got
        )

    def test_no_remote_ref(self):
        cases = table("no-remote-ref.json")["cases"]
        self.assertTrue(cases)
        with tempfile.TemporaryDirectory() as d:
            err = os.path.join(d, "err")
            for case in cases:
                with self.subTest(err=case["err"]):
                    with open(err, "w", encoding="utf-8", newline="") as f:
                        f.write(case["err"])
                    done = run('ccnavi_no_remote_ref "$1"', err)
                    self.assertEqual(case["no_ref"], done.returncode == 0)

    def test_porcelain_paths(self):
        cases = table("approved-carry.json")["porcelain"]
        self.assertTrue(cases)
        entries = "".join(c["entry"] + "\0" for c in cases).encode("utf-8")
        for locale in ("C", "C.UTF-8"):
            with self.subTest(locale=locale):
                done = subprocess.run(
                    [SHELL, "-c", '. "$0"; ccnavi_porcelain_paths', COMMON],
                    input=entries,
                    capture_output=True,
                    env={**os.environ, "LC_ALL": locale},
                    check=False,
                )
                got = done.stdout.decode("utf-8").splitlines()
                self.assertEqual([c["path"] for c in cases], got)

    def test_drop_temp(self):
        cases = table("approved-carry.json")["temp"]
        self.assertTrue(cases)
        paths = "".join(c["path"] + "\n" for c in cases).encode("utf-8")
        done = run("ccnavi_drop_temp", stdin=paths)
        kept = done.stdout.decode("utf-8").splitlines()
        self.assertEqual([c["path"] for c in cases if not c["temp"]], kept)


if __name__ == "__main__":
    unittest.main()
