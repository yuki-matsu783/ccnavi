"""識別子を親のブランチ名にできるかを `--lint` が warn で言う。

親のブランチ名は親の識別子そのものにする。ここでは承認も判定も変えず、
`--lint` の warn だけを足す。
見るのは 4 つ。

1. 新規の提案の識別子の形: ブランチ名として安全でない（`..`・`.lock`・`.` で終わる）、統合先や
   保護されたブランチの名前、`<先頭の語>-<番号>-<slug>` の形でない、番号の重なり、
   既にあるブランチと同じ名前
2. 承認済み・閉じた識別子には 1 を言わない（もう変えられないので、言っても常態になるだけ）
3. 大文字小文字だけが違う識別子
4. 末尾が `-<2 桁>` の親の識別子（`-<2 桁>-<2 桁>` なら子の形そのもの、`-<2 桁>` だけなら
   子の識別子の途中と紛れる）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
import unittest

from ccnavi.infra import settings
from ccnavi.tickets import ticket_ids, ticket_model
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
    """`ticket_ids.branch_name_problems` の見本表。識別子と `issue:` だけを見る。"""

    def problems(self, name, *, parent="", issue=None, issue_repo="", project="", **kw):
        return ticket_ids.branch_name_problems(
            ticket_model.Ticket(
                ticket=name, parent=parent, issue=issue, issue_repo=issue_repo, project=project
            ),
            **kw,
        )

    def test_names_that_are_fine(self):
        for name, issue, project in (
            ("feature-131-login", 131, ""),
            ("feature-12345-x", 12345, ""),
            ("feature-63-integration-branch", None, ""),
            ("feature-64-統合先の解決", None, ""),
            ("hotfix-7-ログイン画面", 7, ""),
            ("fix-1-a.b_c", None, ""),
            ("docs-3-readme", None, ""),
            ("feature-12-web-login", 12, "web"),
            ("feature-12-web-issue", 12, "web"),
            ("feature-80-login", 3, ""),  # issue_repo の課題は番号を比べない（下で別に見る）
        ):
            with self.subTest(name=name):
                repo = "acme/other" if (name, issue) == ("feature-80-login", 3) else ""
                self.assertEqual(
                    [], self.problems(name, issue=issue, project=project, issue_repo=repo)
                )

    def test_names_that_are_not_safe_as_a_branch(self):
        for name, word in (
            ("feature-1-a..b", "`..`"),
            ("feature-1-topic.lock", "`.lock`"),
            ("feature-1-topic.LOCK", "`.lock`"),
            ("feature-1-topic.", "`.` で終わる"),
        ):
            with self.subTest(name=name):
                found = self.problems(name)
                self.assertEqual(1, len(found), found)
                self.assertIn(word, found[0])

    def test_reserved_names(self):
        for name in ("main", "Master", "DEVELOP", "release", "release-2026", "Release-x"):
            with self.subTest(name=name):
                found = self.problems(name)
                self.assertTrue(any("統合先" in f for f in found), found)

    def test_the_integration_branch_is_reserved_when_given(self):
        # その時点の統合先の名前。環境変数は読まず、渡されたときだけ見る。
        ticket = ticket_model.Ticket(ticket="Trunk")
        self.assertFalse(any("統合先" in f for f in ticket_ids.branch_name_problems(ticket)))
        found = ticket_ids.branch_name_problems(ticket, "trunk")
        self.assertIn("統合先の名前（trunk）", found[0])
        # 固定のリストに当たるものは 1 行だけ。
        main = ticket_ids.branch_name_problems(ticket_model.Ticket(ticket="main"), "main")
        self.assertEqual(1, len([f for f in main if "統合先" in f]), main)
        # 子は見ない。
        child = ticket_model.Ticket(ticket="trunk-01-01", parent="trunk")
        self.assertEqual([], ticket_ids.branch_name_problems(child, "trunk-01-01"))

    def test_names_outside_the_form_are_named(self):
        """新しい親は `<先頭の語>-<番号>-<slug>`。前の形とユーザが付けた名前は warn。"""
        for name, issue, word in (
            ("i0131", 131, "`i<番号>` の形はもう使わない"),
            ("I0131", None, "`i<番号>` の形はもう使わない"),
            ("web-i0012", 12, "`i<番号>` の形はもう使わない"),
            ("login-form", None, "通し番号"),
            ("login", 12, "`feature-12-<slug>`"),
            ("feature-login", None, "通し番号"),
            ("Feature-1-x", None, "通し番号"),
            ("feature-01-x", None, "通し番号"),
            ("spike-1-x", None, "通し番号"),
        ):
            with self.subTest(name=name):
                found = self.problems(name, issue=issue)
                self.assertEqual(1, len(found), found)
                self.assertIn(word, found[0])

    def test_the_next_serial_is_suggested(self):
        found = self.problems("login", serial=72)
        self.assertIn("次の通し番号は 72", found[0])

    def test_the_prefixes_can_be_changed(self):
        self.assertEqual([], self.problems("spike-1-x", prefixes=("feature", "spike")))
        found = self.problems("hotfix-1-x", prefixes=("feature",))
        self.assertEqual(1, len(found), found)
        self.assertIn("feature のどれか", found[0])

    def test_the_number_must_match_the_issue(self):
        found = self.problems("feature-132-login", issue=131)
        self.assertEqual(1, len(found), found)
        self.assertIn("`issue: 131` と違う", found[0])
        found = self.problems("hotfix-131-login", issue=132)
        self.assertIn("`hotfix-132-<slug>`", found[0])

    def test_a_project_issue_carries_the_project_name(self):
        found = self.problems("feature-12-login", issue=12, project="web")
        self.assertEqual(1, len(found), found)
        self.assertIn("`feature-12-web-<slug>`", found[0])
        # issue の無いプロジェクトの提案は、通し番号で名前空間を求めない
        self.assertEqual([], self.problems("feature-12-login", project="web"))

    def test_long_names_are_named(self):
        name = "feature-1-" + "a" * 40
        found = self.problems(name)
        self.assertEqual(1, len(found), found)
        self.assertIn("48 文字を超える", found[0])

    def test_issue_identifier(self):
        self.assertEqual("feature-12-issue", ticket_ids.issue_identifier(12))
        self.assertEqual(
            "feature-63-integration-branch",
            ticket_ids.issue_identifier(63, "Integration branch"),
        )
        self.assertEqual("feature-64-統合先の解決", ticket_ids.issue_identifier(64, "統合先の解決"))
        self.assertEqual(
            "feature-12-web-login-form", ticket_ids.issue_identifier(12, "Login form", "web")
        )
        self.assertEqual("feature-12-web-issue", ticket_ids.issue_identifier(12, "", "web"))
        self.assertEqual("hotfix-5-x", ticket_ids.issue_identifier(5, "x", prefix="hotfix"))
        # 全角英数は半角に、半角カナは全角に、記号と空白は `-` にまとめる
        self.assertEqual(
            "feature-1-abc-ガイド-v2", ticket_ids.issue_identifier(1, "ＡＢＣ　ｶﾞｲﾄﾞ / v2!!")
        )
        # 何も残らなければ `issue`
        self.assertEqual("feature-2-issue", ticket_ids.issue_identifier(2, "！？ 😀"))
        # 子の形（`-<2 桁>`）で終わらせない
        self.assertEqual("feature-3-release", ticket_ids.issue_identifier(3, "release 01"))
        self.assertEqual("feature-4-issue", ticket_ids.issue_identifier(4, "07"))
        # 長いタイトルは 48 文字で切る
        long = ticket_ids.issue_identifier(5, "word " * 40)
        self.assertLessEqual(len(long), ticket_ids.SUGGESTED_ID_LENGTH)
        self.assertFalse(long.endswith("-"))
        self.assertTrue(ticket_ids.is_valid_id(long))
        for bad in (0, -1, True, "12"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ticket_ids.issue_identifier(bad)
        with self.assertRaises(ValueError):
            ticket_ids.issue_identifier(12, "x", "../x")
        for prefix in ("release", "Main", "", "a-b"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                ticket_ids.issue_identifier(12, "x", prefix=prefix)

    def test_identifier_characters(self):
        for name in ("feature-64-統合先の解決", "a", "fix-1-カタカナー々", "i0055-01"):
            with self.subTest(name=name):
                self.assertTrue(ticket_ids.is_valid_id(name), ticket_ids.id_problem(name))
        nfd = unicodedata.normalize("NFD", "feature-1-が")
        for name, word in (
            (nfd, "NFC"),
            ("統合", "使えない文字"),
            ("feature-1-ＡＢ", "使えない文字"),
            ("feature-1-a　b", "使えない文字"),
            ("feature-1-a b", "使えない文字"),
            ("feature-1-a/b", "使えない文字"),
            ("feature-1-①", "使えない文字"),
            ("abc\n", "使えない文字"),
            ("-abc", "使えない文字"),
            ("a" * 65, "長すぎる"),
        ):
            with self.subTest(name=name):
                self.assertIn(word, ticket_ids.id_problem(name))
        self.assertTrue(ticket_ids.is_valid_id("a" * 64))

    def test_next_serial(self):
        self.assertEqual(1, ticket_ids.next_serial([]))
        self.assertEqual(1, ticket_ids.next_serial(["login", "abc-01-01"]))
        self.assertEqual(
            71,
            ticket_ids.next_serial(
                [
                    "i0062",
                    "feature-63-x",
                    "hotfix-70-y",
                    "i0062-01-01",
                    "web-i0012",
                    "feature-99-z-01-01",
                ]
            ),
        )
        self.assertEqual(3, ticket_ids.next_serial(["spike-9-x", "fix-2-y"]))
        self.assertEqual(10, ticket_ids.next_serial(["spike-9-x"], ("spike",)))

    def test_children_are_only_checked_for_ref_safety(self):
        # 子の識別子は `<親>-<2 桁>-<2 桁>` で、親の名前の規則は親の側で見る。
        self.assertEqual([], self.problems("i0131-01-01", parent="i0131"))
        self.assertEqual([], self.problems("main-01-01", parent="main"))
        self.assertEqual([], self.problems("feature-1-統合-01-01", parent="feature-1-統合"))


class PrefixSettingTest(unittest.TestCase):
    """`CCNAVI_BRANCH_PREFIXES`。ファイルを書かなくても既定のリストで足りる。"""

    def test_parse(self):
        self.assertEqual(
            (("feature", "spike"), ()), settings.parse_branch_prefixes("feature, spike feature")
        )
        self.assertEqual(
            (("feature",), ("release", "Bad", "a-b")),
            settings.parse_branch_prefixes("feature,release,Bad,a-b"),
        )
        self.assertEqual((settings.DEFAULT_BRANCH_PREFIXES, ()), settings.parse_branch_prefixes(""))
        self.assertNotIn("release", settings.DEFAULT_BRANCH_PREFIXES)

    def test_read_from_the_environment_and_settings_files(self):
        ws = tempfile.mkdtemp(prefix="ccnavi-prefixes-")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        saved = os.environ.pop(settings.BRANCH_PREFIXES_ENV, None)
        self.addCleanup(
            lambda: (
                os.environ.__setitem__(settings.BRANCH_PREFIXES_ENV, saved)
                if saved is not None
                else os.environ.pop(settings.BRANCH_PREFIXES_ENV, None)
            )
        )
        self.assertEqual(settings.DEFAULT_BRANCH_PREFIXES, settings.branch_prefixes(ws)[0])
        write(
            os.path.join(ws, ".claude", "settings.json"),
            json.dumps({"env": {settings.BRANCH_PREFIXES_ENV: "feature,hotfix"}}),
        )
        self.assertEqual(("feature", "hotfix"), settings.branch_prefixes(ws)[0])
        write(
            os.path.join(ws, ".claude", "settings.local.json"),
            json.dumps({"env": {settings.BRANCH_PREFIXES_ENV: "spike"}}),
        )
        self.assertEqual(("spike",), settings.branch_prefixes(ws)[0])
        os.environ[settings.BRANCH_PREFIXES_ENV] = "chore"
        self.assertEqual(("chore",), settings.branch_prefixes(ws)[0])


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
        self.propose("feature-132-x", issue=132)
        self.propose("develop")
        self.propose("feature-1-fix..it")
        lines = self.lint()
        joined = "\n".join(lines)
        self.assertTrue(all(line.startswith("warn: (ticket): ") for line in lines), joined)
        self.assertIn("i0131: `i<番号>` の形はもう使わない", joined)
        self.assertNotIn("feature-132-x:", joined)
        self.assertIn("develop: 識別子が統合先", joined)
        self.assertIn("feature-1-fix..it: 識別子に `..`", joined)
        # 次の通し番号（番号を持つ識別子の最大 + 1）を添える
        self.assertIn("develop: 親の識別子は", joined)
        self.assertIn("次の通し番号は 133", joined)

    def test_a_japanese_identifier_passes(self):
        self.propose("feature-64-統合先の解決")
        self.assertEqual([], self.lint())

    def test_duplicate_numbers_are_named(self):
        self.place("done", "i0062")
        self.place("doing", "feature-63-a")
        self.propose("feature-63-b")
        self.propose("hotfix-62-c")
        self.propose("feature-64-d")
        lines = self.lint()
        joined = "\n".join(lines)
        self.assertIn("feature-63-b: 識別子の番号 63 が feature-63-a と重なる", joined)
        self.assertIn("hotfix-62-c: 識別子の番号 62 が i0062 と重なる", joined)
        self.assertNotIn("feature-64-d:", joined)
        self.assertNotIn("feature-63-a:", joined)

    def test_the_prefix_list_comes_from_the_environment(self):
        self.propose("spike-1-x")
        self.assertEqual(1, len(self.lint()))
        self.assertEqual([], self.lint(env={"CCNAVI_BRANCH_PREFIXES": "feature,spike"}))
        lines = self.lint(env={"CCNAVI_BRANCH_PREFIXES": "spike,release"})
        self.assertEqual(1, len(lines), lines)
        self.assertIn("CCNAVI_BRANCH_PREFIXES の release は", lines[0])

    def test_an_existing_branch_is_named(self):
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(
            [*git, "commit", "--quiet", "--allow-empty", "-m", "x"],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "branch", "feature-5-統合"], cwd=self.ws, check=True, capture_output=True
        )
        self.propose("feature-5-統合")
        self.propose("feature-6-new")
        lines = self.lint()
        self.assertEqual(1, len(lines), lines)
        self.assertIn(
            "feature-5-統合: 親のブランチ名と同じブランチ（feature-5-統合）が既にある",
            lines[0],
        )

    def test_the_integration_branch_flag_reserves_its_name(self):
        self.propose("trunk")
        reserved = [line for line in self.lint() if "統合先" in line]
        self.assertEqual([], reserved)
        # 環境変数は読まない（sh が決めて --integration-branch で渡す）。
        lines = self.lint(env={"CCNAVI_INTEGRATION_BRANCH": "trunk"})
        self.assertEqual([], [line for line in lines if "統合先" in line])
        lines = [line for line in self.lint("--integration-branch", "trunk") if "統合先" in line]
        self.assertEqual(1, len(lines), lines)
        self.assertIn("trunk: 識別子が統合先の名前（trunk）", lines[0])

    def test_the_name_ccnavi_sync_recorded_is_reserved(self):
        # --integration-branch が無ければ、
        # ccnavi-sync.sh が取り込み結果に書いた名前を読む。
        self.propose("trunk")
        write(
            os.path.join(self.ws, "state", "sync", "self", "integration", "head"),
            "remote origin\nbranch trunk\nsource default\nsha x\nfetched_at 1\n",
        )
        lines = [line for line in self.lint() if "統合先" in line]
        self.assertEqual(1, len(lines), lines)
        self.assertIn("trunk: 識別子が統合先の名前（trunk）", lines[0])
        # 渡された名前が先。
        lines = self.lint("--integration-branch", "main-line")
        self.assertEqual([], [line for line in lines if "統合先" in line])

    def test_a_linked_record_is_not_followed(self):
        self.propose("trunk")
        real = write(os.path.join(self.ws, "elsewhere", "head"), "branch trunk\n")
        place = os.path.join(self.ws, "state", "sync", "self", "integration")
        os.makedirs(place)
        try:
            os.symlink(real, os.path.join(place, "head"))
        except (OSError, NotImplementedError):
            self.skipTest("リンクを作れない")
        self.assertEqual([], [line for line in self.lint() if "統合先" in line])

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
        self.propose("Login", issue=None)  # 形の warn も出るが、ここでは大文字小文字だけを見る
        lines = self.lint()
        self.assertTrue(
            any("Login と login は大文字小文字だけが違う" in line for line in lines), lines
        )

    def test_a_parent_shaped_like_a_child(self):
        self.place("done", "abc-01-02")
        lines = self.lint()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("abc-01-02 は親なのに、識別子が子の形", lines[0])
        self.assertIn("abc の子として扱われる", lines[0])
        self.assertIn("末尾を `-<2 桁>` にしない", lines[0])

    def test_a_parent_ending_in_two_digits_is_still_named(self):
        # 右から 2 段を剥がすので親の割り出しは誤らないが、別の親の子の途中
        # （`<親>-<フェーズ>`）と紛れる。
        # 旧い形の子と同じ綴りなので、今までどおり warn にする。
        self.place("done", "abc-01")
        lines = self.lint()
        self.assertEqual(1, len(lines), lines)
        self.assertIn("abc-01 は親なのに、識別子の末尾が `-<2 桁>`", lines[0])
        self.assertIn("子の識別子の途中", lines[0])
        self.assertNotIn("の子として扱われる", lines[0])

    def test_a_parent_ending_in_one_digit_is_not_named(self):
        self.place("done", "abc-1")
        self.place("done", "web-i0012")
        self.assertEqual([], self.lint())

    def test_real_children_are_not_named(self):
        self.place("doing", "abc")
        self.place("doing", "abc-01-01", parent="abc")
        self.assertEqual([], self.lint())


if __name__ == "__main__":
    unittest.main()
