"""`ccnavi-ticket.sh` の start・finish・approval-request・consolidate が打つ事前確認の受入テスト。

ホストの API と git のリモートが使える状態かを、ロックを取る前に確かめる（直接作業では確かめない）。
共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` と `tests/host_stub.py` にある。
ホストは代役（失敗する gh / glab、routes.json で答える curl）で、本物のネットワークには出ない。
"""

from __future__ import annotations

import os
import unittest

from tests import host_stub
from tests.sh.write_and_push_harness import (
    CHILD,
    PARENT,
    WriteAndPushHarness,
    executable,
)

GOOD = {
    f"GET {host_stub.PROJECT}": [200, {"id": 42}],
    f"GET {host_stub.API}/user": [200, {"id": 5}],
    f"GET {host_stub.PROJECT}/members/all/5": [200, {"access_level": 40}],
}


class TicketPreflightTest(WriteAndPushHarness):
    def requests(self):
        if not os.path.exists(self.requests_log):
            return []
        with open(self.requests_log, encoding="utf-8") as f:
            return f.read().splitlines()

    def git_shim(self, body):
        """ls-remote --heads だけ差し替える git の代役。呼ばれたことを ls-remote.log に残す。"""
        shim = os.path.join(self._base, "shim")
        log = os.path.join(self._base, "ls-remote.log")
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" ls-remote --heads "*)\n'
            f"  echo ls-remote >> '{log}'\n"
            f"{body}\n"
            "  ;;\n"
            "esac\n"
            'self=$(dirname "$0")\n'
            "rest=''\n"
            "oldifs=$IFS; IFS=:\n"
            'for p in $PATH; do [ "$p" = "$self" ] || rest="$rest:$p"; done\n'
            "IFS=$oldifs\n"
            'PATH="${rest#:}"\n'
            'exec git "$@"\n',
        )
        return shim, log

    def untouched(self, before_head, before_remote):
        self.assertEqual(self.sha(self.tree, "HEAD"), before_head)
        self.assertEqual(self.remote_sha(), before_remote)
        self.assertFalse(os.path.exists(self.lock_dir()))
        self.assertEqual(self.dirty(), "")

    def test_insufficient_role_stops_start_without_lock_write_or_push(self):
        self.set_routes(
            {**GOOD, f"GET {host_stub.PROJECT}/members/all/5": [200, {"access_level": 20}]}
        )
        head, remote = self.sha(self.tree, "HEAD"), self.remote_sha()
        result = self.ticket("start", PARENT, token="t0k")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("確認: access: NG", result.stderr)
        self.assertIn("次: ", result.stderr)
        self.assertNotIn("t0k", result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.untouched(head, remote)
        self.assertNotIn("POST", " ".join(self.requests()))

    def test_all_ok_prints_nothing(self):
        self.set_routes(GOOD)
        result = self.ticket("start", PARENT, token="t0k")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("確認:", result.stderr)
        self.assertNotIn("注意:", result.stderr)

    def test_unverified_api_with_a_working_git_continues_with_a_note(self):
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("確認: api: 未確認", result.stderr)
        self.assertIn("確認: access: 省略", result.stderr)
        self.assertIn(
            "このあとの review-request・confirm・ready はホストの API が要る", result.stderr
        )
        self.assertNotIn("確認:", result.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_git_is_checked_even_when_the_api_is_unverified(self):
        shim, log = self.git_shim("  :")
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(log, encoding="utf-8") as f:
            self.assertIn("ls-remote", f.read())

    def test_an_unverified_git_stops_without_writing(self):
        shim, _ = self.git_shim(
            "  echo 'fatal: unable to access: Could not resolve host' >&2\n  exit 128"
        )
        head, remote = self.sha(self.tree, "HEAD"), self.remote_sha()
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("確認: git: 未確認", result.stderr)
        self.untouched(head, remote)

    def test_a_git_authentication_failure_stops_without_writing(self):
        shim, _ = self.git_shim("  echo 'fatal: Authentication failed for x' >&2\n  exit 128")
        self.set_routes(GOOD)
        head, remote = self.sha(self.tree, "HEAD"), self.remote_sha()
        result = self.ticket(
            "start", PARENT, token="t0k", PATH=shim + os.pathsep + os.environ.get("PATH", "")
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("確認: git: NG", result.stderr)
        self.assertIn("git の資格情報", result.stderr)
        self.untouched(head, remote)

    def test_propose_keeps_its_first_line_and_asks_the_host_once(self):
        self.set_routes(GOOD)
        result = self.ticket("approval-request", PARENT, token="t0k")
        # 事前確認だけが `GET user` を読む（外側と内側の 2 回起動しても、確かめるのは内側の 1 回）
        asked = [line for line in self.requests() if line == f"GET {host_stub.API}/user"]
        self.assertEqual(len(asked), 1, self.requests())
        self.assertNotIn("確認:", result.stdout)

    def test_a_child_start_and_finish_are_checked_too(self):
        self.set_routes(
            {**GOOD, f"GET {host_stub.PROJECT}/members/all/5": [200, {"access_level": 20}]}
        )
        for verb in ("start", "finish"):
            with self.subTest(verb=verb):
                result = self.ticket(verb, CHILD, token="t0k")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("確認: access: NG", result.stderr)

    def test_consolidate_is_checked_but_cancel_is_not(self):
        self.set_routes(
            {**GOOD, f"GET {host_stub.PROJECT}/members/all/5": [200, {"access_level": 20}]}
        )
        consolidate = self.ticket("consolidate", PARENT, token="t0k")
        self.assertEqual(consolidate.returncode, 1, consolidate.stdout + consolidate.stderr)
        self.assertIn("確認: access: NG", consolidate.stderr)
        # 取り消しは、権限が足りないときでも事前確認で止めない
        cancel = self.ticket("cancel", PARENT, "--reason", "やめた", token="t0k")
        self.assertNotIn("確認:", cancel.stderr)

    def test_status_and_next_id_do_not_check(self):
        self.set_routes(GOOD)
        self.ticket("status", PARENT, token="t0k")
        self.ticket("next-id", token="t0k")
        self.assertEqual(self.requests(), [])


if __name__ == "__main__":
    unittest.main()
