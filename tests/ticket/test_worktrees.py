"""閉じた子と Draft を外した親のワークツリーの片付け（`ccnavi worktree drop|tidy`）と、
レビューを頼む前後の案内（`finish` の出力・`status` の次の一手・最後の `confirm` の知らせ）。

見るのは 4 つ。

1. `finish` で子を `review/` へ動かしただけでは「ユーザのレビューを待つ」と言わない。依頼の前は
   「レビュー準備中」と親の次の一手（request）を言い、依頼の後は待つと言う。依頼の後に子を足して
   閉じたら、依頼し直しが要ると言う。`status` の次の一手も同じ
2. 最後のレビューを `confirm` したら、親の finish から ready までの流れを言う
3. `worktree tidy <親> --phase <N>` は done/ に動いた子のワークツリーを消し、ブランチは残す。
   取り消しで閉じた子のワークツリーも消す（ワークツリーがあるのは作業中の子だけ）
4. `worktree drop <名前>` は生成物を消してから `git worktree remove` する。cwd が中にある・
   未コミットの変更がある・git のワークツリーでないときは何も消さずに言う
"""

from __future__ import annotations

import os

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import git, write


def parent_without_plan(name):
    """全体計画の無い親。子の `human_review` でレビューの要否が決まる。

    フィードバック計画も要らない。
    """
    lines = [
        "---",
        "version: 1",
        f"ticket: {name}",
        "human_review:",
        "  required: true",
        "  reason: t",
        "title: 親",
        "rationale: r",
        "allow:",
        "  - match: Write|Edit",
        '    glob: "src/*"',
        'started_at: ""',
        'completed_at: ""',
        'base_sha: ""',
        "---",
        "",
        "本文",
    ]
    return "\n".join(lines) + "\n"


class ReviewTurnTest(PhaseHarness):
    """子を閉じてからユーザに回るまでの案内と、最後の confirm の知らせ。"""

    def setUp(self):
        super().setUp()
        self.propose("i0001", parent_without_plan("i0001"))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)

    def child(self, name):
        self.propose(name, child_text(name, "i0001", 1, ["src/*"]))
        self.commit_parent(f"propose {name}")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.run_child(name, [(f"src/{name}.py", "x = 1\n")])
        closed = self.close_child(name)
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent(f"close {name}")
        self.merge(name)
        return tree, closed

    def next_step(self, name):
        status = self.ccnavi("ticket", "status", "i0001")
        self.assertEqual(status.returncode, 0, status.stderr)
        lines = status.stdout.splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith(f"- {name} "))
        rest = []
        for line in lines[at + 1 :]:
            if line.startswith("- "):
                break
            rest.append(line)
        return "\n".join(rest)

    def test_finish_and_status_say_who_moves_before_and_after_the_request(self):
        tree, closed = self.child("i0001-01-01")
        # 依頼の前。ユーザには回っていない
        self.assertIn("レビュー準備中", closed.stdout)
        self.assertIn("request --phase 1", closed.stdout)
        self.assertNotIn("ユーザのレビューを待つ", closed.stdout)
        # 誰が done/ へ動かすかは 1 度だけ言う（重ねない）
        self.assertEqual(closed.stdout.count("done/ へ動"), 1, closed.stdout)
        # レビュー待ち（review/）の子のワークツリーは、指摘を直す場所として片付けで残る
        tidy = self.ccnavi("--cwd", self.root, "worktree", "tidy", "i0001")
        self.assertEqual((tidy.returncode, tidy.stdout), (0, ""))
        self.assertTrue(os.path.isdir(tree))
        before = self.next_step("i0001-01-01")
        self.assertIn("次の一手: まだレビュー準備中", before)
        self.assertIn("親（メインエージェント）だけが実行する", before)
        fixture = self.remote()
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        # 依頼の後。ここで初めてユーザを待つ
        after = self.next_step("i0001-01-01")
        self.assertIn("依頼済み（レビュー待ち）", after)
        self.assertIn("ユーザのレビューを待つ", after)
        # 依頼の後に同じフェーズへ子を足して閉じた。依頼の記録は外れ、依頼し直しが要る
        _, again = self.child("i0001-01-02")
        self.assertIn("レビュー準備中", again.stdout)
        self.assertIn("依頼し直しが要る", again.stdout)
        self.assertEqual(again.stdout.count("done/ へ動"), 1, again.stdout)
        self.assertNotIn("ユーザのレビューを待つ", again.stdout)
        self.assertIn("依頼し直しが要る", self.next_step("i0001-01-02"))

    def test_status_does_not_say_wait_when_the_phase_is_unknown(self):
        """子のフェーズが読めない（`review_next` が空）とき、依頼済みかが分からないので
        「ユーザのレビューを待つ」と言わない。"""
        from unittest import mock

        from ccnavi.tickets import phase

        self.child("i0001-01-01")
        with mock.patch.object(phase, "review_next", return_value=""):
            step = self.next_step("i0001-01-01")
        self.assertIn("依頼済みかを言えない", step)
        self.assertIn("request", step)
        self.assertNotIn("次の一手: ユーザのレビューを待つ", step)

    def test_the_last_confirm_tells_the_way_to_ready_and_tidies_the_children(self):
        tree, _ = self.child("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        confirmed = self.confirm(fixture, 1)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertIn("全部のフェーズのレビューが済み", confirmed.stdout)
        self.assertIn("finish i0001", confirmed.stdout)
        self.assertIn("ready", confirmed.stdout)
        self.assertIn("`wip/` の追跡済みのファイル", confirmed.stdout)
        self.assertIn("ready --parent i0001", confirmed.stdout)
        # push と Draft 解除はレビュー済みの合意の範囲に入ると言い切る
        self.assertIn("合意の範囲に入る", confirmed.stdout)
        self.assertIn("確認を取り直さずに進める", confirmed.stdout)
        # confirm の後に sh が打つ片付け。done/ に動いた子のワークツリーを消し、ブランチは残す
        tidy = self.ccnavi("--cwd", self.root, "worktree", "tidy", "i0001", "--phase", "1")
        self.assertEqual(tidy.returncode, 0, tidy.stdout + tidy.stderr)
        self.assertIn("ワークツリー i0001-01-01 を消した", tidy.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.root, "branch", "--list", "i0001-01-01").strip(), "i0001-01-01")
        # もう一度打っても何も起きない
        again = self.ccnavi("--cwd", self.root, "worktree", "tidy", "i0001", "--phase", "1")
        self.assertEqual((again.returncode, again.stdout), (0, ""))

    def test_a_confirm_that_is_not_the_last_says_nothing_more(self):
        self.child("i0001-01-01")
        # 2 番目のフェーズの子が開いている（承認済み・未着手）
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["src/*"]))
        self.commit_parent("propose 02")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.approve().returncode, 0)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        confirmed = self.confirm(fixture, 1)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertNotIn("全部のフェーズのレビューが済み", confirmed.stdout)


class TidyTest(PhaseHarness):
    """閉じた子のワークツリーの片付け。消さないときは名指しする。"""

    def setUp(self):
        super().setUp()
        self.propose("i0001", parent_without_plan("i0001"))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)

    def tidy(self, cwd=None):
        return self.ccnavi("--cwd", cwd or self.root, "worktree", "tidy", "i0001")

    def test_cancelled_children_lose_their_worktrees_too(self):
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        tree = self.run_child("i0001-01-01")
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "やめた")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        tidy = self.tidy()
        self.assertEqual(tidy.returncode, 0, tidy.stdout + tidy.stderr)
        self.assertIn("ワークツリー i0001-01-01 を消した", tidy.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.root, "branch", "--list", "i0001-01-01").strip(), "i0001-01-01")

    def test_only_closed_children_are_picked_and_doing_ones_keep_their_worktrees(self):
        """片付けの候補は閉じた（done/ の）子だけ。作業中の子と取り消した子が並んでいるとき、
        候補に入るのは取り消した子だけで、作業中の子のワークツリーは残る。"""
        from ccnavi.infra import settings
        from ccnavi.tickets import worktrees

        for name in ("i0001-01-01", "i0001-01-02"):
            self.propose(name, child_text(name, "i0001", 1, ["src/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        doing = self.run_child("i0001-01-01")
        gone = self.run_child("i0001-01-02")
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-02", "--reason", "やめた")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        conf, _ = settings.load(self.root)
        conf.approved = ".ccnavi/approved"
        picked = [t.ticket for t in worktrees.tidy_targets(self.root, conf, "i0001")]
        self.assertEqual(picked, ["i0001-01-02"])
        tidy = self.tidy()
        self.assertEqual(tidy.returncode, 0, tidy.stdout + tidy.stderr)
        self.assertIn("ワークツリー i0001-01-02 を消した", tidy.stdout)
        self.assertNotIn("i0001-01-01", tidy.stdout)
        self.assertTrue(os.path.isdir(doing))
        self.assertFalse(os.path.exists(gone))

    def test_a_closed_child_with_uncommitted_changes_is_named_and_kept(self):
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        tree = self.run_child("i0001-01-01", [("src/a.py", "x = 1\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        write(os.path.join(tree, "src", "left.py"), "y = 2\n")
        tidy = self.tidy()
        self.assertEqual(tidy.returncode, 1, tidy.stdout + tidy.stderr)
        self.assertIn("未コミットの変更があるので、何も消さなかった", tidy.stdout)
        self.assertIn("src/left.py", tidy.stdout)
        self.assertTrue(os.path.exists(os.path.join(tree, "src", "left.py")))
        # cwd が中にあれば、未コミットを見る前に止める
        os.remove(os.path.join(tree, "src", "left.py"))
        inside = self.tidy(cwd=os.path.join(tree, "src"))
        self.assertEqual(inside.returncode, 1)
        self.assertIn("cwd がワークツリー i0001-01-01 の中にある", inside.stdout)
        self.assertTrue(os.path.isdir(tree))
        # 外から打てば消える
        self.assertEqual(self.tidy().returncode, 0)
        self.assertFalse(os.path.exists(tree))


class ChildShapedParentTest(PhaseHarness):
    """親の識別子が子の形（`-<2 桁>-<2 桁>`）で終わっていても、子の識別子から親を読み違えない。"""

    def test_tidy_by_the_parent_id_does_not_strip_its_tail(self):
        parent = "rel-2026-10-06"
        child = f"{parent}-01-01"
        tree_parent = self.worktree(parent, "main")
        self.parent_tree = tree_parent
        self.approved = os.path.join(tree_parent, ".ccnavi", "approved")
        self.propose(parent, parent_without_plan(parent))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.propose(child, child_text(child, parent, 1, ["src/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.start_parent(parent)
        tree = self.worktree(child, parent)
        self.assertEqual(self.ccnavi("ticket", "start", child).returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "cancel", child, "--reason", "やめた").returncode, 0)
        # 親の finish の後と同じ形（親の識別子で頼む）。名前の形で末尾を剥がすと rel-2026 になる
        tidy = self.ccnavi("--cwd", self.root, "worktree", "tidy", parent)
        self.assertEqual(tidy.returncode, 0, tidy.stdout + tidy.stderr)
        self.assertIn(f"ワークツリー {child} を消した", tidy.stdout)
        self.assertFalse(os.path.exists(tree))


class DropTest(PhaseHarness):
    """`worktree drop <名前>`。生成物を消してから git worktree remove。消さないときは理由を言う。"""

    def drop(self, name, cwd=None):
        return self.ccnavi("--cwd", cwd or self.root, "worktree", "drop", name)

    def test_drops_the_worktree_after_cleaning_the_build_outputs(self):
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, ".gitignore"), "node_modules/\nout/\n.venv/\n__pycache__/\n")
        write(os.path.join(tree, "ext", "package.json"), "{}")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ignore")
        write(os.path.join(tree, "node_modules", ".pnpm", "x", "index.js"), "")
        write(os.path.join(tree, "ext", "out", "extension.js"), "")
        write(os.path.join(tree, ".venv", "pyvenv.cfg"), "")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 0, dropped.stdout + dropped.stderr)
        self.assertIn("ワークツリー i0002 を消した", dropped.stdout)
        self.assertIn("ブランチは残してある", dropped.stdout)
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.root, "branch", "--list", "i0002").strip(), "i0002")
        self.assertNotIn("i0002", git(self.root, "worktree", "list"))
        # 無いものを消すのは成功（打ち直し）
        absent = self.drop("i0002")
        self.assertEqual(absent.returncode, 0)
        self.assertIn("無い", absent.stdout)

    def test_keeps_the_worktree_with_ignored_files_other_than_build_outputs(self):
        """git が無視している .env などは worktree remove が黙って消すので、名指しして止める。
        生成物（node_modules など）と直下の scratchpad/ は数えない。"""
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, ".gitignore"), ".env\nscratchpad/\nnode_modules/\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ignore")
        write(os.path.join(tree, "node_modules", "x", "index.js"), "")
        write(os.path.join(tree, ".env"), "TOKEN=x\n")
        write(os.path.join(tree, "scratchpad", "memo.md"), "メモ\n")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 1, dropped.stdout + dropped.stderr)
        self.assertIn("git が無視しているファイルがあるので、何も消さなかった", dropped.stdout)
        self.assertIn(".env", dropped.stdout)
        self.assertNotIn("scratchpad", dropped.stdout)
        self.assertNotIn("node_modules", dropped.stdout)
        self.assertIn("退避", dropped.stdout)
        self.assertTrue(os.path.exists(os.path.join(tree, ".env")))
        self.assertTrue(os.path.exists(os.path.join(tree, "scratchpad", "memo.md")))
        self.assertTrue(os.path.exists(os.path.join(tree, "node_modules", "x", "index.js")))
        # .env を退避して打ち直せば、scratchpad/ の下書きごと消える
        os.remove(os.path.join(tree, ".env"))
        again = self.drop("i0002")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertFalse(os.path.exists(tree))

    def test_only_the_top_scratchpad_may_go_with_the_worktree(self):
        """直下の scratchpad/ だけなら消える。入れ子の scratchpad/ は数えて止める。"""
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, ".gitignore"), "scratchpad/\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ignore")
        write(os.path.join(tree, "docs", "scratchpad", "deep.md"), "x\n")
        nested = self.drop("i0002")
        self.assertEqual(nested.returncode, 1, nested.stdout + nested.stderr)
        self.assertIn("docs/scratchpad/", nested.stdout)
        os.remove(os.path.join(tree, "docs", "scratchpad", "deep.md"))
        os.rmdir(os.path.join(tree, "docs", "scratchpad"))
        os.rmdir(os.path.join(tree, "docs"))
        write(os.path.join(tree, "scratchpad", "memo.md"), "メモ\n")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 0, dropped.stdout + dropped.stderr)
        self.assertFalse(os.path.exists(tree))

    def test_tracked_build_output_names_are_not_cleaned(self):
        """生成物の名前でも追跡されているもの（package.json の隣のコミット済みの out/ など）は
        消さない。消すと worktree remove が通らず、壊れたツリーが残る。"""
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, "ext", "package.json"), "{}")
        write(os.path.join(tree, "ext", "out", "kept.js"), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "tracked out")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 0, dropped.stdout + dropped.stderr)
        self.assertFalse(os.path.exists(tree))
        self.assertNotIn("i0002", git(self.root, "worktree", "list"))

    def test_a_locked_worktree_is_left_whole(self):
        """worktree remove が通らない（ロックされた）ワークツリーは、生成物も消さずに残す。"""
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, ".gitignore"), "node_modules/\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ignore")
        write(os.path.join(tree, "node_modules", "x.js"), "")
        git(self.root, "worktree", "lock", tree)
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 1, dropped.stdout + dropped.stderr)
        self.assertIn("ロック", dropped.stdout)
        self.assertTrue(os.path.exists(os.path.join(tree, "node_modules", "x.js")))

    def test_many_ignored_files_are_cut_short(self):
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, ".gitignore"), "*.log\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "ignore")
        for i in range(15):
            write(os.path.join(tree, f"a{i:02d}.log"), "")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 1)
        self.assertIn("…ほか 5 件", dropped.stdout)

    def test_keeps_the_worktree_when_the_cwd_is_inside(self):
        tree = self.worktree("i0002", "main")
        dropped = self.drop("i0002", cwd=tree)
        self.assertEqual(dropped.returncode, 1)
        self.assertIn("cwd がワークツリー i0002 の中にあるので消さなかった", dropped.stdout)
        self.assertIn("ccnavi-clean.sh --worktree i0002", dropped.stdout)
        # 分類器に止められたときの道（ユーザに頼む・ホームの設定に絞った許可を足す）も言う
        self.assertIn("~/.claude/settings.json", dropped.stdout)
        self.assertIn("ccnavi-git.sh *", dropped.stdout)
        self.assertTrue(os.path.isdir(tree))

    def test_keeps_a_worktree_with_untracked_files(self):
        tree = self.worktree("i0002", "main")
        write(os.path.join(tree, "draft.txt"), "書きかけ\n")
        dropped = self.drop("i0002")
        self.assertEqual(dropped.returncode, 1)
        self.assertIn("draft.txt", dropped.stdout)
        self.assertTrue(os.path.exists(os.path.join(tree, "draft.txt")))

    def test_refuses_what_is_not_a_worktree_and_names_that_are_paths(self):
        plain = os.path.join(self.root, ".claude", "worktrees", "plain")
        write(os.path.join(plain, "node_modules", "keep.js"), "")
        dropped = self.drop("plain")
        self.assertEqual(dropped.returncode, 1)
        self.assertIn("登録されていない", dropped.stdout)
        self.assertTrue(os.path.exists(os.path.join(plain, "node_modules", "keep.js")))
        for name in ("..", "a/b", "a\\b"):
            with self.subTest(name=name):
                refused = self.drop(name)
                self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".claude", "worktrees")))
