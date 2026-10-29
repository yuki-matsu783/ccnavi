"""学びの蓄積の完成の条件を 1 本で通す受入テスト。

子が記録を書く → 親の finish が止まる → consolidate → 統合の子が処理を書いて閉じる →
親が閉じる → 統合先に入った後、候補が条件を満たすと通知が出る → 提案を延期 →
観測元が増えると再び通知が出る。

実行ファイルは in-process で呼ぶ（sh の write-and-push は通さない）。
"""

from __future__ import annotations

import os
import subprocess
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.notice import learning_notice
from tests.learningsample import impact, pattern
from tests.ticket.test_learning_consolidate import (
    CHILD,
    CONS,
    EN,
    RECORD,
    FlowHarness,
)
from tests.ticket.test_ticket import git, write

PATTERN = "docs/wiki/patterns/pat.md"


class LearningCycleTest(FlowHarness):
    def notice(self):
        conf, _ = settings.load(self.root)
        return learning_notice.notice(conf, self.root)

    def publish(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", message)
        git(self.root, "push", "--quiet", "origin", "main")
        git(self.root, "fetch", "--quiet", "origin")

    def test_cycle(self):
        # 統合先 main に、すでに 2 つの観測元があるパターンを置いておく
        write(os.path.join(self.root, *PATTERN.split("/")), pattern(["x1", "x2"]))
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "pattern")
        git(self.parent_tree, "merge", "--quiet", "--no-edit", "main")

        # 1. 子が記録を書いて閉じ、親の finish が止まる
        self.setup_family()
        refused = self.parent_finish()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("consolidate", refused.stderr)

        # 2. consolidate が統合の子を作る
        made = self.consolidate()
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "consolidate child")

        # 3. 統合の子が、パターンに観測元を足し、記録に処理の行を書いて閉じる
        tree = self.worktree(CONS, "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", CONS, env=EN).returncode, 0)
        write(
            os.path.join(tree, *PATTERN.split("/")),
            pattern(["x1", "x2", CHILD]),
        )
        record = os.path.join(tree, *RECORD.split("/"))
        with open(record, encoding="utf-8") as f:
            text = f.read()
        write(record, text.rstrip("\n") + "\n- 処理: 反映 pat\n")
        git(tree, "add", "-f", "-A")
        git(tree, "commit", "--quiet", "-m", "process")
        closed = self.ccnavi("ticket", "finish", CONS, env=EN)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.merge(CONS)

        # 4. 親が閉じる
        done = self.parent_finish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

        # 5. 統合先に入った後、観測元が閾値に届いたので通知が出る（1 回だけ）
        git(self.root, "merge", "--quiet", "--no-edit", "i0001")
        # origin はここで足す（足すと、状態の操作は write-and-push の対象になるため）
        bare = os.path.join(self.root, "origin.git")
        subprocess.run(["git", "init", "--quiet", "--bare", bare], check=True)
        git(self.root, "remote", "add", "origin", bare)
        git(self.root, "push", "--quiet", "origin", "main")
        git(self.root, "fetch", "--quiet", "origin")
        git(self.root, "remote", "set-head", "origin", "main")
        first = self.notice()
        self.assertIn("学びの提案の候補が 1 件", first)
        self.assertEqual(self.notice(), "")

        # 6. 提案を延期（その時点の観測元の数 3 を書く）。通知は出ない
        write(
            os.path.join(self.root, "docs", "wiki", "impact", "p1.md"),
            impact("p1", "延期", {"pat": 3}, "2026-10-21T10:00:00+09:00", target="skill"),
        )
        self.publish("defer")
        self.assertEqual(self.notice(), "")

        # 7. 観測元が増えると候補に戻り、再び通知が出る
        write(
            os.path.join(self.root, *PATTERN.split("/")),
            pattern(["x1", "x2", CHILD, "y1"]),
        )
        self.publish("one more source")
        self.assertIn("学びの提案の候補が 1 件", self.notice())


if __name__ == "__main__":
    unittest.main()
