"""変更要求の案内は、ホストごとに出し分ける。判定は変えない。

同じ結果（変更要求のレビューが 1 件）でホストだけ変えて、`confirm`・`decide`・`close-early`・
手で動かした子の確定の拒否の文面が、GitLab と GitHub で分かれることを見る。
ホストは取得した結果の `host`。

- GitLab: 変更を依頼（Request changes）。解除はレビュアーの承認（Approve）。
  再依頼で状態が戻るかは未検証
- GitHub: 変更要求（Changes requested）。解除はレビュアーの Approve か dismiss
"""

from __future__ import annotations

import json
import os
import shutil
import unittest

from ccnavi.tickets import review_host
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text

GITLAB = "変更を依頼（Request changes）が出ています。解除はレビュアーの承認（Approve）で行います。"
GITLAB_UNVERIFIED = "作成者によるレビューの再依頼（Re-request review）で状態が戻るかは未検証です"
GITHUB = (
    "変更要求（Changes requested）が出ています。"
    "解除はレビュアーの Approve か、レビューの dismiss で行います"
)


class NoteTest(unittest.TestCase):
    def test_the_note_by_host(self):
        self.assertIn(GITLAB, review_host.changes_requested_note("gitlab"))
        self.assertIn(GITLAB_UNVERIFIED, review_host.changes_requested_note("gitlab"))
        self.assertIn(
            "ccnavi で状態を取り直して確かめます", review_host.changes_requested_note("gitlab")
        )
        for host in ("github", "fixture", ""):
            self.assertEqual(review_host.changes_requested_note(host), GITHUB)
        self.assertNotIn("Approve か", review_host.changes_requested_note("gitlab"))
        self.assertNotIn("dismiss", review_host.changes_requested_note("gitlab"))


class HostsTest(PhaseHarness):
    def requested(self, host):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        # 依頼の記録のホストを合わせる（結果のホストと照合されるため）
        mark = os.path.join(self.approved, "phases", "i0001", "1.requested")
        body = read_json(mark)
        body["host"] = host
        write(mark, json.dumps(body))
        self.commit_parent("request")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")

    def result(self, host):
        return write(
            os.path.join(self.root, f"result-{host}.json"),
            json.dumps(
                {
                    "host": host,
                    "mr": {"number": 7, "url": "u/7", "draft": True},
                    "threads": [],
                    "reviews": [
                        {
                            "state": "CHANGES_REQUESTED",
                            "url": "u/7#review",
                            "submitted_at": "2026-01-01T00:00:00Z",
                            "author": "rev",
                        }
                    ],
                }
            ),
        )

    def said(self, host, text):
        want, other = (GITLAB, GITHUB) if host == "gitlab" else (GITHUB, GITLAB)
        self.assertIn(want, text)
        self.assertNotIn(other, text)
        if host == "gitlab":
            self.assertIn(GITLAB_UNVERIFIED, text)
        else:
            self.assertNotIn("Re-request", text)

    def test_confirm_decide_and_close_early_say_it_by_host(self):
        for host in ("gitlab", "github"):
            with self.subTest(host=host):
                self.requested(host)
                result = self.result(host)
                confirm = self.confirm(result, 1)
                self.assertNotEqual(confirm.returncode, 0)
                self.said(host, confirm.stderr)
                decide = self.ccnavi(
                    "--cwd",
                    self.parent_tree,
                    "user",
                    "reviewed",
                    "1",
                    "--accept-unresolved",
                    "--preview",
                    "--json",
                    "--result",
                    result,
                )
                self.assertNotEqual(decide.returncode, 0)
                self.said(host, decide.stderr)
                closed = self.ccnavi(
                    "--cwd",
                    self.parent_tree,
                    "user",
                    "close-early",
                    "--reason",
                    "ここまでで十分",
                    "--result",
                    result,
                    stdin="y\n",
                )
                self.assertNotEqual(closed.returncode, 0)
                self.said(host, closed.stderr)
                # 判定は変えない: どのホストでもレビュー済みにならない
                self.assertFalse(
                    os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
                )
                self.tearDownHost()

    def tearDownHost(self):
        """次のホストの回のために、親のツリーと origin を作り直す。"""
        shutil.rmtree(self.root, ignore_errors=True)
        self.setUp()

    def test_a_manually_moved_child_is_held_with_the_host_note(self):
        for host in ("gitlab", "github"):
            with self.subTest(host=host):
                self.requested(host)
                waiting = os.path.join(
                    self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md"
                )
                done = os.path.join(self.approved, "done", "i0001-01-01.md")
                os.makedirs(os.path.dirname(done), exist_ok=True)
                shutil.move(waiting, done)
                self.commit_parent("move by hand")
                git(self.parent_tree, "push", "--quiet", "origin", "i0001")
                held = self.confirm(self.result(host), 1)
                self.assertNotEqual(held.returncode, 0)
                self.said(host, held.stderr)
                self.assertIn("保留", held.stderr)
                self.tearDownHost()


if __name__ == "__main__":
    unittest.main()
