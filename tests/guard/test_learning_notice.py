"""SessionStart の学びの通知（`learning_notice`）と、その hook での出し方。

見るのは、条件（候補の数か閾値以上）と変化（新しい ID か新しく閾値に届いた ID）の両方、統合先の
手元の参照のコミットから数えること、決められないリポジトリは見送って記録を変えないこと、
リポジトリごとに別に判定すること、通知がユーザにだけ見える `systemMessage` であること。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.notice import learning_notice
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


class NoticeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-notice-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "ws")
        self.ws = Repo(self.root, os.path.join(self.tmp, "o.git"))
        self.env_before = dict(os.environ)
        os.environ.pop("CCNAVI_LEARNING_SOURCES", None)
        os.environ.pop("CCNAVI_LEARNING_NOTICE_COUNT", None)
        os.environ.pop("CCNAVI_LEARNING", None)

    def notice(self):
        conf, _ = settings.load(self.root)
        return learning_notice.notice(conf, self.root)

    def test_five_candidates_notify_once(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        first = self.notice()
        self.assertIn("5 件", first)
        self.assertIn("docs 5", first)
        self.assertEqual(self.notice(), "")

    def test_four_candidates_do_not_notify(self):
        self.ws.patterns([f"p{i}" for i in range(4)])
        self.assertEqual(self.notice(), "")

    def test_one_pattern_at_threshold_notifies(self):
        self.ws.patterns(["hot"], sources=3)
        self.assertIn("1 件", self.notice())

    def test_newly_reaching_threshold_notifies_with_the_same_count(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        self.assertTrue(self.notice())
        self.assertEqual(self.notice(), "")
        self.ws.patterns(["p0"], sources=3)
        self.assertIn("5 件", self.notice())
        self.assertEqual(self.notice(), "")

    def test_after_everything_is_settled_new_candidates_notify_again(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        self.assertTrue(self.notice())
        git(self.root, "rm", "-q", "-r", "docs/wiki")
        git(self.root, "commit", "--quiet", "-m", "settled")
        git(self.root, "push", "--quiet", "origin", "main")
        git(self.root, "fetch", "--quiet", "origin")
        self.assertEqual(self.notice(), "")
        self.ws.patterns([f"n{i}" for i in range(5)])
        self.assertIn("5 件", self.notice())

    def test_counts_from_the_integration_ref_even_if_another_branch_is_checked_out(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        git(self.root, "checkout", "--quiet", "-b", "other")
        git(self.root, "rm", "-q", "-r", "docs/wiki")
        git(self.root, "commit", "--quiet", "-m", "other")
        self.assertIn("5 件", self.notice())

    def test_undecidable_repo_is_skipped_and_record_untouched(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        self.assertTrue(self.notice())
        path = learning_notice.state_path(settings.load(self.root)[0], "")
        with open(path, encoding="utf-8") as f:
            before = f.read()
        git(self.root, "remote", "remove", "origin")
        self.assertEqual(self.notice(), "")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_state_names_do_not_collide(self):
        conf, _ = settings.load(self.root)
        names = ["", "学習", "調査", "a b", "a_b", "a:b", "_self"]
        paths = {learning_notice.state_path(conf, n) for n in names}
        self.assertEqual(len(paths), len(names))
        self.assertEqual(
            learning_notice.state_path(conf, "Foo"), learning_notice.state_path(conf, "foo")
        )

    def test_unreadable_record_is_treated_as_empty(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        self.assertTrue(self.notice())
        path = learning_notice.state_path(settings.load(self.root)[0], "")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertTrue(self.notice())

    def test_disabled_learning_is_silent(self):
        self.ws.patterns([f"p{i}" for i in range(5)])
        os.environ["CCNAVI_LEARNING"] = "disable"
        self.assertEqual(self.notice(), "")

    def test_each_repository_is_judged_and_recorded_separately(self):
        os.makedirs(os.path.join(self.root, "projects"))
        proj = Repo(os.path.join(self.root, "projects", "web"), os.path.join(self.tmp, "p.git"))
        proj.patterns([f"q{i}" for i in range(5)])
        got = self.notice()
        self.assertIn("web", got)
        self.assertEqual(self.notice(), "")
        self.ws.patterns(["hot"], sources=3)
        got = self.notice()
        self.assertNotIn("web の", got)
        conf, _ = settings.load(self.root)
        self.assertTrue(os.path.exists(learning_notice.state_path(conf, "web")))
        self.assertTrue(os.path.exists(learning_notice.state_path(conf, "")))


class HookTest(unittest.TestCase):
    def test_system_message_only_and_context_is_unchanged(self):
        tmp = tempfile.mkdtemp(prefix="ccnavi-notice-hook-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = os.path.join(tmp, "ws")
        repo = Repo(root, os.path.join(tmp, "o.git"))
        repo.patterns([f"p{i}" for i in range(5)])
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
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertIn("学びの提案の候補", body["systemMessage"])
        context = body["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("学びの提案の候補", context)
        again = json.loads(run_ccnavi(args, input=payload, cwd=ROOT, env=env).stdout or "{}")
        self.assertNotIn("学びの提案の候補", again.get("systemMessage", ""))


if __name__ == "__main__":
    unittest.main()
