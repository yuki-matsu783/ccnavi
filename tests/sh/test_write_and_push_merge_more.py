"""write-and-push の受入テスト。取り込み（merge）まわりの後半。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    GIT,
    PARENT,
    WriteAndPushMergeHarness,
    executable,
    git,
    write,
)


class WriteAndPushMergeMoreTest(WriteAndPushMergeHarness):
    """取り込み（merge）まわりの後半。push の失敗・コミットの失敗・シグナルで合流ごと戻すことと、
    検査のあとに子が積んだコミットを載せないこと。

    応答が失われた push と送る途中の TERM は、合流の無い形（`test_write_and_push_ticket.py`）で見る
    （sh の同じ段取り）。子と親の未コミットは Go（wapmerge・treegate）、リスクを数える起点は Go
    （git の ChildBase）が見る。
    """

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
            # 書く前の検査（--check）では積まない。計画（write-and-push merge）のあと、書く呼び出しで積む。
            'case " $* " in *" --check "*) ;; *" ticket finish "*)\n'
            f"  cd '{tree}' && echo late > wip/design/late.md && '{GIT}' add -A &&"
            f" '{GIT}' commit -q -m late ;;\n"
            "esac\n"
            f"exec '{self.bin}' \"$@\"\n",
        )
        result = self.ticket("finish", CHILD, CCNAVI_ENTRY_PATH=wrap)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(tree, "HEAD"), planned)
        review = self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md"))
        self.assertIn(f'merged_sha: "{planned}"', review)
        risk = os.path.join(self.tree, *APPROVED.split("/"), "phases", PARENT, CHILD + ".risk.json")
        summary = json.loads(self.read(risk))["summary"]
        self.assertTrue(summary.startswith("1 ファイル、+1 -0 行"), summary)

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
