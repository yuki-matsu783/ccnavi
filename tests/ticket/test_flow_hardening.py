"""子チケットのフローの、2 回目の敵対的なレビューで直したことの受入テスト。

見るのは次のとおり。

1. 名前付きパイプ（FIFO）を読まない。SubagentStart が固まらない
2. ハードリンクのフローを読まない。ハードリンクの別名への書き込みもロックで止める
3. フローは正とするツリーの版だけを読む。着手のときにハッシュを記録し、
   着手のあとに書き換わったら
   SubagentStart と SubagentStop が知らせる。案内は「書けない」と言わない
4. 親のツリーからの起動では手順を並べず、各子のフローのパスと「自分の担当だけ」を言う
5. 承認の前に提案のツリーへ保存したフローを、承認で承認済みチケットのツリーへ動かす
6. lint は承認済みの領域のファイルを「ワークツリーにしかない」と言わない（ユーザの決定）

ファイルの種類（FIFO・ハードリンク・ディレクトリ）を読まないこと、接頭辞の真似・置き場のパス・
大文字小文字のそろえ方は go の flow のテストが見る。
"""

from __future__ import annotations

import json
import os
import threading
import unittest

import yaml

from tests.ticket.test_flow import (
    CHILD,
    CODE_CHANGED,
    FENCE_OPEN,
    WORKFLOW,
    WORKFLOW_YAML,
    FlowHarness,
    fingerprint,
)
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import write
from tests.ticketsample import child_text, parent_text

HAS_FIFO = hasattr(os, "mkfifo")
CHANGED = "着手後にフローが書き換わった"
NOT_REGULAR = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"


class Watchdog:
    """FIFO を読みに行った側が固まっても、テストが止まらないようにする。

    `seconds` 経っても終わらなければ、書き手として FIFO を開いて閉じる。読み手は EOF を受けて戻る。
    """

    def __init__(self, fifo: str, seconds: float = 10.0):
        self.fifo = fifo
        self.seconds = seconds
        self.done = threading.Event()
        self.fired = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        if self.done.wait(self.seconds):
            return
        self.fired = True
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        self.thread.join()


class FlowReadPlaceTest(FlowHarness):
    """読むのは正とするツリーの版だけ（M-2a）。案内は「書けない」と言わない（M-2c）。"""

    def test_the_child_worktree_copy_is_not_read(self):
        os.remove(self.flow_path)
        self.commit_parent("no flow in the parent tree")
        child_tree = self.run_child(CHILD)
        # 子のワークツリーの版に、エージェントがシェルから書いた版。
        write(self.flow_in(child_tree), WORKFLOW_YAML)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn(CHILD, text)
        self.assertNotIn("フロー:", text)
        self.assertNotIn("[askUserQuestion]", text)
        shown = self.board_flow(CHILD)
        self.assertFalse(shown["exists"])
        self.assertEqual(os.path.realpath(shown["tree"]), os.path.realpath(self.parent_tree))

    def test_the_briefing_says_the_flow_is_human_owned_not_unwritable(self):
        child_tree = self.run_child(CHILD)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn("合意済みの手順。編集しない", text)
        self.assertNotIn("エージェントは書けない", text)


class FlowDigestTest(FlowHarness):
    """着手のときにハッシュを記録し、着手のあとに書き換わったら知らせる（M-2b）。止めない。"""

    def record(self):
        path = os.path.join(self.approved, "phases", "i0001", f"{CHILD}.flow.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def start_and_stop(self, child_tree):
        start = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        stop = self.hook("SubagentStop", "", child_tree, agent_id="sub-1")
        return start, stop

    def test_start_records_the_digest_of_the_flow(self):
        self.run_child(CHILD)
        record = self.record()
        self.assertTrue(record["fingerprint"].startswith("sha256:"), record)
        self.assertEqual(record["fingerprint"], fingerprint(self.flow_path))
        self.assertEqual(record["path"], f".ccnavi/approved/flows/{CHILD}.yml")
        self.assertTrue(record["at"])

    def test_an_unchanged_flow_says_nothing(self):
        child_tree = self.run_child(CHILD)
        start, stop = self.start_and_stop(child_tree)
        self.assertNotIn(CHANGED, start.stdout)
        self.assertNotIn("systemMessage", start.stdout)
        self.assertEqual(stop.stdout.strip(), "")

    def test_a_flow_rewritten_after_the_start_is_reported(self):
        """シェルから行き先を追えない形で書き換えた、を直に書いて真似る。止めずに知らせる。"""
        child_tree = self.run_child(CHILD)
        write(self.flow_path, "nodes:\n  - {id: s, type: prompt, name: x}\n")
        start, stop = self.start_and_stop(child_tree)
        for result in (start, stop):
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
            self.assertIn(CHANGED, out["hookSpecificOutput"]["additionalContext"])
            self.assertIn(CHANGED, out["systemMessage"])
            self.assertIn(CODE_CHANGED, out["systemMessage"])
            self.assertIn(CHILD, out["systemMessage"])

    def test_a_removed_or_replaced_flow_is_reported(self):
        child_tree = self.run_child(CHILD)
        os.remove(self.flow_path)
        start, _ = self.start_and_stop(child_tree)
        self.assertIn(CHANGED, json.loads(start.stdout)["systemMessage"])
        self.assertIn("いま 無い", json.loads(start.stdout)["systemMessage"])

    def test_a_flow_that_appears_after_the_start_is_reported(self):
        os.remove(self.flow_path)
        self.commit_parent("no flow")
        child_tree = self.run_child(CHILD)
        self.assertEqual(self.record()["fingerprint"], "absent")
        write(self.flow_path, WORKFLOW_YAML)
        start, _ = self.start_and_stop(child_tree)
        self.assertIn(CHANGED, json.loads(start.stdout)["systemMessage"])

    def test_the_notice_rides_with_a_bounce_on_stop(self):
        """範囲外の変更で差し戻すときも、同じ文に知らせを載せる。"""
        child_tree = self.run_child(CHILD)
        write(os.path.join(child_tree, "src", "stray.py"), "x\n")
        write(self.flow_path, "{}")
        stop = self.hook("SubagentStop", "", child_tree, agent_id="sub-1")
        self.assertEqual(stop.returncode, 2, stop.stdout + stop.stderr)
        self.assertIn(CHANGED, stop.stderr)

    @unittest.skipUnless(HAS_FIFO, "mkfifo が無い")
    def test_a_fifo_swapped_in_after_the_start_does_not_hang_subagent_start(self):
        """着手のあとにフローを名前付きパイプに差し替えても、SubagentStart は固まらない。"""
        child_tree = self.run_child(CHILD)
        os.remove(self.flow_path)
        os.mkfifo(self.flow_path)
        with Watchdog(self.flow_path) as dog:
            result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        self.assertFalse(dog.fired, "FIFO を開いて固まった")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn(NOT_REGULAR, out["hookSpecificOutput"]["additionalContext"])
        self.assertIn(CHANGED, out["systemMessage"])


class FlowHardLinkLockTest(FlowHarness):
    """ハードリンクの別名への書き込みもロックが止める。"""

    def test_a_hard_link_alias_is_locked_while_in_progress(self):
        alias = os.path.join(self.parent_tree, "wip", "notes.yml")
        os.makedirs(os.path.dirname(alias), exist_ok=True)
        os.link(self.flow_path, alias)
        # 着手の前は止めない（ロックは着手中だけ）。
        self.assert_not_locked(self.write_to(alias))
        child_tree = self.run_child(CHILD)
        self.assert_locked(self.write_to(alias))
        self.assert_locked(self.write_to(alias, tool="Edit"))
        # 読まずに、そう言う。
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn("ハードリンク", text)
        self.assertNotIn(FENCE_OPEN, text)
        # 名前が 1 つのふつうのファイルは、ロックの走査もしない。
        plain = write(os.path.join(self.parent_tree, "wip", "plain.yml"), "{}")
        self.assert_not_locked(self.write_to(plain))


class FlowParentBriefingTest(PhaseHarness):
    """親のツリーからの起動では、手順を並べずパスだけを言う。"""

    def test_parent_cwd_lists_paths_only_and_the_child_cwd_gets_the_steps(self):
        self.propose("i0001", parent_text("i0001", ["research"], allow=("src/*", "wip/*")))
        kids = ["i0001-01-01", "i0001-01-02"]
        for i, kid in enumerate(kids, 1):
            self.propose(kid, child_text(kid, "i0001", 1, (f"wip/research/r{i}/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        paths = [os.path.join(self.approved, "flows", f"{k}.yml") for k in kids]
        for path in paths:
            write(path, WORKFLOW_YAML)
        self.commit_parent("flows")
        self.start_parent()
        text = self.reason(self.hook("SubagentStart", "", self.parent_tree, agent_id="sub-1"))
        self.assertNotIn(FENCE_OPEN, text)
        self.assertNotIn("[askUserQuestion]", text)
        for path in paths:
            self.assertIn(f"フロー: {path}".casefold(), text.casefold())
        self.assertIn(
            "自分の担当の子チケットのフローだけを読んで従ってください。他の子のフローには従わない",
            text,
        )
        child_tree = self.run_child("i0001-01-01")
        own = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-2"))
        self.assertIn(FENCE_OPEN, own)
        self.assertIn("3. [askUserQuestion] 方針", own)
        self.assertNotIn("i0001-01-02", own)


class FlowStaysOnApprovalTest(PhaseHarness):
    """子の提案は親のワークツリーに書く。フローも同じツリーに保存し、承認で動かさない。"""

    def setUp(self):
        super().setUp()
        allow = ("src/*", "wip/*", "tests/*")
        self.propose("i0001", parent_text("i0001", ["research"], allow=allow))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.propose(CHILD, child_text(CHILD, "i0001", 1, ("wip/research/*",)))

    def board(self):
        result = self.ccnavi("--explain", "--json")
        return {t["ticket"]: t for t in json.loads(result.stdout)["tickets"]}[CHILD]["flow"]

    def test_the_flow_stays_in_the_parent_tree_and_is_read_by_the_child(self):
        before = self.board()
        self.assertEqual(os.path.realpath(before["tree"]), os.path.realpath(self.parent_tree))
        write(before["path"], WORKFLOW_YAML)
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("フロー", result.stdout)
        after = self.board()
        self.assertEqual(os.path.realpath(after["tree"]), os.path.realpath(self.parent_tree))
        self.assertEqual(after["path"], before["path"])
        self.assertTrue(after["exists"])
        with open(after["path"], encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f)["name"], WORKFLOW["name"])
        child_tree = self.run_child(CHILD)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn("3. [askUserQuestion] 方針", text)

    def test_no_flow_means_nothing_is_said(self):
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("フロー", result.stdout)


class FlowLintTest(FlowHarness):
    """承認済みの領域のファイルは「ワークツリーにしかない」と言わない（ユーザの決定）。"""

    def test_approved_files_in_a_worktree_are_not_warned_but_other_layer_files_are(self):
        extra = write(os.path.join(self.parent_tree, ".ccnavi", "notes", "memo.txt"), "x\n")
        self.assertTrue(os.path.exists(self.flow_path))
        lint = self.ccnavi("--lint")
        lines = [x for x in lint.stdout.splitlines() if "ワークツリーにしかない" in x]
        self.assertFalse([x for x in lines if "/approved/" in x.replace("\\", "/")], lines)
        self.assertTrue([x for x in lines if "notes/memo.txt" in x], lint.stdout)
        os.remove(extra)


if __name__ == "__main__":
    unittest.main()
