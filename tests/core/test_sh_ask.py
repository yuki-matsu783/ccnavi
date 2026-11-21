"""`ccnavi tree parent` の約束。

sh はチケットの YAML を自分で読まず、これに聞く。答えは頭の行のあとに 1 行 1 項目
（`<キー> <値>`）。読むだけで、何も書かない。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi

APPROVED = ".ccnavi/approved"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def front(**fields):
    body = "".join(f"{k}: {v}\n" for k, v in fields.items())
    return f"---\n{body}---\n本文\n"


class Harness(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-sh-ask-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        self.tree = os.path.join(self.ws, ".claude", "worktrees", "p0001")
        os.makedirs(self.tree)

    def ask(self, *words):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(["--root", self.ws, *words], env=environment)

    def answer(self, head, *words):
        result = self.ask(*words)
        self.assertEqual(0, result.returncode, result.stderr)
        out = result.stdout.splitlines()
        self.assertEqual(head, out[0])
        return dict((line.split(" ", 1) + [""])[:2] for line in out[1:])


class TreeParentTest(Harness):
    """答えの行の並び・親の見分け方・push のゲートが聞く 5 行は、go/internal/entry/shask の見本
    （TestAnswersMatchGolden。ファイルの形ごとの場面を含む）が 1 字ずつ見る。
    ここは、実行ファイルの入口（`tree parent`）を通して答えが返ることだけを見る。"""

    def test_the_answer_comes_through_the_entry(self):
        write(
            os.path.join(self.tree, APPROVED, "doing", "p0001.md"),
            front(ticket="p0001", mr=430, started_at="2026-10-18T00:00:00Z"),
        )
        found = self.answer("tree 1", "tree", "parent", self.tree, "p0001")
        self.assertEqual(
            {"parent": "yes", "approved": "yes", "started": "yes", "mr": "430"},
            {k: found[k] for k in ("parent", "approved", "started", "mr")},
        )


if __name__ == "__main__":
    unittest.main()
