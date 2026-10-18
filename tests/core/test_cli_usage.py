"""`ccnavi --help` の全文（`cli_usage.USAGE`）が、引数解析（`cli.py`）の受け付けるものを
全部書いていること。

フラグは `cli.py` の `add_argument` から、副命令は `cli.py` と `cli_ops.py` が見る語の組から
拾い、本文に出てくるかを確かめる。解析にだけ足してヘルプに書き忘れると、ここで落ちる。
"""

from __future__ import annotations

import os
import re
import unittest

from ccnavi.entry import cli_usage
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
        pairs = set(re.findall(r'\["([a-z0-9]+)", "([a-z-]+)"\]', _read(CLI)))
        self.assertIn(("c1", "family"), pairs)
        missing = sorted(f"{a} {b}" for a, b in pairs if f"ccnavi {a} {b}" not in cli_usage.USAGE)
        self.assertEqual(missing, [], "ヘルプに書いていない副命令")
        # `worktree <verb>` は cli_ops が見る。使い方の誤りの文面に並べた形を拾う
        verbs = set(re.findall(r"worktree ([a-z-]+) <", _read(CLI_OPS)))
        self.assertIn("issue-id", verbs)
        missing = sorted(v for v in verbs if f"ccnavi worktree {v} " not in cli_usage.USAGE)
        self.assertEqual(missing, [], "ヘルプに書いていない worktree の副命令")

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
            "templates": ("--draft", "--save-draft", "ticket new ID --title"),
            "agree": ("--plan-order", "--fill-phases", "--digest"),
            "ticket": ("ticket record-risk", "ticket learn", "ticket next-id"),
            "scripts": ("ccnavi c1 family", "ccnavi worktree tidy", "--record-writes"),
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
        self.assertEqual(helped("--help", "nothing"), cli_usage.OVERVIEW)


if __name__ == "__main__":
    unittest.main()
