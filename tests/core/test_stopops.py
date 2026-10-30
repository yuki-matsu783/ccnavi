"""approval-request の操作実績（records.stopops）の単体テスト。"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.hook import events
from ccnavi.infra import hookio, settings
from ccnavi.records import stopops


def _kv(state, name, **fields):
    directory = os.path.join(state, stopops.RESULT_DIR)
    os.makedirs(directory, exist_ok=True)
    files = fields.pop("files", [])
    lines = [f"{k}={v}" for k, v in fields.items()]
    lines += [f"file={sha} {path}" for sha, path in files]
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _result(ident="p1", route="sent", started=100, ended=101, **extra):
    base = dict(id=ident, route=route, rc="0", started=started, ended=ended)
    base.update(extra)
    return base


class IdentTest(unittest.TestCase):
    def test_reads_the_identifier(self):
        cases = {
            ("sh", ".ccnavi/scripts/ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("sh", "C:\\w\\ccnavi-TICKET.sh", "approval-request", "--", "p1"): "p1",
            ("sh", "ccnavi-ticket.sh", "approval-request", "--reason", "x", "p2"): "p2",
            ("sh", "ccnavi-ticket.sh", "start", "p1"): "",
            ("sh", "ccnavi-ticket.sh", "approval-request"): "",
            ("echo", "approval-request", "p1"): "",
            ("FOO=1", "sh", "ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("env", "FOO=1", "sh", "ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("timeout", "60", "sh", "ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("nohup", "sh", "-x", "ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("uv", "run", "sh", "ccnavi-ticket.sh", "approval-request", "p1"): "p1",
            ("echo", "-n", "ccnavi-ticket.sh", "approval-request", "p1"): "",
            ("grep", "ccnavi-ticket.sh", "approval-request", "p1"): "",
        }
        for words, want in cases.items():
            with self.subTest(words=words):
                self.assertEqual(stopops.propose_ident(list(words)), want)

    def test_a_pipe_around_propose_still_registers_the_call(self):
        # `| head` のように出力を切るパイプの左右にあるapproval-requestも、
        # コマンドの語として読んで登録する。
        # `sh -c '…'` は引数1本の文字列で、実装は中を読まないので登録しない。
        cases = {
            "sh ccnavi-ticket.sh approval-request p1 | head -1": True,
            "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request p1 2>&1 | tail -n 3": True,
            "head -1 | sh ccnavi-ticket.sh approval-request p1": True,
            "sh -c 'sh ccnavi-ticket.sh approval-request p1 | head -n 1'": False,
        }
        with tempfile.TemporaryDirectory() as state:
            conf = settings.Settings(state=state, stop_review_agent=True)
            for at, (command, registered) in enumerate(cases.items()):
                tool_use_id = f"tu{at}"
                payload = hookio.Input(
                    event=hookio.PRE_TOOL_USE,
                    session_id="s1",
                    tool_name="Bash",
                    tool_input={"command": command},
                    tool_use_id=tool_use_id,
                )
                with self.subTest(command=command):
                    events._track_proposals(conf, payload)
                    path = stopops._pre_path(state, tool_use_id, "p1")
                    self.assertEqual(os.path.exists(path), registered)


class BindTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name

    def test_bind_by_ident_and_time(self):
        _kv(
            self.state, "a.kv", **_result(commit="c1", files=[("sha1", "wip/proposals/todo/p1.md")])
        )
        _kv(self.state, "b.kv", **_result(ident="other"))
        stopops.register(self.state, "s1", "tu1", "p1", 100)
        op = stopops.bind(self.state, "tu1", 102)
        self.assertEqual((op["route"], op["commit"]), ("sent", "c1"))
        self.assertEqual(op["files"], [{"sha": "sha1", "path": "wip/proposals/todo/p1.md"}])
        self.assertIsNone(stopops.bind(self.state, "tu1", 102))  # 2 度目は結ばない

    def test_a_result_is_bound_to_one_call_only(self):
        _kv(self.state, "a.kv", **_result())
        stopops.register(self.state, "s1", "tu1", "p1", 100)
        stopops.register(self.state, "s1", "tu2", "p1", 100)
        self.assertIsNotNone(stopops.bind(self.state, "tu1", 102))
        self.assertIsNone(stopops.bind(self.state, "tu2", 102))

    def test_result_outside_the_window_is_not_bound(self):
        _kv(self.state, "a.kv", **_result(started=50, ended=51))
        stopops.register(self.state, "s1", "tu1", "p1", 100)
        self.assertIsNone(stopops.bind(self.state, "tu1", 102))

    def test_rebind_later_then_unlinked(self):
        stopops.register(self.state, "s1", "tu1", "p1", 100)
        self.assertIsNone(
            stopops.bind(self.state, "tu1", 102)
        )  # 結果がまだ無い（バックグラウンド）
        _kv(self.state, "a.kv", **_result(started=101, ended=140))
        stopops.rebind(self.state, "s1", 141)
        self.assertEqual(len(stopops.turn_ops(self.state, "s1", 0)), 1)

        stopops.register(self.state, "s1", "tu9", "p9", 200)
        stopops.rebind(self.state, "s1", 201)
        self.assertEqual(stopops.unlinked(self.state, "s1"), 0)
        stopops.rebind(self.state, "s1", 200 + stopops.UNLINK_AFTER_SECONDS + 1)
        self.assertEqual(stopops.unlinked(self.state, "s1"), 1)

    def test_unreadable_or_unknown_route_is_ignored(self):
        _kv(self.state, "a.kv", **_result(route="mystery"))
        directory = os.path.join(self.state, stopops.RESULT_DIR)
        with open(os.path.join(directory, "b.kv"), "w", encoding="utf-8") as handle:
            handle.write("garbage\n")
        stopops.register(self.state, "s1", "tu1", "p1", 100)
        self.assertIsNone(stopops.bind(self.state, "tu1", 102))

    def test_turn_start(self):
        self.assertEqual(stopops.turn_start(self.state, "s1"), 0.0)
        stopops.start_turn(self.state, "s1", 123.0)
        self.assertEqual(stopops.turn_start(self.state, "s1"), 123.0)

    def test_turn_ops_respect_session_and_turn(self):
        _kv(self.state, "a.kv", **_result(started=10, ended=11))
        _kv(self.state, "b.kv", **_result(started=200, ended=201))
        for tu, t0 in (("old", 10), ("new", 200)):
            stopops.register(self.state, "s1", tu, "p1", t0)
            stopops.bind(self.state, tu, t0 + 2)
        stopops.register(self.state, "s2", "x", "p1", 200)
        self.assertEqual(
            [o["tool_use_id"] for o in stopops.turn_ops(self.state, "s1", 150)], ["new"]
        )
        self.assertEqual(len(stopops.turn_ops(self.state, "s1", 0)), 2)


class FinalVersionTest(unittest.TestCase):
    def _op(self, route, ended, ident="p1", **extra):
        return dict(ident=ident, route=route, ended=ended, tool_use_id=f"t{ended}", **extra)

    def test_last_sent_wins(self):
        ops = [self._op("sent", 1, commit="c1"), self._op("sent", 2, commit="c2")]
        self.assertEqual(stopops.final_versions(ops)["p1"]["commit"], "c2")

    def test_failure_after_a_sent_version_keeps_it(self):
        ops = [self._op("sent", 1, commit="c1"), self._op("failed_before", 2)]
        self.assertEqual(stopops.final_versions(ops)["p1"]["commit"], "c1")

    def test_unchanged_does_not_move_the_version(self):
        ops = [self._op("sent", 1, commit="c1"), self._op("unchanged", 2)]
        self.assertEqual(stopops.final_versions(ops)["p1"]["route"], "sent")

    def test_only_failures_report_the_last_reason(self):
        ops = [self._op("failed_before", 1), self._op("stopped", 2)]
        self.assertEqual(stopops.final_versions(ops)["p1"]["route"], "stopped")

    def test_parent_and_children_are_separate(self):
        ops = [
            self._op("sent", 1, ident="p1", commit="c1"),
            self._op("stopped", 2, ident="p1-01-01"),
        ]
        self.assertEqual(sorted(stopops.final_versions(ops)), ["p1", "p1-01-01"])

    def test_target_key_names_the_version(self):
        self.assertEqual(
            stopops.target_key(self._op("sent", 1, commit="abcdef0123456789")),
            "proposal:p1:abcdef012345",
        )
        self.assertEqual(stopops.target_key(self._op("stopped", 1)), "proposal:p1:stopped")


class UnsentTest(unittest.TestCase):
    def test_written_ident(self):
        cases = {
            "/w/wip/proposals/todo/p1.md": "p1",
            "C:\\w\\WIP\\Proposals\\todo\\P1-01-02.md": "P1-01-02",
            "/w/wip/proposals/todo/sub/p1.md": "",
            "/w/wip/proposals/flows/p1.yml": "",
            "/w/wip/proposals/todo/p1.txt": "",
            "/w/wip/proposals/todo/.md": "",
            "/w/src/todo/p1.md": "",
        }
        for path, want in cases.items():
            with self.subTest(path=path):
                self.assertEqual(stopops.written_ident(path, "wip/proposals"), want)

    def _write(self, ident, t):
        return {"ident": ident, "t": t, "path": f"/w/wip/proposals/todo/{ident}.md"}

    def _sent(self, ended, *idents):
        files = [{"sha": "x", "path": f"wip/proposals/todo/{i}.md"} for i in idents]
        return {"route": "sent", "ended": ended, "files": files}

    def test_written_and_never_sent(self):
        self.assertEqual(
            stopops.unsent_writes([self._write("p1", 10)], []), [self._write("p1", 10)]
        )

    def test_sent_after_writing_covers_the_family(self):
        writes = [self._write("p1", 10), self._write("p1-01-01", 11)]
        ops = [self._sent(20, "p1", "p1-01-01")]
        self.assertEqual(stopops.unsent_writes(writes, ops), [])

    def test_edit_after_sending_is_unsent_again(self):
        self.assertEqual(
            [
                r["ident"]
                for r in stopops.unsent_writes([self._write("p1", 30)], [self._sent(20, "p1")])
            ],
            ["p1"],
        )

    def test_a_failed_propose_does_not_cover(self):
        failed = {"route": "failed_before", "ended": 20, "files": []}
        self.assertEqual(len(stopops.unsent_writes([self._write("p1", 10)], [failed])), 1)

    def test_notes_are_per_session_and_turn(self):
        with tempfile.TemporaryDirectory() as state:
            stopops.note_written(state, "s1", "p1", "/w/p1.md", 10)
            stopops.note_written(
                state, "s1", "P1", "/w/p1.md", 50
            )  # 同じ識別子（大文字小文字違い）
            stopops.note_written(state, "s2", "p2", "/w/p2.md", 50)
            self.assertEqual([r["t"] for r in stopops.turn_writes(state, "s1", 0)], [50])
            self.assertEqual(stopops.turn_writes(state, "s1", 60), [])


if __name__ == "__main__":
    unittest.main()
