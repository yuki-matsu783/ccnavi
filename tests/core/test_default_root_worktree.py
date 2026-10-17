"""ワークツリー内から default_root() を呼んでも、ワークスペースルートを返す（#423）。

ワークツリーは `.claude` を追跡して自分も持つので、`.claude` だけで止まると
ワークスペースルートをワークツリーと取り違える。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ccnavi.entry import cli


class DefaultRootWorktreeTest(unittest.TestCase):
    def test_worktree_内からはワークスペースルートを返す(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.realpath(tmp)
            wt = os.path.join(ws, ".claude", "worktrees", "x")
            sub = os.path.join(wt, "sub")
            os.makedirs(os.path.join(wt, ".claude"))
            os.makedirs(sub)
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
            old = os.getcwd()
            try:
                with mock.patch.dict(os.environ, env, clear=True):
                    for start in (wt, sub):
                        os.chdir(start)
                        self.assertEqual(os.path.realpath(cli.default_root()), ws)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
