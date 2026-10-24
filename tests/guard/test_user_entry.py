"""ユーザ専用の入口（`ccnavi user <操作>`）を確かめる（src/ccnavi/tickets/user_only.py）。

1. 表: `user` の操作と、`user` の外で断るオプションが、食い違わない
2. 実行ファイル: `user <操作>` を内部の形に読み替える。`user` の外のユーザ専用のオプションは
   何も動かさずに断り、見るだけの形は断らない
3. hook: `user` の入口と `ccnavi-user-*.sh` を、呼び方に依らず止める。端末を求めない経路
   （`--yes`）は、名前を変えたコピーでも止める
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra.modes import EXIT_BLOCK, EXIT_ERROR
from ccnavi.tickets import phase_forms, user_only
from tests import ROOT
from tests.guard.test_cleanup_forms import HookCase
from tests.inproc import run_ccnavi


class TableTest(unittest.TestCase):
    def test_every_verb_reads_into_a_gated_option(self):
        gated = {row.name for row in user_only.FLAGS if row.gated}
        for verb in user_only.VERBS:
            with self.subTest(verb=verb.words):
                argv = ["user", *verb.words, *(["VALUE"] if verb.value else [])]
                rewritten, entry = user_only.rewrite(argv)
                self.assertIs(entry, verb)
                self.assertEqual(rewritten, [*verb.internal, *(["VALUE"] if verb.value else [])])
                self.assertIn(verb.internal[-1], gated)

    def test_every_gated_option_has_a_verb_or_is_a_companion(self):
        # `--yes` はどの操作にも付く。ほかは、読み替えの先として `user` の操作に入っている
        internal = {verb.internal[-1] for verb in user_only.VERBS}
        for row in user_only.FLAGS:
            if row.gated and row.name != "--yes":
                with self.subTest(option=row.name):
                    self.assertIn(row.name, internal)

    def test_operations_that_ask_for_a_terminal_exist(self):
        for op_id in ("approve", "reviewed", "close-early", "prune", "handoff", "mirror"):
            with self.subTest(op=op_id):
                self.assertTrue(user_only.terminal(op_id))

    def test_unknown_operation_is_an_error(self):
        with self.assertRaises(KeyError):
            user_only.terminal("decide")

    def test_handoff_is_a_board_verb(self):
        self.assertIn("handoff", user_only.WORD_GROUPS["user-board-verb"])
        flags = set(json.loads(run_ccnavi(["--version", "--json"]).stdout)["flags"])
        self.assertIn("--handoff", flags)

    def test_version_says_handoff(self):
        features = json.loads(run_ccnavi(["--version", "--json"]).stdout)["features"]
        self.assertIn("handoff", features)

    def test_mirror_is_a_board_verb_and_the_version_says_so(self):
        self.assertIn("mirror", user_only.WORD_GROUPS["user-board-verb"])
        version = json.loads(run_ccnavi(["--version", "--json"]).stdout)
        self.assertIn("--mirror", set(version["flags"]))
        self.assertIn("mirror", version["features"])


class RewriteTest(unittest.TestCase):
    def test_wrapper_options_are_skipped(self):
        argv = ["--root", "R", "--cwd=C", "--record-writes", "W", "user", "agree", "A"]
        rewritten, entry = user_only.rewrite(argv)
        self.assertEqual(entry.words, ("agree",))
        self.assertEqual(
            rewritten, ["--root", "R", "--cwd=C", "--record-writes", "W", "--agree", "A"]
        )

    def test_the_word_ignores_case(self):
        rewritten, entry = user_only.rewrite(["USER", "Close-Early", "--reason", "x"])
        self.assertEqual(rewritten, ["--close-early", "--reason", "x"])
        self.assertEqual(entry.words, ("close-early",))

    def test_other_places_are_not_the_entry(self):
        for argv in (
            ["--docs", "--keyword", "user"],
            ["--", "user", "agree"],
            ["--title", "user", "agree"],
            ["ticket", "user"],
            ["--agree", "--preview"],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(user_only.rewrite(argv), (argv, None))

    def test_a_value_is_taken_from_the_next_word(self):
        rewritten, _ = user_only.rewrite(["user", "reviewed", "3", "--accept-unresolved"])
        self.assertEqual(rewritten, ["--reviewed", "3", "--accept-unresolved"])
        rewritten, entry = user_only.rewrite(["user", "handoff", "a.md", "b.md", "--json"])
        self.assertEqual(rewritten, ["--handoff", "a.md", "b.md", "--json"])
        self.assertEqual(entry.op, "handoff")
        rewritten, entry = user_only.rewrite(["user", "mirror", "lib", "app", "--json"])
        self.assertEqual(rewritten, ["--mirror", "lib", "app", "--json"])
        self.assertEqual(entry.op, "mirror")

    def test_only_ascii_letters_are_folded(self):
        # `casefold` は `ſ`（U+017F）を `s` に、`ﬀ`（U+FB00）を `ff` にする。ASCII の綴りだけを
        # 入口の語として読み、互換の読みで入口の語になる綴りは断る。
        rewritten, entry = user_only.rewrite(["USER", "HandOff", "a.md"])
        self.assertEqual(rewritten, ["--handoff", "a.md"])
        for argv in (
            ["u\u017fer", "handoff"],
            ["\uff55ser", "agree"],
            ["user", "hando\ufb00"],
            ["user", "agre\u0435"],
            ["--root", "R", "user", "\u0430gree"],
        ):
            with self.subTest(argv=argv), self.assertRaises(user_only.UserEntryError):
                user_only.rewrite(argv)
        # 互換の読みでも入口の語にならない ASCII でない語は、入口ではないだけ。
        self.assertEqual(user_only.rewrite(["ユーザ", "agree"]), (["ユーザ", "agree"], None))

    def test_mistakes_say_what_is_wrong(self):
        for argv in (["user"], ["user", "bogus"], ["user", "reviewed"]):
            with self.subTest(argv=argv), self.assertRaises(user_only.UserEntryError):
                user_only.rewrite(argv)


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
            "ccnavi-user-handoff.sh --yes はユーザーがターミナルから実行する必要があります",
            result.stderr,
        )

    def test_user_mirror_writing_needs_a_terminal(self):
        result = run_ccnavi(["--entry", "sh", "user", "mirror", "--yes", "--expect", "x"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ccnavi-user-mirror.sh --yes はユーザーがターミナルから実行する必要があります",
            result.stderr,
        )

    def test_user_prune_needs_a_terminal(self):
        result = run_ccnavi(["--entry", "sh", "user", "prune"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ccnavi-user-prune.sh はユーザーがターミナルから実行する必要があります", result.stderr
        )


class EntryMarkTest(unittest.TestCase):
    """入口の印（`--entry sh|board`）。印の無い `user <操作>` は、直に打った形として断る。"""

    def test_the_mark_is_taken_out_of_the_leading_wrapper_options(self):
        for argv, rest, mark in (
            (["--entry", "sh", "user", "agree"], ["user", "agree"], "sh"),
            (["--entry=board", "user", "mirror"], ["user", "mirror"], "board"),
            (
                ["--root", "R", "--entry", "sh", "--cwd", "C", "user", "agree", "i1"],
                ["--root", "R", "--cwd", "C", "user", "agree", "i1"],
                "sh",
            ),
            (["user", "agree"], ["user", "agree"], ""),
            (["--agree", "--preview"], ["--agree", "--preview"], ""),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(user_only.take_entry(argv), (rest, mark))

    def test_a_mark_in_another_place_is_left_for_argparse(self):
        argv = ["user", "agree", "--entry", "sh"]
        self.assertEqual(user_only.take_entry(argv), (argv, ""))
        result = run_ccnavi(["--entry", "sh", "user", "prune", "--entry", "sh"])
        self.assertEqual(result.returncode, EXIT_ERROR, result.stderr)

    def test_a_bad_mark_is_an_error(self):
        for argv in (
            ["--entry", "terminal", "user", "agree"],
            ["--entry"],
            ["--entry", "sh"],
            ["--entry", "sh", "agree"],
            ["--entry", "sh", "--entry", "board", "user", "agree"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(user_only.UserEntryError):
                    user_only.take_entry(argv)
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

    def test_every_verb_has_a_script_that_exists(self):
        for verb in user_only.VERBS:
            with self.subTest(verb=verb.words):
                self.assertTrue(verb.script)
                path = os.path.join(ROOT, ".ccnavi", "scripts", f"ccnavi-user-{verb.script}.sh")
                self.assertTrue(os.path.isfile(path), path)

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

    def test_the_version_says_the_mark(self):
        self.assertIn(
            "entry-marker", json.loads(run_ccnavi(["--version", "--json"]).stdout)["features"]
        )


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
        # 値を空の引用符にしたラッパーのオプション（hook が読む前に引用符ごと消えて、`user` が値に見える）。
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
        self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)
        self.assertIn("ccnavi user", reason)

    def test_prune_follows_the_core_files_guard(self):
        decision, reason = self.hook("ccnavi user prune")
        self.assertEqual(decision, "deny")
        self.assertIn(phase_forms.CODE_RECORDS_PRUNE, reason)

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
                    self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)

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
                self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)

    def test_reading_is_not_stopped(self):
        for command in (
            "ccnavi --docs --keyword user",
            "ccnavi --keyword user --docs",
            "ccnavi --agree --preview --json",
            "ccnavi --agree --preview --verify",
            "ccnavi --prune --preview",
            "grep -rn 'user agree' docs",
            "cat docs/adr/0143-user-only-entries-are-separate.md",
            "ccnavi --lint",
            "ccnavi --templates --json",
            "ccnavi --templates --draft i0001",
            "grep -rn 'user handoff' docs",
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertNotIn(phase_forms.CODE_TICKET_APPROVAL, reason)
                self.assertNotIn(phase_forms.CODE_RECORDS_PRUNE, reason)


class LauncherSpellingTest(HookCase):
    """端末を求めない経路は hook の正規表現が唯一の防御なので、実行ファイルを Python から走らせる
    表記の揺れ（インタプリタの選択肢・`ccnavi.__main__`・`py`・実行役）でも止める。"""

    def assert_stopped(self, commands):
        for command in commands:
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)

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
                self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)

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
                self.assertNotIn(phase_forms.CODE_TICKET_APPROVAL, reason)
                self.assertNotIn(phase_forms.CODE_RECORDS_PRUNE, reason)


if __name__ == "__main__":
    unittest.main()
