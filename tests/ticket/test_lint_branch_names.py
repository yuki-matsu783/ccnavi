"""識別子を親のブランチ名にできるかを `--lint` が warn で言う。

親のブランチ名は親の識別子そのものにする。ここでは承認も判定も変えず、
`--lint` の warn だけを足す。
見るのは 4 つ。

1. 新規の提案の識別子の形: ブランチ名として安全でない（`..`・`.lock`・`.` で終わる）、統合先や
   保護されたブランチの名前、`issue:` の無い `i<番号>`
2. 承認済み・閉じた識別子には 1 を言わない（もう変えられないので、言っても常態になるだけ）
3. 大文字小文字だけが違う識別子
4. 子の形（`<親>-<2 桁>`）に当たる親の識別子
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi import ticket as ticket_mod
from tests.inproc import run_ccnavi

ADR = "（親のブランチ名の規則）"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def ticket_text(name, *, parent="", issue=None, approved=False):
    lines = ["---", "version: 1", f"ticket: {name}", "title: t"]
    if parent:
        lines += [f"parent: {parent}", "phase: 1"]
    if issue is not None:
        lines.append(f"issue: {issue}")
    if approved:
        lines.append(
            "ccnavi_approved: "
            '{approved_at: "2026-01-01T00:00:00Z", source_tree: "", source_path: p}'
        )
    lines += ["allow:", '- {glob: "src/*", match: "Write|Edit"}', "---", ""]
    return "\n".join(lines)


class BranchNameRulesTest(unittest.TestCase):
    """`ticket.branch_name_problems` の見本表。識別子と `issue:` だけを見る。"""

    def problems(self, name, *, parent="", issue=None, issue_repo="", project=""):
        return ticket_mod.branch_name_problems(
            ticket_mod.Ticket(
                ticket=name, parent=parent, issue=issue, issue_repo=issue_repo, project=project
            )
        )

    def test_names_that_are_fine(self):
        for name, issue in (
            ("i0131", 131),
            ("i12345", 12345),
            ("login-form", None),
            ("login-form", 12),
            ("mainline", None),
            ("releases", None),
            ("v1.2", None),
        ):
            with self.subTest(name=name):
                self.assertEqual([], self.problems(name, issue=issue))

    def test_names_that_are_not_safe_as_a_branch(self):
        for name, word in (
            ("a..b", "`..`"),
            ("topic.lock", "`.lock`"),
            ("topic.LOCK", "`.lock`"),
            ("topic.", "`.` で終わる"),
        ):
            with self.subTest(name=name):
                found = self.problems(name)
                self.assertEqual(1, len(found), found)
                self.assertIn(word, found[0])

    def test_reserved_names(self):
        for name in ("main", "Master", "DEVELOP", "release", "release-2026", "Release-x"):
            with self.subTest(name=name):
                found = self.problems(name)
                self.assertEqual(1, len(found), found)
                self.assertIn("統合先", found[0])

    def test_the_integration_branch_is_reserved_when_given(self):
        # その時点の統合先の名前。環境変数は読まず、渡されたときだけ見る。
        ticket = ticket_mod.Ticket(ticket="Trunk")
        self.assertEqual([], ticket_mod.branch_name_problems(ticket))
        found = ticket_mod.branch_name_problems(ticket, "trunk")
        self.assertEqual(1, len(found), found)
        self.assertIn("統合先の名前（trunk）", found[0])
        # 固定の並びに当たるものは 1 行だけ。
        self.assertEqual(
            1, len(ticket_mod.branch_name_problems(ticket_mod.Ticket(ticket="main"), "main"))
        )
        # 子は見ない。
        child = ticket_mod.Ticket(ticket="trunk-01", parent="trunk")
        self.assertEqual([], ticket_mod.branch_name_problems(child, "trunk-01"))

    def test_issue_shaped_names_need_an_issue(self):
        for name in ("i0131", "I0131", "i7"):
            with self.subTest(name=name):
                found = self.problems(name)
                self.assertEqual(1, len(found), found)
                self.assertIn("`issue:`", found[0])
        self.assertEqual([], self.problems("i0131", issue=131))

    def test_issue_shaped_names_must_match_the_issue(self):
        """issue から決める形の識別子は番号と置き場に合わせる。

        ワークスペースなら `i<番号>`、プロジェクトなら `<名前>-i<番号>`。
        """
        for name, issue, project in (
            ("I0131", 131, ""),
            ("i7", 7, ""),
            ("i0132", 131, ""),
            ("i0131", 131, "web"),
            ("web-i0013", 12, "web"),
            ("api-i0012", 12, "web"),
        ):
            with self.subTest(name=name, project=project):
                found = self.problems(name, issue=issue, project=project)
                self.assertEqual(1, len(found), found)
                self.assertIn("から決まる", found[0])
        self.assertEqual([], self.problems("i0007", issue=7))
        self.assertEqual([], self.problems("web-i0012", issue=12, project="web"))
        self.assertEqual([], self.problems("web-i12345", issue=12345, project="web"))

    def test_project_shaped_names_are_reserved(self):
        """`<名前>-i<番号>` は issue の無い提案とワークスペースの提案では使わない。"""
        # 人が付けた名前（issue が無い）は、そのプロジェクトの issue から決まる名前と
        # 重なるときだけ言う
        self.assertEqual([], self.problems("web-i0012"))
        self.assertEqual([], self.problems("fix-i2", project="web"))
        found = self.problems("web-i0012", project="web")
        self.assertEqual(1, len(found), found)
        self.assertIn("`issue:` の無い提案", found[0])
        self.assertEqual(1, len(self.problems("WEB-i0012", project="web")))
        found = self.problems("web-i0012", issue=12)
        self.assertEqual(1, len(found), found)
        self.assertIn("ワークスペースの提案", found[0])
        # 形に当たらない名前は、issue があっても人が付けた名前でよい（フォールバック。8.6）
        self.assertEqual([], self.problems("fix-i18n"))
        self.assertEqual([], self.problems("login", issue=12, project="web"))

    def test_an_issue_in_another_repository_needs_a_human_name(self):
        """`owner/repo#N` の課題は、識別子を issue から決める形にしない（識別子は人が付ける）。"""
        for name, project in (("i0012", ""), ("web-i0012", "web")):
            with self.subTest(name=name):
                found = self.problems(name, issue=12, issue_repo="acme/other", project=project)
                self.assertEqual(1, len(found), found)
                self.assertIn("acme/other#12", found[0])
        self.assertEqual([], self.problems("login", issue=12, issue_repo="acme/other"))

    def test_issue_identifier(self):
        self.assertEqual("i0012", ticket_mod.issue_identifier(12))
        self.assertEqual("i12345", ticket_mod.issue_identifier(12345))
        self.assertEqual("web-i0012", ticket_mod.issue_identifier(12, "web"))
        for bad in (0, -1, True, "12"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ticket_mod.issue_identifier(bad)
        with self.assertRaises(ValueError):
            ticket_mod.issue_identifier(12, "../x")

    def test_children_are_only_checked_for_ref_safety(self):
        # 子の識別子は `<親>-<2 桁>` で、親の名前の規則は親の側で見る。
        self.assertEqual([], self.problems("i0131-01", parent="i0131"))
        self.assertEqual([], self.problems("main-01", parent="main"))


class LintBranchNamesTest(unittest.TestCase):
    """`--lint` を外から呼び、warn の行だけを見る。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-branch-names-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )
        self.rules = write(os.path.join(self.ws, "rules.yml"), json.dumps({"version": 1}))

    def propose(self, name, **kwargs):
        write(
            os.path.join(self.ws, "wip", "proposals", "todo", name + ".md"),
            ticket_text(name, **kwargs),
        )

    def place(self, state, name, **kwargs):
        write(
            os.path.join(self.ws, ".ccnavi", "approved", state, name + ".md"),
            ticket_text(name, approved=True, **kwargs),
        )

    def lint(self, *extra, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        result = run_ccnavi(
            [
                *extra,
                "--root",
                self.ws,
                "--rules",
                self.rules,
                "--approved",
                ".ccnavi/approved",
                "--state",
                os.path.join(self.ws, "state"),
                "--log",
                "",
                "--lint",
                "--mode",
                "enable",
            ],
            input="",
            env=environment,
        )
        return [line for line in result.stdout.splitlines() if ADR in line]

    def test_new_proposals_are_named(self):
        self.propose("i0131")
        self.propose("i0132", issue=132)
        self.propose("develop")
        self.propose("fix..it")
        lines = self.lint()
        joined = "\n".join(lines)
        self.assertTrue(all(line.startswith("warn: (ticket): ") for line in lines), joined)
        self.assertIn("i0131: `i<番号>` の形", joined)
        self.assertNotIn("i0132:", joined)
        self.assertIn("develop: 識別子が統合先", joined)
        self.assertIn("fix..it: 識別子に `..`", joined)

    def test_the_integration_branch_flag_reserves_its_name(self):
        self.propose("trunk")
        self.assertEqual([], self.lint())
        # 環境変数は読まない（sh が決めて --integration-branch で渡す）。
        self.assertEqual([], self.lint(env={"CCNAVI_INTEGRATION_BRANCH": "trunk"}))
        lines = self.lint("--integration-branch", "trunk")
        self.assertEqual(1, len(lines), lines)
        self.assertIn("trunk: 識別子が統合先の名前（trunk）", lines[0])

    def test_the_name_ccnavi_sync_recorded_is_reserved(self):
        # --integration-branch が無ければ、ccnavi-sync.sh が控えに書いた名前を読む。
        self.propose("trunk")
        write(
            os.path.join(self.ws, "state", "sync", "self", "integration", "head"),
            "remote origin\nbranch trunk\nsource default\nsha x\nfetched_at 1\n",
        )
        lines = self.lint()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("trunk: 識別子が統合先の名前（trunk）", lines[0])
        # 渡された名前が先。
        self.assertEqual([], self.lint("--integration-branch", "main-line"))

    def test_a_linked_record_is_not_followed(self):
        self.propose("trunk")
        real = write(os.path.join(self.ws, "elsewhere", "head"), "branch trunk\n")
        place = os.path.join(self.ws, "state", "sync", "self", "integration")
        os.makedirs(place)
        try:
            os.symlink(real, os.path.join(place, "head"))
        except (OSError, NotImplementedError):
            self.skipTest("リンクを作れない")
        self.assertEqual([], self.lint())

    def test_approved_and_closed_ids_are_left_alone(self):
        # 既存の i0055 などは issue: を持たない。承認済みの識別子は変えられないので言わない。
        self.place("doing", "i0055")
        self.place("done", "i0060")
        self.assertEqual([], self.lint())

    def test_a_revision_of_an_approved_parent_is_not_new(self):
        self.place("doing", "i0055")
        self.propose("i0055")
        self.assertEqual([], [line for line in self.lint() if "i0055:" in line])

    def test_ids_that_differ_only_in_case(self):
        self.place("done", "login")
        self.propose("Login", issue=None)
        lines = self.lint()
        self.assertTrue(
            any("Login と login は大文字小文字だけが違う" in line for line in lines), lines
        )

    def test_a_parent_shaped_like_a_child(self):
        self.place("done", "abc-01")
        lines = self.lint()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("abc-01 は親なのに、識別子が子の形", lines[0])
        self.assertIn("abc の子として扱われる", lines[0])

    def test_real_children_are_not_named(self):
        self.place("doing", "abc")
        self.place("doing", "abc-01", parent="abc")
        self.assertEqual([], self.lint())


if __name__ == "__main__":
    unittest.main()
