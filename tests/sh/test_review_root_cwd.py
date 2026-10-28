"""ワークスペースルート（origin は GitHub）から review の confirm・decide・request・ready を打ったときの止まり方。

親は cwd のワークツリーから引く。ルートから打つと親が決まらないので、ホストへ繋ぐ前に
「親のワークツリーの中で打つ」と止める。先にホストへ繋ぐと、親のプロジェクトが GitLab でも、
ルート自身の origin（GitHub）で見て「gh が github.com で利用できず」と別の理由で止まっていた。
sh を起動するので、sh のグループに置く。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT

SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPT = os.path.join(SH_DIR, "ccnavi-review.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
CURL = shutil.which("curl")


@unittest.skipUnless(SHELL and JQ and CURL, "sh / jq / curl のどれかが無い")
class RootCwdTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-rootcwd-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        marker = os.path.join(self.dir, ".ccnavi", "scripts", "ccnavi-common.sh")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "a", encoding="utf-8").close()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.dir, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            + ["commit", "-q", "--allow-empty", "-m", "seed"],
            cwd=self.dir,
            check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/o/r.git"],
            cwd=self.dir,
            check=True,
        )
        # 統合先は origin/main から決まる。
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=self.dir, check=True
        )
        # 認証の通らない gh を先頭に置き、トークンも渡さない。ホストへ繋ぐと必ず
        # 「gh が github.com で利用できず」で止まる。
        self.shim = os.path.join(self.dir, "shim")
        os.makedirs(self.shim)
        gh = os.path.join(self.shim, "gh")
        with open(gh, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(gh, 0o755)

    def run_review(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CCNAVI_") and k not in ("GITHUB_TOKEN", "GH_TOKEN")
        }
        env["CCNAVI_LEARNING"] = "disable"
        env["CCNAVI_ENTRY_PATH"] = sys.executable
        env["PATH"] = self.shim + os.pathsep + env.get("PATH", "")
        return subprocess.run(
            [SHELL, SCRIPT, *args],
            cwd=self.dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )

    def test_stops_before_connecting_to_the_host(self):
        cases = {
            "confirm": ["confirm", "--phase", "1"],
            "decide": ["decide", "1", "--preview"],
            "request": ["request", "--phase", "1", "--body-file", "b.md", "--eli5", "e.html"],
            "ready": ["ready"],
        }
        for name, args in cases.items():
            with self.subTest(sub=name):
                done = self.run_review(*args)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertNotIn("利用できず", done.stderr)
                self.assertIn("の中で打ってください", done.stderr)
                # 止める理由に cwd が出る
                self.assertIn(os.path.basename(self.dir), done.stderr)
                nexts = [x for x in done.stderr.splitlines() if x.startswith("次: ")]
                self.assertEqual(len(nexts), 1, done.stderr)
                self.assertIn("cd ", nexts[0])
                self.assertIn(f"ccnavi-review.sh' {args[0]}", nexts[0])


if __name__ == "__main__":
    unittest.main()
