"""ユーザ専用の入口（`ccnavi user <操作>`）を確かめる（Go の tickets/model/useronly）。

1. 実行ファイル: `user <操作>` を内部の形に読み替える。`user` の外のユーザ専用のオプションは
   何も動かさずに断り、見るだけの形は断らない
2. hook: `user` の入口と `ccnavi-user-*.sh` を、呼び方に依らず止める。ターミナルを求めない経路
   （`--yes`）は、名前を変えたコピーでも止める

表（`user` の操作と、`user` の外で断るオプション）が食い違わないことと、読み替えの細部は Go のテスト
（go/internal/tickets/model/useronly）が確かめる。
"""

from __future__ import annotations

import os
import unittest

from tests.guard.test_cleanup_forms import HookCase
from tests.inproc import run_ccnavi

# 止めたときの終了コードと、使い方の誤りの終了コード。
EXIT_BLOCK = 2
EXIT_ERROR = 1
# ユーザだけが打つ操作を止めたときと、記録を消す操作を止めたときの拒否コード。
CODE_TICKET_APPROVAL = "DENY_TICKET_APPROVAL_CLI"
CODE_RECORDS_PRUNE = "DENY_RECORDS_PRUNE"


class ExecutableTest(unittest.TestCase):
    def test_user_only_options_outside_the_entry_are_refused(self):
        for argv, shown in (
            (["--agree"], "ccnavi-user-agree.sh"),
            (["--agree", "--yes", "i1"], "ccnavi-user-agree.sh"),
            (["--reviewed", "0"], "ccnavi-user-decide.sh"),
            (["--close-early"], "ccnavi-user-close-early.sh"),
            (["--prune"], "ccnavi-user-prune.sh"),
            (["--handoff"], "ccnavi-user-handoff.sh"),
            (["--handoff", "a.md", "--yes", "--expect", "x"], "ccnavi-user-handoff.sh"),
            (["--mirror"], "ccnavi-user-mirror.sh"),
            (["--mirror", "lib", "--yes", "--expect", "x"], "ccnavi-user-mirror.sh"),
            (["--yes", "i1"], "ユーザ専用"),
        ):
            with self.subTest(argv=argv):
                result = run_ccnavi(argv)
                self.assertEqual(result.returncode, EXIT_BLOCK, result.stderr)
                self.assertIn(shown, result.stderr)

    def test_read_only_forms_are_not_refused(self):
        for argv in (
            ["--agree", "--preview", "--json"],
            ["--agree", "--preview", "--verify"],
            ["--prune", "--preview"],
            ["--templates", "--json"],
        ):
            with self.subTest(argv=argv):
                result = run_ccnavi(argv)
                self.assertNotIn("ユーザ専用", result.stderr)

    def test_a_value_of_another_option_is_not_taken_for_the_option(self):
        result = run_ccnavi(["--docs", "--keyword=--agree"])
        self.assertNotIn("ユーザ専用", result.stderr)

    def test_user_with_an_unknown_operation_is_refused(self):
        for argv in (["user"], ["user", "bogus"], ["user", "reviewed"]):
            with self.subTest(argv=argv):
                result = run_ccnavi(argv)
                self.assertEqual(result.returncode, EXIT_BLOCK, result.stderr)
                self.assertIn("user", result.stderr)

    def test_a_non_ascii_spelling_of_the_entry_is_refused(self):
        for argv in (["u\u017fer", "handoff"], ["user", "hando\ufb00"]):
            with self.subTest(argv=argv):
                result = run_ccnavi(argv)
                self.assertEqual(result.returncode, EXIT_BLOCK, result.stderr)
                self.assertIn("ASCII", result.stderr)

    def test_user_handoff_writing_needs_a_terminal(self):
        result = run_ccnavi(["--entry", "sh", "user", "handoff", "--yes", "--expect", "x"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ccnavi-user-handoff.sh --yes はユーザがターミナルから実行する必要があります",
            result.stderr,
        )

    def test_user_mirror_writing_needs_a_terminal(self):
        result = run_ccnavi(["--entry", "sh", "user", "mirror", "--yes", "--expect", "x"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ccnavi-user-mirror.sh --yes はユーザがターミナルから実行する必要があります",
            result.stderr,
        )

    def test_user_prune_needs_a_terminal(self):
        result = run_ccnavi(["--entry", "sh", "user", "prune"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ccnavi-user-prune.sh はユーザがターミナルから実行する必要があります", result.stderr
        )


class EntryMarkTest(unittest.TestCase):
    """入口の印（`--entry sh|board`）。印の無い `user <操作>` は、直に打った形として断る。"""

    def test_a_mark_in_another_place_is_left_for_argparse(self):
        result = run_ccnavi(["--entry", "sh", "user", "prune", "--entry", "sh"])
        self.assertEqual(result.returncode, EXIT_ERROR, result.stderr)

    def test_the_decide_check_form_keeps_the_mark_next_to_user(self):
        """decide の sh が打つ事前検査の並び。`--check` は `user reviewed N` の後ろに置く。"""
        good = ["--entry", "sh", "user", "reviewed", "1", "--check", "--accept-unresolved"]
        result = run_ccnavi(good)
        self.assertNotIn("入口と一緒に", result.stderr)
        bad = ["--entry", "sh", "--check", "user", "reviewed", "1", "--accept-unresolved"]
        self.assertEqual(run_ccnavi(bad).returncode, EXIT_BLOCK)

    def test_a_bad_mark_is_an_error(self):
        for argv in (
            ["--entry", "terminal", "user", "agree"],
            ["--entry"],
            ["--entry", "sh"],
            ["--entry", "sh", "agree"],
            ["--entry", "sh", "--entry", "board", "user", "agree"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run_ccnavi(argv).returncode, EXIT_BLOCK)

    def test_every_operation_without_a_mark_names_its_script(self):
        for words, script in (
            (["agree"], "ccnavi-user-agree.sh"),
            (["reviewed", "3"], "ccnavi-user-decide.sh"),
            (["close-early"], "ccnavi-user-close-early.sh"),
            (["prune"], "ccnavi-user-prune.sh"),
            (["handoff"], "ccnavi-user-handoff.sh"),
            (["mirror"], "ccnavi-user-mirror.sh"),
            (["handoff", "--json"], "ccnavi-user-handoff.sh"),
            (["agree", "--yes", "i1", "--digest", "x", "--json"], "ccnavi-user-agree.sh"),
        ):
            with self.subTest(words=words):
                result = run_ccnavi(["user", *words])
                self.assertEqual(result.returncode, EXIT_BLOCK, result.stderr)
                self.assertIn(script, result.stderr)
                self.assertIn("直に打つ入口ではありません", result.stderr)

    def test_a_marked_entry_goes_on(self):
        for mark in ("sh", "board"):
            result = run_ccnavi(["--entry", mark, "user", "prune"])
            with self.subTest(mark=mark):
                self.assertNotIn("直に打つ入口ではありません", result.stderr)
                self.assertIn("ターミナル", result.stderr)

    def test_the_guard_switch_lets_the_tests_type_the_entry_directly(self):
        env = {**os.environ, "CCNAVI_GUARD_TICKET_APPROVAL": "disable"}
        result = run_ccnavi(["user", "prune", "--preview"], env=env)
        self.assertNotIn("直に打つ入口ではありません", result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)


class HookTest(HookCase):
    STOPPED = (
        "ccnavi user agree",
        "ccnavi user agree i0001-01-01",
        "ccnavi --root /x --cwd /y user reviewed 3 --accept-unresolved",
        "ccnavi --root=/x user close-early --reason r --result /t.json",
        "ccnavi --root '/a b' user handoff",
        "ccnavi.exe USER handoff --json",
        "python -m ccnavi user handoff .ccnavi/docs/a.md --yes --expect x",
        "ccnavi --cwd /y user handoff .claude/settings.json",
        "ccnavi --handoff --json",
        "ccnavi user mirror",
        "ccnavi --root /x user mirror lib --json",
        "python -m ccnavi user mirror --yes --expect x",
        "ccnavi --mirror --json",
        "uv run python -m ccnavi user close-early",
        ".ccnavi/bin/ccnavi -- user agree",
        "sh .ccnavi/scripts/ccnavi-launcher.sh user agree",
        "echo ok && ccnavi us''er agree",
        'ccnavi "user" agree',
        # 入口の印を付けても、エージェントが打つ形は止まる（印は案内のための印で、守りではない）。
        "ccnavi --entry sh user agree",
        "ccnavi --root /x --entry board user mirror --json",
        "ccnavi --entry=sh user handoff .ccnavi/docs/a.md --yes --expect x",
        "ccnavi --entry sh user close-early --reason r --result /t.json",
        # 値を空の引用符にしたラッパーのオプション
        # （hook が読む前に引用符ごと消えて、`user` が値に見える）。
        "ccnavi --entry sh --root '' user agree",
        'ccnavi --root "" --entry board user mirror',
        "ccnavi --cwd '' user agree",
        "ccnavi --root '' user handoff --json",
    )

    def test_the_entry_is_stopped_whatever_the_form(self):
        for command in self.STOPPED:
            for agent in ("", "a1"):
                with self.subTest(command=command, agent=agent):
                    decision, reason = self.hook(command, agent)
                    self.assertEqual(decision, "deny", reason)

    def test_the_entry_is_stopped_by_the_approval_rule(self):
        decision, reason = self.hook("ccnavi user agree")
        self.assertEqual(decision, "deny")
        self.assertIn(CODE_TICKET_APPROVAL, reason)
        self.assertIn("ccnavi user", reason)

    def test_prune_follows_the_core_files_guard(self):
        decision, reason = self.hook("ccnavi user prune")
        self.assertEqual(decision, "deny")
        self.assertIn(CODE_RECORDS_PRUNE, reason)

    def test_an_expanded_word_that_may_become_the_entry_is_stopped(self):
        for command in (
            "ccnavi $X agree",
            "ccnavi --root /x u${X}ser agree",
            "ccnavi us*r agree",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision, "deny", reason)

    def test_user_scripts_are_stopped_by_their_names(self):
        for command in (
            "sh .ccnavi/scripts/ccnavi-user-agree.sh",
            "bash -x .ccnavi/scripts/ccnavi-user-forget.sh P",
            "flock /tmp/l sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason r",
            "cd /x && sh /tmp/copy/CCNAVI-USER-DECIDE.SH 3",
            "setsid sh .ccnavi/scripts/ccnavi-user-clean-branch.sh i0001-01-01",
        ):
            for agent in ("", "a1"):
                with self.subTest(command=command, agent=agent):
                    decision, reason = self.hook(command, agent)
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn(CODE_TICKET_APPROVAL, reason)

    def test_the_board_path_is_stopped_even_under_another_name(self):
        for command in (
            "/tmp/x user agree --yes i1 --digest d",
            "uv run python main.py user agree --yes i1 --digest d",
            "flock /tmp/l ccnavi user reviewed 3 --accept-unresolved --yes 1",
            "python3 -c \"import subprocess;subprocess.run(['c','user','agree','--yes','i1'])\"",
            "/tmp/x user handoff a.md --yes --expect h --via board --json",
            "/tmp/x user mirror lib --yes --expect h --via board --json",
            "python3 -c \"import subprocess;subprocess.run(['c','user','mirror','--yes'])\"",
            "python3 -c \"import subprocess;subprocess.run(['c','user','handoff','--yes'])\"",
            "/tmp/x --agree --yes i1 --digest d",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(CODE_TICKET_APPROVAL, reason)

    def test_reading_is_not_stopped(self):
        for command in (
            "ccnavi --docs --keyword user",
            "ccnavi --keyword user --docs",
            "ccnavi --agree --preview --json",
            "ccnavi --agree --preview --verify",
            "ccnavi --prune --preview",
            "grep -rn 'user agree' docs",
            "cat docs/30_adr/rules/0143-user-only-entries-are-separate.md",
            "ccnavi --lint",
            "ccnavi --templates --json",
            "ccnavi --templates --draft i0001",
            "grep -rn 'user handoff' docs",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertNotIn(CODE_TICKET_APPROVAL, reason)
                self.assertNotIn(CODE_RECORDS_PRUNE, reason)


class LauncherSpellingTest(HookCase):
    """ターミナルを求めない経路は hook の正規表現が唯一の防御なので、
    実行ファイルを Python から走らせる
    表記の揺れ（インタプリタの選択肢・`ccnavi.__main__`・`py`・実行役）でも止める。"""

    def assert_stopped(self, commands):
        for command in commands:
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(CODE_TICKET_APPROVAL, reason)

    def test_interpreter_options_and_module_spellings(self):
        self.assert_stopped(
            (
                "python3 -u -m ccnavi user agree",
                "python3 -I -m ccnavi user agree",
                "python3 -OO -m ccnavi user handoff",
                "python3 -W ignore -m ccnavi user agree",
                "python3 -Wignore -m ccnavi user agree",
                "python3 -X utf8 -m ccnavi user agree",
                "python3 -Xutf8 -m ccnavi user agree",
                "python3 -Im ccnavi user agree",
                "python3 -mccnavi user agree",
                "python3 -m ccnavi.__main__ user agree",
                "/usr/bin/python3.12 -B -m ccnavi user reviewed 3",
                "python3 main.py user agree",
                "python3 /repo/main.py user handoff",
                "py -m ccnavi user agree",
                "py -3 -m ccnavi user agree",
                "python3 -u -m ccnavi ticket finish i0001",
            )
        )

    def test_runners_before_the_interpreter(self):
        self.assert_stopped(
            (
                "uv run python -m ccnavi user agree",
                "uv run --frozen python -m ccnavi user agree",
                "uv run --with x --project /p python3 -u -m ccnavi user agree",
                "uv run -m ccnavi user agree",
                "uv run main.py user close-early",
                "poetry run python -m ccnavi user agree",
                "pipenv run python -m ccnavi user agree",
                "poetry run ccnavi user agree",
                "python3 -u -m ccnavi $X agree",
                "poetry run ccnavi $X agree",
            )
        )

    def test_an_expanded_option_next_to_the_user_word_is_stopped(self):
        # 入口の表記が読めなくても（名前を変えたコピー）、`user` と展開を含む `--` の語が
        # 並べば止める。
        self.assert_stopped(
            (
                "/tmp/x user agree --y${X}es i1",
                "/tmp/x user agree --y$(echo es) i1",
                "/tmp/x user agree --y`echo es` i1",
                '/tmp/x user handoff a.md "--y$X" --expect h',
                "/tmp/x --root /w user agree --${Y}",
            )
        )

    def test_the_board_mark_alone_is_stopped(self):
        self.assert_stopped(
            (
                "/tmp/x foo --via board",
                "/tmp/x --handoff a.md --via=board",
                "/tmp/x --via board --json",
                "/tmp/x user handoff a.md --y$X --expect h --via board",
            )
        )

    def test_an_expanded_guard_variable_name_is_stopped(self):
        for command in (
            "env CCNAVI_GUARD_TICKET_${X}APPROVAL=disable /tmp/x user agree",
            "env CCNAVI_GUARD_${X}=disable /tmp/x user agree --yes i1",
            "export CCNAVI_GUARD_TICKET_$(printf APPROVAL)=disable",
            "env CCNAVI_GUARD_TICKET_`echo APPROVAL`=disable /tmp/x",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(CODE_TICKET_APPROVAL, reason)

    def test_reading_forms_still_pass(self):
        for command in (
            "python3 -u -m ccnavi --agree --preview --json",
            "python3 -m ccnavi --prune --preview",
            "uv run python -m ccnavi --docs --keyword user",
            "python3 -m ccnavi --templates --draft i0001",
            "python3 -m unittest discover -s tests/guard",
            "uv run --frozen python -m pytest -q",
            "py -m pip list",
            "python3 build.py",
            "echo user --x=$Y",
            "echo $CCNAVI_ROOT$X",
            "ls ${CCNAVI_HOME}",
            "sh .ccnavi/scripts/ccnavi-ticket.sh status",
            "sh .ccnavi/scripts/ccnavi-review.sh decide 3 --preview",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertNotIn(CODE_TICKET_APPROVAL, reason)
                self.assertNotIn(CODE_RECORDS_PRUNE, reason)


if __name__ == "__main__":
    unittest.main()
