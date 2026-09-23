"""GraphQL が塞がれている環境での逃げ道を、詰まったその場で案内する。

GitHub ではスレッドの解決状態（`threads`）と Draft 外し（`undraft`）が GraphQL でしか
扱えない。GraphQL を塞ぐ実行環境があり（Claude Code のセッションは REST だけ通す）、
そこでは `confirm` / `fetch` / `ready` だけが 403 で止まる。curl の経路は `-f` が本文を
捨てるので、画面に残るのは番号だけになり、認証の失敗と見分けが付かない。

案内そのものは `api_failed` の `graphql` の枝が出す。本物の GitHub の GraphQL を
落とす土台は作れない（`remote_kind` はホスト名で github と読むので、127.0.0.1 に
立てた偽物は gitlab として読まれる）。そこで 2 本に分ける。

- 枝が在ることと、案内が次の一手を名指ししていること: スクリプトの綴りを読む
  （`test_sh_portability.py` と同じやり方）
- GraphQL 以外の失敗では案内を出さないこと: 閉じたポートへ実際に打つ
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT

SCRIPT = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-review.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
CURL = shutil.which("curl")

# 案内が名指しするもの。どれが欠けても、読んだ人は次の一手に届かない。
MUST_NAME = (
    "ccnavi review confirm",
    "--result",
    "MCP",
    "fetch_all",
)


def script_text():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def api_failed_body(text):
    """`api_failed()` の本体だけを切り出す。次の関数の定義までを見る。"""
    start = text.index("api_failed() {")
    rest = text[start:]
    end = rest.index("\n}\n")
    return rest[: end + 3]


class TheHintIsInTheFailurePathTest(unittest.TestCase):
    """案内は `api_failed` に置く。"""

    def test_api_failed_has_a_graphql_branch(self):
        """`confirm` の側ではなく `api_failed`。`fetch` と `ready` も同じ経路で詰まる。"""
        body = api_failed_body(script_text())
        self.assertIn('case "$2" in', body, "GraphQL のときだけ足す枝が無い")
        self.assertIn("graphql)", body)

    def test_the_hint_names_the_next_move(self):
        """番号だけでは次の一手に届かない。写しの作り方まで名指しする。"""
        body = api_failed_body(script_text())
        for word in MUST_NAME:
            with self.subTest(word=word):
                self.assertIn(word, body)


@unittest.skipUnless(SHELL and JQ and CURL, "sh / jq / curl のどれかが無い")
class OrdinaryFailuresStaySilentTest(unittest.TestCase):
    """GraphQL 以外の失敗では案内を出さない。出る条件を広げない見張り。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-403-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        marker = os.path.join(self.dir, ".ccnavi", "scripts", "ccnavi-common.sh")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "a", encoding="utf-8").close()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.dir, check=True)
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
        # 閉じたポート。最初の API 呼び出し（マージリクエストを探す GET）で落ちる。
        subprocess.run(
            ["git", "remote", "add", "origin", "http://127.0.0.1:9/root/p.git"],
            cwd=self.dir,
            check=True,
        )

    def test_a_rest_failure_does_not_print_the_graphql_hint(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env["CCNAVI_BIN_PATH"] = sys.executable
        env["GITLAB_TOKEN"] = "x"
        env["GITHUB_TOKEN"] = "x"
        result = subprocess.run(
            [SHELL, SCRIPT, "fetch"],
            cwd=self.dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=120,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("が失敗した", result.stderr)
        self.assertNotIn("MCP", result.stderr)


if __name__ == "__main__":
    unittest.main()
