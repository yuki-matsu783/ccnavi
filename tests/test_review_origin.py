"""origin の URL の読み方。exe の `remote_kind` と、sh の `origin` の両方を見る。

URL にトークンを埋めた形（`https://oauth2:<token>@host/g/p.git`）は普通にある。
ホストにユーザ情報が混ざると API の綴りが壊れ、`origin` の出力にトークンが漏れる。
実物の GitLab で踏んだ穴なので、両方の読み手で固定する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from ccnavi import review

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".claude", "scripts", "ccnavi-review.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
CURL = shutil.which("curl")


class RemoteKindTest(unittest.TestCase):
    def test_reads_host_through_userinfo_and_port(self):
        cases = {
            "https://github.com/o/r.git": "github",
            "git@github.com:o/r.git": "github",
            "https://oauth2:glpat-secret@github.com/o/r.git": "github",
            "http://localhost:8929/root/p.git": "gitlab",
            "http://oauth2:glpat-secret@localhost:8929/root/p.git": "gitlab",
            "ssh://git@gitlab.example.com:2222/g/p.git": "gitlab",
            "git@gitlab.example.com:g/p.git": "gitlab",
            # ユーザ情報に `@` が入る。git は最後の `@` で切るので、こちらもそう読む。
            "https://user:p@ss@github.com/o/r.git": "github",
            "https://oauth2:glpat-A@B@localhost:8929/root/p.git": "gitlab",
            # パスに `@` があっても authority の外なので混ざらない。
            "git@github.com:o/r@x.git": "github",
            "https://[::1]:9/g/p.git": "gitlab",
            # sh が読めない綴りは、こちらも読めない扱い。
            "ssh://user@host.example.com/g/p.git": "",
            "HTTPS://github.com/o/r.git": "",
            "not a url": "",
        }
        for url, kind in cases.items():
            with self.subTest(url=url):
                self.assertEqual(review.remote_kind(url), kind)


@unittest.skipUnless(SHELL and JQ and CURL, "sh / jq / curl のどれかが無い")
class OriginSubcommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-origin-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        # ワークスペースルートにもする。sh は `.claude/scripts/` を持つディレクトリを
        # cwd から上へ探して根を決めるので、無いと判定まで届かない。
        os.makedirs(os.path.join(self.dir, ".claude", "scripts"), exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.dir, check=True)
        # sh は今居るブランチを rev-parse で読む。コミットが無いと HEAD が解けない。
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "seed",
            ],
            cwd=self.dir,
            check=True,
        )

    def origin(self, url: str) -> subprocess.CompletedProcess:
        subprocess.run(["git", "remote", "add", "origin", url], cwd=self.dir, check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        # `origin` は exe を呼ばない。在ることだけ見るので、確実に在る実行ファイルを指す。
        env["CCNAVI_BIN_PATH"] = sys.executable
        env["GITLAB_TOKEN"] = "x"
        env["GITHUB_TOKEN"] = "x"
        return subprocess.run(
            [SHELL, SCRIPT, "origin"],
            cwd=self.dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )

    def test_userinfo_is_dropped_and_hidden(self):
        # 閉じたポート。glab の疎通の試みがすぐ失敗して curl に落ちる。
        result = self.origin("http://oauth2:glpat-secret@127.0.0.1:9/root/p.git")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("host=127.0.0.1:9\n", result.stdout)
        self.assertIn("path=root/p\n", result.stdout)
        self.assertIn("api_base=http://127.0.0.1:9/api/v4\n", result.stdout)
        self.assertNotIn("glpat-secret", result.stdout + result.stderr)
        self.assertIn("origin=http://<伏せた>@127.0.0.1:9/root/p.git", result.stdout)


if __name__ == "__main__":
    unittest.main()
