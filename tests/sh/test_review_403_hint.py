"""GraphQL が制限されている環境で、REST にも失敗したときの案内。

GitHub ではスレッドの解決状態（`threads`）と Draft 外し（`undraft`）が GraphQL の道を持つ。
GraphQL を制限する実行環境があり（Claude Code のクラウドは REST だけ通す）、
そこでは GraphQL が 403 になる。ccnavi は GraphQL を先に試し、403 のときだけ
REST（`/ccr/` の道）に替える（`test_review_rest_fallback.py`）。

この試験が見るのは、REST にも失敗したときの案内（`rest_fallback_failed`）。

- 案内は、ユーザに JSON を作らせて `ccnavi review confirm --result` を打たせる手順を書かない。
  その手順は write-and-push に止められる（`--record-tree` が付かない）。
  書くと、拒否される手順を案内することになる
- 案内が、止まってユーザに接続の確認を頼むことを名指しする
- GraphQL 以外の失敗では案内を出さない: 閉じたポートへ実際に打つ

実際の GitHub の GraphQL を失敗させる環境は作れない（`remote_kind` はホスト名で github と読むので、
127.0.0.1 に立てた偽物は gitlab として読まれる）。GraphQL の 403 は `github_host.py` の代役で作る。
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
# `api_failed` の置き場。ccnavi-review.sh とユーザ専用のレビューの sh が共有する、
# ホストを読む側の部品。
SHARED = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-review.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
CURL = shutil.which("curl")

# 案内が名指しするもの。どれが欠けても、読んだ人は次に何をすればよいか分からない。
MUST_NAME = (
    "REST の代わりの道",
    "ユーザに",
    "止まって",
)
# 案内に書かない手順。JSON を作ることはユーザにもできない（write-and-push に止められる）。
MUST_NOT_NAME = (
    "ccnavi review confirm",
    "--result",
    "MCP",
    "fetch_all",
)


def script_text():
    with open(SHARED, encoding="utf-8") as f:
        return f.read()


def function_body(text, name):
    """`name() {` から次の `}` だけの行までを切り出す。"""
    start = text.index(f"{name}() {{")
    rest = text[start:]
    end = rest.index("\n}\n")
    return rest[: end + 3]


class TheHintIsInTheFailurePathTest(unittest.TestCase):
    """案内は、REST にも失敗したときの文面に置く。"""

    def test_api_failed_has_no_graphql_only_branch(self):
        """GraphQL の失敗は `gql` が分ける。`api_failed` は共通の文面だけにする。"""
        body = function_body(script_text(), "api_failed")
        self.assertNotIn("graphql)", body)
        for word in MUST_NOT_NAME:
            with self.subTest(word=word):
                self.assertNotIn(word, body)

    def test_the_hint_names_the_next_move(self):
        body = function_body(script_text(), "rest_fallback_failed")
        for word in MUST_NAME:
            with self.subTest(word=word):
                self.assertIn(word, body)

    def test_the_hint_does_not_ask_for_a_refused_procedure(self):
        body = function_body(script_text(), "rest_fallback_failed")
        for word in MUST_NOT_NAME:
            with self.subTest(word=word):
                self.assertNotIn(word, body)

    def test_the_no_transport_message_does_not_ask_for_a_json_either(self):
        text = script_text()
        start = text.index("fail no-transport ")
        line = text[start : text.index("\n", start)]
        for word in MUST_NOT_NAME:
            with self.subTest(word=word):
                self.assertNotIn(word, line)


@unittest.skipUnless(SHELL and JQ and CURL, "sh / jq / curl のどれかが無い")
class OrdinaryFailuresStaySilentTest(unittest.TestCase):
    """GraphQL 以外の失敗では案内を出さない。出る条件を広げないための確認。"""

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
        # 閉じたポート。最初の API 呼び出し（マージリクエストを探す GET）で失敗する。
        subprocess.run(
            ["git", "remote", "add", "origin", "http://127.0.0.1:9/root/p.git"],
            cwd=self.dir,
            check=True,
        )

    def test_a_rest_failure_does_not_print_the_graphql_hint(self):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env["CCNAVI_ENTRY_PATH"] = sys.executable
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
