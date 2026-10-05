"""空白を含むパスへのリダイレクト（`> "a b/.ccnavi/x"`）を、組み込みの保護が止める（#208）。

引用の中の空白は shellread が目印（WORD_SEP）に置き換える。リダイレクトの行き先を
拾う式がその目印で止まると、空白を含むパスだけが通っていた。
"""

from __future__ import annotations

import re
import unittest

from ccnavi.infra import shellread
from ccnavi.policy import selfguard_shell

ROOT_WITH_SPACE = "/work/my ws"


def hits(command: str, root: str = "/work/ws") -> bool:
    text = shellread.read(command).text
    return re.search(selfguard_shell.guard_shell_regex(root), text, re.IGNORECASE) is not None


class RedirectSpaceTest(unittest.TestCase):
    def test_quoted_path_with_space_is_stopped(self):
        self.assertTrue(hits('echo x > "projects/has space/.ccnavi/config/rules.yml"'))
        self.assertTrue(hits('echo x >> "projects/has space/.ccnavi/config/rules.yml"'))
        self.assertTrue(hits("echo x > 'has space/.claude/settings.json'"))

    def test_path_without_space_is_stopped(self):
        self.assertTrue(hits("echo x > projects/p/.ccnavi/config/rules.yml"))
        self.assertTrue(hits('echo x > "projects/p/.ccnavi/config/rules.yml"'))

    def test_unquoted_escaped_space_is_stopped(self):
        self.assertTrue(hits(r"echo x > projects/has\ space/.ccnavi/config/rules.yml"))

    def test_absolute_path_with_space_in_root_is_stopped(self):
        command = f'echo x > "{ROOT_WITH_SPACE}/.ccnavi/config/rules.yml"'
        self.assertTrue(hits(command, ROOT_WITH_SPACE))
        self.assertTrue(hits(f'echo x > "{ROOT_WITH_SPACE}/logs/decisions.jsonl"', ROOT_WITH_SPACE))

    def test_unrelated_paths_with_space_are_not_stopped(self):
        self.assertFalse(hits('echo x > "has space/notes.txt"'))
        self.assertFalse(hits('echo x > "has space/ccnavi notes.txt"'))
        self.assertFalse(hits('echo x > /dev/null "has space/.ccnavi/x"'))

    def test_quoted_operator_is_not_a_redirect(self):
        self.assertFalse(hits('echo "a > .ccnavi/x"'))
        self.assertFalse(hits('grep ">.ccnavi/x" notes.txt'))
        self.assertFalse(hits('echo a">".ccnavi/x'))


if __name__ == "__main__":
    unittest.main()
