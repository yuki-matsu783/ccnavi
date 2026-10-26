"""ccnavi-session-start.sh と ccnavi-dirs.sh の受入テスト。
使い捨ての木に sh をコピーして外から呼ぶ。

見るのは次の 4 つ。

1. 入口は、取り込み（fetch）が失敗しても、clone 済みリポジトリの一覧（dirs）を出して 0 で終わる
2. 一覧は 10 件までで、超えると残りの件数を添える
3. projects/ が無いとき、または .git を持つものが無いときは何も出さない
4. 10 件ちょうどなら案内を出さない
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")


def run(script: str, project: str, scripts: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": project}
    return subprocess.run(
        [SHELL, os.path.join(scripts, script)],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@unittest.skipUnless(SHELL, "sh が無い")
class SessionStartSh(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = os.path.join(self._tmp.name, "project")
        self.scripts = os.path.join(self._tmp.name, "scripts")
        os.makedirs(self.project)
        os.makedirs(self.scripts)
        for name in ("ccnavi-session-start.sh", "ccnavi-dirs.sh"):
            shutil.copy(os.path.join(SH_DIR, name), self.scripts)

    def commit_at(self, path: str, ts: int):
        date = f"{ts} +0000"
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", path, *args], env=env, check=True, capture_output=True)

    def make_dirs(self, count: int):
        for i in range(count):
            path = os.path.join(self.project, "projects", f"d{i:02d}")
            os.makedirs(path)
            self.commit_at(path, 1_000_000_000 + i)

    def stub_fetch(self, body: str):
        with open(os.path.join(self.scripts, "ccnavi-fetch.sh"), "w", encoding="utf-8") as f:
            f.write(body)

    def test_lists_newest_first_up_to_ten(self):
        self.make_dirs(12)
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertEqual(r.returncode, 0)
        lines = r.stdout.splitlines()
        self.assertEqual(
            [x.strip() for x in lines[1:11]], [f"- d{i:02d}" for i in range(11, 1, -1)]
        )
        self.assertIn("ほかに 2 件", lines[11])

    def test_no_more_note_when_within_ten(self):
        self.make_dirs(10)
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertNotIn("ほかに", r.stdout)

    def test_prints_nothing_without_directories(self):
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_orders_by_latest_commit_not_dir_mtime(self):
        self.make_dirs(3)
        old = os.path.join(self.project, "projects", "d02")
        os.utime(old, (2_000_000_000, 2_000_000_000))
        os.makedirs(os.path.join(self.project, "projects", "nocommit", ".git"))
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        names = [x.strip() for x in r.stdout.splitlines()[1:]]
        self.assertEqual(names, ["- d02", "- d01", "- d00", "- nocommit"])

    def test_skips_directories_without_git(self):
        self.make_dirs(1)
        os.makedirs(os.path.join(self.project, "projects", "plain"))
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertIn("- d00", r.stdout)
        self.assertNotIn("plain", r.stdout)

    def test_counts_git_file_and_odd_names(self):
        base = os.path.join(self.project, "projects")
        os.makedirs(os.path.join(base, "sub mod"))
        with open(os.path.join(base, "sub mod", ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: ../x\n")
        os.makedirs(os.path.join(base, "-dash", ".git"))
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertIn("- sub mod", r.stdout)
        self.assertIn("- -dash", r.stdout)

    def test_ignores_top_level_directories(self):
        os.makedirs(os.path.join(self.project, "src", ".git"))
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_entry_runs_dirs_even_when_fetch_fails(self):
        self.make_dirs(1)
        self.stub_fetch("echo fetch-ran\nexit 1\n")
        r = run("ccnavi-session-start.sh", self.project, self.scripts)
        self.assertEqual(r.returncode, 0)
        self.assertIn("fetch-ran", r.stdout)
        self.assertIn("- d00", r.stdout)


if __name__ == "__main__":
    unittest.main()
