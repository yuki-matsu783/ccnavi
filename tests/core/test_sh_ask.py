"""`ccnavi tree parent` の約束。

sh はチケットの YAML を自分で読まず、これに聞く。答えは頭の行のあとに 1 行 1 項目
（`<キー> <値>`）。読むだけで、何も書かない。
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
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(["--root", self.ws, *words], env=environment)

    def answer(self, head, *words):
        result = self.ask(*words)
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout.splitlines()
        self.assertEqual(head, out[0])
        return dict((line.split(" ", 1) + [""])[:2] for line in out[1:])


class TreeParentTest(Harness):
    """答えの行の並びと、無い・壊れた・退避だけの親の答えは、go/internal/entry/shask の見本
    （TestAnswersMatchGolden）が 1 字ずつ見る。ここは親の見分け方を、ファイルの形ごとに見る。"""

    def parent(self, name="p0001", tree=None):
        return self.answer("tree 1", "tree", "parent", tree or self.tree, name)["parent"]

    def test_the_shape_of_a_ticket_file_decides_whether_it_is_a_parent(self):
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
        doing = os.path.join(self.tree, APPROVED, "doing", "p0001.md")
        archived = os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md")
        cases = [
            (doing, front(ticket="p0001", parent="x"), "no"),
            (doing, front(ticket="p0002"), "no"),
            (doing, front(ticket='"p0001"'), "yes"),
            (doing, "---\nticket: p0001\n---\nparent: これは本文\n", "yes"),
        ]
        for path, text, want in cases:
            with self.subTest(text=text):
                write(path, text)
                self.assertEqual(want, self.parent())
        os.remove(doing)
        write(archived, front(ticket="p0001", parent="x"))
        self.assertEqual("no", self.parent())


class TreeParentGateFactsTest(Harness):
    """push のゲートが聞く 5 行（approved・requested・archived・started・mr）。"""

    def facts(self, name="p0001"):
        return self.answer("tree 1", "tree", "parent", self.tree, name)

    def test_started_needs_a_started_at_in_the_approved_parent(self):
        path = os.path.join(self.tree, APPROVED, "doing", "p0001.md")
        write(path, front(ticket="p0001"))
        self.assertEqual("no", self.facts()["started"])
        write(path, front(ticket="p0001", started_at='""'))
        self.assertEqual("no", self.facts()["started"])
        write(path, front(ticket="p0001", started_at="2026-10-18T00:00:00Z"))
        found = self.facts()
        self.assertEqual("yes", found["started"])
        self.assertEqual("yes", found["approved"])
        # 提案・子・読めない frontmatter では始まっていない。
        write(path, front(ticket="p0001", parent="x", started_at="2026-10-18T00:00:00Z"))
        self.assertEqual("no", self.facts()["started"])
        write(path, "---\nticket: [p0001\n---\n")
        self.assertEqual("no", self.facts()["started"])
        os.remove(path)
        write(
            os.path.join(self.tree, PROPOSALS, "todo", "p0001.md"),
            front(ticket="p0001", started_at="2026-10-18T00:00:00Z"),
        )
        self.assertEqual("no", self.facts()["started"])

    def test_approved_counts_doing_and_done_but_not_proposals_or_children(self):
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
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            front(ticket="p0001", parent="x"),
        )
        self.assertEqual("no", self.facts()["approved"])

    def test_requested_needs_a_requested_marker_of_this_parent(self):
        marks = os.path.join(self.tree, APPROVED, "phases", "p0001")
        write(os.path.join(marks, "1.reviewed"), "{}\n")
        write(os.path.join(marks, "x.requested"), "{}\n")
        write(os.path.join(self.tree, APPROVED, "phases", "p0002", "1.requested"), "{}\n")
        self.assertEqual("no", self.facts()["requested"])
        write(os.path.join(marks, "2.requested"), "{}\n")
        self.assertEqual("yes", self.facts()["requested"])

    def test_archived_is_the_local_archive_of_a_parent(self):
        archive = os.path.join(self.ws, "logs", "archive", "self", "done", "p0001.md")
        write(archive, front(ticket="p0001", parent="x"))
        self.assertEqual("no", self.facts()["archived"])
        write(archive, front(ticket="p0001"))
        found = self.facts()
        self.assertEqual("yes", found["archived"])
        self.assertEqual("yes", found["parent"])

    def test_mr_is_the_mr_field_of_the_approved_parent(self):
        path = os.path.join(self.tree, APPROVED, "doing", "p0001.md")
        write(path, front(ticket="p0001", mr=430))
        self.assertEqual("430", self.facts()["mr"])
        for value in ("0", "-3", "abc", "[1]", '""'):
            with self.subTest(value=value):
                write(path, front(ticket="p0001", mr=value))
                self.assertEqual("-", self.facts()["mr"])
        # 提案の mr は使わない。
        os.remove(path)
        write(os.path.join(self.tree, PROPOSALS, "todo", "p0001.md"), front(ticket="p0001", mr=5))
        self.assertEqual("-", self.facts()["mr"])


if __name__ == "__main__":
    unittest.main()
