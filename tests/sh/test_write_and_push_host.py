"""write-and-push の受入テスト（ホストの代役（GitLab・GitHub）とレビューの状態）。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

from ccnavi.infra import pathcase
from tests import ROOT
from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    ELI5,
    GIT,
    HALF,
    PARENT,
    SHELL,
    URL_GIT,
    Host,
    WriteAndPushHarness,
    WriteAndPushHostHarness,
    executable,
    git,
    write,
)
from tests.ticketsample import parent_text


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class WriteAndPushHostTest(WriteAndPushHostHarness):
    """ホストに触る副命令（request・confirm・ready）と、ユーザ専用の decide・close-early。"""

    def test_request_confirm_and_ready_are_carried(self):
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", ELI5)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.requested")
        self.assertEqual(len(Host.notes), 1)
        # 依頼文は ELI5 の在りか（差分の中の相対パス）と crit push の送り先を言う。
        # 指摘はユーザが crit push で行のスレッドとして送る
        self.assertIn(f"`crit review {ELI5}`", Host.notes[0]["body"])
        self.assertIn("`crit push 1`", Host.notes[0]["body"])
        log = os.path.join(self._base, "tidy.log")
        logger = executable(
            os.path.join(self._base, "bin", "logger"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f"*\" worktree \"*) printf '%s\\n' \"$*\" >>'{log}' ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        confirmed = self.review("confirm", "--phase", "1", CCNAVI_ENTRY_PATH=logger)
        self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.reviewed")
        # done/ へ動いた子のワークツリーの片付けを、送った後に実行ファイルへ頼む
        with open(log, encoding="utf-8") as f:
            self.assertIn(f"worktree tidy {PARENT} --phase 1", f.read())
        ready = self.review("ready", CCNAVI_ENTRY_PATH=logger)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/ready.json")
        # 最後に親のワークツリーを消そうとするが、cwd が中にあるので消さず、外から打つ 1 本を出す
        with open(log, encoding="utf-8") as f:
            logged = f.read()
        self.assertIn(f"worktree retire {PARENT}", logged)
        # retire の後に、残っているワークツリーの一覧を頼む
        self.assertLess(
            logged.index(f"worktree retire {PARENT}"), logged.index(f"worktree remaining {PARENT}")
        )
        self.assertIn(f"cwd がワークツリー {PARENT} の中にあるので削除しませんでした", ready.stdout)
        self.assertIn(f"ccnavi-clean.sh --worktree {PARENT}", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draft を解除した"), ready.stdout.index("cwd がワークツリー")
        )
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_and_pushes_the_archive_removals_before_undrafting(self):
        """ready の退避（閉じたチケットの削除）は、write-and-push がコミットして push してから
        Draft を外す。"""
        old = f"{APPROVED}/phases/old/closed.json"
        write(os.path.join(self.tree, *old.split("/")), "{}\n")
        git(self.tree, "add", old)
        git(self.tree, "commit", "-q", "-m", "old")
        git(self.tree, "push", "-q", "origin", PARENT)
        mover = executable(
            os.path.join(self._base, "bin", "mover"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    prev="$a"; done\n'
            f'  rm "$tree/{old}"\n'
            f"  printf '{old}\\n' >\"$list\"\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        ready = self.review("ready", CCNAVI_ENTRY_PATH=mover)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.carried(old)
        self.assertFalse(os.path.exists(os.path.join(self.tree, *old.split("/"))))
        self.assertEqual(git(self.tree, "ls-tree", "HEAD", "--", old).stdout, "")

    def test_ready_from_outside_drops_the_parent_worktree_after_undrafting(self):
        """`ready --parent <親>` を親のワークツリーの外（ワークスペースルート）から打つ。
        中から打ったときと同じに Draft を外し、最後に親のワークツリーを消す（ブランチは残す）。"""
        ready = self.sh(
            "ccnavi-review.sh",
            "ready",
            "--parent",
            PARENT,
            cwd=self.ws,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_ENTRY_PATH=self.half,
            HALF_TREE=self.tree,
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を削除しました", ready.stdout)
        self.assertLess(
            ready.stdout.index("Draft を解除した"),
            ready.stdout.index(f"ワークツリー {PARENT} を削除しました"),
        )
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(git(self.ws, "branch", "--list", PARENT).stdout.strip(), PARENT)
        # 送ったものはリモートに届いている（ready のマーカー）
        shown = git(self.ws, "show", "--name-only", "--format=", f"{PARENT}").stdout
        self.assertIn(f"{APPROVED}/phases/{PARENT}/ready.json", shown)

    def test_ready_keeps_the_parent_worktree_when_the_draft_stays(self):
        """Draft を外し損ねたら、外から打っても親のワークツリーは消さない。

        ready を打ち直せるようにするため。
        """
        Host.keep_draft = True
        ready = self.sh(
            "ccnavi-review.sh",
            "ready",
            "--parent",
            PARENT,
            cwd=self.ws,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_ENTRY_PATH=self.half,
            HALF_TREE=self.tree,
        )
        self.assertNotEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除できませんでした", ready.stderr)
        self.assertNotIn("を削除した", ready.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_from_outside_by_a_relative_script_path(self):
        """ワークスペースルートから相対パス（`sh .ccnavi/scripts/ccnavi-review.sh`）で打っても、
        親のワークツリーへ移った後に write-and-push の取り込み（ccnavi-sync.sh）を見失わない。

        親のワークツリーにはスクリプトのコピーを置かない（相対パスがそこで解かれると、見失うか、
        ワークツリーの古いコピーを使う）。
        """
        git(self.tree, "rm", "-r", "-q", ".ccnavi/scripts")
        git(self.tree, "commit", "-q", "-m", "no scripts here")
        git(self.tree, "push", "-q", "origin", PARENT)
        env = self.env(
            PATH=self.path, GITLAB_TOKEN="t0k", CCNAVI_ENTRY_PATH=self.half, HALF_TREE=self.tree
        )
        ready = subprocess.run(
            [SHELL, ".ccnavi/scripts/ccnavi-review.sh", "ready", "--parent", PARENT],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.assertIn(f"ワークツリー {PARENT} を削除しました", ready.stdout)
        self.assertFalse(os.path.exists(self.tree))

    def test_ready_parent_refuses_what_is_not_a_parent_worktree(self):
        for args in (
            ("--parent",),
            ("--parent", "../x"),
            ("--parent", "i0009"),
            ("--parent=",),
            ("--parent", ""),
            ("--parent", PARENT, "--parent"),
            ("--parent", PARENT, f"--parent={PARENT}"),
        ):
            with self.subTest(args=args):
                refused = self.sh(
                    "ccnavi-review.sh",
                    "ready",
                    *args,
                    cwd=self.ws,
                    PATH=self.path,
                    GITLAB_TOKEN="t0k",
                    CCNAVI_ENTRY_PATH=self.half,
                    HALF_TREE=self.tree,
                )
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertNotIn("Draft を解除した", refused.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    def test_ready_commits_the_wip_removals_and_names_them(self):
        """ready が消した wip/ の追跡済みのファイルは、write-and-push
        が退避と一緒にコミットして push する。
        消したものと、戻せる版（消す前の HEAD）を出す。"""
        self.eli5()
        head = self.sha(self.tree, "HEAD")
        mover = executable(
            os.path.join(self._base, "bin", "mover"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    [ "$prev" = --root ] && root="$a"\n'
            '    prev="$a"; done\n'
            f'  rm "$tree/{ELI5}"\n'
            f"  printf '{ELI5}\\n' >\"$list\"\n"
            '  mkdir -p "$root/logs/state"; : >"$root/logs/state/note.md"\n'
            "  printf '%s\\n' \"$root/logs/state/note.md\"\n"
            "  printf 'tree %s\\n' \"$tree\"\n"
            f"  printf 'wip-from {head}\\nwip {ELI5}\\n'\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        ready = self.review("ready", CCNAVI_ENTRY_PATH=mover)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("wip/ の追跡済みのファイルを削除しました（1 件", ready.stdout)
        self.assertIn(head[:12], ready.stdout)
        self.assertIn(f"  {ELI5}", ready.stdout)
        self.carried(ELI5)
        self.assertEqual(git(self.tree, "ls-tree", "HEAD", "--", ELI5).stdout, "")

    def test_the_real_ready_moves_the_rest_again_after_a_failed_push(self):
        """実際の実行ファイルの ready（退避を始めた後の打ち直し）を write-and-push の中で通す。

        1 回目の push をリモートが断る → write-and-push が戻す → 取り込みからやり直して残りを移し、
        送ってから
        Draft を外す。
        """
        archive_base = os.path.join(self.ws, "logs", "archive", "self")
        # 親はもう退避にだけある（前の回が移した）。ツリーには統合先からの過去の親子が残る。
        for rel in (f"doing/{PARENT}.md", f"doing/{CHILD}.md"):
            git(self.tree, "rm", "-q", "--", f"{APPROVED}/{rel}")
        write(
            os.path.join(archive_base, "done", f"{PARENT}.md"),
            parent_text(PARENT, list(self.plan), allow=("src/*", "wip/*")),
        )
        write(os.path.join(archive_base, "phases", PARENT, "ready.json"), '{"mr": 1}')
        old = (
            "---\nversion: 1\nticket: old\ntitle: 古い親\nrationale: r\n"
            "human_review:\n  required: false\n  reason: t\n"
            'allow:\n  - match: Write|Edit\n    glob: "src/*"\n---\n'
        )
        write(os.path.join(self.tree, APPROVED, "done", "old.md"), old)
        write(os.path.join(self.tree, APPROVED, "phases", "old", "closed.json"), "{}\n")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "退避の途中")
        git(self.tree, "push", "-q", "origin", PARENT)
        head = self.sha(self.tree, "HEAD")
        write(
            os.path.join(archive_base, "ready", f"{PARENT}.json"),
            json.dumps(
                {"parent": PARENT, "tree": pathcase.fold(os.path.realpath(self.tree)),
                 "head": head, "files": []}
            ),
        )  # fmt: skip
        # リモートは 1 回目の push だけ断る
        once = os.path.join(self._base, "refused-once")
        executable(
            os.path.join(self.remote, "hooks", "pre-receive"),
            f"#!/bin/sh\n[ -e '{once}' ] && exit 0\n: >'{once}'\necho refused >&2\nexit 1\n",
        )
        ready = self.review("ready", CCNAVI_ENTRY_PATH=self.bin)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertTrue(os.path.exists(once))
        self.assertIn("push が通らなかった", ready.stderr)
        self.assertIn("Draft を解除した", ready.stdout)
        self.carried(f"{APPROVED}/done/old.md")
        for rel in ("done/old.md", "phases/old/closed.json"):
            self.assertEqual(
                git(self.tree, "ls-tree", "HEAD", "--", f"{APPROVED}/{rel}").stdout, ""
            )
            self.assertTrue(os.path.isfile(os.path.join(archive_base, *rel.split("/"))), rel)

    def test_a_request_is_not_posted_twice_across_runs(self):
        """投稿の後に失敗した依頼を打ち直しても、同じ目印の依頼は投稿し直さない。"""
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        once = os.path.join(self._base, "failed-once")
        html = self.eli5()
        first = self.review(
            "request", "--phase", "1", "--body-file", body, "--eli5", html, HALF_FAIL=once
        )
        self.assertNotEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(len(Host.notes), 1)
        again = self.review(
            "request", "--phase", "1", "--body-file", body, "--eli5", html, HALF_FAIL=once
        )
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("投稿済み", again.stdout)
        self.assertEqual(len(Host.notes), 1)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.requested")

    def test_terminal_decide_chooses_outside_write_and_push_and_writes_inside(self):
        """ターミナルの decide は、選ぶのを write-and-push の外で済ませ、write-and-push の中では
        --yes で書くだけ。"""
        chooser = executable(
            os.path.join(self._base, "bin", "chooser"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" --choose-out "*)\n'
            '  prev=""; for a in "$@"; do [ "$prev" = --choose-out ] && out="$a"; prev="$a"; done\n'
            f"  [ -d '{self.lock_dir()}' ] && echo LOCKED-WHILE-CHOOSING >&2\n"
            '  printf \'{"choices": {"u1": "keep"}, "digest": "d0"}\' >"$out"\n'
            "  exit 0 ;;\n"
            # 受け入れる指摘の計画を立てる --yes は write-and-push の外で走り、何も置かない。
            # 受け入れる指摘は無いという計画を返す（実物は依頼の記録を読むので、ここでは使わない）
            '*" --accept-plan "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done\n'
            '  printf \'{"threads": []}\' >"$plan"\n'
            '  printf \'{"version":1,"ok":true}\\n\'; exit 0 ;;\n'
            '*" --yes "*)\n'
            '  prev=""; for a in "$@"; do\n'
            '    [ "$prev" = --record-writes ] && list="$a"\n'
            '    [ "$prev" = --record-tree ] && tree="$a"\n'
            '    prev="$a"; done\n'
            '  mkdir -p "$tree/.ccnavi/approved/phases/i0001"\n'
            "  printf '{}' >\"$tree/.ccnavi/approved/phases/i0001/1.reviewed\"\n"
            "  printf '.ccnavi/approved/phases/i0001/1.reviewed\\n' >\"$list\"\n"
            '  printf \'{"version":1,"ok":true}\\n\'; exit 0 ;;\n'
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        done = self.review("1", script="ccnavi-user-decide.sh", CCNAVI_ENTRY_PATH=chooser)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("行き先を置いて送った", done.stdout)
        self.assertNotIn("LOCKED-WHILE-CHOOSING", done.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/1.reviewed")

    def test_close_early_hands_over_to_the_carrier(self):
        closed = self.review("--reason", "r", "--no-issue", script="ccnavi-user-close-early.sh")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.carried(f"{APPROVED}/phases/{PARENT}/2.skipped")


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class WriteAndPushReviewTest(WriteAndPushHarness):
    """レビューの状態を書く sh も write-and-push で回す（ccnavi-user-decide.sh を代表に見る）。"""

    def setUp(self):
        super().setUp()
        import http.server
        import threading

        from tests.sh.test_review_decide import GitLab

        GitLab.posted = []
        GitLab.fail_notes = False
        server = http.server.HTTPServer(("127.0.0.1", 0), GitLab)
        GitLab.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.posted = GitLab.posted
        shim = os.path.join(self._base, "shim")
        url = f"http://127.0.0.1:{GitLab.port}/demo/greeter.git"
        executable(os.path.join(shim, "git"), URL_GIT.format(url=url, git=GIT))
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.half = executable(
            os.path.join(self._base, "bin", "half"),
            HALF.format(root=ROOT, python=sys.executable),
        )

    def decide(self, *args):
        return self.sh(
            "ccnavi-user-decide.sh",
            *args,
            cwd=self.tree,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
            CCNAVI_ENTRY_PATH=self.half,
        )

    def test_decide_is_carried_and_the_answer_stays_json(self):
        done = self.decide("1", "--choices", '{"u1":"keep"}', "--digest", "d0")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(answer["ok"])
        self.assertEqual(len(done.stdout.strip().splitlines()), 1, done.stdout)
        rel = f"{APPROVED}/phases/{PARENT}/1.reviewed"
        self.assertEqual(self.committed(), [rel])
        self.assertEqual(
            self.subjects(1), [f"ccnavi: {PARENT} のフェーズ 1 の指摘の行き先を決めた"]
        )
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        # 決めた内容のコメントは、送った後に投稿する。
        self.assertEqual([kind for kind, _ in self.posted], ["note"])

    def test_decide_preview_takes_no_lock_and_writes_nothing(self):
        head = self.sha(self.tree, "HEAD")
        os.makedirs(self.lock_dir())
        write(os.path.join(self.lock_dir(), "owner"), "other 1 1 1-1 Other\n")
        shown = self.sh(
            "ccnavi-review.sh",
            "decide",
            "1",
            "--preview",
            cwd=self.tree,
            PATH=self.path,
            GITLAB_TOKEN="t0k",
        )
        self.assertNotIn("ロック", shown.stderr)
        self.assertEqual(self.sha(self.tree, "HEAD"), head)


if __name__ == "__main__":
    unittest.main()
