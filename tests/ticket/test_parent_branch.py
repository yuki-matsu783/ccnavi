"""親のブランチ名と識別子の変換（識別子 = ブランチ名の `/` を `-` に置き換えたもの）。

一時ディレクトリの git で、識別子 `feature-12-login` の親が `feature/12-login` を
直接チェックアウトする。
見るのは次のとおり（sh を通す主な経路は tests/sh/test_branch_field_sh.py が見る）。

1. 字と形（`ticket_ids.branch_problem`）。保護されたブランチの名前と git で使えない表記は error
2. チケットに `branch:` の欄があればエラー（使えない欄）。子に書いても同じ
3. 承認画面と JSON にブランチの行は出ない。ブランチ名と同じ識別子の既存ブランチが別の所にあれば warn
4. 識別子が大文字小文字だけ違う親は承認しない
5. lint: 親のワークツリーの HEAD を変換した識別子がフォルダ名と合わなければ warn。既存のブランチを
   採用した親（親のワークツリーが在り、HEAD が合う）には名前の形式の warn を出さない
6. `write-and-push family` の `branch` は親のワークツリーの HEAD から読む。読めない・合わないときは
   `branch_refused`
7. Chrome の入口: ブランチ名を変換した識別子の親チケットがそのブランチの上にあれば親子のチケットで、
   書くものはそのブランチだけ。同じ識別子になる別のブランチに親チケットがあれば、先行の親子のチケット
   でも決めない
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.entry import version
from ccnavi.tickets.model import ticket as ticket_mod
from ccnavi.tickets.model import ticket_ids
from tests import ROOT, common_path, config_path, userform
from tests.inproc import run_ccnavi
from tests.ticket.test_swap_points import STAMP
from tests.ticket.test_ticket import git, write
from tests.ticketsample import PHASES, RULES, child_text, load_chrome, parent_text

PARENT = "feature-12-login"
BRANCH = "feature/12-login"
CHILD = PARENT + "-01-01"
HEAD = "0" * 40


def with_branch(text, branch):
    """親の提案の frontmatter に古い `branch:` を足す（`ticket:` の行の後ろ）。"""
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("ticket:"))
    return "\n".join([*lines[: at + 1], f"branch: {branch}", *lines[at + 1 :]])


class BranchProblemTest(unittest.TestCase):
    def test_names_that_can_be_a_parent_branch(self):
        for name in ("feature/123-login", "hotfix/45", "user/x_y.z", "feature-1-x", "a/日本語"):
            with self.subTest(name=name):
                self.assertEqual(ticket_ids.branch_problem(name), "")

    def test_names_that_cannot(self):
        for name in (
            "",
            "a..b",
            "a b",
            "a\tb",
            "a$b",
            "a;b",
            "a~1",
            "a^b",
            "a:b",
            "a?b",
            "a*b",
            "a[b",
            "a\\b",
            "HEAD",
            "origin/main",
            "origin/HEAD",
            "refs/heads/main",
            "refs/remotes/origin/main",
            "heads/main",
            "remotes/x",
            "tags/v1",
            "upstream/x",
            "FETCH_HEAD",
            "main/x",
            "Develop/x",
            "x/HEAD",
            "x/ORIG_HEAD/y",
            "-x",
            "/x",
            "x/",
            "x.",
            "a//b",
            "a/.b",
            ".x",
            "x.lock",
            "x.lock/y",
            "@",
            "a@{1}",
            "main",
            "Master",
            "develop",
            "release",
            "release/1.0",
            "Release-2",
            "ｆｕｌｌ",
            "x" * 201,
        ):
            with self.subTest(name=name):
                self.assertNotEqual(ticket_ids.branch_problem(name), "")


class BranchFieldParseTest(unittest.TestCase):
    """`branch:` の欄は使えない（エラー）。"""

    def test_a_parent_with_the_field_is_an_error(self):
        t, problems = ticket_mod.parse(with_branch(parent_text(PARENT, ["design"]), BRANCH))
        self.assertIsNone(t)
        self.assertTrue(
            any(
                p.severity == "error" and "`branch` は指定できない欄" in p.detail for p in problems
            ),
            problems,
        )

    def test_a_child_with_the_field_is_an_error_too(self):
        text = child_text(CHILD, PARENT, 1, ("wip/*",))
        lines = text.split("\n")
        lines.insert(3, f"branch: {BRANCH}")
        t, problems = ticket_mod.parse("\n".join(lines))
        self.assertIsNone(t)
        self.assertTrue(any("`branch` は指定できない欄" in p.detail for p in problems), problems)


class ParentBranchTest(unittest.TestCase):
    """ワークスペースと親のワークツリー（`.claude/worktrees/feature-12-login`）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-parent-branch-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        write(common_path(self.root, "rules"), json.dumps(RULES))
        write(config_path(self.root, "phases"), PHASES)
        self.state = os.path.join(self.root, "logs", "state")
        self.tree = os.path.join(self.root, ".claude", "worktrees", PARENT)

    def ccnavi(self, *args, stdin=""):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            userform.place(
                [
                    "--root",
                    self.root,
                    "--guard-core-files",
                    "disable",
                    "--restore-if-deny",
                    "disable",
                    "--guard-ticket-approval",
                    "disable",
                ],
                args,
            ),
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def parent_tree(self, branch=BRANCH, create=True):
        args = ["worktree", "add", "--quiet", self.tree]
        args += ["-b", branch, "main"] if create else [branch]
        git(self.root, *args)

    def propose(self, where=None, plan=("design",), ident=PARENT, allow=("src/*", "wip/*")):
        base = where or self.tree
        write(
            os.path.join(base, "wip", "proposals", "todo", ident + ".md"),
            parent_text(ident, list(plan), allow=allow),
        )

    def preview(self):
        done = self.ccnavi("--agree", "--preview", "--verify", "--json")
        return json.loads(done.stdout)

    def approve(self):
        done = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_screen_has_no_branch_line(self):
        self.parent_tree()
        self.propose()
        body = self.preview()
        self.assertTrue(body["verify"]["ok"], body)
        entry = body["batch"][0]
        self.assertNotIn("branch", entry)
        self.assertNotIn("existing_branch", entry)
        self.assertNotIn("■ ブランチ", body["text"])
        self.assertEqual(body["branch_warnings"], [])

    def test_a_proposal_outside_the_parent_worktree_is_refused_not_warned(self):
        """提案が親のワークツリーの外（ここではワークスペースルート）にあれば、承認の対象に入らない。

        親のブランチ名の警告は、親のワークツリーの中の提案にだけ付く（その HEAD が親のブランチ）。
        """
        git(self.root, "branch", BRANCH)
        self.propose(where=self.root)
        body = self.preview()
        self.assertEqual(body["batch"], [])
        self.assertEqual(body["branch_warnings"], [])
        self.assertTrue(
            any("親のワークツリー" in " ".join(r["problems"]) for r in body["rejected"]), body
        )

    def test_a_proposal_on_its_own_branch_is_not_warned(self):
        git(self.root, "branch", BRANCH)
        self.parent_tree(create=False)
        self.propose()
        self.assertEqual(self.preview()["branch_warnings"], [])

    def test_identifiers_differing_only_in_case_are_refused(self):
        # 大文字小文字を区別しないファイルシステム（macOS の既定）では、2 つの提案が
        # 1 つのファイルになり、「大文字小文字だけが違う識別子」の状態を作れない
        with tempfile.TemporaryDirectory() as probe:
            write(os.path.join(probe, "A"), "")
            if os.path.exists(os.path.join(probe, "a")):
                self.skipTest("大文字小文字を区別しないファイルシステム")
        other = "Feature-12-Login"
        self.propose(where=self.root, ident=other)
        self.propose(where=self.root)
        body = self.preview()
        problems = " ".join(" ".join(r["problems"]) for r in body["rejected"])
        self.assertIn("大文字小文字だけが違う識別子", problems)
        self.assertEqual(sorted(r["ticket"] for r in body["rejected"]), sorted([PARENT, other]))

    def test_lint_is_quiet_on_the_branch_of_the_identifier(self):
        self.parent_tree()
        self.propose()
        self.approve()
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("の上にある", lint.stdout)

    def test_lint_warns_when_the_parent_tree_is_on_another_branch(self):
        self.parent_tree()
        self.propose()
        self.approve()
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")
        git(self.tree, "checkout", "-q", "-b", "elsewhere")
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn(f"親 {PARENT} のワークツリーが elsewhere の上にある", lint.stdout)
        self.assertIn(f"`/` を `-` に置き換えたものがフォルダ名（{PARENT}）", lint.stdout)

    def test_lint_names_a_left_over_field_as_an_error(self):
        self.parent_tree()
        write(
            os.path.join(self.tree, "wip", "proposals", "todo", PARENT + ".md"),
            with_branch(parent_text(PARENT, ["design"], allow=("src/*", "wip/*")), BRANCH),
        )
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("`branch` は指定できない欄", lint.stdout)

    def test_lint_does_not_say_the_name_form_of_an_adopted_parent(self):
        """既存のブランチを採用した親（ワークツリーが在り、HEAD が合う）は名前の形式を言わない。"""
        ident = "weird-login"
        self.tree = os.path.join(self.root, ".claude", "worktrees", ident)
        self.propose(where=self.root, ident=ident)
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn(f"{ident}: 親の識別子は", lint.stdout)
        shutil.rmtree(os.path.join(self.root, "wip"))
        git(self.root, "worktree", "add", "--quiet", self.tree, "-b", "weird/login", "main")
        self.propose(ident=ident)
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn(f"{ident}: 親の識別子は", lint.stdout)

    def test_write_and_push_family_reads_the_branch_from_head(self):
        self.parent_tree()
        self.propose()
        self.approve()
        done = self.ccnavi("write-and-push", "family", PARENT)
        self.assertIn(f"branch {BRANCH}\n", done.stdout)

    def test_write_and_push_family_refuses_without_a_matching_head(self):
        done = self.ccnavi("write-and-push", "family", "feature-99-none")
        self.assertIn("branch_refused ", done.stdout)
        self.assertNotIn("\nbranch ", done.stdout)
        self.parent_tree()
        git(self.tree, "checkout", "-q", "-b", "elsewhere")
        done = self.ccnavi("write-and-push", "family", PARENT)
        self.assertIn("branch_refused ", done.stdout)
        self.assertNotIn("\nbranch ", done.stdout)


def _workspace_files():
    compat = os.path.join(".ccnavi", "scripts", "ccnavi-common.sh").replace(os.sep, "/")
    return {
        ".claude/settings.json": json.dumps({"env": {"CCNAVI_TICKET_CONTROL": "enable"}}) + "\n",
        ".ccnavi/config/project/phases.yml": PHASES,
        ".ccnavi/config/common/rules.yml": json.dumps(RULES),
        compat: f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n",
    }


def _approved(text):
    return text.replace(
        "---\n\n本文", "ccnavi_approved:\n  approved_at: 2026-10-01T00:00:00+0900\n---\n\n本文"
    )


def _family_files(ident=PARENT, predecessors=()):
    """親のブランチの置き場。承認済みの親チケットと、承認待ちの子の提案。"""
    child = child_text(f"{ident}-01-01", ident, 1, ["wip/research/*"], False)
    if predecessors:
        lines = child.split("\n")
        lines[5:5] = ["predecessors:", *[f"  - {p}" for p in predecessors]]
        child = "\n".join(lines)
    return {
        f".ccnavi/approved/doing/{ident}.md": _approved(
            parent_text(ident, ["research"], allow=("src/*", "wip/*"))
        ),
        f"wip/proposals/todo/{ident}-01-01.md": child,
    }


class ChromeBranchFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-chrome-branch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.chrome = load_chrome()

    def ask(self, op, family=BRANCH, branches=None, absent=None, **extra):
        files = _workspace_files()
        snapshot = {
            "integration": {"name": "main", "source": "default", "head": HEAD},
            "branches": {
                "main": {"head": HEAD, "files": files, "binary": []},
                **(
                    branches
                    if branches is not None
                    else {BRANCH: {"head": HEAD, "files": _family_files()}}
                ),
            },
            # 識別子と同じ名前のブランチ（同じ識別子になる別のブランチが無いかを確かめに読む）は
            # ホストに無い
            "absent": [PARENT] if absent is None else absent,
        }
        request = {
            "schema": self.chrome.SCHEMA,
            "op": op,
            "family": family,
            "stamp": STAMP,
            "settings": files[".claude/settings.json"],
            "snapshot": snapshot,
            **extra,
        }
        return json.loads(self.chrome.handle(json.dumps(request), os.path.join(self.tmp, "memfs")))

    def test_the_family_is_named_by_the_converted_branch(self):
        found = self.ask("families", candidates=[BRANCH])
        self.assertNotIn("error", found, found)
        self.assertEqual(
            [(f["name"], f["family"], f["conflict"]) for f in found["families"]],
            [(BRANCH, PARENT, "")],
        )

    def test_a_proposal_is_a_family_too(self):
        proposal = {
            f"wip/proposals/todo/{PARENT}.md": parent_text(PARENT, ["research"], allow=("src/*",))
        }
        branches = {BRANCH: {"head": HEAD, "files": proposal}}
        found = self.ask("families", candidates=[BRANCH], branches=branches)
        self.assertEqual([(f["name"], f["family"]) for f in found["families"]], [(BRANCH, PARENT)])

    def test_a_branch_whose_converted_name_is_not_the_identifier_is_not_a_family(self):
        found = self.ask(
            "families",
            candidates=["feature/12-elsewhere"],
            branches={"feature/12-elsewhere": {"head": HEAD, "files": _family_files()}},
        )
        self.assertEqual(found["families"], [])

    def test_the_board_and_the_plan_write_only_the_branch(self):
        board = self.ask("board")
        self.assertNotIn("error", board, board)
        self.assertEqual(board["ident"], PARENT)
        self.assertEqual([e["ticket"] for e in board["batch"]], [CHILD])
        body = self.ask(
            "plan",
            ids=[e["ticket"] for e in board["batch"]],
            actor={"account": "a"},
        )
        self.assertNotIn("error", body, body)
        self.assertEqual(list(body["changes"]), [BRANCH])
        paths = {r["path"] for r in body["changes"][BRANCH]}
        self.assertIn(f".ccnavi/approved/doing/{CHILD}.md", paths)

    def test_two_branches_with_one_identifier_are_not_judged(self):
        rival = {
            f".ccnavi/approved/doing/{PARENT}.md": _approved(
                parent_text(PARENT, ["research"], allow=("src/*",))
            ),
        }
        branches = {
            BRANCH: {"head": HEAD, "files": _family_files()},
            PARENT: {"head": HEAD, "files": rival},
        }
        found = self.ask("families", candidates=[BRANCH, PARENT], branches=branches)
        conflicts = [f["conflict"] for f in found["families"]]
        self.assertTrue(all("1 本ではありません" in c for c in conflicts), found)
        board = self.ask("board", branches=branches, absent=[])
        self.assertIn("1 本ではありません", board.get("undecided", ""), board)
        plan = self.ask("plan", branches=branches, absent=[], ids=[CHILD])
        self.assertIn("1 本ではありません", plan.get("error", ""), plan)

    def test_a_predecessor_family_with_two_branches_is_not_judged(self):
        other = "feature-20-dep"
        dep = _family_files(ident=other)
        branches = {
            BRANCH: {"head": HEAD, "files": _family_files(predecessors=(f"{other}-01-01",))},
            "feature/20-dep": {"head": HEAD, "files": dep},
            other: {"head": HEAD, "files": dep},
        }
        closure = self.ask("closure", branches=branches)
        self.assertEqual(closure["ambiguous"], [other])
        board = self.ask("board", branches=branches)
        self.assertIn(
            "先行の親子のチケット（feature-20-dep）になるブランチが 1 本ではありません",
            board["undecided"],
        )

    def test_the_closure_asks_for_the_identifier_branch(self):
        closure = self.ask("closure", absent=[])
        self.assertEqual(closure["idents"], {BRANCH: PARENT})
        self.assertEqual(closure["rivals"], [PARENT])
        self.assertIn(PARENT, closure["need"])


if __name__ == "__main__":
    unittest.main()
