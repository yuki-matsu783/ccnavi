"""`ccnavi tree parent` の約束。

sh はチケットの YAML を自分で読まず、これに聞く。答えは頭の行のあとに 1 行 1 項目
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


class TreeParentGateFactsTest(Harness):
    """push の関門が聞く 4 行（approved・requested・archived・mr）。"""

    def facts(self, name="p0001"):
        return self.answer("tree 1", "tree", "parent", self.tree, name)

    def test_the_lines_come_in_a_fixed_order(self):
        out = self.ask("tree", "parent", self.tree, "p0001").stdout.splitlines()
        self.assertEqual(
            ["tree 1", "parent no", "approved no", "requested no", "archived no", "mr -"], out
        )

    def test_nothing_says_no_everywhere(self):
        self.assertEqual(
            {
                "parent": "no",
                "approved": "no",
                "requested": "no",
                "archived": "no",
                "mr": "-",
            },
            self.facts(),
        )

    def test_approved_counts_doing_and_done_but_not_proposals(self):
        for rel in (f"{APPROVED}/doing", f"{APPROVED}/done"):
            with self.subTest(rel=rel):
                path = os.path.join(self.tree, rel, "p0001.md")
                write(path, front(ticket="p0001"))
                self.assertEqual("yes", self.facts()["approved"])
                os.remove(path)
        for rel in (f"{PROPOSALS}/todo", f"{PROPOSALS}/review"):
            with self.subTest(rel=rel):
                path = os.path.join(self.tree, rel, "p0001.md")
                write(path, front(ticket="p0001"))
                found = self.facts()
                self.assertEqual("yes", found["parent"])
                self.assertEqual("no", found["approved"])
                os.remove(path)

    def test_an_approved_child_is_not_an_approved_parent(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            front(ticket="p0001", parent="x"),
        )
        self.assertEqual("no", self.facts()["approved"])

    def test_unreadable_frontmatter_is_unreadable_for_approved_too(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            "---\nticket: [p0001\n---\n",
        )
        found = self.facts()
        self.assertEqual("unreadable", found["parent"])
        self.assertEqual("unreadable", found["approved"])

    def test_requested_needs_a_requested_marker(self):
        marks = os.path.join(self.tree, APPROVED, "phases", "p0001")
        write(os.path.join(marks, "1.reviewed"), "{}\n")
        write(os.path.join(marks, "x.requested"), "{}\n")
        self.assertEqual("no", self.facts()["requested"])
        write(os.path.join(marks, "2.requested"), "{}\n")
        self.assertEqual("yes", self.facts()["requested"])

    def test_a_marker_of_another_parent_does_not_count(self):
        write(os.path.join(self.tree, APPROVED, "phases", "p0002", "1.requested"), "{}\n")
        self.assertEqual("no", self.facts()["requested"])

    def test_archived_is_the_local_archive_of_a_parent(self):
        archive = os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md")
        write(archive, front(ticket="p0001", parent="x"))
        self.assertEqual("no", self.facts()["archived"])
        write(archive, front(ticket="p0001"))
        found = self.facts()
        self.assertEqual("yes", found["archived"])
        self.assertEqual("yes", found["parent"])

    def test_the_archive_is_not_read_outside_the_worktrees(self):
        outside = os.path.join(self.ws, "elsewhere")
        os.makedirs(outside)
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001"),
        )
        found = self.answer("tree 1", "tree", "parent", outside, "p0001")
        self.assertEqual("no", found["archived"])

    def test_mr_is_the_mr_field_of_the_approved_parent(self):
        write(os.path.join(self.tree, APPROVED, "doing", "p0001.md"), front(ticket="p0001", mr=430))
        self.assertEqual("430", self.facts()["mr"])

    def test_a_bad_mr_is_a_dash(self):
        for value in ("0", "-3", "abc", "[1]", '""'):
            with self.subTest(value=value):
                write(
                    os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
                    front(ticket="p0001", mr=value),
                )
                self.assertEqual("-", self.facts()["mr"])

    def test_mr_of_a_proposal_is_not_used(self):
        write(os.path.join(self.tree, PROPOSALS, "todo", "p0001.md"), front(ticket="p0001", mr=5))
        self.assertEqual("-", self.facts()["mr"])

    def test_mr_comes_from_the_archive_after_ready(self):
        write(
            os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md"),
            front(ticket="p0001", mr=77),
        )
        self.assertEqual("77", self.facts()["mr"])

    def test_the_old_closed_answer_is_gone(self):
        result = self.ask("sync", "closed", "p0001", "self", "main", self.tree)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)


if __name__ == "__main__":
    unittest.main()
