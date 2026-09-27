"""先行（`predecessors`）を承認と着手で求める（ADR-0088）の受入テスト。道具を外から呼んで応答だけを見る。

見るのは 6 つ。

1. 承認は、先行が全部 `done/` に在って取り消しでないときだけ。待てば通るもの（承認待ち・作業中・
   レビュー待ち）と、待っても通らないもの（取り消し・どこにも無い・複数の場所）を文面で分ける
2. ボードの承認のプレビューと `--verify` に、落ちた理由が出る。`--lint` は待てば通るものを warn、
   待っても通らないものを error で言う
3. 着手（`ticket start`）も同じ検査で止まる。置き場を手で動かして承認した子と、承認のあとで先行が
   戻された子
4. ボードの JSON のチケットに、満たしていない先行（`predecessors_unmet`）が載る
5. 続きの子（`decide` の「このフェーズで直す」）は、取り消した子を先行に入れない
6. エージェントは承認済みチケットの `predecessors` を書き換えて迂回できない

道具は並行するチケットの受入テスト（test_ticket.TicketTest）のものを借りる。借りるだけで、
あちらのテストはここでは走らせない（`load_tests`）。
"""

from __future__ import annotations

import json
import os
import shutil
import unittest

from ccnavi import modes, settings
from tests.ticket.test_ticket import TicketTest, git, read_json, write


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りた TicketTest のテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(PredecessorTest)):
        if name.startswith("test_"):
            suite.addTest(PredecessorTest(name))
    return suite


class PredecessorTest(TicketTest):
    def propose_after(self, name, *predecessors, phase=1, allow=("src/c/*",)):
        self.propose(name, parent="i0001", phase=phase, allow=allow, predecessors=predecessors)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "propose " + name)

    def placed(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def finish(self, name):
        done = self.ccnavi("ticket", "finish", name)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "finish " + name)

    # ---- 1. 承認

    def test_approval_waits_until_the_predecessor_is_done(self):
        """作業中の先行 → 承認しない。done/ に入れば同じ提案のまま通る。"""
        self.family(review=(False, False))
        self.propose_after("i0001-03", "i0001-01")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("i0001-03 は承認の対象にしない", refused.stderr)
        self.assertIn("先行 i0001-01 が閉じていない（いまは 作業中（doing/））", refused.stderr)
        self.assertIn("predecessors から外して", refused.stderr)
        self.assertFalse(self.placed("i0001-03"))

        self.finish("i0001-01")  # レビュー不要のフェーズ。done/ へ動く
        passed = self.approve()
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertTrue(self.placed("i0001-03"))

    def test_a_predecessor_waiting_for_review_is_not_done(self):
        """レビュー待ち（review/）は作業としては終わっているが、done/ ではないので満たさない。"""
        self.family(review=(True, False))
        self.finish("i0001-01")
        self.propose_after("i0001-03", "i0001-01")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("いまは レビュー待ち（review/）", refused.stderr)

    def test_a_predecessor_approved_in_the_same_batch_is_not_done(self):
        self.family()
        self.propose("i0001-03", parent="i0001", phase=1, allow=("src/c/*",))
        self.propose_after("i0001-04", "i0001-03")
        refused = self.approve()
        self.assertIn("先行 i0001-03 が閉じていない（いまは 承認待ち（todo/））", refused.stderr)
        self.assertTrue(self.placed("i0001-03"))
        self.assertFalse(self.placed("i0001-04"))

    def test_a_cancelled_predecessor_never_satisfies(self):
        self.family()
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01", "--reason", "やめた")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        self.propose_after("i0001-03", "i0001-01")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("先行 i0001-01 は取り消し済み", refused.stderr)
        self.assertIn("満たせない", refused.stderr)
        self.assertFalse(self.placed("i0001-03"))

    def test_a_missing_predecessor_never_satisfies(self):
        self.family()
        self.propose_after("i0001-03", "i0001-99")
        refused = self.approve()
        self.assertIn("先行 i0001-99 がどの置き場", refused.stderr)
        self.assertIn("綴りを直すか", refused.stderr)
        self.assertFalse(self.placed("i0001-03"))

    def test_a_predecessor_in_two_places_is_not_taken_as_done(self):
        """同じ識別子が doing/ と done/ の両方に在る（動かす途中で止まった跡）なら満たさない。"""
        self.family(review=(False, False))
        self.finish("i0001-01")
        shutil.copyfile(
            os.path.join(self.approved, "done", "i0001-01.md"),
            os.path.join(self.approved, "doing", "i0001-01.md"),
        )
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "stray")
        self.propose_after("i0001-03", "i0001-01")
        refused = self.approve()
        self.assertIn("先行 i0001-01 が複数の場所にある", refused.stderr)
        self.assertIn("1 つに決める", refused.stderr)
        self.assertFalse(self.placed("i0001-03"))

    def test_self_parent_and_loops_are_errors_that_name_the_cause(self):
        """自分自身・自分の親・輪は待っても満たさない。error で原因を言う。"""
        self.family(review=(False, False))
        self.propose_after("i0001-03", "i0001-03")
        self.propose_after("i0001-04", "i0001")
        self.propose_after("i0001-05", "i0001-06")
        self.propose_after("i0001-06", "i0001-05")
        refused = self.approve()
        self.assertIn("先行 i0001-03 は自分自身", refused.stderr)
        self.assertIn("先行 i0001 は自分の親", refused.stderr)
        self.assertIn("先行 i0001-06 は先行を辿ると自分に戻る", refused.stderr)
        self.assertIn("i0001-05 → i0001-06 → i0001-05", refused.stderr)
        self.assertIn("i0001-06 → i0001-05 → i0001-06", refused.stderr)
        for name in ("i0001-03", "i0001-04", "i0001-05", "i0001-06"):
            self.assertFalse(self.placed(name))
        found = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        for fragment in ("自分自身", "自分の親", "自分に戻る"):
            hits = [p["severity"] for p in found if fragment in p["detail"]]
            self.assertTrue(hits, fragment)
            self.assertEqual(set(hits), {"error"}, fragment)
        # 閉じた先行の先は辿らない（満たしているので輪は切れている）。
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        entry = next(t for t in board["tickets"] if t["ticket"] == "i0001-05")
        self.assertEqual([p["state"] for p in entry["predecessors_unmet"]], ["cycle"])

    def test_start_names_a_self_predecessor(self):
        self.family(review=(False, False))
        self.propose(
            "i0001-03", parent="i0001", phase=1, allow=("src/c/*",), predecessors=("i0001-03",)
        )
        self.hand_move("i0001-03")
        self.worktree("i0001-03", "i0001")
        refused = self.ccnavi("ticket", "start", "i0001-03")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("- 先行 i0001-03: 自分自身", refused.stderr)
        self.assertIn("待っても満たせない", refused.stderr)

    # ---- 2. プレビュー・確かめ・lint

    def test_the_board_preview_and_verify_show_why(self):
        self.family()
        self.propose_after("i0001-03", "i0001-01")
        shown = self.ccnavi("--approve", "--preview", "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        body = json.loads(shown.stdout)
        self.assertEqual(body["batch"], [])
        rejected = {r["ticket"]: r["problems"] for r in body["rejected"]}
        self.assertTrue(any("先行 i0001-01 が閉じていない" in p for p in rejected["i0001-03"]))
        verified = self.ccnavi("--approve", "--preview", "--verify")
        self.assertEqual(verified.returncode, modes.EXIT_ANSWER_NO, verified.stdout)
        self.assertIn("落ちる", verified.stdout)
        self.assertIn("先行 i0001-01", verified.stdout)

    def test_lint_warns_on_waiting_and_errs_on_what_waiting_cannot_fix(self):
        self.family()
        self.propose_after("i0001-03", "i0001-01")
        self.propose_after("i0001-04", "i0001-99")
        linted = self.ccnavi("--lint", "--json")
        found = json.loads(linted.stdout)["problems"]

        def severity(fragment):
            return [p["severity"] for p in found if fragment in p["detail"]]

        self.assertEqual(severity("i0001-03: 先行 i0001-01"), ["warn"])
        self.assertEqual(severity("i0001-04: 先行 i0001-99"), ["error"])

    # ---- 3. 着手

    def test_start_refuses_a_hand_moved_child_whose_predecessor_is_open(self):
        """置き場を手で動かして承認した子（ADR-0058）は承認の検査を通らない。着手が同じ検査で止める。"""
        self.family(review=(False, False))
        self.propose(
            "i0001-03", parent="i0001", phase=1, allow=("src/c/*",), predecessors=("i0001-01",)
        )
        self.hand_move("i0001-03")
        self.worktree("i0001-03", "i0001")
        refused = self.ccnavi("ticket", "start", "i0001-03")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("i0001-03 の先行が満たされていないので着手しない", refused.stderr)
        self.assertIn("- 先行 i0001-01: 作業中（doing/）", refused.stderr)
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        self.assertIn(f"'{ticket_sh} finish <先行>'", refused.stderr)
        self.assertIn(f"'{ticket_sh} cancel i0001-03 --reason <理由>'", refused.stderr)
        with open(os.path.join(self.approved, "doing", "i0001-03.md"), encoding="utf-8") as f:
            self.assertIn('started_at: ""', f.read())

        self.finish("i0001-01")
        started = self.ccnavi("ticket", "start", "i0001-03")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

    def test_start_refuses_when_the_predecessor_was_reopened_after_approval(self):
        """承認のときは done/ だった先行を人が doing/ へ戻した（再開）。着手はもう一度見る。"""
        self.family(review=(False, False))
        self.finish("i0001-01")
        self.propose_after("i0001-03", "i0001-01")
        self.assertEqual(self.approve().returncode, 0)
        os.replace(
            os.path.join(self.approved, "done", "i0001-01.md"),
            os.path.join(self.approved, "doing", "i0001-01.md"),
        )
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "reopen")
        self.worktree("i0001-03", "i0001")
        refused = self.ccnavi("ticket", "start", "i0001-03")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("先行 i0001-01", refused.stderr)

    def test_start_refuses_a_cancelled_or_missing_predecessor_and_says_it_cannot_be_met(self):
        self.family()
        self.assertEqual(self.ccnavi("ticket", "cancel", "i0001-01", "--reason", "x").returncode, 0)
        self.propose(
            "i0001-03",
            parent="i0001",
            phase=1,
            allow=("src/c/*",),
            predecessors=("i0001-01", "i0001-99"),
        )
        self.hand_move("i0001-03")
        self.worktree("i0001-03", "i0001")
        refused = self.ccnavi("ticket", "start", "i0001-03")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("- 先行 i0001-01: 取り消し済み", refused.stderr)
        self.assertIn("- 先行 i0001-99: どの置き場にも無い", refused.stderr)
        self.assertIn("満たせない", refused.stderr)

    # ---- 4. ボード

    def test_the_board_names_the_unmet_predecessors(self):
        self.family(review=(False, False))
        self.propose_after("i0001-03", "i0001-01", "i0001-02")
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        entry = next(t for t in board["tickets"] if t["ticket"] == "i0001-03")
        self.assertEqual(
            [(p["ticket"], p["state"]) for p in entry["predecessors_unmet"]],
            [("i0001-01", "doing"), ("i0001-02", "doing")],
        )
        self.assertEqual(entry["predecessors_unmet"][0]["label"], "作業中（doing/）")
        self.finish("i0001-01")
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        entry = next(t for t in board["tickets"] if t["ticket"] == "i0001-03")
        self.assertEqual([p["ticket"] for p in entry["predecessors_unmet"]], ["i0001-02"])
        # 先行を持たないチケットは空。
        other = next(t for t in board["tickets"] if t["ticket"] == "i0001-02")
        self.assertEqual(other["predecessors_unmet"], [])

    # ---- 5. 続きの子

    def test_a_followup_does_not_wait_for_a_cancelled_child(self):
        """フェーズの子の 1 本を取り消していても、続きの子はその子を先行に持たず、着手できる。"""
        self.family()
        self.assertEqual(self.ccnavi("ticket", "cancel", "i0001-02", "--reason", "x").returncode, 0)
        self.finish("i0001-01")
        fixture = self.remote(merge=("i0001-01",))
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        data["threads"] = [
            {"id": "t1", "resolved": False, "url": "u1", "path": "src/a/x.py", "body": "ここ"}
        ]
        write(fixture, json.dumps(data))
        chosen = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="f\n",
        )
        self.assertEqual(chosen.returncode, 0, chosen.stdout + chosen.stderr)
        with open(os.path.join(self.approved, "doing", "i0001-03.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("predecessors:\n- i0001-01\n", text)
        self.assertNotIn("- i0001-02\n", text.split("human_review")[0])
        self.worktree("i0001-03", "i0001")
        started = self.ccnavi("ticket", "start", "i0001-03")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

    # ---- 6. 迂回できない

    def test_an_agent_cannot_drop_the_predecessor_from_the_approved_ticket(self):
        """止められたエージェントが、承認済みチケットの predecessors を消して通る道は無い。"""
        self.family(review=(False, False))
        self.propose(
            "i0001-03", parent="i0001", phase=1, allow=("src/c/*",), predecessors=("i0001-01",)
        )
        self.hand_move("i0001-03")
        copy = os.path.join(self.approved, "doing", "i0001-03.md")
        for tool, extra in (
            ("Edit", {"file_path": copy, "old_string": "predecessors", "new_string": "x"}),
            ("Write", {"file_path": copy, "content": "---\n---\n"}),
        ):
            result = self.hook("PreToolUse", tool, self.parent_tree, **extra)
            decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
            self.assertEqual(decision, "deny", (tool, result.stdout))

        # シェルから書く形。組み込みの守り（コアファイル）を本番と同じく効かせて打つ
        # （このテストの道具は既定で切っている）。
        def bash(command, permission_mode=""):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": self.parent_tree,
                "session_id": "s1",
                "tool_input": {"command": command},
            }
            if permission_mode:
                payload["permission_mode"] = permission_mode
            result = self.ccnavi(
                "--mode", "enable", "--guard-core-files", "enable", stdin=json.dumps(payload)
            )
            if not result.stdout.strip():
                return ""
            return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]

        # リダイレクトは ccnavi ディレクトリの守りが止める。
        self.assertEqual(bash("echo x > .ccnavi/approved/doing/i0001-03.md"), "deny")
        # 書き込み先を読めないコマンド（sed -i）は黙っては通らない。聞ける者が居る権限モードでは
        # Claude Code が利用者に聞き（ccnavi は判定を出さない）、居なければ ccnavi が断る
        # （judge.undeclared_verdict）。書かれても実行後の監視が書き換えとして言う（ADR-0075）。
        sed = "sed -i 's/predecessors.*//' .ccnavi/approved/doing/i0001-03.md"
        self.assertIn(bash(sed), ("", "ask", "deny"))
        self.assertEqual(bash(sed, "bypassPermissions"), "deny")
        # 承認を自分で出す道（端末の外からの --approve / --yes）も止まる。
        approve = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            guard_ticket_approval="enable",
            command="ccnavi --approve --yes i0001-03",
        )
        self.assertIn("DENY_TICKET_APPROVAL_CLI", self.reason(approve))
        # サブエージェントは着手そのものを打てない。
        sub = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            agent_id="sub-1",
            command="sh .ccnavi/scripts/ccnavi-ticket.sh start i0001-03",
        )
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(sub))


if __name__ == "__main__":
    unittest.main()
