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

識別子の形の見本表（ブランチ名・課題からの識別子・通し番号・先頭の語の設定）は go の ids・ticket・
config のテストが見る。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

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
