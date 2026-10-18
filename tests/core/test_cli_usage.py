"""`ccnavi --help` の本文（`cli_usage.USAGE`）が、引数解析（`cli.py`）の受け付けるものを
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

    def test_docs_help_is_only_the_docs_section(self):
        for listed in ("--keyword", "--text", "--sort", "--format", "--no-refresh"):
            self.assertIn(listed, cli_usage.DOCS_USAGE)
        for other in ("--templates", "--agree", "--lint", "ccnavi ticket"):
            self.assertNotIn(other, cli_usage.DOCS_USAGE)
        self.assertIn(cli_usage.DOCS_USAGE, cli_usage.USAGE)


if __name__ == "__main__":
    unittest.main()
