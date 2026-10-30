"""関門の条件（`treegate`）が、宣言した操作の全部で拒否すること。

実 git のワークツリー（bare のリモートを持つ）を一時ディレクトリに作り、操作ごとに
`treegate.check` を呼ぶ。どの操作がどの条件を見るかは `treegate.WATCHES` の宣言どおり。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import treegate

OPERATIONS = (
    treegate.APPROVAL_REQUEST,
    treegate.FINISH,
    treegate.REVIEW_REQUEST,
    treegate.READY,
)
CODE_WATCHERS = (treegate.APPROVAL_REQUEST, treegate.REVIEW_REQUEST, treegate.READY)
PLACE_FILE = "wip/proposals/todo/i0001.md"


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def put(tree, rel, text):
    path = os.path.join(tree, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TreegateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        remote = os.path.join(tmp.name, "remote.git")
        self.tree = os.path.join(tmp.name, "parent")
        git(tmp.name, "init", "-q", "--bare", "-b", "main", remote)
        git(tmp.name, "clone", "-q", remote, self.tree)
        git(self.tree, "checkout", "-q", "-b", "main")
        put(self.tree, "src/keep.py", "print(1)\n")
        put(self.tree, PLACE_FILE, "v1\n")
        put(self.tree, "wip/note.md", "n\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "first")
        git(self.tree, "push", "-q", "origin", "main")
        self.conf = settings.Settings()

    def kinds(self, operation):
        return [r.kind for r in treegate.check(operation, self.tree, self.conf)]

    def commit(self, rel):
        put(self.tree, rel, "new\n")
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", "x")

    def test_a_clean_tree_passes_every_operation(self):
        for operation in OPERATIONS:
            self.assertEqual(self.kinds(operation), [], operation)

    def test_every_operation_refuses_an_uncommitted_change_outside_the_places(self):
        put(self.tree, "src/keep.py", "print(2)\n")
        for operation in OPERATIONS:
            found = treegate.check(operation, self.tree, self.conf)
            self.assertEqual([r.kind for r in found], [treegate.DIRTY_PARENT], operation)
            self.assertEqual(found[0].paths, ("src/keep.py",), operation)

    def test_a_change_inside_the_places_is_not_dirty_and_the_case_is_folded(self):
        put(self.tree, PLACE_FILE, "v2\n")
        for operation in OPERATIONS:
            self.assertEqual(self.kinds(operation), [], operation)
        self.assertTrue(treegate.in_place(self.conf, "WIP/Proposals/todo/x.md"))
        self.assertTrue(treegate.in_place(self.conf, ".CCNAVI/Approved/doing/x.md"))
        self.assertFalse(treegate.in_place(self.conf, "wip/proposalsX/x.md"))

    def test_only_ready_lets_a_removed_wip_file_through(self):
        os.remove(os.path.join(self.tree, "wip", "note.md"))
        for operation in OPERATIONS:
            expected = [] if operation == treegate.READY else [treegate.DIRTY_PARENT]
            self.assertEqual(self.kinds(operation), expected, operation)

    def test_unpushed_code_is_refused_by_the_operations_that_watch_it(self):
        self.commit("src/new.py")
        for operation in OPERATIONS:
            expected = [treegate.UNPUSHED_CODE] if operation in CODE_WATCHERS else []
            self.assertEqual(self.kinds(operation), expected, operation)

    def test_unpushed_places_only_matter_to_ready(self):
        self.commit("wip/proposals/todo/i0002.md")
        for operation in OPERATIONS:
            expected = [treegate.UNPUSHED_CODE] if operation == treegate.READY else []
            self.assertEqual(self.kinds(operation), expected, operation)

    def test_a_branch_never_sent_passes_only_the_approval_request(self):
        git(self.tree, "checkout", "-q", "-b", "fresh")
        for operation in OPERATIONS:
            expected = [treegate.UNPUSHED_CODE] if operation in CODE_WATCHERS[1:] else []
            self.assertEqual(self.kinds(operation), expected, operation)

    def test_the_approval_request_names_the_code_and_the_next_step(self):
        self.commit("src/new.py")
        (found,) = treegate.check(treegate.APPROVAL_REQUEST, self.tree, self.conf)
        self.assertIn("src/new.py", found.reason)
        self.assertEqual(found.paths, ("src/new.py",))
        self.assertTrue(found.hints)

    def test_every_operation_is_declared(self):
        self.assertEqual(set(treegate.WATCHES), set(OPERATIONS))


if __name__ == "__main__":
    unittest.main()
