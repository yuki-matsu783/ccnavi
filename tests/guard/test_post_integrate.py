"""実行後チェックが、`ccnavi-git.sh integrate-done` の残したコミットを外す条件のテスト。

実際の git リポジトリを一時ディレクトリに作る。記録は `.git` の中にありエージェントにも書けるので、
記録の内容を信用せず git の状態で確かめていること（偽の記録で任意のコミットを通せない）が要点。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.hook import post_findings


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args} failed: {done.stderr}")
    return done.stdout.strip()


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def merge_tree_available():
    done = subprocess.run(
        ["git", "merge-tree", "--write-tree", "HEAD", "HEAD"], capture_output=True, text=True
    )
    return done.returncode == 0


@unittest.skipUnless(merge_tree_available(), "git merge-tree --write-tree (git 2.38+) is needed")
class SameButHandoffTest(unittest.TestCase):
    """2 つのツリーの差が `wip/handoff/` の下だけかを見る判定。"""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "a.txt", "a\n")
        write(self.repo, "wip/handoff/draft.txt", "draft\n")
        write(self.repo, "wip/proposals/todo/p.md", "p\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")

    def test_same_but_handoff_only_ignores_the_handoff_place(self):
        left = git(self.repo, "write-tree")
        git(self.repo, "rm", "-q", "-f", "--", "wip/handoff/draft.txt")
        only_scratch = git(self.repo, "write-tree")
        self.assertTrue(post_findings._same_but_handoff(self.repo, left, only_scratch))
        git(self.repo, "rm", "-q", "-f", "--", "wip/proposals/todo/p.md")
        also_proposal = git(self.repo, "write-tree")
        self.assertFalse(post_findings._same_but_handoff(self.repo, left, also_proposal))
        self.assertFalse(post_findings._same_but_handoff(self.repo, left, "0" * 40))
        self.assertFalse(post_findings._same_but_handoff(self.repo, left, ""))


@unittest.skipUnless(merge_tree_available(), "git merge-tree --write-tree (git 2.38+) is needed")
class IntegratedCommitsTest(unittest.TestCase):
    """`integrate-done` が積んだ squash のコミットを、ターンの終わりの検査から外す条件。

    片付けの後（ワークツリーもブランチも無い）で、記録の写しを手がかりに git で確かめる。
    """

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "a.txt", "a\n")
        write(self.repo, "gone.txt", "gone\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.turn = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "-c", "feat")
        write(self.repo, "a.txt", "a2\n")
        write(self.repo, "new.txt", "new\n")
        write(self.repo, "wip/handoff/draft.txt", "draft\n")
        git(self.repo, "rm", "-q", "gone.txt")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "work")
        self.tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "merge", "-q", "--squash", "feat")
        # integrate は下書きを外して staged にする。
        git(self.repo, "rm", "-q", "-f", "--", "wip/handoff/draft.txt")
        self.tree = git(self.repo, "write-tree")
        git(self.repo, "commit", "-q", "-m", "squash")
        self.squash = git(self.repo, "rev-parse", "HEAD")
        # integrate-done の片付け（ブランチは消える）。
        git(self.repo, "branch", "-q", "-D", "feat")

    def done(self, name="feat", **over):
        fields = {
            "state": "staged",
            "branch": name,
            "branch_sha": self.tip,
            "base": self.base,
            "tree": self.tree,
        }
        fields.update(over)
        ddir = os.path.join(self.repo, ".git", post_findings.INTEGRATE_DONE_DIR)
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, name), "w", encoding="utf-8") as f:
            f.writelines(f"{k} {v}\n" for k, v in fields.items())

    def paths(self):
        return post_findings.integrated_paths(self.repo, self.turn)

    def test_squash_commit_is_dropped(self):
        self.done()
        self.assertEqual(self.paths(), {"a.txt", "new.txt", "gone.txt"})

    def test_no_record_is_reported(self):
        self.assertEqual(self.paths(), set())

    def test_no_base_is_reported(self):
        self.done()
        self.assertEqual(post_findings.integrated_paths(self.repo, ""), set())

    def test_direct_commit_after_the_squash_is_reported(self):
        # 同じターンの直接のコミットは外さない。同じパスを触ったら、squash の分も外さない。
        self.done()
        write(self.repo, "a.txt", "edited directly\n")
        write(self.repo, "evil.txt", "x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "direct")
        self.assertEqual(self.paths(), {"new.txt", "gone.txt"})

    def test_forged_record_naming_the_commit_itself_is_reported(self):
        # 直接のコミットを、そのコミット自身を branch_sha に書いた記録で外させない。
        write(self.repo, "evil.txt", "x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "direct")
        direct = git(self.repo, "rev-parse", "HEAD")
        self.done(
            base=self.squash,
            tree=git(self.repo, "rev-parse", "HEAD^{tree}"),
            branch_sha=direct,
        )
        self.assertEqual(self.paths(), set())

    def test_forged_record_with_unrelated_branch_is_reported(self):
        # 記録の base と tree を直接のコミットに合わせても、merge-tree の結果が違えば外れない。
        write(self.repo, "evil.txt", "x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "direct")
        self.done(base=self.squash, tree=git(self.repo, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.paths(), set())

    def test_forged_tree_is_reported(self):
        self.done(tree="0" * 40)
        self.assertEqual(self.paths(), set())

    def test_forged_base_is_reported(self):
        self.done(base="0" * 40)
        self.assertEqual(self.paths(), set())

    def test_unknown_branch_sha_is_reported(self):
        self.done(branch_sha="0" * 40)
        self.assertEqual(self.paths(), set())

    def test_live_branch_holding_the_tip_is_reported(self):
        # 片付けが済んでいない（ブランチが残っている）なら外さない。
        git(self.repo, "branch", "-q", "feat", self.tip)
        self.done()
        self.assertEqual(self.paths(), set())

    def test_record_name_mismatch_is_reported(self):
        self.done(name="feat", branch="other")
        self.assertEqual(self.paths(), set())

    def test_ticket_bound_name_is_reported(self):
        self.done()
        write(self.repo, ".ccnavi/approved/doing/feat.md", "---\nticket: feat\n---\n")
        self.assertEqual(self.paths(), set())

    def test_name_on_origin_is_reported(self):
        # origin に同名のブランチが在る名前は、`integrate` が断る（親のブランチ）。片付けの後で
        # ワークツリーが無くても、手元の参照で見分けて、偽の記録で外させない。
        self.done()
        git(self.repo, "update-ref", "refs/remotes/origin/feat", "HEAD")
        self.assertEqual(self.paths(), set())

    def test_squash_before_the_turn_is_not_counted(self):
        # ターンの基準より前のコミットは数えない（gitstate.committed と同じ範囲）。
        self.done()
        self.assertEqual(post_findings.integrated_paths(self.repo, self.squash), set())


class DirectCommitsTest(unittest.TestCase):
    """版 4 の `integrate-done` が fast-forward で載せた K を、ターンの終わりの検査から外す条件。

    K の親が記録の `head`、K の木が記録の `c_tree` で、名前がチケットに結び付かないとき。
    専用の ref は使わず、記録（done の写し）と git のオブジェクトで照らす。
    """

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "a.txt", "a\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.turn = git(self.repo, "rev-parse", "HEAD")
        write(self.repo, "a.txt", "a2\n")
        write(self.repo, "new.txt", "new\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "K")
        self.k = git(self.repo, "rev-parse", "HEAD")
        self.c_tree = git(self.repo, "rev-parse", "HEAD^{tree}")

    def done(self, name="feat", **over):
        fields = {
            "version": "4",
            "k": self.k,
            "head": self.turn,
            "c_tree": self.c_tree,
            "base_branch": "main",
        }
        fields.update(over)
        ddir = os.path.join(self.repo, ".git", post_findings.INTEGRATE_DONE_DIR)
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, name), "w", encoding="utf-8") as f:
            f.writelines(f"{k} {v}\n" for k, v in fields.items())

    def paths(self):
        return post_findings.integrated_paths(self.repo, self.turn)

    def test_k_is_dropped(self):
        self.done()
        self.assertEqual(self.paths(), {"a.txt", "new.txt"})

    def test_an_empty_k_field_still_matches_by_parent_and_tree(self):
        self.done(k="-")
        self.assertEqual(self.paths(), {"a.txt", "new.txt"})

    def test_no_record_is_reported(self):
        self.assertEqual(self.paths(), set())

    def test_a_different_parent_is_reported(self):
        self.done(head="0" * 40)
        self.assertEqual(self.paths(), set())

    def test_a_different_tree_is_reported(self):
        self.done(c_tree="0" * 40)
        self.assertEqual(self.paths(), set())

    def test_a_record_naming_another_commit_as_k_is_reported(self):
        self.done(k="1" * 40)
        self.assertEqual(self.paths(), set())

    def test_a_ticket_bound_name_is_reported(self):
        write(self.repo, ".ccnavi/approved/doing/feat.md", "---\n---\n")
        self.done()
        self.assertEqual(self.paths(), set())

    def test_a_later_user_commit_on_the_same_path_is_reported(self):
        self.done()
        write(self.repo, "a.txt", "a3\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "user")
        self.assertEqual(self.paths(), {"new.txt"})

    def test_an_old_format_record_is_not_read_as_version_4(self):
        self.done(version="3")
        self.assertEqual(self.paths(), set())


if __name__ == "__main__":
    unittest.main()
