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
        self.assertNotIn("ユーザのレビューを待つ", again.stdout)
        self.assertIn("依頼し直しが要る", self.next_step("i0001-01-02"))

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

    def test_doing_children_keep_their_worktrees(self):
        # ワークツリーがあるのは作業中の子だけ。作業中の子のものは消さない
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        tree = self.run_child("i0001-01-01")
        tidy = self.tidy()
        self.assertEqual((tidy.returncode, tidy.stdout), (0, ""))
        self.assertTrue(os.path.isdir(tree))

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
