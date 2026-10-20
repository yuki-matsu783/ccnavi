"""write-and-push の受入テスト。取り込み（merge）まわりの後半。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest

from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    CHILD_B,
    GIT,
    LOST_REPLY,
    PARENT,
    SHELL,
    WriteAndPushMergeHarness,
    executable,
    git,
    write,
)


class WriteAndPushMergeMoreTest(WriteAndPushMergeHarness):
    """取り込み（merge）まわりの後半。"""

    def test_an_uncommitted_change_in_the_child_stops(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        write(os.path.join(tree, "wip", "design", "late.md"), "not committed\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("サブエージェントの作業がコミットされていません", result.stderr)
        self.assertIn("wip/design/late.md", result.stderr)
        self.assert_untouched(before)

    def test_an_uncommitted_change_in_the_parent_stops(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        write(os.path.join(self.tree, "src", "keep.py"), "print(2)\n")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("親のワークツリーに未コミットの変更がある", result.stderr)
        self.assertIn("src/keep.py", result.stderr)
        self.assertFalse(self.merging())
        self.assertTrue(os.path.isfile(self.copy(CHILD)))

    def test_two_rejected_pushes_undo_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\necho busy >&2\nexit 1\n",
        )
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)

    def test_a_rejected_push_is_retried_with_the_merge_done_again(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\n"
            f"n=$(cat '{counter}'); echo $((n + 1)) > '{counter}'\n"
            'if [ "$n" -lt 1 ]; then echo busy >&2; exit 1; fi\n',
        )
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("戻した", result.stderr)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [before, child_head])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_a_failed_commit_undoes_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", "false")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("合流のコミットができなかった", result.stderr)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_before_the_push_undoes_the_merge(self):
        import signal

        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        mark = os.path.join(self._base, "pushing")
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        executable(os.path.join(hooks, "pre-push"), f"#!/bin/sh\n: >'{mark}'\nsleep 5\nexit 1\n")
        running = subprocess.Popen(
            [SHELL, os.path.join(self.scripts, "ccnavi-ticket.sh"), "finish", CHILD],
            cwd=self.ws,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not os.path.exists(mark) and time.monotonic() < deadline:
            time.sleep(0.1)
        running.send_signal(signal.SIGTERM)
        _, err = running.communicate(timeout=60)
        self.assertIn("送る前に止められた", err.decode("utf-8", "replace"))
        self.assert_rolled_back(before)

    def test_a_push_whose_reply_was_lost_counts_as_sent_with_the_merge(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        shim = os.path.join(self._base, "shim")
        executable(os.path.join(shim, "git"), LOST_REPLY.format(git=GIT))
        path = shim + os.pathsep + os.environ.get("PATH", "")
        result = self.ticket("finish", CHILD, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("届いていた", result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[2], child_head)

    # ---- 5. 親を取り込んだ子でも、ほかの子の変更が混ざらない

    def test_taking_the_parent_into_a_child_does_not_mix_in_the_other_childs_changes(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        self.work(first, "wip/design/a.md", "first\n")
        self.work(second, "wip/design/b.md", "second\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        # 衝突を解くつもりで、子 2 へ親を取り込む
        git(second, "merge", "-q", "--no-edit", PARENT)
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        risk = os.path.join(
            self.tree, *APPROVED.split("/"), "phases", PARENT, CHILD_B + ".risk.json"
        )
        summary = json.loads(self.read(risk))["summary"]
        self.assertTrue(summary.startswith("1 ファイル、+1 -0 行"), summary)

    def test_a_commit_after_the_scope_check_is_not_merged(self):
        """範囲の検査の直後に子がコミットしても、検査していないコミットは合流しない。"""
        self.start_parent()
        tree = self.start_child()
        checked = self.work(tree, "wip/design/a.md", "a\n")
        mark = os.path.join(self._base, "checked")
        # 子のワークツリーで範囲の検査（diff -z）が済んだ直後に、範囲外のファイルをコミットする。
        path = self.git_shim(
            'case " $* " in *" diff --name-only --no-renames --ignore-submodules=none -z "*)\n'
            f"  if [ ! -e '{mark}' ] && [ \"$(pwd -P)\" = \"$(cd '{tree}' && pwd -P)\" ]; then\n"
            f'    "$REAL" "$@"; rc=$?; : >\'{mark}\'\n'
            '    mkdir -p src && echo evil > src/evil.py && "$REAL" add src/evil.py >/dev/null &&'
            ' "$REAL" commit -q -m evil >/dev/null\n'
            "    exit $rc\n  fi ;;\nesac",
        )
        result = self.ticket("finish", CHILD, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(mark), "検査の差分を読んでいない")
        self.assertNotEqual(self.sha(tree, "HEAD"), checked)
        self.assertNotEqual(
            git(self.tree, "cat-file", "-e", "HEAD:src/evil.py", check=False).returncode, 0
        )
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[2], checked)

    def test_a_commit_after_the_plan_still_records_the_merged_head(self):
        """検査のあとに子がコミットしても、合流した先頭を merged_sha に書き、それまでを数える。"""
        self.start_parent()
        tree = self.start_child()
        planned = self.work(tree, "wip/design/a.md", "a\n")
        wrap = executable(
            os.path.join(self._base, "bin", "wrap"),
            "#!/bin/sh\n"
            'case " $* " in *" ticket finish "*)\n'
            f"  cd '{tree}' && echo late > wip/design/late.md && '{GIT}' add -A &&"
            f" '{GIT}' commit -q -m late ;;\n"
            "esac\n"
            f"exec '{self.bin}' \"$@\"\n",
        )
        result = self.ticket("finish", CHILD, CCNAVI_BIN_PATH=wrap)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(tree, "HEAD"), planned)
        review = self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md"))
        self.assertIn(f'merged_sha: "{planned}"', review)
        risk = os.path.join(self.tree, *APPROVED.split("/"), "phases", PARENT, CHILD + ".risk.json")
        summary = json.loads(self.read(risk))["summary"]
        self.assertTrue(summary.startswith("1 ファイル、+1 -0 行"), summary)

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_during_the_merge_undoes_it_and_finish_can_run_again(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        err = self.term_after(" merge --no-ff --no-commit ")
        self.assertIn("合流の途中で止められた", err)
        self.assert_rolled_back(before)
        again = self.ticket("finish", CHILD)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_right_after_the_merge_commit_undoes_it(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        err = self.term_after(" commit --quiet --no-verify -m ")
        self.assertIn("送る前に止められた", err)
        self.assert_rolled_back(before)
        self.assertEqual(self.remote_sha(), before)
        again = self.ticket("finish", CHILD)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))


if __name__ == "__main__":
    unittest.main()
