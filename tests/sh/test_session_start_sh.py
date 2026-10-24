"""ccnavi-session-start.sh と ccnavi-dirs.sh の受入テスト。
使い捨ての木に sh をコピーして外から呼ぶ。

見るのは次の 4 つ。

1. 入口は、取り込み（fetch）が失敗しても、ディレクトリ一覧（dirs）を出して 0 で終わる
2. 一覧は 10 件までで、超えると残りの件数と ls を案内する
3. ディレクトリが無ければ何も出さない
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

    def make_dirs(self, count: int):
        for i in range(count):
            path = os.path.join(self.project, f"d{i:02d}")
            os.makedirs(path)
            os.utime(path, (1_000_000 + i, 1_000_000 + i))

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
        self.assertIn("ls -dt", lines[11])

    def test_no_more_note_when_within_ten(self):
        self.make_dirs(10)
        r = run("ccnavi-dirs.sh", self.project, self.scripts)
        self.assertNotIn("ほかに", r.stdout)

    def test_prints_nothing_without_directories(self):
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
