"""親チケットの `branch:`（ADR-0100 の 5 章）の読み方・承認・lint・Chrome の入口。

一時ディレクトリの git で、識別子 `feature-12-login` の親に `branch: feature/12-login` を書く。
見るのは次のとおり（sh を通す主な経路は tests/sh/test_branch_field_sh.py が見る）。

1. 字と形（`ticket.branch_problem`）。保護されたブランチの名前と git で使えない綴りは error
2. 子に書いた `branch:` は warn で読まない。使えない名前の提案は読めない（error）
3. 承認画面と JSON: 「■ ブランチ」と「既存のブランチ <名前> を使う」/「新しく切るブランチ」、
   `branch`・`existing_branch`。既にあるブランチとのぶつかりの warn は出さない
4. 改版で `branch:` を変えさせない。統合先の名前に当たる `branch:` は承認しない
5. lint: 親のワークツリーが親のブランチ（`branch:` の値）の上に居なければ warn
6. Chrome の入口: 家族はそのブランチを名乗る承認済みの親の写しで決まり（承認前の提案は識別子で
   名乗る）、書くものはそのブランチだけ。同じ家族を名乗るブランチが 2 本あれば、先行の家族でも
   決めない
7. 承認前の提案の `branch:` は c1 family も lint も使わない。統合先（origin/HEAD を含む）と、
   2 つの家族が同じブランチを名乗る形は承認しない
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.entry import version
from ccnavi.tickets import ticket as ticket_mod
from tests import ROOT, common_path
from tests.inproc import run_ccnavi
from tests.ticket.test_core import STAMP, _chrome
from tests.ticket.test_phases import PHASES, child_text, parent_text
from tests.ticket.test_ticket import RULES, git, write

PARENT = "feature-12-login"
BRANCH = "feature/12-login"
CHILD = PARENT + "-01"
HEAD = "0" * 40


def with_branch(text, branch):
    """親の提案の frontmatter に `branch:` を足す（`ticket:` の行の後ろ）。"""
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("ticket:"))
    return "\n".join([*lines[: at + 1], f"branch: {branch}", *lines[at + 1 :]])


class BranchProblemTest(unittest.TestCase):
    def test_names_that_can_be_a_parent_branch(self):
        for name in ("feature/123-login", "hotfix/45", "user/x_y.z", "feature-1-x", "a/日本語"):
            with self.subTest(name=name):
                self.assertEqual(ticket_mod.branch_problem(name), "")

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
                self.assertNotEqual(ticket_mod.branch_problem(name), "")

    def test_the_branch_name_of_a_ticket(self):
        parent = ticket_mod.Ticket(ticket=PARENT, branch=BRANCH)
        self.assertEqual(ticket_mod.branch_name(parent), BRANCH)
        self.assertEqual(ticket_mod.branch_name(ticket_mod.Ticket(ticket=PARENT)), PARENT)
        child = ticket_mod.Ticket(ticket=CHILD, parent=PARENT, branch=BRANCH)
        self.assertEqual(ticket_mod.branch_name(child), CHILD)


class BranchFieldParseTest(unittest.TestCase):
    def test_a_parent_reads_the_field(self):
        t, problems = ticket_mod.parse(with_branch(parent_text(PARENT, ["design"]), BRANCH))
        self.assertIsNotNone(t)
        self.assertEqual(t.branch, BRANCH)
        self.assertEqual([p for p in problems if p.severity == "error"], [])

    def test_a_child_does_not_read_the_field(self):
        text = child_text(CHILD, PARENT, 1, ("wip/*",))
        lines = text.split("\n")
        lines.insert(3, f"branch: {BRANCH}")
        t, problems = ticket_mod.parse("\n".join(lines))
        self.assertIsNotNone(t)
        self.assertEqual(t.branch, "")
        self.assertTrue(any("親だけの欄" in p.detail for p in problems), problems)

    def test_an_unusable_name_makes_the_proposal_unreadable(self):
        for name in ("main", "a..b", "release/1"):
            with self.subTest(name=name):
                t, problems = ticket_mod.parse(with_branch(parent_text(PARENT, ["design"]), name))
                self.assertIsNone(t)
                self.assertTrue(any("`branch:" in p.detail for p in problems), problems)


class BranchFieldApprovalTest(unittest.TestCase):
    """ワークスペースと親のワークツリー（`.claude/worktrees/feature-12-login`）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-branch-field-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        write(common_path(self.root, "rules"), json.dumps(RULES))
        write(common_path(self.root, "phases"), PHASES)
        self.state = os.path.join(self.root, "state")
        self.tree = os.path.join(self.root, ".claude", "worktrees", PARENT)

    def ccnavi(self, *args, stdin=""):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--approved",
                ".ccnavi/approved",
                "--state",
                self.state,
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def parent_tree(self, branch=BRANCH, create=True):
        args = ["worktree", "add", "--quiet", self.tree]
        args += ["-b", branch, "main"] if create else [branch]
        git(self.root, *args)

    def propose(self, branch=BRANCH, where=None, plan=("design",)):
        base = where or self.tree
        write(
            os.path.join(base, "wip", "proposals", "todo", PARENT + ".md"),
            with_branch(parent_text(PARENT, list(plan), allow=("src/*", "wip/*")), branch),
        )

    def preview(self):
        done = self.ccnavi("--agree", "--preview", "--verify", "--json")
        return json.loads(done.stdout)

    def test_the_screen_says_the_existing_branch_is_used(self):
        self.parent_tree()
        self.propose()
        body = self.preview()
        self.assertTrue(body["verify"]["ok"], body)
        entry = body["batch"][0]
        self.assertEqual((entry["branch"], entry["existing_branch"]), (BRANCH, True))
        self.assertIn(f"■ ブランチ: {BRANCH}", body["text"])
        self.assertIn(f"既存のブランチ {BRANCH} を使う", body["text"])
        self.assertEqual(body["branch_warnings"], [])

    def test_an_existing_branch_elsewhere_is_named_without_a_warning(self):
        git(self.root, "branch", BRANCH)
        self.propose(where=self.root)
        body = self.preview()
        self.assertIn(f"既存のブランチ {BRANCH} を使う", body["text"])
        self.assertEqual(body["branch_warnings"], [])

    def test_a_branch_to_be_cut_is_said_so(self):
        self.propose(where=self.root, branch="hotfix/45")
        body = self.preview()
        entry = body["batch"][0]
        self.assertEqual((entry["branch"], entry["existing_branch"]), ("hotfix/45", False))
        self.assertIn("新しく切るブランチ", body["text"])

    def test_the_field_is_in_the_digest(self):
        self.propose(where=self.root)
        first = self.preview()["digest"]
        self.propose(where=self.root, branch="feature/12-other")
        self.assertNotEqual(self.preview()["digest"], first)

    def test_the_integration_branch_is_refused(self):
        write(
            os.path.join(self.root, ".claude", "settings.local.json"),
            json.dumps({"env": {"CCNAVI_INTEGRATION_BRANCH": "trunk/next"}}),
        )
        self.propose(where=self.root, branch="trunk/next")
        body = self.preview()
        self.assertFalse(body["verify"]["ok"], body)
        problems = " ".join(" ".join(r["problems"]) for r in body["rejected"])
        self.assertIn("統合先の名前", problems)

    def test_a_revision_cannot_change_the_branch(self):
        self.parent_tree()
        self.propose()
        approved = self.ccnavi("--agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.propose(branch="feature/12-other", plan=("design", "acceptance"))
        body = self.preview()
        problems = " ".join(" ".join(r["problems"]) for r in body["rejected"])
        self.assertIn("`branch:` が承認済みチケットと違う", problems)

    def test_the_default_branch_from_origin_head_is_refused(self):
        """取り込みをしていないリポジトリでも、origin/HEAD が指す統合先は親のブランチにしない。"""
        git(self.root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
        git(self.root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.propose(where=self.root, branch="trunk")
        body = self.preview()
        self.assertFalse(body["verify"]["ok"], body)
        problems = " ".join(" ".join(r["problems"]) for r in body["rejected"])
        self.assertIn("統合先の名前（trunk）", problems)

    def test_two_families_cannot_claim_one_branch(self):
        other = "feature-13-other"
        write(
            os.path.join(self.root, "wip", "proposals", "todo", other + ".md"),
            parent_text(other, ["design"], allow=("src/*",)),
        )
        self.propose(where=self.root, branch=other)
        body = self.preview()
        problems = " ".join(" ".join(r["problems"]) for r in body["rejected"])
        self.assertIn(f"親のブランチ {other} を別の家族（{other}）も", problems)
        # どちらの家族のブランチか決まらないので、両方とも承認しない
        self.assertEqual(sorted(r["ticket"] for r in body["rejected"]), sorted([PARENT, other]))

    def test_lint_warns_when_the_parent_tree_is_off_its_branch(self):
        # 承認前の提案の branch: は使わない: branch: のブランチの上に居れば、識別子と違うと言う
        self.parent_tree()
        self.propose()
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn(f"親 {PARENT} のワークツリーが {BRANCH} の上に居る", lint.stdout)
        # 承認の後、識別子のブランチの上に居れば、承認済みの branch: と違うと言う
        self.approve()
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")
        git(self.tree, "checkout", "-q", "-b", PARENT)
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn(f"親 {PARENT} のワークツリーが {PARENT} の上に居る", lint.stdout)
        self.assertIn(f"branch: の {BRANCH}", lint.stdout)

    def test_lint_is_quiet_on_the_approved_branch(self):
        self.parent_tree()
        self.propose()
        self.approve()
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotIn("の上に居る", lint.stdout)

    def approve(self):
        done = self.ccnavi("--agree", stdin="y\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_c1_family_answers_only_an_approved_branch(self):
        self.propose(where=self.root)
        done = self.ccnavi("c1", "family", PARENT)
        self.assertIn(f"branch {PARENT}\n", done.stdout)
        self.approve()
        done = self.ccnavi("c1", "family", PARENT)
        self.assertIn(f"branch {BRANCH}\n", done.stdout)
        done = self.ccnavi("c1", "family", "feature-99-none")
        self.assertIn("branch feature-99-none\n", done.stdout)

    def test_c1_family_refuses_an_unusable_approved_branch(self):
        self.propose(where=self.root)
        self.approve()
        copy = os.path.join(self.root, ".ccnavi", "approved", "doing", PARENT + ".md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        write(copy, text.replace(f"branch: {BRANCH}", "branch: origin/main"))
        done = self.ccnavi("c1", "family", PARENT)
        self.assertIn("branch_refused 親のブランチ origin/main は使えない", done.stdout)
        self.assertNotIn("\nbranch ", done.stdout)


def _workspace_files():
    compat = os.path.join(".ccnavi", "scripts", "ccnavi-common.sh").replace(os.sep, "/")
    return {
        ".claude/settings.json": json.dumps({"env": {"CCNAVI_TICKET_CONTROL": "enable"}}) + "\n",
        ".ccnavi/common/phases.yml": PHASES,
        ".ccnavi/common/rules.yml": json.dumps(RULES),
        compat: f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n",
    }


def _approved(text):
    return text.replace(
        "---\n\n本文", "ccnavi_approved:\n  approved_at: 2026-10-01T00:00:00+0900\n---\n\n本文"
    )


def _family_files(branch=BRANCH, ident=PARENT, predecessors=()):
    """親のブランチの置き場。承認済みの親の写し（`branch:` 付き）と、承認待ちの子の提案。"""
    child = child_text(f"{ident}-01", ident, 1, ["wip/research/*"], False)
    if predecessors:
        lines = child.split("\n")
        lines[5:5] = ["predecessors:", *[f"  - {p}" for p in predecessors]]
        child = "\n".join(lines)
    return {
        f".ccnavi/approved/doing/{ident}.md": _approved(
            with_branch(parent_text(ident, ["research"], allow=("src/*", "wip/*")), branch)
        ),
        f"wip/proposals/todo/{ident}-01.md": child,
    }


class ChromeBranchFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-chrome-branch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.chrome = _chrome()

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
            # 識別子と同じ名前のブランチ（同じ家族を名乗るかを確かめに読む）はホストに無い
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

    def test_the_family_is_named_by_the_claiming_parent(self):
        found = self.ask("families", candidates=[BRANCH])
        self.assertNotIn("error", found, found)
        self.assertEqual(
            [(f["name"], f["family"], f["conflict"]) for f in found["families"]],
            [(BRANCH, PARENT, "")],
        )

    def test_a_proposal_claims_only_its_identifier(self):
        """承認前の提案の branch: では名乗らない（識別子のブランチの家族として読む）。"""
        proposal = {
            f"wip/proposals/todo/{PARENT}.md": with_branch(
                parent_text(PARENT, ["research"], allow=("src/*",)), BRANCH
            )
        }
        branches = {
            BRANCH: {"head": HEAD, "files": proposal},
            PARENT: {"head": HEAD, "files": proposal},
        }
        found = self.ask("families", candidates=[BRANCH, PARENT], branches=branches)
        self.assertEqual([(f["name"], f["family"]) for f in found["families"]], [(PARENT, PARENT)])

    def test_a_branch_not_claimed_is_not_a_family(self):
        files = _family_files(branch="feature/12-elsewhere")
        found = self.ask(
            "families", candidates=[BRANCH], branches={BRANCH: {"head": HEAD, "files": files}}
        )
        self.assertEqual(found["families"], [])

    def test_the_board_and_the_plan_write_only_the_branch(self):
        board = self.ask("board")
        self.assertNotIn("error", board, board)
        self.assertEqual(board["ident"], PARENT)
        self.assertEqual([e["ticket"] for e in board["batch"]], [CHILD])
        shown = {"ids": [e["ticket"] for e in board["batch"]], "digest": board["digest"]}
        body = self.ask(
            "plan", only=board["only"], shown=shown, actor={"account": "a", "version": "1"}
        )
        self.assertNotIn("error", body, body)
        self.assertEqual(list(body["changes"]), [BRANCH])
        paths = {r["path"] for r in body["changes"][BRANCH]}
        self.assertIn(f".ccnavi/approved/doing/{CHILD}.md", paths)

    def test_two_branches_claiming_one_family_are_not_judged(self):
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
        self.assertTrue(all("1 本でない" in f["conflict"] for f in found["families"]), found)
        board = self.ask("board", branches=branches, absent=[])
        self.assertIn("1 本でない", board.get("undecided", ""), board)
        plan = self.ask("plan", branches=branches, absent=[], shown={"ids": [CHILD], "digest": "x"})
        self.assertIn("1 本でない", plan.get("error", ""), plan)

    def test_a_predecessor_family_claimed_twice_is_not_judged(self):
        other = "feature-20-dep"
        dep = _family_files(branch="feature/20-dep", ident=other)
        dep2 = {
            f".ccnavi/approved/doing/{other}.md": _approved(
                parent_text(other, ["research"], allow=("src/*",))
            )
        }
        branches = {
            BRANCH: {"head": HEAD, "files": _family_files(predecessors=(f"{other}-01",))},
            "feature/20-dep": {"head": HEAD, "files": dep},
            other: {"head": HEAD, "files": dep2},
        }
        closure = self.ask("closure", branches=branches)
        self.assertEqual(closure["ambiguous"], [other])
        board = self.ask("board", branches=branches)
        self.assertIn(
            "先行の家族（feature-20-dep）を名乗るブランチが 1 本でない", board["undecided"]
        )

    def test_the_closure_asks_for_the_identifier_branch(self):
        closure = self.ask("closure", absent=[])
        self.assertEqual(closure["idents"], {BRANCH: PARENT})
        self.assertEqual(closure["rivals"], [PARENT])
        self.assertIn(PARENT, closure["need"])


if __name__ == "__main__":
    unittest.main()
