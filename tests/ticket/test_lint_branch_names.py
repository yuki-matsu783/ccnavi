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
import re
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
    if not parent:
        lines.append("mr: 1")
        if issue is None:
            # issue の欄は必須。識別子の番号があればそれに合わせる
            # （番号の食い違いを見るテストを妨げない）
            found = re.match(r"^[a-z]+-(\d+)-", name)
            issue = int(found.group(1)) if found else 1
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
        # 固定のリストにヒットするものは 1 行だけ。
        main = ticket_ids.branch_name_problems(ticket_model.Ticket(ticket="main"), "main")
        self.assertEqual(1, len([f for f in main if "統合先" in f]), main)
        child = ticket_model.Ticket(ticket="trunk-01-01", parent="trunk")
        self.assertEqual([], ticket_ids.branch_name_problems(child, "trunk-01-01"))

    def test_names_outside_the_form_are_named(self):
        """新しい親は `<先頭の語>-<番号>-<slug>`。前の形とユーザが付けた名前は warn。"""
        for name, issue, word in (
            ("i0131", 131, "`feature-131-<slug>`"),
            ("I0131", None, "issue の番号"),
            ("web-i0012", 12, "`feature-12-<slug>`"),
            ("login-form", None, "issue の番号"),
            ("login", 12, "`feature-12-<slug>`"),
            ("feature-login", None, "issue の番号"),
            ("Feature-1-x", None, "issue の番号"),
            ("feature-01-x", None, "issue の番号"),
            ("spike-1-x", None, "issue の番号"),
        ):
            with self.subTest(name=name):
                found = self.problems(name, issue=issue)
                self.assertEqual(1, len(found), found)
                self.assertIn(word, found[0])

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

    def test_issue_slug_cuts_at_the_hyphen(self):
        title = "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"
        # 入りきらない最後の語は落とす。語の途中（「実装す」）では切らない
        self.assertEqual(
            "feature-41-テキスト整形cli-main-sh-を-readme",
            ticket_ids.issue_identifier(41, title),
        )
        word = "Add retry to the HTTP client when the server returns 503"
        self.assertEqual(
            "feature-41-add-retry-to-the-http-client-when-the",
            ticket_ids.issue_identifier(41, word),
        )
        self.assertEqual("add-retry", ticket_ids.issue_slug(word, 10))
        # 最初の 1 語が入りきらないときだけ、その語を上限で切る（失敗しない）
        one = "あ" * 60
        self.assertEqual("あ" * 5, ticket_ids.issue_slug(one, 5))
        ident = ticket_ids.issue_identifier(7, one)
        self.assertEqual("feature-7-" + "あ" * 38, ident)
        self.assertTrue(ticket_ids.is_valid_id(ident))
        # 収まる題はそのまま。番号だけ・記号だけは従来どおり
        self.assertEqual("add-retry", ticket_ids.issue_slug("Add retry", 48))
        self.assertEqual("", ticket_ids.issue_slug("！？", 48))
        self.assertEqual("feature-2-issue", ticket_ids.issue_identifier(2, "！？"))
        self.assertEqual("feature-4-issue", ticket_ids.issue_identifier(4, "07"))
        # 切った結果の末尾が `-<2 桁>` なら、従来どおり削る
        tail = "a" * 33 + " 12 tail"
        self.assertEqual("feature-5-" + "a" * 33, ticket_ids.issue_identifier(5, tail))

    def test_issue_identifier_parts_says_when_the_slug_was_cut(self):
        title = "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"
        self.assertEqual(
            ("feature-41-テキスト整形cli-main-sh-を-readme", True),
            ticket_ids.issue_identifier_parts(41, title),
        )
        self.assertEqual(
            ("feature-41-login-form", False), ticket_ids.issue_identifier_parts(41, "Login form")
        )
        # 空の題・記号だけ・末尾の `-<2 桁>` を除いただけでは、短くしたと言わない
        for text in ("", "！？", "release 01", "07"):
            with self.subTest(title=text):
                self.assertFalse(ticket_ids.issue_identifier_parts(3, text)[1])

    def test_the_cut_is_noticed_when_nfkc_makes_the_title_longer(self):
        # NFKC で 1 字が 2 字に増える題（㍿ → 株式）でも、切ったことを返す
        ident, shortened = ticket_ids.issue_identifier_parts(7, "㍿" * 10)
        self.assertTrue(shortened, ident)
        self.assertTrue(ticket_ids.is_valid_id(ident))

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
        # 長いタイトルは 48 文字に収める
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
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        result = run_ccnavi(
            [
                *extra,
                "--root",
                self.ws,
                "--rules",
                self.rules,
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
        self.assertIn("i0131: 親の識別子は", joined)
        self.assertNotIn("feature-132-x:", joined)
        self.assertIn("develop: 識別子が統合先", joined)
        self.assertIn("feature-1-fix..it: 識別子に `..`", joined)
        self.assertIn("develop: 親の識別子は", joined)
        self.assertNotIn("通し番号は", joined)

    def test_a_japanese_identifier_passes(self):
        self.propose("feature-64-統合先の解決")
        self.assertEqual([], self.lint())

    def test_duplicate_numbers_are_named(self):
        self.place("done", "feature-62-z")
        self.place("doing", "feature-63-a")
        self.propose("feature-63-b")
        self.propose("hotfix-62-c")
        self.propose("feature-64-d")
        lines = self.lint()
        joined = "\n".join(lines)
        self.assertIn("feature-63-b: 識別子の番号 63 が feature-63-a と重なる", joined)
        self.assertIn("hotfix-62-c: 識別子の番号 62 が feature-62-z と重なる", joined)
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

    def repo_with_origin(self, path, branch):
        """origin を持ち、`origin/HEAD` が `branch` を指すリポジトリ。"""
        os.makedirs(path, exist_ok=True)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
        bare = path + ".origin.git"
        for args, cwd in (
            (["init", "--quiet", "-b", branch], path),
            (["commit", "--quiet", "--allow-empty", "-m", "x"], path),
            (["init", "--quiet", "--bare", bare], os.path.dirname(path)),
            (["remote", "add", "origin", bare], path),
            (["push", "--quiet", "origin", branch], path),
            (["remote", "set-head", "origin", branch], path),
        ):
            subprocess.run([*git, *args], cwd=cwd, check=True, capture_output=True)

    def test_each_repository_reserves_its_own_default_branch(self):
        # 統合先はそのリポジトリの origin/HEAD だけで決まる。ワークスペースが main でも、
        # projects/app（trunk）向けのチケットは trunk を予約する。
        shutil.rmtree(self.ws)
        os.makedirs(os.path.join(self.ws, ".claude"))
        self.repo_with_origin(self.ws, "main")
        self.repo_with_origin(os.path.join(self.ws, "projects", "app"), "trunk")
        self.rules = write(os.path.join(self.ws, "rules.yml"), json.dumps({"version": 1}))
        write(
            os.path.join(self.ws, "projects", "app", "wip", "proposals", "todo", "trunk.md"),
            ticket_text("trunk"),
        )
        self.propose("Trunk-ws")
        lines = [line for line in self.lint() if "統合先" in line]
        self.assertEqual(1, len(lines), lines)
        self.assertIn("trunk: 識別子が統合先の名前（trunk）", lines[0])

    def test_the_workspace_reserves_its_own_default_branch(self):
        shutil.rmtree(self.ws)
        os.makedirs(os.path.join(self.ws, ".claude"))
        self.repo_with_origin(self.ws, "trunk")
        self.rules = write(os.path.join(self.ws, "rules.yml"), json.dumps({"version": 1}))
        self.propose("trunk")
        lines = [line for line in self.lint() if "統合先" in line]
        self.assertEqual(1, len(lines), lines)

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
        # 古い形の子と同じ綴りなので、今までどおり warn にする。
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
