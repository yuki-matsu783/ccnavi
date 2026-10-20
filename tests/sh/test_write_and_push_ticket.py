"""write-and-push の受入テスト（チケットの状態を書く操作の順序・ロック・競合・戻し）。

道具と全体の説明は `tests/sh/write_and_push_harness.py`。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest

from tests import live_sh_pid, requires_symlink
from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    GIT,
    LOST_REPLY,
    PARENT,
    SHELL,
    WriteAndPushHarness,
    executable,
    git,
    write,
)
from tests.ticketsample import child_text


class WriteAndPushTicketTest(WriteAndPushHarness):
    # ---- 1. 順序と、書いたパスだけのコミット

    def test_start_commits_only_what_it_wrote_and_pushes(self):
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("に着手した", result.stdout)
        head = self.sha(self.tree, "HEAD")
        self.assertNotEqual(head, before)
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(
            self.committed(),
            [f"{APPROVED}/doing/{PARENT}.md"],
        )
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.dirty(), "")
        # 親子ごとの取り込み状態も、統合先のコピーも書かない。
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_other_staged_changes_stay_out_of_the_commit(self):
        """commit --only: 索引の他の変更はコミットに入らず、索引に残る。"""
        write(os.path.join(self.tree, "src", "other.py"), "x = 1\n")
        git(self.tree, "add", "--", "src/other.py")
        # 取り込みの merge は索引が HEAD と同じときだけなので、リモートと同じ形で見る。
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("src/other.py", self.committed())
        self.assertIn("A  src/other.py", self.dirty())

    def test_a_child_is_carried_on_the_parent_branch(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        self.child_tree()
        result = self.ticket("start", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{APPROVED}/doing/{CHILD}.md", self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    # ---- 8. hook の書きかけ（b）は先にコミットする。ユーザの判断（c）と知らない変更（d）は止める

    def test_hook_marks_are_committed_before_the_take_in(self):
        pending = self.mark(1, "pending", {"review": "mr", "at": "2026-09-29T00:00:00+0900"})
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.subjects(2),
            [
                f"ccnavi: {PARENT} に着手",
                f"ccnavi: {PARENT} の hook のマーカーをコミットする",
            ],
        )
        self.assertEqual(self.committed("HEAD~1"), [pending])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_a_human_decision_not_yet_sent_stops_without_writing(self):
        self.mark(1, "reviewed", {"by": "decide", "at": "t"})
        copy = self.read(self.copy(PARENT))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)
        self.assertIn(f"ccnavi-user-push-approved.sh {PARENT}", result.stderr)
        self.assertEqual(self.read(self.copy(PARENT)), copy)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_an_unknown_change_in_the_place_stops(self):
        write(os.path.join(self.tree, *APPROVED.split("/"), "notes.txt"), "x\n")
        # 形は hook のマーカーでも、変更前が在る（書き換え）なら hook のものとは読まない。
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi が把握していない変更", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    def test_a_forged_mark_with_other_fields_is_not_carried(self):
        self.mark(1, "skipped", {"by": "agent", "at": "t"})
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ユーザの判断が未送信", result.stderr)

    def test_an_unsent_commit_in_the_place_stops(self):
        """未送信の置き場のコミットが (b) でなければ止める。"""
        rel = f"{APPROVED}/doing/{CHILD}.md"
        with open(os.path.join(self.tree, rel), "a", encoding="utf-8") as f:
            f.write("tampered\n")
        git(self.tree, "commit", "-q", "-am", "edit a copy")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("未送信のコミット", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    # ---- 2. ロック

    def test_a_held_lock_stops_without_writing(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        now = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout)
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {now} {live_sh_pid(self)}-{now} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ロックを他の操作が持っている", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertTrue(os.path.isdir(self.lock_dir()))

    # ---- 3. 競合

    def test_a_remote_ahead_is_taken_in_before_writing(self):
        rel = f"{APPROVED}/doing/i0001-01-02.md"
        remote = self.remote_commit(rel, child_text("i0001-01-02", PARENT, 1, ("wip/design/*",)))
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)))
        self.assertEqual(self.sha(self.tree, "HEAD~1"), remote)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_a_take_in_that_finds_an_error_writes_nothing(self):
        """取り込みの後の検査が error を言えば（ccnavi-sync.sh が 1 で終わる）、何も書かずに止める。
        状態は書かない。入れ子のロックで検査できる。"""
        rel = f"{APPROVED}/doing/i0001-01-02.md"
        self.remote_commit(rel, "---\nticket: i0001-01-02\n---\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("取り込みの後の検査で止める理由がある", result.stderr)
        self.assertIn("取り込めなかった", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_a_diverged_parent_is_merged_before_writing(self):
        self.mark(1, "pending", {"review": "mr", "at": "t"})
        self.remote_commit("src/remote.py", "y = 2\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "src", "remote.py")))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD~1").stdout.split()
        self.assertEqual(len(parents), 3)  # 取り込みの merge

    def test_a_conflicting_take_in_writes_nothing(self):
        pending = f"{APPROVED}/phases/{PARENT}/1.pending"
        self.mark(1, "pending", {"review": "mr", "at": "l"})
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "hook")
        self.remote_commit(pending, json.dumps({"review": "mr", "at": "r"}))
        head = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("衝突", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        merge_head = git(self.tree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        self.assertFalse(os.path.exists(os.path.join(self.tree, merge_head)))

    def test_a_hook_writing_between_sorting_and_taking_in_is_retried_once(self):
        """3 と 4 の間に hook が書いて取り込みが重なったら、見分けからもう 1 回だけやり直す。"""
        rel = f"{APPROVED}/phases/{PARENT}/2.pending"
        body = json.dumps({"review": "mr", "at": "t"})
        self.remote_commit(rel, body)
        once = os.path.join(self._base, "once")
        shim = os.path.join(self._base, "hookshim")
        target = os.path.join(self.tree, *rel.split("/"))
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f'*" fetch "*"refs/heads/{PARENT}:"*)\n'
            f"  if [ ! -e '{once}' ]; then : >'{once}'; mkdir -p '{os.path.dirname(target)}';"
            f" printf '%s' '{body}' >'{target}'; fi ;;\n"
            "esac\n"
            f"exec '{GIT}' \"$@\"\n",
        )
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("もう 1 回だけやり直す", result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- 4. 途中の操作

    def test_an_operation_in_progress_stops_before_anything(self):
        path = git(self.tree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        path = path if os.path.isabs(path) else os.path.join(self.tree, path)
        write(path, self.sha(self.tree, "HEAD") + "\n")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        result = self.ticket("start", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("途中の操作", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))

    # ---- 5. 戻し

    def reject_pushes(self, times):
        """リモートの pre-receive が、最初の <times> 回の push を断る（-1 はずっと）。"""
        counter = os.path.join(self._base, "rejected")
        write(counter, "0\n")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            "#!/bin/sh\n"
            f"n=$(cat '{counter}'); echo $((n + 1)) > '{counter}'\n"
            f'if [ {times} -lt 0 ] || [ "$n" -lt {times} ]; then echo busy >&2; exit 1; fi\n',
        )

    def test_a_rejected_push_is_undone_and_retried_once(self):
        self.reject_pushes(1)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("戻した", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD~1"), before)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    def test_two_rejected_pushes_leave_nothing_written(self):
        self.reject_pushes(-1)
        before = self.sha(self.tree, "HEAD")
        copy = self.read(self.copy(PARENT))
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ccnavi-sync.sh", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.read(self.copy(PARENT)), copy)
        self.assertEqual(self.dirty(), "")

    def test_a_moved_head_is_not_undone(self):
        """戻すのは先頭が自分のコミットのときだけ（比較つきの update-ref）。"""
        # push の前に、別の操作が先頭に積んだ形を pre-push で作る。
        hook = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hook = hook if os.path.isabs(hook) else os.path.join(self.tree, hook)
        executable(
            os.path.join(hook, "pre-push"),
            "#!/bin/sh\ngit commit -q --allow-empty -m other >/dev/null 2>&1\nexit 1\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("戻さずに止めた", result.stderr)
        self.assertEqual(self.subjects(2), ["other", f"ccnavi: {PARENT} に着手"])

    @unittest.skipUnless(hasattr(os, "killpg"), "TERM を捕まえさせられない（os.killpg が無い）")
    def test_a_term_before_the_push_ends_undoes_the_commit(self):
        """送る前に TERM が来たら、自分のコミットを戻して抜ける。"""
        import signal
        import time

        mark = os.path.join(self._base, "pushing")
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        executable(os.path.join(hooks, "pre-push"), f"#!/bin/sh\n: >'{mark}'\nsleep 5\nexit 1\n")
        before = self.sha(self.tree, "HEAD")
        running = subprocess.Popen(
            [SHELL, os.path.join(self.scripts, "ccnavi-ticket.sh"), "start", PARENT],
            cwd=self.ws,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60
        while not os.path.exists(mark) and time.monotonic() < deadline:
            time.sleep(0.1)
        running.send_signal(signal.SIGTERM)
        _, err = running.communicate(timeout=60)
        self.assertIn("送る前に止められた", err.decode("utf-8", "replace"))
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(os.path.exists(self.lock_dir()))

    # ---- 6. 届いていた push

    def test_a_push_whose_reply_was_lost_counts_as_sent(self):
        shim = os.path.join(self._base, "shim")
        executable(os.path.join(shim, "git"), LOST_REPLY.format(git=GIT))
        path = shim + os.pathsep + os.environ.get("PATH", "")
        result = self.ticket("start", PARENT, PATH=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("届いていた", result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_a_delivered_push_whose_check_also_failed_is_not_written_twice(self):
        """push の応答も ls-remote も失敗したが届いていた。

        戻して取り込み直すと自分のコミットが戻るので、書き直さずに成功で終える。
        push の関門の ls-remote（push の前）は数えない。push のあとの 1 回目だけ失敗させる。
        """
        count = os.path.join(self._base, "ls-count")
        write(count, "0\n")
        pushed = os.path.join(self._base, "pushed")
        shim = os.path.join(self._base, "shim2")
        executable(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = push ]; then\n'
            f"    : > '{pushed}'\n"
            f"    '{GIT}' \"$@\" >/dev/null 2>&1; echo 'fatal: hung up' >&2; exit 128\n"
            "  fi\n"
            f"  if [ \"$a\" = refs/heads/{PARENT} ] && [ -e '{pushed}' ]; then\n"
            f"    n=$(cat '{count}'); echo $((n + 1)) > '{count}'\n"
            "    if [ \"$n\" = 0 ]; then echo 'fatal: unable to access' >&2; exit 128; fi\n"
            "  fi\n"
            "done\n"
            f"exec '{GIT}' \"$@\"\n",
        )
        result = self.ticket("start", PARENT, PATH=shim + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("書き直さずに終える", result.stderr)
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.subjects(2)[1], "approve")
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- コミット（`--no-verify` とタイムアウト監視の時間）と、失敗したときの索引

    def test_a_failed_commit_leaves_nothing_staged(self):
        """署名に失敗してコミットできなければ、add した新しいファイルも索引から外す。"""
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", "false")
        self.assertEqual(self.ticket("start", PARENT).returncode, 1)
        git(self.ws, "config", "commit.gpgsign", "false")
        self.child_tree()
        before = self.sha(self.tree, "HEAD")
        git(self.ws, "config", "commit.gpgsign", "true")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("コミットできなかった", result.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), before)
        self.assertEqual(git(self.tree, "diff", "--cached", "--name-only").stdout, "")
        self.assertEqual(self.dirty(), "")

    def test_a_hanging_signer_is_cut_off(self):
        signer = executable(os.path.join(self._base, "slow-gpg"), "#!/bin/sh\nsleep 30\n")
        git(self.ws, "config", "commit.gpgsign", "true")
        git(self.ws, "config", "gpg.program", signer)
        result = self.ticket("start", PARENT, CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT="2")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("制限時間", result.stderr)
        self.assertEqual(self.dirty(), "")

    def test_the_users_commit_hooks_are_skipped(self):
        hooks = git(self.tree, "rev-parse", "--git-path", "hooks").stdout.strip()
        hooks = hooks if os.path.isabs(hooks) else os.path.join(self.tree, hooks)
        for name in ("pre-commit", "commit-msg"):
            executable(os.path.join(hooks, name), "#!/bin/sh\nexit 1\n")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    # ---- write-and-push を飛ばさせない

    def test_a_double_dash_does_not_slip_past_write_and_push(self):
        result = self.ticket("start", "--", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subjects(1), [f"ccnavi: {PARENT} に着手"])
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

    def test_the_executable_refuses_a_state_operation_without_write_and_push(self):
        refused = self.exe("ticket", "start", PARENT)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("write-and-push の対象", refused.stderr)
        self.assertEqual(self.dirty(), "")

    def test_a_long_lock_of_a_live_owner_is_not_taken(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        old = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout) - 3600
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {old} {live_sh_pid(self)}-{old} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("10 分を超えて取られたまま", result.stderr)
        self.assertTrue(os.path.isdir(self.lock_dir()))

    def test_a_long_lock_names_its_owner_and_how_to_stop_it(self):
        os.makedirs(self.lock_dir())
        host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
        system = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
        old = int(subprocess.run(["date", "+%s"], capture_output=True, text=True).stdout) - 3600
        write(
            os.path.join(self.lock_dir(), "owner"),
            f"{host} {live_sh_pid(self)} {old} {live_sh_pid(self)}-{old} {system}\n",
        )
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))
        self.assertIn(f"持ち主は pid {live_sh_pid(self)}・ホスト {host}・開始 {at}", result.stderr)
        self.assertIn("ユーザに終了させてもらってから再実行して", result.stderr)
        self.assertTrue(os.path.isdir(self.lock_dir()))

    # ---- 基点のリンク

    @requires_symlink
    def test_a_linked_workspace_still_carries(self):
        linked = self.ws + "-link"
        os.symlink(self.ws, linked)
        self.addCleanup(os.remove, linked)
        result = subprocess.run(
            [
                SHELL,
                os.path.join(linked, ".ccnavi", "scripts", "ccnavi-ticket.sh"),
                "start",
                PARENT,
            ],
            cwd=linked,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")

    # ---- 止める親子のチケット

    def test_a_parent_gone_from_the_remote_stops_at_the_take_in(self):
        """リモートから消えた親（マージされていない）は、取り込み（ccnavi-sync.sh）が終了コード 1 で
        止める。何も書かない。"""
        write(
            os.path.join(self.scripts, "ccnavi-review.sh"),
            '#!/bin/sh\n[ "$1" = merged ] && echo none\nexit 0\n',
        )
        git(self.ws, "--git-dir", self.remote, "update-ref", "-d", f"refs/heads/{PARENT}")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("取り込めなかった", result.stderr)
        self.assertIn("戻し方 1", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertEqual(self.remote_sha(), "")

    def test_a_parent_gone_from_the_remote_is_refused_by_the_push_gate(self):
        """取り込みが通っても（代役が 0 で終わる）、リモートに無い親のブランチは作り直さない。
        write-and-push の push の前の関門が止める。"""
        write(os.path.join(self.scripts, "ccnavi-sync.sh"), "#!/bin/sh\nexit 0\n")
        git(self.ws, "--git-dir", self.remote, "update-ref", "-d", f"refs/heads/{PARENT}")
        result = self.ticket("start", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"親のブランチ {PARENT} がリモートに無い", result.stderr)
        self.assertIn("何も反映していません", result.stderr)
        self.assertNotIn("started_at: 20", self.read(self.copy(PARENT)))
        self.assertEqual(self.remote_sha(), "")

    def test_a_take_in_that_tidied_the_parent_away_writes_nothing(self):
        """取り込み（ccnavi-sync.sh）が、マージ済みの親のワークツリーを片付けて 0 で終わっても、
        存在しないツリーには書かずに止まる。"""
        write(
            os.path.join(self.scripts, "ccnavi-sync.sh"),
            f'#!/bin/sh\ngit -C "{self.ws}" worktree remove --force "{self.tree}"\nexit 0\n',
        )
        before = self.remote_sha()
        result = self.ticket("start", PARENT)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("閉じた（マージ済み）か、ワークツリーが無い", result.stderr)
        self.assertIn("何も反映していません", result.stderr)
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(self.remote_sha(), before)

    def test_record_risk_is_not_write_and_push_and_rides_on_the_finish(self):
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)
        tree = self.child_tree()
        self.assertEqual(self.ticket("start", CHILD).returncode, 0)
        write(os.path.join(tree, "wip", "design", "a.md"), "a\n")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "work")
        head = self.sha(self.tree, "HEAD")
        judge = os.path.join(
            self.tree, *APPROVED.split("/"), "phases", PARENT, f"{CHILD}.judge.json"
        )
        write(judge, json.dumps({"x": {"hit": False, "reason": "r", "head": "h", "at": "t"}}))
        # 記録（judge.json）が未コミットでも、finish の write-and-push は止めずにコミットして push
        # する。
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.sha(self.tree, "HEAD"), head)
        self.assertIn(os.path.relpath(judge, self.tree).replace(os.sep, "/"), self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")


if __name__ == "__main__":
    unittest.main()
