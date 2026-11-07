"""SessionStart で、終わっていない `integrate` の記録を知らせる受入テスト。

記録（取り込み先のツリーの `.git/ccnavi-integrate/<名前>/marker`）を、モデルの文脈と
`systemMessage` の両方に出す。記録が無ければ何も足さない。記録ごとの文は
go/internal/tickets/notice の pending_test.go が見る。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket.test_ticket import TicketTest, git, write


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in sorted(vars(PendingIntegrateTest)):
        if name.startswith("test_"):
            suite.addTest(PendingIntegrateTest(name))
    return suite


HEAD = "終わっていない integrate の記録があります"


class PendingIntegrateTest(TicketTest):
    def marker(self, name, **fields):
        gitdir = os.path.join(self.root, ".git", "ccnavi-integrate", name)
        os.makedirs(gitdir, exist_ok=True)
        write(
            os.path.join(gitdir, "marker"),
            "".join(f"{k} {v}\n" for k, v in fields.items()),
        )

    def head(self):
        return git(self.root, "rev-parse", "HEAD").strip()

    def start(self, **extra):
        payload = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "session_id": "ss",
            "cwd": self.root,
        }
        payload.update(extra)
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def test_no_record_says_nothing(self):
        self.assertNotIn(HEAD, self.start().stdout)

    def test_session_start_gives_both_context_and_system_message_before_docs(self):
        head = self.head()
        self.marker("w7", state="conflict", branch="w7", branch_sha=head, base=head)
        out = json.loads(self.start().stdout)
        self.assertIn(HEAD, out["hookSpecificOutput"]["additionalContext"])
        self.assertIn(HEAD, out["systemMessage"])

    def test_the_notice_does_not_write_to_the_object_database(self):
        head = self.head()
        tree = git(self.root, "rev-parse", "HEAD^{tree}").strip()
        self.marker("w10", state="staged", branch="w10", branch_sha=head, base=head, tree=tree)

        def objects():
            found = []
            for base, _dirs, files in os.walk(os.path.join(self.root, ".git", "objects")):
                found.extend(os.path.join(base, f) for f in files)
            return sorted(found)

        before = objects()
        self.assertIn(HEAD, self.start().stdout)
        self.assertEqual(before, objects())

    def test_a_subagent_is_not_told(self):
        head = self.head()
        self.marker("w8", state="conflict", branch="w8", branch_sha=head, base=head)
        self.assertNotIn(HEAD, self.start(agent_id="a1").stdout)


if __name__ == "__main__":
    unittest.main()
