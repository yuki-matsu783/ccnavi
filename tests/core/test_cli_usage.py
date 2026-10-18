"""`ccnavi --help` の全文（`cli_usage.USAGE`）に、引数解析（`cli.py`）の受け付けるものが
すべて書かれていることを確かめる。

フラグは `cli.py` の `add_argument` から、サブコマンドは `cli.py` と `cli_ops.py` が見る語の組から
拾い、本文に出てくるかを確かめる。解析にだけ足してヘルプに書き忘れると、ここで落ちる。
"""

from __future__ import annotations

import os
import re
import unittest

from ccnavi.entry import cli_args, cli_usage
from tests import ROOT
from tests.inproc import run_ccnavi

CLI = os.path.join(ROOT, "src", "ccnavi", "entry", "cli.py")
CLI_OPS = os.path.join(ROOT, "src", "ccnavi", "entry", "cli_ops.py")

# ヘルプに書かないフラグ。`-h` / `--help` はヘルプそのもの、`-r` / `--reverse` は
# `--docs` の節に `-r` で書いてある。
UNLISTED = {"--help", "--reverse"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class CliUsageTest(unittest.TestCase):
    def test_every_flag_is_in_the_help(self):
        flags = set(
            re.findall(r'add_argument\(\s*(?:"-[a-zA-Z]",\s*)?"(--[a-z][a-z-]*)"', _read(CLI))
        )
        self.assertGreater(len(flags), 50, "add_argument を拾えていない")
        missing = sorted(f for f in flags - UNLISTED if f not in cli_usage.USAGE)
        self.assertEqual(missing, [], "ヘルプに書いていないフラグ")

    def test_every_subcommand_is_in_the_help(self):
        pairs = set(re.findall(r'\["([a-z0-9-]+)", "([a-z-]+)"\]', _read(CLI)))
        self.assertIn(("write-and-push", "family"), pairs)
        missing = sorted(f"{a} {b}" for a, b in pairs if f"ccnavi {a} {b}" not in cli_usage.USAGE)
        self.assertEqual(missing, [], "ヘルプに書いていないサブコマンド")
        # `worktree <verb>` は cli_ops が見る。使い方の誤りの文面に並べた形を拾う
        verbs = set(re.findall(r"worktree ([a-z-]+) <", _read(CLI_OPS)))
        self.assertIn("issue-id", verbs)
        missing = sorted(v for v in verbs if f"ccnavi worktree {v} " not in cli_usage.USAGE)
        self.assertEqual(missing, [], "ヘルプに書いていない worktree のサブコマンド")

    def test_every_topic_is_listed_in_the_overview(self):
        for name, _, summary, _ in cli_usage.TOPICS:
            self.assertIn(f"    {name} ", cli_usage.OVERVIEW)
            self.assertIn(summary, cli_usage.OVERVIEW)
        self.assertLess(len(cli_usage.OVERVIEW.splitlines()), 40)

    def test_the_full_text_is_every_topic(self):
        for *_, body in cli_usage.TOPICS:
            self.assertIn(body, cli_usage.USAGE)
        self.assertIn(cli_usage.DOCS_USAGE, cli_usage.USAGE)

    def test_a_topic_holds_only_its_own_commands(self):
        own = {
            "docs": ("--text", "--sort", "--format", "--no-refresh"),
            "templates": ("--draft", "ticket new ID --title"),
            "agree": ("--plan-order", "--fill-phases", "--digest"),
            "ticket": ("ticket record-risk", "ticket learn", "ticket next-id"),
            "scripts": ("ccnavi write-and-push family", "ccnavi worktree tidy", "--record-writes"),
            "logs": ("--prune", "--suggest"),
        }
        for name, flags in own.items():
            text = cli_usage.topic(name)
            for flag in flags:
                self.assertIn(flag, text, name)
            for other, others in own.items():
                if other != name:
                    for flag in others:
                        self.assertNotIn(flag, text, f"{name} に {other} のもの")

    def test_help_picks_the_topic_from_the_flag_or_the_word(self):
        def helped(*args):
            done = run_ccnavi(list(args), cwd=ROOT)
            self.assertEqual(done.returncode, 1, done.stdout)
            return done.stderr

        self.assertEqual(helped("--help"), cli_usage.OVERVIEW)
        self.assertEqual(helped("--help", "--all"), cli_usage.USAGE)
        self.assertEqual(helped("--docs", "--help"), cli_usage.topic("docs"))
        self.assertEqual(helped("--help", "--prune"), cli_usage.topic("logs"))
        self.assertEqual(helped("--agree", "--help"), cli_usage.topic("agree"))
        self.assertEqual(helped("ticket", "--help"), cli_usage.topic("ticket"))
        self.assertEqual(helped("--help", "docs"), cli_usage.topic("docs"))
        self.assertEqual(helped("worktree", "--help"), cli_usage.topic("scripts"))
        self.assertEqual(helped("--lint", "--help"), cli_usage.topic("lint"))
        self.assertEqual(helped("--version", "--help"), cli_usage.topic("version"))
        self.assertEqual(helped("--explain", "--help"), cli_usage.topic("explain"))
        self.assertEqual(helped("--templates", "--help"), cli_usage.topic("templates"))
        self.assertEqual(helped("--help", "--via", "board"), cli_usage.topic("scripts"))
        self.assertEqual(helped("--help", "--close-early"), cli_usage.topic("scripts"))
        self.assertEqual(helped("-h", "--suggest"), cli_usage.topic("logs"))
        for word in ("review", "sync", "tree", "write-and-push", "branches"):
            self.assertEqual(helped(word, "--help"), cli_usage.topic("scripts"), word)
        # 旗の優先は TOPICS ではなく _HELP_FLAGS の順（上が勝つ）。
        self.assertEqual(helped("--help", "--templates", "--docs"), cli_usage.topic("docs"))

    def test_help_of_test_is_reachable_though_test_takes_two_words(self):
        for args in (
            ("--test", "--help"),
            ("--test", "Bash", "--help"),
            ("--test", "Bash", "ls", "--help"),
            ("--help", "--test"),
        ):
            with self.subTest(args=args):
                done = run_ccnavi(list(args), cwd=ROOT)
                self.assertEqual(done.stderr, cli_usage.topic("test"))

    def test_an_unknown_word_says_so_before_the_overview(self):
        done = run_ccnavi(["--help", "nothing"], cwd=ROOT)
        self.assertTrue(done.stderr.startswith("ccnavi: 話題 'nothing' はありません"), done.stderr)
        self.assertTrue(done.stderr.endswith(cli_usage.OVERVIEW))

    def test_all_is_refused_without_help(self):
        done = run_ccnavi(["--docs", "--all"], cwd=ROOT)
        self.assertEqual(done.returncode, 1)
        self.assertIn("--all は --help と一緒にだけ", done.stderr)

    def test_every_help_flag_topic_exists(self):
        names = {name for name, *_ in cli_usage.TOPICS}
        self.assertEqual({name for name, _ in cli_args._HELP_FLAGS} - names, set())


if __name__ == "__main__":
    unittest.main()
