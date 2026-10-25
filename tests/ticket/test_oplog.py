"""操作記録（`tickets/oplog.py`）。保存・段階の更新・未完了の判定・消さない条件を確かめる。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from ccnavi.records import prune
from ccnavi.tickets import oplog

REPO = {"host": "github.com", "kind": "github", "path": "o/r"}
STAGES = [("reply", oplog.STAGE_REPLY), ("issue", oplog.STAGE_ISSUE), ("note", oplog.STAGE_COMMENT)]


class OplogTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = os.path.join(directory.name, "logs", "state")

    def begin(self, **extra):
        return oplog.begin(
            self.state,
            oplog.KIND_DECIDE,
            repo=REPO,
            parent="i0001",
            phase=1,
            mr=7,
            stages=STAGES,
            **extra,
        )

    def test_begin_saves_before_anything_else_and_returns_a_32_hex_id(self):
        op_id = self.begin(choices={"k": "keep"})
        self.assertTrue(oplog.valid_id(op_id))
        record = oplog.load(self.state, op_id)
        self.assertEqual(record["kind"], "decide")
        self.assertEqual(record["choices"], {"k": "keep"})
        self.assertEqual([s["state"] for s in record["stages"]], ["pending"] * 3)
        self.assertEqual(record["version"], 1)
        self.assertTrue(record["created_at"])

    def test_the_record_keeps_no_body_text(self):
        op_id = self.begin(shown=[{"key": "k", "version": "v", "body": "指摘"}])
        with open(oplog.record_path(self.state, op_id), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("comment_body", text)
        self.assertIn("指摘", text)  # 見せた指摘の本文は shown に持つ

    def test_begin_with_an_existing_id_does_not_overwrite(self):
        op_id = self.begin()
        oplog.stage_set(self.state, op_id, "reply", oplog.SENT, url="u")
        again = oplog.begin(self.state, oplog.KIND_DECIDE, repo=REPO, stages=STAGES, op_id=op_id)
        self.assertEqual(again, op_id)
        self.assertEqual(oplog.load(self.state, op_id)["stages"][0]["state"], "sent")

    def test_a_bad_id_or_kind_is_refused(self):
        with self.assertRaises(oplog.OpError):
            oplog.begin(self.state, "unknown", repo=REPO)
        with self.assertRaises(oplog.OpError):
            oplog.begin(self.state, oplog.KIND_DECIDE, repo=REPO, op_id="../x")
        with self.assertRaises(oplog.OpError):
            oplog.load(self.state, "../../etc/passwd")

    def test_stage_set_records_url_account_and_tries(self):
        op_id = self.begin()
        oplog.stage_set(self.state, op_id, "issue", oplog.UNKNOWN)
        oplog.stage_set(self.state, op_id, "issue", oplog.PENDING)
        oplog.stage_set(self.state, op_id, "issue", oplog.SENT, url="https://x/1", account="me")
        stage = oplog.load(self.state, op_id)["stages"][1]
        self.assertEqual(
            (stage["state"], stage["url"], stage["account"]), ("sent", "https://x/1", "me")
        )
        self.assertGreaterEqual(stage["tries"], 1)

    def test_a_sent_stage_cannot_go_back(self):
        op_id = self.begin()
        oplog.stage_set(self.state, op_id, "reply", oplog.SENT)
        with self.assertRaises(oplog.OpError):
            oplog.stage_set(self.state, op_id, "reply", oplog.PENDING)
        with self.assertRaises(oplog.OpError):
            oplog.stage_set(self.state, op_id, "nope", oplog.SENT)
        with self.assertRaises(oplog.OpError):
            oplog.stage_set(self.state, op_id, "reply", "weird")

    def test_pending_lists_only_unfinished_records(self):
        done = self.begin()
        for sid, _ in STAGES:
            oplog.stage_set(self.state, done, sid, oplog.SENT)
        half = self.begin()
        oplog.stage_set(self.state, half, "reply", oplog.SENT)
        ids = [r["id"] for r in oplog.pending(self.state)]
        self.assertEqual(ids, [half])
        self.assertTrue(oplog.complete_if_done(self.state, done))
        self.assertFalse(oplog.complete_if_done(self.state, half))
        self.assertIsNone(oplog.load(self.state, done))
        self.assertIsNotNone(oplog.load(self.state, half))

    def test_a_broken_record_is_reported_not_hidden(self):
        op_id = self.begin()
        with open(oplog.record_path(self.state, op_id), "w", encoding="utf-8") as f:
            f.write("{not json")
        found = oplog.pending(self.state)
        self.assertEqual(len(found), 1)
        self.assertIn("broken", found[0])
        self.assertIn("壊れている", oplog.summary(found[0]))

    def test_applied_set_keeps_what_was_applied_locally(self):
        op_id = self.begin()
        oplog.applied_set(self.state, op_id, "moved", ["i0001-01-01"])
        self.assertEqual(oplog.load(self.state, op_id)["applied"]["moved"], ["i0001-01-01"])

    def test_the_record_is_valid_json_with_the_documented_fields(self):
        op_id = self.begin()
        with open(oplog.record_path(self.state, op_id), encoding="utf-8") as f:
            data = json.load(f)
        for key in (
            "version",
            "id",
            "kind",
            "repo",
            "parent",
            "phase",
            "mr",
            "request_head",
            "shown",
            "choices",
            "plan",
            "applied",
            "stages",
            "created_at",
            "updated_at",
        ):
            self.assertIn(key, data)


class MarkSentTest(unittest.TestCase):
    def test_an_unknown_post_found_by_its_marker_becomes_sent_and_a_missing_key_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            op_id = oplog.id_for("request", "p1", "0", "7", "k")
            oplog.begin(
                state,
                "request",
                repo={},
                parent="p1",
                mr=7,
                stages=[("comment", "comment")],
                op_id=op_id,
            )
            oplog.stage_set(state, op_id, "comment", oplog.UNKNOWN)
            self.assertEqual(
                oplog.mark_sent(state, "request", "p1", "7", "k", "comment", "u"), op_id
            )
            self.assertEqual(oplog.load(state, op_id)["stages"][0]["state"], "sent")
            self.assertEqual(oplog.pending(state), [])


class RetireParentTest(unittest.TestCase):
    def test_only_finished_records_of_that_parent_are_removed(self):
        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            done = oplog.begin(state, oplog.KIND_COMMENT, repo=REPO, parent="p1", stages=STAGES)
            for sid, _ in STAGES:
                oplog.stage_set(state, done, sid, oplog.SENT)
            open_op = oplog.begin(state, oplog.KIND_COMMENT, repo=REPO, parent="p1", stages=STAGES)
            other = oplog.begin(state, oplog.KIND_COMMENT, repo=REPO, parent="p2", stages=[])
            self.assertEqual(oplog.retire_parent(state, "p1"), [done])
            self.assertIsNone(oplog.load(state, done))
            self.assertIsNotNone(oplog.load(state, open_op))
            self.assertIsNotNone(oplog.load(state, other))


class ConcurrentUpdateTest(unittest.TestCase):
    def test_parallel_updates_do_not_lose_each_other(self):
        import threading

        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            op_id = oplog.begin(state, oplog.KIND_DECIDE, repo=REPO, stages=STAGES)
            keys = [f"k{i}" for i in range(12)]
            threads = [
                threading.Thread(target=oplog.applied_set, args=(state, op_id, k, True))
                for k in keys
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sorted(oplog.load(state, op_id)["applied"]), sorted(keys))


class ReconcileTest(unittest.TestCase):
    """送信の照合の表。目印の一致、最後まで読めた0件、打ち切り、アカウント違い、複数、読めない。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = os.path.join(directory.name, "logs", "state")
        self.op = oplog.begin(self.state, oplog.KIND_DECIDE, repo=REPO, stages=STAGES)

    def record(self):
        return oplog.load(self.state, self.op)

    def test_one_match_is_sent(self):
        got = oplog.reconcile(self.record(), "issue", ["https://x/1"], True, "me")
        self.assertEqual(got[0], oplog.V_SENT)

    def test_many_matches_stop(self):
        got = oplog.reconcile(self.record(), "issue", ["a", "b"], True, "me")
        self.assertEqual(got[0], oplog.V_STOP)

    def test_zero_matches_on_a_fully_read_list_means_not_sent(self):
        got = oplog.reconcile(self.record(), "issue", [], True, "me")
        self.assertEqual(got[0], oplog.V_SEND)

    def test_a_truncated_list_cannot_confirm_zero(self):
        got = oplog.reconcile(self.record(), "issue", [], False, "me")
        self.assertEqual(got[0], oplog.V_UNKNOWN)
        self.assertIn("打ち切った", got[1])

    def test_an_unreadable_list_stops(self):
        self.assertEqual(oplog.reconcile(self.record(), "issue", [], None, "me")[0], oplog.V_STOP)

    def test_a_different_account_cannot_confirm_zero(self):
        oplog.stage_set(self.state, self.op, "issue", oplog.UNKNOWN, account="alice")
        got = oplog.reconcile(self.record(), "issue", [], True, "bob")
        self.assertEqual(got[0], oplog.V_UNKNOWN)

    def test_a_just_tried_stage_may_not_be_listed_yet(self):
        oplog.stage_set(self.state, self.op, "issue", oplog.UNKNOWN, account="me")
        record = self.record()
        got = oplog.reconcile(record, "issue", [], True, "me")
        self.assertEqual(got[0], oplog.V_UNKNOWN)
        later = time.time() + oplog.RECENT_SECONDS + 60
        self.assertEqual(
            oplog.reconcile(record, "issue", [], True, "me", now=later)[0], oplog.V_SEND
        )

    def test_an_unknown_stage_name_stops(self):
        self.assertEqual(oplog.reconcile(self.record(), "nope", [], True, "me")[0], oplog.V_STOP)


class DiscardTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = os.path.join(directory.name, "logs", "state")

    def begin(self, parent="i0001", phase=1):
        return oplog.begin(
            self.state, oplog.KIND_DECIDE, repo=REPO, parent=parent, phase=phase, stages=STAGES
        )

    def test_old_untouched_records_for_the_same_target_go_away(self):
        old, mine = self.begin(), self.begin()
        gone = oplog.discard_untouched(self.state, oplog.KIND_DECIDE, "i0001", 1, keep=mine)
        self.assertEqual(gone, [old])
        self.assertIsNone(oplog.load(self.state, old))
        self.assertIsNotNone(oplog.load(self.state, mine))

    def test_records_where_something_happened_stay(self):
        sent, applied, other = self.begin(), self.begin(), self.begin(phase=2)
        oplog.stage_set(self.state, sent, "reply", oplog.SENT)
        oplog.applied_set(self.state, applied, "local", True)
        mine = self.begin()
        gone = oplog.discard_untouched(self.state, oplog.KIND_DECIDE, "i0001", 1, keep=mine)
        self.assertEqual(gone, [])
        for kept in (sent, applied, other):
            self.assertIsNotNone(oplog.load(self.state, kept))


class PruneKeepsUnfinishedOpsTest(unittest.TestCase):
    def test_prune_never_deletes_the_ops_directory(self):
        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            op_id = oplog.begin(state, oplog.KIND_DECIDE, repo=REPO, stages=STAGES)
            path = oplog.record_path(state, op_id)
            old = time.time() - 400 * 86400
            os.utime(path, (old, old))
            report = prune.run(
                root,
                os.path.join(root, "logs", "decisions.jsonl"),
                state,
                "own-session",
                dry_run=False,
            )
            self.assertTrue(os.path.exists(path), report)

    def test_a_finished_record_is_not_counted_as_unfinished(self):
        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            op_id = oplog.begin(
                state, oplog.KIND_COMMENT, repo=REPO, stages=[("comment", "comment")]
            )
            self.assertEqual(prune.run(root, "", state, "s", dry_run=True).kept_ops, 1)
            oplog.stage_set(state, op_id, "comment", oplog.SENT, "u")
            self.assertEqual(prune.run(root, "", state, "s", dry_run=True).kept_ops, 0)
            self.assertTrue(os.path.exists(oplog.record_path(state, op_id)))


class BeginAddsStagesTest(unittest.TestCase):
    def test_begin_again_adds_only_the_missing_stages(self):
        with tempfile.TemporaryDirectory() as root:
            state = os.path.join(root, "logs", "state")
            fixed = oplog.id_for("close-early", "p", "0", "7", "k")
            for stages in ([("issue", "issue")], [("issue", "issue"), ("comment", "comment")]):
                oplog.begin(state, oplog.KIND_CLOSE_EARLY, repo={}, stages=stages, op_id=fixed)
            oplog.stage_set(state, fixed, "issue", oplog.SENT, "u")
            record = oplog.load(state, fixed)
            self.assertEqual([x["id"] for x in record["stages"]], ["issue", "comment"])
            self.assertEqual(record["stages"][0]["state"], "sent")


if __name__ == "__main__":
    unittest.main()
