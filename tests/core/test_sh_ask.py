"""`ccnavi tree parent` と `ccnavi sync closed` の約束。

sh はチケットの YAML を自分で読まず、この 2 つに聞く。答えは頭の行のあとに 1 行 1 項目
（`<鍵> <値>`）。読むだけで、何も書かない。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

APPROVED = ".ccnavi/approved"
PROPOSALS = "wip/proposals"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def front(**fields):
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    return f"---\n{body}---\n本文\n"


class Harness(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-sh-ask-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        self.tree = os.path.join(self.ws, ".claude", "worktrees", "p0001")
        os.makedirs(self.tree)

    def ask(self, *words):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(["--root", self.ws, *words], env=environment)

    def answer(self, head, *words):
        result = self.ask(*words)
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout.splitlines()
        self.assertEqual(head, out[0])
        return dict((line.split(" ", 1) + [""])[:2] for line in out[1:])


class TreeParentTest(Harness):
    def parent(self, name="p0001", tree=None):
        return self.answer("tree 1", "tree", "parent", tree or self.tree, name)["parent"]

    def test_no_ticket_is_not_a_parent(self):
        self.assertEqual("no", self.parent())

    def test_each_place_holds_a_parent(self):
        for rel in (
            f"{APPROVED}/doing",
            f"{APPROVED}/done",
            f"{PROPOSALS}/todo",
            f"{PROPOSALS}/review",
        ):
            with self.subTest(rel=rel):
                path = os.path.join(self.tree, rel, "p0001.md")
                write(path, front(ticket="p0001"))
                self.assertEqual("yes", self.parent())
                os.remove(path)

    def test_a_child_is_not_a_parent(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            front(ticket="p0001", parent="x"),
        )
        self.assertEqual("no", self.parent())

    def test_another_id_inside_is_not_a_parent(self):
        write(os.path.join(self.tree, APPROVED, "doing", "p0001.md"), front(ticket="p0002"))
        self.assertEqual("no", self.parent())

    def test_quoted_id_is_read_as_yaml(self):
        write(os.path.join(self.tree, APPROVED, "doing", "p0001.md"), front(ticket='"p0001"'))
        self.assertEqual("yes", self.parent())

    def test_a_parent_line_in_the_body_does_not_make_a_child(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            "---\nticket: p0001\n---\nparent: これは本文\n",
        )
        self.assertEqual("yes", self.parent())

    def test_unreadable_frontmatter_is_said_so(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            "---\nticket: [p0001\n---\n",
        )
        self.assertEqual("unreadable", self.parent())

    def test_the_archive_counts_after_ready(self):
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001"),
        )
        self.assertEqual("yes", self.parent())

    def test_an_archived_child_does_not_count(self):
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001", parent="x"),
        )
        self.assertEqual("no", self.parent())

    def test_a_bad_name_is_refused(self):
        result = self.ask("tree", "parent", self.tree, "../x")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)

    def test_a_missing_tree_is_refused(self):
        result = self.ask("tree", "parent", os.path.join(self.ws, "nowhere"), "p0001")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


class SyncClosedTest(Harness):
    def setUp(self):
        super().setUp()
        git(self.tree, "init", "-q", "-b", "p0001")
        git(self.tree, "commit", "-q", "--allow-empty", "-m", "init")

    def closed(self):
        return self.answer("closed 1", "sync", "closed", "p0001", "self", "main", self.tree)

    def integrate(self, text):
        # 統合先（refs/remotes/origin/main）の done/ に親チケットを置く。
        other = os.path.join(self.ws, "other")
        os.makedirs(other)
        git(other, "init", "-q", "-b", "main")
        write(os.path.join(other, APPROVED, "done", "p0001.md"), text)
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "done")
        git(self.tree, "fetch", "-q", other, "main:refs/remotes/origin/main")

    def mine(self, **fields):
        write(os.path.join(self.tree, APPROVED, "doing", "p0001.md"), front(**fields))

    def test_nothing_is_not_closed(self):
        self.assertEqual({"integration": "no", "archive": "no"}, self.closed())

    def test_the_same_parent_in_the_integration_is_closed(self):
        self.mine(ticket="p0001", base_sha="abc")
        self.integrate(front(ticket="p0001", base_sha="abc"))
        self.assertEqual("yes", self.closed()["integration"])

    def test_another_parent_of_the_same_id_is_not_closed(self):
        self.mine(ticket="p0001", base_sha="abc")
        self.integrate(front(ticket="p0001", base_sha="def"))
        self.assertEqual("no", self.closed()["integration"])

    def test_no_parent_in_the_tree_is_not_closed(self):
        self.integrate(front(ticket="p0001", base_sha="abc"))
        self.assertEqual("no", self.closed()["integration"])

    def test_a_child_in_the_integration_is_not_closed(self):
        self.mine(ticket="p0001", base_sha="abc")
        self.integrate(front(ticket="p0001", parent="x", base_sha="abc"))
        self.assertEqual("no", self.closed()["integration"])

    def test_the_archive_without_a_parent_in_the_tree_is_closed(self):
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001", base_sha="abc"),
        )
        self.assertEqual("yes", self.closed()["archive"])

    def test_the_archive_of_another_parent_is_not_closed(self):
        self.mine(ticket="p0001", base_sha="abc")
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001", base_sha="def"),
        )
        self.assertEqual("no", self.closed()["archive"])

    def test_bad_words_are_refused(self):
        for words in (
            ("../x", "self", "main", self.tree),
            ("p0001", "../x", "main", self.tree),
            ("p0001", "self", "-x", self.tree),
            ("p0001", "self", "a..b", self.tree),
            ("p0001", "self", "main", os.path.join(self.ws, "nowhere")),
        ):
            with self.subTest(words=words):
                result = self.ask("sync", "closed", *words)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
