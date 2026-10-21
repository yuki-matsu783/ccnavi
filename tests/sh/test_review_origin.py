"""origin の URL の読み方。exe の `remote_kind` と、sh の `origin` の両方を見る。

両方の読み手を同じファイルに置くのは、同じ URL を両方が同じに読むことを並べて確かめるため。
sh を起動するので sh のグループに置く。

URL にトークンを埋めた形（`https://oauth2:<token>@host/g/p.git`）は普通にある。
ホストにユーザ情報が入り込むと API の URL が不正になり、`origin` の出力にトークンが漏れる。
実物の GitLab で実際に起きた穴なので、両方の読み手で固定する。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from ccnavi.tickets import review_host
from tests import ROOT

SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPT = os.path.join(SH_DIR, "ccnavi-review.sh")
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
            # パスに `@` があっても authority の外なのでホストに入らない。
            "git@github.com:o/r@x.git": "github",
            "https://[::1]:9/g/p.git": "gitlab",
            # scheme の大文字小文字は区別しない。
            "HTTPS://github.com/o/r.git": "github",
            # sh が読めない表記は、こちらも読めない扱い。
            "ssh://user@host.example.com/g/p.git": "",
            "not a url": "",
        }
        for url, kind in cases.items():
            with self.subTest(url=url):
                self.assertEqual(review_host.remote_kind(url), kind)


@unittest.skipUnless(SHELL and JQ and CURL, "sh / jq / curl のどれかが無い")
class OriginSubcommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-origin-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        # ワークスペースルートにもする。sh は `.ccnavi/scripts/ccnavi-common.sh` を持つ
        # ディレクトリを cwd から上へ探して根を決めるので、無いと判定まで届かない。
        marker = os.path.join(self.dir, ".ccnavi", "scripts", "ccnavi-common.sh")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "a", encoding="utf-8").close()
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

    def origin(self, url: str, extra_env=None, sub="origin") -> subprocess.CompletedProcess:
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.dir, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", url], cwd=self.dir, check=True)
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.update(extra_env or {})
        # `origin` は exe を呼ばない。在ることだけ見るので、確実に在る実行ファイルを指す。
        env["CCNAVI_BIN_PATH"] = sys.executable
        env["GITLAB_TOKEN"] = "x"
        env["GITHUB_TOKEN"] = "x"
        return subprocess.run(
            [SHELL, SCRIPT, sub],
            cwd=self.dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )

    def test_userinfo_is_dropped_and_hidden(self):
        # 閉じたポート。glab の疎通の試みがすぐ失敗して curl に切り替わる。
        result = self.origin("http://oauth2:glpat-secret@127.0.0.1:9/root/p.git")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("host=127.0.0.1:9\n", result.stdout)
        self.assertIn("path=root/p\n", result.stdout)
        self.assertIn("api_base=http://127.0.0.1:9/api/v4\n", result.stdout)
        self.assertNotIn("glpat-secret", result.stdout + result.stderr)
        self.assertIn("origin=http://<伏せた>@127.0.0.1:9/root/p.git", result.stdout)

    def test_keeps_the_port_and_scheme(self):
        """origin の URL から、ホスト・ポート・scheme を欠かさずに API の URL を組むこと。

        host を `[^/:]+` で切るとポートが欠け、欠けたポートがプロジェクトのパスの先頭に
        入り込む（`8929/demo/greeter`）。scheme を https に決め打ちすると、手元や社内に
        平文で立てた GitLab（`http://localhost:8929`）に届かない。
        """
        cases = [
            (
                "http://localhost:8929/demo/greeter.git",
                "gitlab",
                "http",
                "localhost:8929",
                "demo/greeter",
            ),
            (
                "https://gitlab.example.com:8443/g/p.git",
                "gitlab",
                "https",
                "gitlab.example.com:8443",
                "g/p",
            ),
            ("git@gitlab.example.com:g/p.git", "gitlab", "https", "gitlab.example.com", "g/p"),
            ("https://github.com/o/r.git", "github", "https", "github.com", "o/r"),
        ]
        for url, kind, scheme, host, path in cases:
            with self.subTest(url=url):
                read = self.origin(url)
                self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
                got = dict(line.split("=", 1) for line in read.stdout.splitlines() if "=" in line)
                self.assertEqual(got.get("kind"), kind)
                self.assertEqual(got.get("scheme"), scheme)
                self.assertEqual(got.get("host"), host)
                self.assertEqual(got.get("path"), path)
                expected = (
                    "https://api.github.com" if kind == "github" else f"{scheme}://{host}/api/v4"
                )
                self.assertEqual(got.get("api_base"), expected)

    def test_an_unreadable_origin_stops(self):
        """sh の前半（場所と道具の解決）が Windows でも通ること。origin が読めなければ止まる。"""
        bare = os.path.join(self.dir, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        done = self.origin(bare, sub="fetch")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("origin の URL を読めない", done.stderr)


if __name__ == "__main__":
    unittest.main()
