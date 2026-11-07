"""SessionStart の学びの通知の、hook での出し方。

見るのは、通知がユーザにだけ見える `systemMessage` であること、2 回目は言わないこと、統合先の
手元の参照のコミットから数えること。条件（候補の数か閾値以上）と変化、決められないリポジトリの見送り、
リポジトリごとの判定は Go のテスト（go/internal/tickets/notice の learning_test.go）が確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi
from tests.learningsample import pattern

ENV = {"CCNAVI_LEARNING": "enable"}


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class Repo:
    """origin 付きのリポジトリ。統合先 main の wiki を書いて push できる。"""

    def __init__(self, path, origin):
        self.path = path
        os.makedirs(path, exist_ok=True)
        git(path, "init", "--quiet", "-b", "main")
        write(path, "README", "x\n")
        git(path, "add", "-A")
        git(path, "commit", "--quiet", "-m", "init")
        subprocess.run(["git", "init", "--quiet", "--bare", origin], check=True)
        git(path, "remote", "add", "origin", origin)
        git(path, "push", "--quiet", "origin", "main")
        git(path, "fetch", "--quiet", "origin")
        git(path, "remote", "set-head", "origin", "main")

    def patterns(self, names, sources=1, target="docs"):
        for n in names:
            count = sources[n] if isinstance(sources, dict) else sources
            write(
                self.path,
                f"docs/wiki/patterns/{n}.md",
                pattern([f"s{i}" for i in range(count)], target),
            )
        git(self.path, "add", "-A")
        git(self.path, "commit", "--quiet", "-m", "wiki")
        git(self.path, "push", "--quiet", "origin", "main")
        git(self.path, "fetch", "--quiet", "origin")


def session_start(root):
    """SessionStart の hook を 1 回打ち、応答の JSON を返す。"""
    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
    payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup", "cwd": root})
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.update(ENV)
    args = [
        "--root",
        root,
        "--mode",
        "enable",
        "--guard-core-files",
        "disable",
        "--restore-if-deny",
        "disable",
    ]
    done = run_ccnavi(args, input=payload, cwd=ROOT, env=env)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout or "{}")


class HookTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="ccnavi-notice-hook-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.root = os.path.join(tmp, "ws")
        self.repo = Repo(self.root, os.path.join(tmp, "o.git"))

    def test_system_message_only_and_context_is_unchanged(self):
        self.repo.patterns([f"p{i}" for i in range(5)])
        body = session_start(self.root)
        self.assertIn("学びの提案の候補", body["systemMessage"])
        context = body["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("学びの提案の候補", context)
        again = session_start(self.root)
        self.assertNotIn("学びの提案の候補", again.get("systemMessage", ""))

    def test_counts_from_the_integration_ref_even_if_another_branch_is_checked_out(self):
        self.repo.patterns([f"p{i}" for i in range(5)])
        git(self.root, "checkout", "--quiet", "-b", "other")
        git(self.root, "rm", "-q", "-r", "docs/wiki")
        git(self.root, "commit", "--quiet", "-m", "other")
        self.assertIn("5 件", session_start(self.root).get("systemMessage", ""))


if __name__ == "__main__":
    unittest.main()
