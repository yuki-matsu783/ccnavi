"""write-and-push の受入テスト（提案・片付け・子のワークツリー・状態なし・マージリクエストなし）。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import unittest

from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    PARENT,
    SHELL,
    WriteAndPushHarness,
    executable,
    git,
    write,
)
from tests.ticketsample import parent_text


class ProposeTest(WriteAndPushHarness):
    """`ccnavi-ticket.sh propose`: 承認待ちの提案と子のフローの下書きを親のブランチへ送る。"""

    TODO = "wip/proposals/todo"
    FLOWS = "wip/proposals/flows"

    def put(self, rel, text="x\n"):
        write(os.path.join(self.tree, *rel.split("/")), text)
        return rel

    def test_new_proposals_are_committed_and_pushed(self):
        todo = self.put(f"{self.TODO}/{PARENT}.md")
        child = self.put(f"{self.TODO}/{CHILD}.md")
        flow = self.put(f"{self.FLOWS}/{CHILD}.yml")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertNotEqual(head, before)
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(self.committed(), sorted([todo, child, flow]))
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} の提案を送る"])
        self.assertEqual(self.dirty(), "")
        self.assertIn(f"提案: {todo}", result.stdout)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if line.startswith("次: ")],
            ["次: ユーザに承認を頼んで返事を待つ。承認が済むまで実装はしない"],
        )
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_a_parent_never_pushed_stops_with_the_options_and_writes_nothing(self):
        """ccnavi-start.sh を通さずに手で切った親。内部のエラーでなく、事実と次の手で止まる。"""
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0002")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0002", "main")
        write(
            os.path.join(tree, *f"{self.TODO}/i0002.md".split("/")),
            parent_text("i0002", ["design"], allow=("src/*", "wip/*")),
        )
        before = self.sha(tree, "HEAD")
        result = self.sh("ccnavi-ticket.sh", "propose", "i0002", cwd=tree, **self.host_env({}))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        err = result.stderr
        self.assertIn("親のブランチ i0002 はリモートにありません", err)
        self.assertIn("何も反映していません", err)
        self.assertEqual([x for x in err.splitlines() if x.startswith("次の手: ")].__len__(), 1)
        self.assertIn("その MR のブランチで作業するかを聞く", err)
        self.assertIn("ccnavi-start.sh' --issue <番号> で新しく切る", err)
        self.assertIn("git push -u origin i0002", err)
        self.assertIn("直接実装には切り替えない", err)
        self.assertNotIn("bad revision", err)
        self.assertNotIn("差分を読めない", err)
        self.assertEqual(self.sha(tree, "HEAD"), before)
        self.assertEqual(self.remote_sha("i0002"), "")
        self.assertFalse(os.path.exists(os.path.join(self.state, "locks", "self", "i0002")))

    def test_start_on_a_parent_never_pushed_stops_with_the_options(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0003")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0003", "main")
        write(
            os.path.join(tree, *f"{self.TODO}/i0003.md".split("/")),
            parent_text("i0003", ["design"], allow=("src/*", "wip/*")),
        )
        before = self.sha(tree, "HEAD")
        result = self.sh("ccnavi-ticket.sh", "start", "i0003", cwd=tree, **self.host_env({}))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("親のブランチ i0003 はリモートにありません", result.stderr)
        self.assertIn("次の手: ユーザに状況を伝えて", result.stderr)
        self.assertNotIn("bad revision", result.stderr)
        self.assertEqual(self.sha(tree, "HEAD"), before)

    def test_other_operations_on_a_parent_never_pushed_say_only_the_fact(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "i0004")
        git(self.ws, "worktree", "add", "-q", tree, "-b", "i0004", "main")
        write(
            os.path.join(tree, *f"{self.TODO}/i0004.md".split("/")),
            parent_text("i0004", ["design"], allow=("src/*", "wip/*")),
        )
        result = self.sh("ccnavi-ticket.sh", "finish", "i0004", cwd=tree, **self.host_env({}))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("親のブランチ i0004 はリモートにありません", result.stderr)
        self.assertNotIn("次の手: ユーザに状況を伝えて", result.stderr)

    def test_other_families_and_template_drafts_stay_out(self):
        mine = self.put(f"{self.TODO}/{PARENT}.md")
        other = self.put(f"{self.TODO}/i0002.md")
        sibling = self.put(f"{self.TODO}/{PARENT}x.md")
        draft = self.put("wip/handoff/.ccnavi/config/common/templates/login.yml")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [mine])
        status = self.dirty()
        for rel in (other, sibling, draft):
            self.assertIn(f"?? {rel}", status)

    def feedback_parent(self, feedback):
        """計画が 2 ステップの親の提案を書く。フィードバック計画の番号は 3 から始まる。"""
        text = parent_text(PARENT, ["design", "implement"], feedback, allow=("src/*", "wip/*"))
        return self.put(f"{self.TODO}/{PARENT}.md", text)

    def test_a_feedback_step_pointing_into_the_plan_is_refused_and_nothing_is_sent(self):
        """承認で「フィードバック計画の番号だけを指せる」と落ちる提案は、送る時点で断る。"""
        self.feedback_parent([{"type": "implement-feedback", "after": [1]}])
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("`feedback[0]` の `after` の 1 は読めない", result.stderr)
        self.assertIn("フィードバック計画の番号だけを指せる", result.stderr)
        self.assertEqual(
            [x for x in result.stderr.splitlines() if x.startswith("次: ")],
            [f"次: 直して `ccnavi-ticket.sh propose {PARENT}` を打ち直す"],
        )
        self.assertNotIn("提案:", result.stdout)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.remote_sha(), before)

    def test_a_feedback_plan_with_two_ends_is_refused(self):
        self.feedback_parent(
            [
                {"type": "implement-feedback", "after": []},
                {"type": "implement-feedback", "after": []},
            ]
        )
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("終端は 1 つにしてください", result.stderr)
        self.assertNotIn("提案:", result.stdout)

    def test_a_correct_feedback_plan_is_sent(self):
        rel = self.feedback_parent([{"type": "implement-feedback", "after": []}])
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [rel])

    def test_a_plan_with_a_misplaced_after_is_refused_even_without_feedback(self):
        text = parent_text(
            PARENT, ["design", {"type": "implement", "after": [5]}], allow=("src/*", "wip/*")
        )
        self.put(f"{self.TODO}/{PARENT}.md", text)
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("`plan[1]` の `after` の 5 は読めない", result.stderr)

    def test_children_and_unplanned_proposals_are_not_checked(self):
        """子の提案・計画の無い提案・読めない提案は検査せず、今までどおり送る。"""
        bad = parent_text(
            PARENT, ["design", "implement"], [{"type": "x", "after": [1]}], allow=("src/*",)
        )
        child = self.put(f"{self.TODO}/{CHILD}.md", bad)
        flow = self.put(f"{self.FLOWS}/{CHILD}.yml", "after: [1]\n")
        free = self.put(f"{self.TODO}/{PARENT}.md", "plan も feedback も無い\n")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), sorted([child, flow, free]))

    def test_a_child_id_sends_the_whole_family(self):
        todo = self.put(f"{self.TODO}/{PARENT}.md")
        result = self.ticket("propose", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [todo])

    def test_an_edited_tracked_proposal_is_sent(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put(rel, "v2\n")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [rel])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_an_unchanged_proposal_sends_only_the_unpushed_commit(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", "propose by hand")
        before = self.sha(self.tree, "HEAD")
        self.assertNotEqual(self.remote_sha(), before)
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.remote_sha(), before)

    def results(self):
        """propose の結果ファイル（Stop の AI 判定の操作実績）を、書かれた順に読む。"""
        directory = os.path.join(self.ws, "logs", "state", "stopops-result")
        found = []
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            if not name.endswith(".kv"):
                continue
            text = self.read(os.path.join(directory, name))
            row = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            row["text"] = text
            found.append(row)
        return found

    def test_the_result_file_records_the_route_sent(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        (result,) = self.results()
        self.assertEqual((result["id"], result["route"], result["rc"]), (PARENT, "sent", "0"))
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(result["commit"], head)
        self.assertIn(f"file={self.sha(self.tree, f'HEAD:{rel}')} {rel}", result["text"])

    def test_the_result_file_records_unchanged_unpushed_code_and_failure(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", "propose by hand")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put("src/secret.py", "x = 1\n")
        git(self.tree, "add", "--", "src/secret.py")
        git(self.tree, "commit", "-q", "-m", "wip code")
        self.put(f"{self.TODO}/{PARENT}.md", "v2\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 1)
        routes = [r["route"] for r in self.results()]
        self.assertEqual(routes[-1], "unpushed_code")
        self.assertIn(routes[0], ("sent", "unchanged"))

    def test_a_rejected_push_leaves_a_failed_before_result(self):
        self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.reject_pushes()
        self.assertNotEqual(self.ticket("propose", PARENT).returncode, 0)
        (result,) = self.results()
        self.assertEqual(result["route"], "failed_before")
        self.assertNotEqual(result["rc"], "0")

    def test_the_result_file_records_unchanged_exactly(self):
        self.put(f"{self.TODO}/{PARENT}.md")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        sent = self.sha(self.tree, "HEAD")
        again = self.ticket("propose", PARENT)  # 変更も未pushのコミットも無い状態での打ち直し
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), sent)
        # 結果ファイルの名前は開始秒とpidで決まるため、同じ秒の2つは順番が決まらない。
        # 経路で見分ける。
        by_route = {r["route"]: r for r in self.results()}
        self.assertEqual(sorted(by_route), ["sent", "unchanged"])
        unchanged = by_route["unchanged"]
        self.assertEqual((unchanged["id"], unchanged["rc"]), (PARENT, "0"))
        self.assertTrue(unchanged["started"].isdigit() and unchanged["ended"].isdigit())
        self.assertNotIn("commit", unchanged)  # 送ったものが無いので版は書かない
        self.assertNotIn("file", unchanged)

    def test_the_result_file_records_write_only_without_origin(self):
        git(self.ws, "remote", "remove", "origin")
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        head = self.sha(self.tree, "HEAD")
        remote = git(self.remote, "rev-parse", f"refs/heads/{PARENT}").stdout  # originを外した後
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)  # 送らず、コミットもしない
        self.assertEqual(git(self.remote, "rev-parse", f"refs/heads/{PARENT}").stdout, remote)
        self.assertIn(f"?? {rel}", self.dirty())
        (row,) = self.results()
        self.assertEqual((row["id"], row["route"], row["rc"]), (PARENT, "write_only", "0"))
        self.assertTrue(row["started"].isdigit() and row["ended"].isdigit())
        self.assertNotIn("commit", row)

    def test_the_result_file_records_stopped_without_a_parent_worktree(self):
        # 親のワークツリーが無い親子のチケットは、shが何も書かずにtarget stopで止める。
        result = self.ticket("propose", "i0099")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("書かずに止めた", result.stderr)
        (row,) = self.results()
        self.assertEqual((row["id"], row["route"], row["rc"]), ("i0099", "stopped", "1"))
        self.assertNotIn("commit", row)

    def test_no_proposal_and_a_user_decision_are_failed_before_not_stopped(self):
        # 送る提案が無い場合や、未送信のユーザの判断がある場合は、
        # stoppedではなく送る前の失敗になる。
        self.assertEqual(self.ticket("propose", PARENT).returncode, 1)
        self.put(f"{self.TODO}/{PARENT}.md")
        write(os.path.join(self.tree, *APPROVED.split("/"), "done", "zzz.md"), "x\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 1)
        rows = self.results()
        self.assertEqual([r["route"] for r in rows], ["failed_before"] * 2)
        self.assertEqual([r["rc"] for r in rows], ["1", "1"])

    def piped_propose(self):
        """`sh -c '<ccnavi-ticket.sh> propose <親> | head -n 1'`。出力を先頭の1行で切る。"""
        script = shlex.quote(os.path.join(self.scripts, "ccnavi-ticket.sh"))
        return subprocess.run(
            [SHELL, "-c", f"{shlex.quote(SHELL)} {script} propose {PARENT} | head -n 1"],
            cwd=self.ws,
            env=self.ticket_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_a_propose_with_its_output_cut_writes_exactly_one_result_for_the_pushed_commit(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md")
        done = self.piped_propose()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"提案: {rel}\n")
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)  # 出力を切られても、送るところまでは済む
        (row,) = self.results()
        self.assertEqual(row["id"], PARENT)
        self.assertEqual(row["commit"], head)
        self.assertIn(f"file={self.sha(self.tree, f'HEAD:{rel}')} {rel}", row["text"])

    @unittest.skipUnless(os.name == "posix", "SIGPIPE の終わり方は POSIX の sh を前提にする")
    def test_a_propose_with_its_output_cut_is_still_recorded_as_sent(self):
        # pushが済んだ後の「次:」の出力先が閉じていても、送った結果は sent・rc=0 のまま。
        self.put(f"{self.TODO}/{PARENT}.md")
        done = self.piped_propose()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        (row,) = self.results()
        self.assertEqual(self.remote_sha(), row["commit"])
        self.assertEqual(row["route"], "sent")
        self.assertEqual(row["rc"], "0")

    def reject_pushes(self):
        """リモートの push をすべて断る（pre-receive が 1 で終わる）。"""
        hook = os.path.join(self.remote, "hooks", "pre-receive")
        write(hook, "#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)

    def test_a_rejected_push_keeps_the_proposals_the_user_wrote(self):
        """送れなかったときに戻すのは自分のコミットだけ。書いた提案はワークツリーに残る。"""
        new = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        flow = self.put(f"{self.FLOWS}/{CHILD}.yml", "flow\n")
        self.reject_pushes()
        before = self.sha(self.tree, "HEAD")
        remote_before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.remote_sha(), remote_before)
        for rel, text in ((new, "v1\n"), (flow, "flow\n")):
            self.assertEqual(self.read(os.path.join(self.tree, rel)), text)
        self.assertNotIn("次: ", result.stdout)

    def test_a_rejected_push_keeps_an_edit_of_a_sent_proposal(self):
        rel = self.put(f"{self.TODO}/{PARENT}.md", "v1\n")
        self.assertEqual(self.ticket("propose", PARENT).returncode, 0)
        self.put(rel, "v2 precious edit\n")
        self.reject_pushes()
        result = self.ticket("propose", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read(os.path.join(self.tree, rel)), "v2 precious edit\n")
        self.assertNotIn("次: ", result.stdout)

    def test_unpushed_code_is_not_sent_along_with_the_proposal(self):
        self.put("src/secret.py", "x = 1\n")
        git(self.tree, "add", "--", "src/secret.py")
        git(self.tree, "commit", "-q", "-m", "wip code")
        self.put(f"{self.TODO}/{PARENT}.md")
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("src/secret.py", result.stderr)
        self.assertEqual(self.remote_sha(), before)
        self.assertIn(f"?? {self.TODO}/{PARENT}.md", self.dirty())
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_the_extension_is_compared_without_case(self):
        rel = self.put(f"{self.TODO}/{PARENT}.MD")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(), [rel])

    def test_no_proposal_stops_without_changing_anything(self):
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("送る提案がありません", result.stderr)
        self.assertEqual(self.remote_sha(), before)
        self.assertEqual(self.dirty(), "")

    def test_it_reads_only_the_parent_worktree(self):
        # ワークスペースルートにコピーがあっても、親のワークツリーに無ければ送らない。
        write(os.path.join(self.ws, "wip", "proposals", "todo", PARENT + ".md"), "x\n")
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("送る提案がありません", result.stderr)

    def test_the_executable_alone_is_refused_for_a_pushed_family(self):
        self.put(f"{self.TODO}/{PARENT}.md")
        result = self.exe("ticket", "propose", PARENT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ccnavi-ticket.sh", result.stderr)

    def test_a_blocking_user_decision_stops_before_sending(self):
        # 未送信のユーザの判断（review/ から done/ への移動など）があれば、提案も送らない。
        self.put(f"{self.TODO}/{PARENT}.md")
        write(os.path.join(self.tree, *APPROVED.split("/"), "done", "zzz.md"), "x\n")
        before = self.remote_sha()
        result = self.ticket("propose", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), before)


class TidyAfterFinishTest(WriteAndPushHarness):
    """finish が通ったら、sh が実行ファイルに `worktree tidy <識別子>` を頼む。

    write-and-push なら送った後に頼む。
    """

    def test_finish_asks_the_exe_to_tidy_after_sending(self):
        log = os.path.join(self._base, "tidy.log")
        logger = executable(
            os.path.join(self._base, "bin", "logger"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f"*\" worktree tidy \"*) printf '%s\\n' \"$*\" >>'{log}' ;;\n"
            "esac\n"
            f"exec '{self.bin}' \"$@\"\n",
        )
        self.assertEqual(self.ticket("start", PARENT, CCNAVI_ENTRY_PATH=logger).returncode, 0)
        self.assertFalse(os.path.exists(log))
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD, CCNAVI_ENTRY_PATH=logger).returncode, 0)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        result = self.ticket("finish", CHILD, CCNAVI_ENTRY_PATH=logger)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        with open(log, encoding="utf-8") as f:
            called = f.read()
        # 識別子は名前の形で剥がさずにそのまま渡す（親へ割り出すのは実行ファイル）。
        # この子はレビュー待ち（review/）へ動いたので、見返す場所としてワークツリーは残る
        # （消すのは confirm が done/ へ動かした後）
        self.assertIn(f"worktree tidy {CHILD}", called)
        self.assertIn("--cwd", called)
        self.assertTrue(os.path.isdir(tree))

    def test_cancel_drops_the_cancelled_childs_worktree(self):
        """取り消した子は作業中でないので、送った後にそのワークツリーを消す。"""
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD).returncode, 0)
        result = self.ticket("cancel", CHILD, "--reason", "やめた")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertIn(f"ワークツリー {CHILD} を削除しました", result.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.ws, "branch", "--list", CHILD).stdout.strip(), CHILD)


class WriteAndPushChildWorktreeTest(WriteAndPushHarness):
    """子の start で、このPCに子のワークツリーが無ければ、取り込み直後の親ブランチから作る。

    別のPCで承認された子でも、どのPCでも同じ場所に作る。
    """

    def child_path(self):
        return os.path.join(self.ws, ".claude", "worktrees", CHILD)

    def start_parent(self):
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_start_creates_the_missing_child_worktree_from_the_parent_branch(self):
        self.start_parent()
        self.assertFalse(os.path.exists(self.child_path()))
        parent_head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isdir(self.child_path()))
        branch = git(self.child_path(), "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual(branch, CHILD)
        # 子のワークツリーは、着手を書く前の（取り込み直後の）親ブランチの先頭から切れている
        base = git(self.child_path(), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(base, parent_head)
        self.assertIn("子のワークツリー", result.stderr)

    def test_a_child_branch_left_behind_the_parent_is_reused(self):
        self.start_parent()
        git(self.ws, "branch", CHILD, "main")  # 古い手元の子のブランチ（親の先頭の祖先）
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            git(self.child_path(), "rev-parse", "HEAD").stdout.strip(),
            self.sha(self.tree, "HEAD~1"),
        )

    def test_a_child_branch_with_its_own_commits_is_not_touched(self):
        self.start_parent()
        git(self.ws, "branch", CHILD, "main")
        side = os.path.join(self._base, "side")
        git(self.ws, "worktree", "add", "-q", side, CHILD)
        write(os.path.join(side, "mine.txt"), "x\n")
        git(side, "add", "-A")
        git(side, "commit", "-q", "-m", "mine")
        git(self.ws, "worktree", "remove", "--force", side)
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("に無いコミットがある", result.stderr)
        self.assertFalse(os.path.exists(self.child_path()))


class WriteAndPushNoStateTest(WriteAndPushHarness):
    """10. 親子ごとの取り込み状態を持たなくても、着手は write-and-push
    で取り込み・コミット・push まで行う。

    write-and-push の対象は、親のワークツリーがあり、origin があり、HEAD の確認に通るもの。
    取り込み状態は、どの PC でも作らない。
    """

    def test_start_fetches_commits_and_pushes_without_any_state(self):
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = self.sha(self.tree, "HEAD")
        self.assertNotEqual(after, head)
        self.assertEqual(self.remote_sha(), after)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_finish_pushes_without_any_state_too(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        child_tree = self.child_tree()
        started = self.ticket("start", CHILD)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        write(os.path.join(child_tree, "wip", "design", "a.md"), "a\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "-q", "-m", "work")
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_origin_less_family_is_not_write_and_push(self):
        git(self.ws, "remote", "remove", "origin")
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(f"{APPROVED}/doing/{PARENT}.md", self.dirty())


class WriteAndPushWithoutMrTest(WriteAndPushHarness):
    """10. マージリクエストで見るフェーズが無い親子のチケットも write-and-push
    の対象（外した条件）。"""

    plan = ("research",)

    def test_start_commits_and_pushes(self):
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = self.sha(self.tree, "HEAD")
        self.assertNotEqual(after, head)
        self.assertEqual(self.remote_sha(), after)
        family = self.exe("write-and-push", "family", PARENT)
        self.assertIn("target yes", family.stdout)


if __name__ == "__main__":
    unittest.main()
