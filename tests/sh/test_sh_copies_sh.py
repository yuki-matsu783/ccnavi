"""実行ファイルを起こさずに動く sh が持つ規則の写しと、実行ファイルの一致。

`ccnavi-fetch.sh`・`ccnavi-user-push-approved.sh` は、実行ファイルの入れ替え中でも
取ってくる・送るために、次の写しを sh に持つ。実行ファイルの側は go test が同じ表で見る。

- 置き場の名前（`ccnavi-common.sh` の `ccnavi_worktrees_dir` など。`tests/fixtures/layout.json`）
- 置き場の変更のパスの読みと、書きかけの一時ファイル（`ccnavi_porcelain_paths`・
  `ccnavi_drop_temp`）、パスの比べ（`ccnavi_path_under`・`ccnavi_path_pick`）。
  `tests/fixtures/approved-carry.json`
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

    def test_path_under(self):
        whole = table("approved-carry.json")
        self.assertTrue(whole["under"])
        for locale in ("C", "C.UTF-8"):
            for case in whole["under"]:
                with self.subTest(locale=locale, path=case["path"], base=case["base"]):
                    done = subprocess.run(
                        [SHELL, "-c", '. "$0"; ccnavi_path_under "$1" "$2"', COMMON]
                        + [case["path"], case["base"]],
                        capture_output=True,
                        env={**os.environ, "LC_ALL": locale},
                        check=False,
                    )
                    if case["rest"] is None:
                        self.assertNotEqual(0, done.returncode, done.stdout)
                    else:
                        self.assertEqual(0, done.returncode, done.stderr)
                        self.assertEqual(case["rest"], done.stdout.decode("utf-8").rstrip("\n"))
        # ASCII の外の字の大文字小文字は、sh では畳まない（実行ファイルだけが同じとみなす）。
        for case in whole["go_only"]:
            done = run('ccnavi_path_under "$1" "$2"', case["path"], case["base"])
            self.assertNotEqual(0, done.returncode)

    def test_path_pick(self):
        cases = table("approved-carry.json")["same"]
        self.assertTrue(cases)
        with tempfile.TemporaryDirectory() as d:
            want = os.path.join(d, "want")
            for case in cases:
                with self.subTest(a=case["a"], b=case["b"]):
                    with open(want, "w", encoding="utf-8", newline="\n") as f:
                        f.write(case["a"] + "\n")
                    done = run(
                        'ccnavi_path_pick "$1"', want, stdin=(case["b"] + "\n").encode("utf-8")
                    )
                    picked = done.stdout.decode("utf-8").splitlines()
                    self.assertEqual([case["b"]] if case["same"] else [], picked)


if __name__ == "__main__":
    unittest.main()
