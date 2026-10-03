"""`gitstate` が git を起こすときの期限。

index を書き換える git（restore と rm --cached）は `.git/index.lock` を必ず取る。
期限で殺されるとその lock が残り、以後の add や commit が止まるので、読むだけの
git より長く待つ。読む側の期限は短いまま据え置く。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from ccnavi.infra import gitcmd, gitstate


class TimeoutTest(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix="ccnavi-gitstate-")
        self.addCleanup(lambda: shutil.rmtree(self.top, ignore_errors=True))
        self.aside = os.path.join(self.top, "aside")

    def seen(self, call):
        """call の中で起こされた git の (引数, 期限) を並べて返す。git は起こさない。"""
        calls = []

        def record(cwd, args, timeout=gitcmd.TIMEOUT_SECONDS, **kwargs):
            calls.append((args, timeout))
            return gitcmd.Done(code=0)

        with mock.patch.object(gitstate.gitcmd, "run", record):
            call()
        self.assertTrue(calls, "git が起こされていない")
        return calls

    def test_restore_of_a_changed_file_waits_five_seconds(self):
        change = gitstate.Change(kind=gitstate.KIND_CHANGED, path="a.txt")
        calls = self.seen(lambda: gitstate.restore(self.top, change, self.aside))
        self.assertEqual([(["restore", "--staged", "--worktree", "--", "a.txt"], 5.0)], calls)

    def test_rm_cached_of_a_staged_new_file_waits_five_seconds(self):
        with open(os.path.join(self.top, "b.txt"), "w", encoding="utf-8") as f:
            f.write("b")
        change = gitstate.Change(kind=gitstate.KIND_NEW, path="b.txt", staged=True)
        calls = self.seen(lambda: gitstate.restore(self.top, change, self.aside))
        self.assertEqual([(["rm", "--cached", "--", "b.txt"], 5.0)], calls)

    def test_restore_committed_waits_five_seconds(self):
        calls = self.seen(lambda: gitstate.restore_committed(self.top, "c.txt"))
        self.assertEqual([(["restore", "--staged", "--worktree", "--", "c.txt"], 5.0)], calls)

    def test_reading_still_waits_two_seconds(self):
        reads = [
            lambda: gitstate.read(self.top),
            lambda: gitstate.head(self.top),
            lambda: gitstate.committed(self.top, "HEAD"),
            lambda: gitstate.committed_text(self.top, "c.txt"),
        ]
        for call in reads:
            for args, timeout in self.seen(call):
                with self.subTest(git=args[0]):
                    self.assertEqual(2.0, timeout)


if __name__ == "__main__":
    unittest.main()
