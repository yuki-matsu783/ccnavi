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


class TreeBoundTest(Harness):
    """`ccnavi tree bound`。`ccnavi-git.sh integrate` が断る条件（Stop の促しと同じ判定）。"""

    def setUp(self):
        super().setUp()
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "commit", "-q", "--allow-empty", "-m", "init")
        git(self.ws, "worktree", "add", "-q", "-b", "p0001", self.tree)

    def bound(self):
        return self.answer("tree 1", "tree", "bound", self.tree, "p0001")

    def test_nothing_ties_the_name_to_a_ticket(self):
        self.assertEqual({"bound": "no"}, self.bound())

    def test_a_ticket_or_proposal_of_the_same_name_binds(self):
        for rel in (
            f"{APPROVED}/doing/p0001.md",
            f"{APPROVED}/done/p0001.md",
            f"{PROPOSALS}/todo/p0001.md",
            f"{PROPOSALS}/review/p0001.md",
        ):
            path = os.path.join(self.ws, *rel.split("/"))
            write(path, front(ticket="p0001"))
            out = self.bound()
            self.assertEqual("yes", out["bound"], rel)
            self.assertIn(rel, out["why"])
            os.remove(path)
        self.assertEqual("no", self.bound()["bound"])

    def test_unregistered_directories_are_searched_too(self):
        """外れたワークツリーの残骸や、git リポジトリでないプロジェクトの置き場でも断る。"""
        for rel in (
            f".claude/worktrees/stale/{APPROVED}/doing/p0001.md",
            f"projects/pp/{APPROVED}/doing/p0001.md",
        ):
            path = os.path.join(self.ws, *rel.split("/"))
            write(path, front(ticket="p0001"))
            out = self.bound()
            self.assertEqual("yes", out["bound"], rel)
            self.assertIn(rel, out["why"])
            os.remove(path)

    def test_the_file_name_is_compared_ignoring_case(self):
        write(os.path.join(self.ws, *f"{APPROVED}/doing/P0001.md".split("/")), front(ticket="p"))
        self.assertEqual("yes", self.bound()["bound"])

    def test_a_branch_on_origin_binds(self):
        git(self.ws, "update-ref", "refs/remotes/origin/p0001", "p0001")
        out = self.bound()
        self.assertEqual("yes", out["bound"])
        self.assertIn("origin/p0001", out["why"])

    def test_a_bad_name_or_tree_is_refused(self):
        self.assertEqual(1, self.ask("tree", "bound", self.tree, "../x").returncode)
        self.assertEqual(
            1, self.ask("tree", "bound", os.path.join(self.ws, "none"), "p0001").returncode
        )


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
    """push の関門が聞く 5 行（approved・requested・archived・started・mr）。"""

    def facts(self, name="p0001"):
        return self.answer("tree 1", "tree", "parent", self.tree, name)

    def test_the_lines_come_in_a_fixed_order(self):
        out = self.ask("tree", "parent", self.tree, "p0001").stdout.splitlines()
        self.assertEqual(
            [
                "tree 1",
                "parent no",
                "approved no",
                "requested no",
                "archived no",
                "started no",
                "mr -",
            ],
            out,
        )

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

    def test_started_is_no_for_a_proposal_a_child_and_unreadable_frontmatter(self):
        write(
            os.path.join(self.tree, PROPOSALS, "todo", "p0001.md"),
            front(ticket="p0001", started_at="2026-10-18T00:00:00Z"),
        )
        self.assertEqual("no", self.facts()["started"])
        done = os.path.join(self.tree, APPROVED, "doing", "p0001.md")
        write(done, front(ticket="p0001", parent="x", started_at="2026-10-18T00:00:00Z"))
        self.assertEqual("no", self.facts()["started"])
        write(done, "---\nticket: [p0001\n---\n")
        self.assertEqual("no", self.facts()["started"])

    def test_nothing_says_no_everywhere(self):
        self.assertEqual(
            {
                "parent": "no",
                "approved": "no",
                "requested": "no",
                "archived": "no",
                "started": "no",
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


class TreeBaseBoundTest(Harness):
    """`ccnavi tree base-bound`。取り込み先のブランチがチケットに結び付くか。
    `ccnavi-worktree.sh` はこれで断る。

    識別子の衝突はワークスペース全体、結び付きは取り込み先のリポジトリだけを見る。
    """

    def setUp(self):
        super().setUp()
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "commit", "-q", "--allow-empty", "-m", "init")
        self.p = os.path.join(self.ws, "projects", "p")
        self.q = os.path.join(self.ws, "projects", "q")
        for repo in (self.p, self.q):
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "commit", "-q", "--allow-empty", "-m", "init")

    def bound(self, repo=None, branch="feature/x"):
        return self.answer("tree 1", "tree", "base-bound", repo or self.p, branch)

    def test_nothing_ties_the_branch(self):
        self.assertEqual({"bound": "no"}, self.bound())

    def test_each_material_binds(self):
        cases = {
            "identifier": (f"projects/p/{APPROVED}/doing/feature-x.md", front(ticket="feature-x")),
            "branch field": (
                f"projects/p/{APPROVED}/done/t1.md",
                front(ticket="t1", branch="feature/x"),
            ),
            "proposal anywhere": (f"projects/q/{PROPOSALS}/todo/feature-x.md", front(ticket="x")),
        }
        for label, (rel, text) in cases.items():
            with self.subTest(label):
                path = os.path.join(self.ws, *rel.split("/"))
                write(path, text)
                out = self.bound()
                self.assertEqual("yes", out["bound"])
                self.assertIn(rel, out["why"])
                os.remove(path)
        self.assertEqual("no", self.bound()["bound"])

    def test_another_repository_branch_field_does_not_bind(self):
        write(
            os.path.join(self.q, *f"{APPROVED}/doing/t1.md".split("/")),
            front(ticket="t1", branch="feature/x"),
        )
        self.assertEqual({"bound": "no"}, self.bound(self.p))
        self.assertEqual("yes", self.bound(self.q)["bound"])

    def test_identifier_collision_is_checked_across_the_workspace(self):
        # 識別子の衝突はワークスペース全体。別のリポジトリの承認済みチケットでも、
        # 同じ識別子なら取り込み先のブランチは断る（結び付きの `branch:` 欄とは範囲が違う）。
        rel = f"projects/q/{APPROVED}/doing/feature-x.md"
        write(os.path.join(self.ws, *rel.split("/")), front(ticket="feature-x"))
        out = self.bound(self.p)
        self.assertEqual("yes", out["bound"])
        self.assertIn(rel, out["why"])

    def test_leftover_ticket_files_on_the_default_branch_do_not_bind(self):
        write(
            os.path.join(self.p, *f"{APPROVED}/done/old.md".split("/")),
            front(ticket="old", branch="other"),
        )
        self.assertEqual({"bound": "no"}, self.bound(self.p, "main"))

    def test_undecidable_is_unknown(self):
        write(os.path.join(self.p, *f"{APPROVED}/doing/broken.md".split("/")), "---\n: [\n---\n")
        self.assertEqual("unknown", self.bound()["bound"])
        self.assertEqual("unknown", self.bound(os.path.join(self.ws, "projects"))["bound"])

    def test_a_bad_tree_is_refused(self):
        self.assertEqual(
            1, self.ask("tree", "base-bound", os.path.join(self.ws, "none"), "x").returncode
        )
