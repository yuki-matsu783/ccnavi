"""git のラッパースクリプトの受入テスト。外からスクリプトを動かす。

読み返すのは標準出力・標準エラー・終了コードと、logs/ に残った記録だけ。
中の関数も変数も見ないので、書き方が変わってもテストは真であり続ける。

使い捨ての git リポジトリを毎回作る。このリポジトリ自身で走らせると、
テストが「コードのこと」ではなく「走った機械の作業ツリーのこと」を報告する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT

SCRIPT = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-git.sh")
SHELL = shutil.which("sh") or shutil.which("bash")


def git(cwd, *args):
    """素の git。ラッパースクリプトを通さずに見本のリポジトリを組み立てるために使う。"""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(cwd):
    """コミットが 1 件あり、追跡外のファイルが 1 件ある使い捨てのリポジトリ。

    ワークスペースルートにもする。ラッパースクリプトは `.ccnavi/scripts/ccnavi-common.sh` を
    持つディレクトリを cwd から上へ探して根を決める（`ccnavi_workspace`）ので、それが無いと
    「ワークスペースの外」として断られ、判定まで届かない。
    """
    marker = os.path.join(cwd, ".ccnavi", "scripts", "ccnavi-common.sh")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    open(marker, "a", encoding="utf-8").close()
    git(cwd, "init", "-q")
    git(cwd, "config", "user.email", "t@example.invalid")
    git(cwd, "config", "user.name", "t")
    with open(os.path.join(cwd, "tracked.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(f"line {n}" for n in range(200)) + "\n")
    git(cwd, "add", "tracked.txt")
    git(cwd, "commit", "-q", "-m", "seed")
    with open(os.path.join(cwd, "untracked.txt"), "w", encoding="utf-8") as f:
        f.write("x\n")


def logs_of(cwd):
    """記録の中身を新しい順に返す。"""
    directory = os.path.join(cwd, "logs")
    if not os.path.isdir(directory):
        return []
    # 診断ログの置き場（logs/diag/）はディレクトリなので数えない。
    names = sorted(
        (n for n in os.listdir(directory) if os.path.isfile(os.path.join(directory, n))),
        reverse=True,
    )
    out = []
    for name in names:
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as f:
            out.append(f.read())
    return out


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
class GitWrapperTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-gitwrap-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_repo(self.dir)

    def run_wrapper(self, *args, env=None):
        environment = dict(os.environ)
        environment.update(env or {})
        return subprocess.run(
            [SHELL, SCRIPT, *args],
            cwd=self.dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def make_bare(self):
        """送り先の空のリポジトリを、その回だけの場所に作る。

        名前を決め打ちにして Temp の直下へ置くと、別のセッションが同じテストを
        走らせたときに同じ場所を指す。片方の後始末がもう片方の送り先を消して、
        コードと関係のない失敗になる。
        """
        parent = tempfile.mkdtemp(prefix="ccnavi-gitwrap-origin-")
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        bare = os.path.join(parent, "origin.git")
        git(self.dir, "init", "-q", "--bare", bare)
        return bare

    def assertRejected(self, *args):
        """拒否は終了コード 2 で、git を 1 度も動かさない。"""
        result = self.run_wrapper(*args)
        self.assertEqual(2, result.returncode, f"stdout={result.stdout!r}")
        self.assertIn("ccnavi-git:", result.stderr)
        self.assertEqual("", result.stdout)
        self.assertEqual([], logs_of(self.dir), "拒否したのに git が走って記録が残っている")
        return result


class RejectTest(GitWrapperTest):
    def test_push_to_an_integration_branch_is_rejected(self):
        """統合先へ直接は送らない。統合は利用者がマージリクエストで行う。"""
        result = self.assertRejected("push", "origin", "main")
        self.assertIn("統合", result.stderr)

    def test_push_forms_that_cannot_be_undone_are_rejected(self):
        git(self.dir, "checkout", "--quiet", "-b", "i0001")
        for args in (
            ("push", "--force"),
            ("push", "-f", "origin"),
            ("push", "--force-with-lease"),
            ("push", "--delete", "origin", "i0001"),
            ("push", "--all"),
            ("push", "--mirror"),
            ("push", "--tags"),
            ("push", "--no-verify"),
            ("push", "origin", "HEAD:main"),
            ("push", "origin", "other"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)

    def test_global_config_option_is_rejected_without_reading_its_value(self):
        # `git -c diff.external=<コマンド>` は分類上ただの diff のまま任意コマンドを
        # 実行する。危ない設定名の列挙は網羅できないので、値を見ずに形で落とす。
        self.assertRejected("-c", "diff.external=echo", "diff")
        self.assertRejected("--config-env=diff.external=X", "diff")

    def test_options_that_turn_reads_into_writes_are_rejected(self):
        self.assertRejected("diff", "--output=stolen.txt")
        self.assertRejected("log", "--exec-path=/tmp")
        self.assertRejected("status", "--git-dir=/elsewhere/.git")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "stolen.txt")))

    def test_destructive_subcommands_are_rejected(self):
        for args in (
            ("reset", "--hard"),
            ("clean", "-fd"),
            ("rebase", "main"),
            ("cherry-pick", "HEAD"),
            ("config", "--get", "user.email"),
            ("clone", "https://example.invalid/x.git"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)

    def test_commit_checkout_and_pull_pass_but_their_escape_hatches_do_not(self):
        # commit / checkout / pull は通す。止めるのは、点検を飛ばす形と
        # 作業ツリーの書きかけを捨てる形だけ。
        for args in (
            ("commit", "--no-verify", "-m", "x"),
            ("commit", "-an", "-m", "x"),
            ("checkout", "--", "tracked.txt"),
            ("checkout", "-f", "main"),
            ("switch", "--discard-changes", "main"),
            ("pull", "--force"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)

    def test_dangerous_forms_of_allowed_subcommands_are_rejected(self):
        for args in (
            ("branch", "-D", "x"),
            ("branch", "--force", "x"),
            ("branch", "-u", "origin/main"),
            ("tag", "-d", "v1"),
            ("remote", "add", "o", "u"),
            ("worktree", "remove", "--force", "p"),
            ("stash", "drop"),
            ("stash", "clear"),
            ("restore", "."),
            ("merge", "-X", "ours", "other"),
            ("merge", "-Xtheirs", "other"),
            ("merge", "-s", "ours", "other"),
            ("merge", "--no-verify", "other"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)

    def test_unknown_subcommand_is_rejected_by_default(self):
        self.assertRejected("frobnicate")

    def test_rm_passes_but_forcing_it_does_not(self):
        # git rm は索引や HEAD と食い違うファイルを既定で拒む。-f はその線を
        # 越えてコミットしていない変更ごと消すので、こちらでも同じ場所で止める。
        for args in (
            ("rm",),
            ("rm", "-f", "tracked.txt"),
            ("rm", "--force", "tracked.txt"),
            ("rm", "-rf", "."),
            ("rm", "."),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "tracked.txt")))

    def test_the_alternative_it_names_is_not_a_denied_form(self):
        # 生の git は PreToolUse で止まる。案内が `git stash push -u` と書くと、
        # 案内された先でもう 1 度拒否される。代わりの手段が拒否される案内は、
        # 案内が無いのとほとんど同じ。名乗るならラッパースクリプトの形で名乗る。
        for args in (
            ("reset", "--hard"),
            ("clean", "-fd"),
            ("checkout", "-f", "main"),
            ("checkout", "--", "tracked.txt"),
            ("stash", "drop"),
            ("branch", "-D", "other"),
            ("restore",),
            ("pull", "--force"),
        ):
            with self.subTest(args=args):
                result = self.assertRejected(*args)
                stderr = result.stderr
                self.assertIn("ccnavi-git.sh", stderr, f"代わりの形を名乗っていない: {stderr}")
                for form in ("git stash", "git restore", "git branch", "git worktree"):
                    self.assertNotIn(
                        form, stderr.replace("ccnavi-git.sh", ""), f"生の git を勧めた: {stderr}"
                    )

    def test_long_option_values_do_not_trip_the_short_option_scan(self):
        # `--contains=feature/dev` の f と d を -f -d と読み違えないこと。
        # 短いオプションをまとめた形 (-rd) を 1 文字ずつ見る判定の巻き添え。
        result = self.run_wrapper("branch", "--contains=feature/dev")
        self.assertNotEqual(2, result.returncode, result.stderr)


class PassTest(GitWrapperTest):
    def test_rm_removes_a_tracked_file(self):
        # rules.yml が rm -rf の代わりに名指しで勧める経路。勧めた先が
        # 通らないと、案内どおりに進めなくなる。
        result = self.run_wrapper("rm", "tracked.txt")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "tracked.txt")))

    def test_commit_goes_through(self):
        self.assertEqual(0, self.run_wrapper("add", "untracked.txt").returncode)
        result = self.run_wrapper("commit", "-m", "足した")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertTrue(result.stdout.startswith("ok  git commit"), result.stdout)

    def test_a_merge_that_is_not_a_fast_forward_goes_through(self):
        # worktree の手順は、main が先に進んだ状態から取り込む形を必ず通る。
        # ここを止めると、枝分かれしたブランチが永久に統合されない。
        self.assertEqual(0, self.run_wrapper("checkout", "-b", "topic").returncode)
        with open(os.path.join(self.dir, "topic.txt"), "w", encoding="utf-8") as f:
            f.write("topic\n")
        self.run_wrapper("add", "topic.txt")
        self.run_wrapper("commit", "-m", "topic")
        self.run_wrapper("checkout", "-")
        with open(os.path.join(self.dir, "main.txt"), "w", encoding="utf-8") as f:
            f.write("main\n")
        self.run_wrapper("add", "main.txt")
        self.run_wrapper("commit", "-m", "main")

        result = self.run_wrapper("merge", "topic", "--no-edit")

        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "topic.txt")))

    def test_push_sends_the_current_branch(self):
        """作業用のブランチは、そのままの名前で送れる。

        レビューはマージリクエストの実物に結ぶので、そこまではエージェントが進められる。
        統合（マージ）は利用者の側に残してある。
        """
        bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", bare)
        git(self.dir, "checkout", "--quiet", "-b", "i0001")

        result = self.run_wrapper("push", "-u", "origin", "i0001")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        landed = subprocess.run(
            ["git", "--git-dir", bare, "rev-parse", "i0001"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, landed.returncode, landed.stderr)
        here = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.dir,
            capture_output=True,
            text=True,
        )
        self.assertEqual(here.stdout.strip(), landed.stdout.strip())

    def test_push_from_a_child_ticket_worktree_is_rejected(self):
        """子チケットのワークツリーからは送れない。親が合流してから親のツリーで送る。

        見分けるのは承認済みチケットに `parent:` があるかだけ。承認済みチケットの無いツリーと
        親の承認済みチケットを持つツリーは通す。
        """
        bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", bare)
        copies = os.path.join(self.dir, ".ccnavi", "approved")
        os.makedirs(os.path.join(copies, "doing"))
        with open(os.path.join(copies, "doing", "i0001.md"), "w", encoding="utf-8") as f:
            f.write("---\nversion: 1\nticket: i0001\n---\n")
        with open(os.path.join(copies, "doing", "i0001-01.md"), "w", encoding="utf-8") as f:
            f.write("---\nversion: 1\nticket: i0001-01\nparent: i0001\nphase: 1\n---\n")
        # 閉じた子。承認済みチケットは done/ に動いているが、ツリーはまだ子のもの。
        os.makedirs(os.path.join(copies, "done"))
        with open(os.path.join(copies, "done", "i0001-02.md"), "w", encoding="utf-8") as f:
            f.write("---\nversion: 1\nticket: i0001-02\nparent: i0001\nphase: 1\n---\n")
        trees = {}
        for name in ("i0001", "i0001-01", "i0001-02", "free"):
            path = os.path.join(self.dir, ".claude", "worktrees", name)
            git(self.dir, "worktree", "add", "-q", path, "-b", name)
            trees[name] = path

        def push_from(name):
            environment = dict(os.environ)
            return subprocess.run(
                [SHELL, SCRIPT, "push", "-u", "origin", name],
                cwd=trees[name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )

        for name in ("i0001-01", "i0001-02"):
            with self.subTest(tree=name):
                child = push_from(name)
                self.assertEqual(2, child.returncode, child.stdout + child.stderr)
                self.assertIn("子チケット", child.stderr)
                self.assertIn("i0001", child.stderr)
        self.assertEqual([], logs_of(self.dir), "拒否したのに git が走って記録が残っている")
        for name in ("i0001", "free"):
            with self.subTest(tree=name):
                result = push_from(name)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                landed = subprocess.run(
                    ["git", "--git-dir", bare, "rev-parse", "--verify", name],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, landed.returncode, landed.stderr)

    def test_push_is_rejected_when_the_child_ticket_lives_in_the_parent_tree(self):
        """子の承認済みチケットは親のワークツリーに置かれる（approval.home_dir）。そこも見る。

        ワークスペースルートには何も無く、親のツリーにだけ子の承認済みチケットがある形。
        """
        bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", bare)
        trees = {}
        for name in ("i0001", "i0001-01"):
            path = os.path.join(self.dir, ".claude", "worktrees", name)
            git(self.dir, "worktree", "add", "-q", path, "-b", name)
            trees[name] = path
        doing = os.path.join(trees["i0001"], ".ccnavi", "approved", "doing")
        os.makedirs(doing)
        with open(os.path.join(doing, "i0001.md"), "w", encoding="utf-8") as f:
            f.write("---\nversion: 1\nticket: i0001\n---\n")
        with open(os.path.join(doing, "i0001-01.md"), "w", encoding="utf-8") as f:
            f.write("---\nversion: 1\nticket: i0001-01\nparent: i0001\nphase: 1\n---\n")

        def push_from(name):
            return subprocess.run(
                [SHELL, SCRIPT, "push", "-u", "origin", name],
                cwd=trees[name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        child = push_from("i0001-01")
        self.assertEqual(2, child.returncode, child.stdout + child.stderr)
        self.assertIn("子チケット", child.stderr)
        parent = push_from("i0001")
        self.assertEqual(0, parent.returncode, parent.stdout + parent.stderr)

    def test_checkout_moves_between_branches(self):
        result = self.run_wrapper("checkout", "-b", "topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        head = self.run_wrapper("rev-parse", "--abbrev-ref", "HEAD")
        self.assertIn("topic", head.stdout)


class MergeFileTest(GitWrapperTest):
    """merge-file は結果を標準出力に出す形 (-p / --stdout) だけ通す。

    付けないと git は 1 つめのファイルを直に書き換える。ファイルを書くのは Edit / Write に
    任せ、hook が行き先を見られるようにする。
    """

    def write_versions(self):
        """衝突する 3 つの版。現在・祖先・相手の順で名前を返す。"""
        versions = {
            "current.txt": "a\nmine\nc\n",
            "base.txt": "a\nb\nc\n",
            "other.txt": "a\ntheirs\nc\n",
        }
        for name, text in versions.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write(text)
        return list(versions)

    # 衝突の最中の 3 つの版。merge-file が取る順（現在・祖先・相手）。
    STAGES = (":2:tracked.txt", ":1:tracked.txt", ":3:tracked.txt")

    def read(self, name):
        with open(os.path.join(self.dir, name), encoding="utf-8") as f:
            return f.read()

    def test_forms_that_write_a_file_are_rejected(self):
        current, base, other = self.write_versions()
        for args in (
            # -p が無いと 1 つめのファイルを書き換える。
            ("merge-file", current, base, other),
            ("merge-file", "--union", current, base, other),
            # -L の値の -p はラベル。-p を付けたことにならず、git はファイルを書く。
            ("merge-file", "-L", "-p", current, base, other),
            # `--` の後ろはファイル名。
            ("merge-file", "--union", "--", "-p", base, other),
            # 打ち消しと略記は git が受け取るので、知らない綴りとして止める。
            ("merge-file", "-p", "--no-stdout", current, base, other),
            ("merge-file", "--std", current, base, other),
            ("merge-file", "-pq", current, base, other),
            ("merge-file", "-p", "--obj", current, base, other),
            ("merge-file", "-p", "--no-object-id", current, base, other),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertEqual("a\nmine\nc\n", self.read(current))

    def test_union_to_stdout_goes_through_and_writes_nothing(self):
        current, base, other = self.write_versions()
        labels = ("-L", "ours", "-L", "base", "-L", "theirs")
        result = self.run_wrapper("merge-file", "-p", "--union", *labels, current, base, other)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertTrue(result.stdout.startswith("ok  git merge-file"), result.stdout)
        self.assertIn("mine\ntheirs\n", result.stdout)
        self.assertEqual("a\nmine\nc\n", self.read(current))

    def conflict(self):
        """tracked.txt を両側で書き換えてマージし、衝突したまま止める。"""
        branch = git_out(self.dir, "branch", "--show-current")
        git(self.dir, "checkout", "-q", "-b", "other")
        with open(os.path.join(self.dir, "tracked.txt"), "w", encoding="utf-8") as f:
            f.write("other\n")
        git(self.dir, "commit", "-q", "-am", "other")
        git(self.dir, "checkout", "-q", branch)
        with open(os.path.join(self.dir, "tracked.txt"), "w", encoding="utf-8") as f:
            f.write("mine\n")
        git(self.dir, "commit", "-q", "-am", "mine")
        subprocess.run(["git", "merge", "other"], cwd=self.dir, capture_output=True)
        self.assertIn("UU tracked.txt", git_out(self.dir, "status", "--porcelain"))
        return self.read("tracked.txt")

    def test_object_id_without_p_is_rejected_and_writes_no_object(self):
        # -p を外した --object-id は、結果をオブジェクトとしてリポジトリに書く。
        conflicted = self.conflict()
        before = git_out(self.dir, "count-objects")
        self.assertRejected("merge-file", "--union", "--object-id", *self.STAGES)
        self.assertEqual(before, git_out(self.dir, "count-objects"))
        self.assertEqual(conflicted, self.read("tracked.txt"))

    def test_union_of_the_three_stages_goes_through_and_writes_nothing(self):
        # 使い方が勧める 1 回で済む形。一時ファイルを作らずに両方を取り込んだ結果を読む。
        conflicted = self.conflict()
        before = git_out(self.dir, "count-objects")
        result = self.run_wrapper("merge-file", "-p", "--union", "--object-id", *self.STAGES)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertTrue(result.stdout.startswith("ok  git merge-file"), result.stdout)
        self.assertIn("mine\nother\n", result.stdout)
        self.assertNotIn("<<<<<<<", result.stdout)
        self.assertEqual(conflicted, self.read("tracked.txt"))
        self.assertEqual(before, git_out(self.dir, "count-objects"))

    def test_the_three_stages_of_a_conflict_can_be_read(self):
        self.conflict()
        for stage, expected in (("1", "line 0"), ("2", "mine"), ("3", "other")):
            with self.subTest(stage=stage):
                result = self.run_wrapper("show", f":{stage}:tracked.txt")
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                self.assertIn(expected, result.stdout)


class OutputTest(GitWrapperTest):
    def test_success_returns_a_summary_line_and_the_body(self):
        result = self.run_wrapper("status", "--porcelain")
        self.assertEqual(0, result.returncode, result.stderr)
        first = result.stdout.splitlines()[0]
        self.assertTrue(first.startswith("ok  git status"), first)
        self.assertIn("log=logs/git-", first)
        self.assertIn("untracked.txt", result.stdout)

    def test_long_output_is_capped_and_the_rest_stays_in_the_log(self):
        result = self.run_wrapper("show", "HEAD", env={"CCNAVI_GIT_MAX_LINES": "5"})
        self.assertEqual(0, result.returncode, result.stderr)
        body = result.stdout.splitlines()
        self.assertEqual(7, len(body), result.stdout)  # 要約 + 本文 5 行 + 残りの案内
        self.assertTrue(body[-1].startswith("... 残り "), body[-1])
        self.assertIn("line 199", logs_of(self.dir)[0], "全量が記録に残っていない")
        self.assertNotIn("line 199", result.stdout, "抑えたはずの本文が出ている")

    def test_failure_returns_exit_1_and_the_tail(self):
        result = self.run_wrapper("show", "no-such-ref")
        self.assertEqual(1, result.returncode)
        self.assertTrue(result.stdout.startswith("fail  git show"), result.stdout)
        self.assertIn("log=logs/git-", result.stdout.splitlines()[0])
        self.assertIn("no-such-ref", logs_of(self.dir)[0])

    def test_the_log_records_what_was_run(self):
        self.run_wrapper("status", "--porcelain")
        self.assertIn("$ git status --porcelain", logs_of(self.dir)[0])

    def test_old_logs_are_dropped_by_generation(self):
        for _ in range(4):
            self.run_wrapper("rev-parse", "HEAD", env={"CCNAVI_GIT_KEEP_LOGS": "2"})
        self.assertEqual(2, len(logs_of(self.dir)))


class EnvironmentTest(GitWrapperTest):
    # 引数で `-c` を拒んでも、環境変数から同じ設定を差し込めるなら穴は開いたまま。
    INJECT = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "status.showUntrackedFiles",
        "GIT_CONFIG_VALUE_0": "no",
    }

    def test_the_injection_works_on_plain_git(self):
        """見本が有効なことを先に確かめる。有効でない見本では次のテストが何も確かめられない。"""
        environment = dict(os.environ)
        environment.update(self.INJECT)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.dir,
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertNotIn("untracked.txt", result.stdout)

    def test_config_injected_through_the_environment_is_dropped(self):
        result = self.run_wrapper("status", "--porcelain", env=self.INJECT)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("untracked.txt", result.stdout, "環境からの設定注入が通っている")


def hint_lines(stdout):
    """ラッパースクリプトが失敗の文面の末尾に足す案内の行。"""
    return [line for line in stdout.splitlines() if line.startswith("案内:")]


class WorktreeRemoveHintTest(GitWrapperTest):
    """worktree remove が Permission denied で止まったときだけ、立て直し方を案内する。

    Windows では、プロセスの cwd がそのディレクトリを使用中にする。Bash ツールの cwd は呼び出しを
    またいで残る親のシェルのものなので、ワークツリーの中へ cd したまま remove すると、
    最後のディレクトリで Permission denied になり、空のディレクトリが残る。
    """

    def add_worktree(self, name):
        path = os.path.join(self.dir, ".claude", "worktrees", name)
        git(self.dir, "worktree", "add", "-q", path, "-b", name)
        return path

    @unittest.skipUnless(os.name == "nt", "cwd がディレクトリを掴んで消せなくなるのは Windows だけ")
    def test_permission_denied_on_remove_tells_how_to_recover(self):
        path = self.add_worktree("held")
        # ワークツリーの中に cwd を持つプロセス。Bash ツールの残った親のシェルの代わり。
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=path)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)

        result = self.run_wrapper("worktree", "remove", path)

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("Permission denied", result.stdout)
        hints = hint_lines(result.stdout)
        self.assertEqual(1, len(hints), result.stdout)
        hint = hints[0]
        for word in ("cwd", "サブシェル", "worktree list", "rmdir", "利用者"):
            self.assertIn(word, hint)
        # 案内が勧める形は、生の git ではなくラッパースクリプトの形で名乗る。
        self.assertIn("ccnavi-git.sh worktree list", hint)
        self.assertNotIn("git worktree", hint.replace("ccnavi-git.sh worktree", ""))
        # 消し残しを消す rmdir には、打ったパスをそのまま入れる。
        self.assertIn(f"rmdir {path}", hint)

    def test_other_remove_failures_get_no_hint(self):
        missing = self.run_wrapper("worktree", "remove", os.path.join(self.dir, "no-such"))
        self.assertEqual(1, missing.returncode, missing.stdout + missing.stderr)
        self.assertEqual([], hint_lines(missing.stdout))

        # 未追跡のファイルが残っているワークツリーは git が消さない。これは cwd の話ではない。
        path = self.add_worktree("dirty")
        with open(os.path.join(path, "left.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")
        dirty = self.run_wrapper("worktree", "remove", path)
        self.assertEqual(1, dirty.returncode, dirty.stdout + dirty.stderr)
        self.assertEqual([], hint_lines(dirty.stdout))

    def test_failures_of_other_subcommands_get_no_hint(self):
        result = self.run_wrapper("show", "no-such-ref")
        self.assertEqual(1, result.returncode)
        self.assertEqual([], hint_lines(result.stdout))


def git_out(cwd, *args):
    """素の git の標準出力。見本のリポジトリの状態を読むために使う。"""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()


class ResetGuidanceTest(GitWrapperTest):
    """reset は通さず、リモートに合わせたいときは ccnavi-sync.sh を案内する（ADR-0093 の 2b。D36）。

    前は `checkout -B <ブランチ> <リモート>/<ブランチ>` を案内していた。付け替えはブランチにしか無い
    コミットを何も言わずに外し、親のブランチなら承認済みチケットの置き場ごと中身を変えるので、
    ccnavi-sync.sh（早送りか merge、衝突したら取りやめる）ができた段階 2b で止め、案内を移した。
    """

    def diverge(self):
        """squash マージの後の形。upstream が先に進み、今のブランチにだけコミットがある。"""
        branch = git_out(self.dir, "branch", "--show-current")
        git(self.dir, "checkout", "-q", "-b", "upstream")
        with open(os.path.join(self.dir, "tracked.txt"), "w", encoding="utf-8") as f:
            f.write("upstream\n")
        git(self.dir, "commit", "-q", "-am", "upstream")
        git(self.dir, "checkout", "-q", branch)
        with open(os.path.join(self.dir, "local.txt"), "w", encoding="utf-8") as f:
            f.write("local\n")
        git(self.dir, "add", "local.txt")
        git(self.dir, "commit", "-q", "-m", "local")
        return branch

    def test_reset_names_ccnavi_sync_and_merge_not_checkout_B(self):
        stderr = self.assertRejected("reset", "--hard", "origin/main").stderr
        for form in (
            "ccnavi-sync.sh <P>",
            "fetch <リモート> <ブランチ>",
            "merge <リモート>/<ブランチ>",
            "利用者",
        ):
            self.assertIn(form, stderr)
        self.assertNotIn("checkout -B", stderr)
        # 勧める形はラッパースクリプトの形で名乗る。生の git を勧めると、勧めた先でもう 1 度止まる。
        rest = stderr.replace("ccnavi-git.sh", "").replace("ccnavi-sync.sh", "")
        for raw in ("git checkout", "git merge", "git fetch", "git stash"):
            self.assertNotIn(raw, rest)

    def test_usage_names_ccnavi_sync_not_checkout_B(self):
        usage = self.run_wrapper("--help").stdout
        self.assertIn("ccnavi-sync.sh <P>", usage)
        self.assertNotIn("checkout -B <ブランチ>", usage)

    def test_clean_does_not_mention_checkout_B(self):
        stderr = self.assertRejected("clean", "-fd").stderr
        self.assertNotIn("checkout -B", stderr)

    def test_forced_branch_moves_are_rejected(self):
        branch = self.diverge()
        before = git_out(self.dir, "rev-parse", branch)
        for args in (
            ("checkout", "-B", branch, "upstream"),
            ("checkout", "-qB", branch, "upstream"),
            ("switch", "--force-create", branch, "upstream"),
            ("switch", "--force-create=" + branch, "upstream"),
        ):
            with self.subTest(args=args):
                result = self.assertRejected(*args)
                self.assertIn("ccnavi-sync.sh", result.stderr)
                self.assertIn("付け替え", result.stderr)
        # switch -C は、全引数の `-C`（判定の起点を動かす）で前から止まっている。
        self.assertRejected("switch", "-C", branch, "upstream")
        self.assertEqual(before, git_out(self.dir, "rev-parse", branch))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "local.txt")))

    def test_creating_a_new_branch_still_passes(self):
        for args in (("checkout", "-b", "fresh", "HEAD"), ("switch", "--create", "fresh2", "HEAD")):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class BranchForceMoveTest(GitWrapperTest):
    """`branch -M`（強制の改名）と `-C`（強制の複製）を止める（ADR-0093 の段階 0）。

    短いオプションの検査は小文字の f・m・u だけを見ていたので、大文字の -M は通っていた。
    ブランチの名前はワークツリーの名前とチケットの識別子に結び付いていて、改名すると引けなくなる。
    """

    def test_force_rename_and_force_copy_are_rejected(self):
        git(self.dir, "branch", "topic")
        before = git_out(self.dir, "branch", "--list")
        for args in (
            ("branch", "-M", "topic", "renamed"),
            ("branch", "-M", "renamed"),
            ("branch", "-rM", "origin/x", "y"),
            ("branch", "--move", "topic", "renamed"),
            ("branch", "--move=topic"),
            ("branch", "-C", "topic", "copied"),
            ("branch", "-rC", "topic", "copied"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertEqual(before, git_out(self.dir, "branch", "--list"))

    def test_force_rename_names_why_and_the_wrapper(self):
        stderr = self.assertRejected("branch", "-M", "renamed").stderr
        self.assertIn("識別子", stderr)
        self.assertIn("利用者", stderr)

    def test_lower_case_move_stays_rejected(self):
        # -m は以前から止めている。-M を足しても変わらない。
        for args in (("branch", "-m", "renamed"), ("branch", "-rm", "x")):
            with self.subTest(args=args):
                self.assertRejected(*args)

    def test_safe_delete_and_listing_still_pass(self):
        git(self.dir, "branch", "topic")
        for args in (("branch", "--list"), ("branch", "-v"), ("branch", "-d", "topic")):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_usage_lists_what_it_rejects(self):
        usage = self.run_wrapper("--help").stdout
        self.assertIn("-D -m -M -C -f -u は不可", usage)


class FetchRefspecTest(GitWrapperTest):
    """fetch・pull は refspec（`:` と `+` を含む引数）を通さない（ADR-0093 の段階 0）。

    `+refs/heads/x:refs/heads/x` は取ってきたものを手元のブランチへ直に書き、`+` は
    早送りでない書き換えも通す。取ってくるのはリモート名とブランチ名だけにする。
    """

    def setUp(self):
        super().setUp()
        self.bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", self.bare)
        self.branch = git_out(self.dir, "branch", "--show-current")
        git(self.dir, "push", "-q", "origin", self.branch)

    def test_refspecs_and_urls_are_rejected(self):
        before = git_out(self.dir, "for-each-ref")
        b = self.branch
        for args in (
            ("fetch", "origin", f"+refs/heads/{b}:refs/heads/{b}"),
            ("fetch", "origin", f"{b}:{b}"),
            ("fetch", "origin", f"{b}:refs/heads/other"),
            ("fetch", "origin", f"+{b}"),
            ("fetch", "origin", f":refs/heads/{b}"),
            ("fetch", "--refmap=+refs/heads/*:refs/remotes/o/*", "origin"),
            ("fetch", self.bare + ":x"),
            ("fetch", "https://example.invalid/x.git", b),
            ("pull", "origin", f"{b}:{b}"),
            ("pull", "origin", f"+{b}"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertEqual(before, git_out(self.dir, "for-each-ref"))

    def test_the_rejection_names_the_wrapper_form(self):
        stderr = self.assertRejected("fetch", "origin", "main:main").stderr
        self.assertIn("ccnavi-git.sh fetch <リモート> <ブランチ>", stderr)
        self.assertNotIn("git fetch", stderr.replace("ccnavi-git.sh", ""))
        # 手元の ref を進めるのは ccnavi-sync.sh（段階 2b で文面を書き換えた。3.1 の 10）。
        self.assertIn("ccnavi-sync.sh <ブランチ> が進めます", stderr)

    def test_remote_and_branch_still_pass(self):
        for args in (
            ("fetch", "origin"),
            ("fetch", "origin", self.branch),
            ("pull", "origin", self.branch),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class StoreRewindTest(GitWrapperTest):
    """承認済みチケットの置き場を過去の中身に戻す形を止める（ADR-0093 の段階 0）。

    `checkout <ref> <パス>` と `restore --source <ref>` は置き場を別のコミットの中身に戻し、
    `restore --ours / --theirs` は置き場の衝突を片側にそろえる。どれも承認が無かったことにも、
    取り下げた承認が戻ったことにもなる。置き場に当たらないパスは今までどおり通す。
    """

    COPY = ".ccnavi/approved/doing/i0001.md"
    REVIEW = "wip/proposals/review/i0001-01.md"

    def setUp(self):
        super().setUp()
        for rel, text in ((self.COPY, "old\n"), (self.REVIEW, "old\n")):
            path = os.path.join(self.dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        git(self.dir, "add", self.COPY, self.REVIEW)
        git(self.dir, "commit", "-q", "-m", "old")
        for rel in (self.COPY, self.REVIEW, "tracked.txt"):
            with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as f:
                f.write("new\n")
        git(self.dir, "commit", "-q", "-am", "new")

    def read(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as f:
            return f.read()

    def assertUntouched(self):
        self.assertEqual("new\n", self.read(self.COPY))
        self.assertEqual("new\n", self.read(self.REVIEW))

    def test_checkout_of_a_ref_onto_the_store_is_rejected(self):
        for args in (
            ("checkout", "HEAD~1", self.COPY),
            ("checkout", "HEAD~1", self.REVIEW),
            ("checkout", "HEAD~1", "tracked.txt", self.COPY),
            ("checkout", "HEAD~1", ".ccnavi"),
            ("checkout", "HEAD~1", ".ccnavi/approved/"),
            ("checkout", "HEAD~1", "wip"),
            ("checkout", "HEAD~1", "x/../" + self.COPY),
            ("checkout", "HEAD~1", ".CCNAVI/Approved/doing/i0001.md"),
            ("checkout", "HEAD~1", ".ccnavi\\approved\\doing\\i0001.md"),
            ("checkout", "HEAD~1", ".ccnavi/approved/*"),
            ("checkout", "HEAD~1", ":(glob).ccnavi/**"),
            ("checkout", "-p", "HEAD~1", self.COPY),
            ("checkout", "HEAD~1", "--pathspec-from-file=list.txt"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertUntouched()

    def test_restore_from_another_commit_onto_the_store_is_rejected(self):
        for args in (
            ("restore", "--source", "HEAD~1", self.COPY),
            ("restore", "--source=HEAD~1", "--", self.COPY),
            ("restore", "-s", "HEAD~1", self.COPY),
            ("restore", "-sHEAD~1", self.COPY),
            ("restore", "-Ws", "HEAD~1", self.COPY),
            ("restore", "-SWs", "HEAD~1", "--", self.REVIEW),
            ("restore", "--source", "HEAD~1", ".ccnavi"),
            ("restore", "--source", "HEAD~1", "--pathspec-from-file=list.txt"),
            ("restore", "--source", "HEAD~1", "wip/proposals/review/*"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertUntouched()

    def test_resolving_a_store_conflict_to_one_side_is_rejected(self):
        for args in (
            ("restore", "--ours", "--", self.COPY),
            ("restore", "--theirs", self.REVIEW),
        ):
            with self.subTest(args=args):
                stderr = self.assertRejected(*args).stderr
                self.assertIn("merge --abort", stderr)
                self.assertIn("ccnavi-git.sh", stderr)

    def test_paths_relative_to_a_subdirectory_are_resolved(self):
        cwd = os.path.join(self.dir, ".ccnavi")
        result = subprocess.run(
            [SHELL, SCRIPT, "checkout", "HEAD~1", "approved/doing/i0001.md"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("置き場", result.stderr)
        self.assertUntouched()

    def test_a_moved_store_is_followed(self):
        # 置き場の綴りを設定で動かしても、その綴りで止める。
        result = self.run_wrapper(
            "restore",
            "--source",
            "HEAD~1",
            "tickets/approved/doing/i0001.md",
            env={"CCNAVI_TICKETS_APPROVED": "tickets/approved"},
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)

    def test_forms_outside_the_store_still_pass(self):
        for args in (
            ("checkout", "HEAD~1", "tracked.txt"),
            ("restore", "--source", "HEAD~1", "tracked.txt"),
            ("restore", "--source", "HEAD~1", ".ccnavi-other/x.md"),
            ("restore", self.COPY),
            ("restore", "--staged", self.COPY),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertNotEqual(2, result.returncode, result.stdout + result.stderr)
        # 置き場の外は戻った（HEAD~1 の tracked.txt は見本を作ったときの中身）。
        self.assertTrue(self.read("tracked.txt").startswith("line 0\n"))

    def test_moving_between_branches_still_passes(self):
        # 行き先だけの形と、-b / -c の値と起点は、パスと読まない（-B / -C は段階 2b で止めた）。
        for args in (
            ("checkout", "-b", "topic", "HEAD~1"),
            ("switch", "--create", "topic2", "HEAD"),
            ("switch", "topic"),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


def run_in(cwd, *args):
    """ラッパースクリプトを cwd で動かす（ワークツリーの中から打つ形）。"""
    return subprocess.run(
        [SHELL, SCRIPT, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class WorktreeNameTest(GitWrapperTest):
    """worktree add は行き先の名前とブランチ名を揃える形だけ通す（ADR-0093 の 3.1 の 10。段階 2b）。

    親のブランチ名は親の識別子で、ワークツリーの名前も同じ。-B（既存のブランチの付け替え）・
    --detach・-f はその結び付きを崩すか、親のブランチを別のコミットへ向け直す。
    """

    def test_mismatched_and_forced_forms_are_rejected(self):
        git(self.dir, "branch", "other")
        for args in (
            ("worktree", "add", ".claude/worktrees/a", "-b", "b"),
            ("worktree", "add", ".claude/worktrees/a", "-b", "b", "HEAD"),
            ("worktree", "add", "-B", "a", ".claude/worktrees/a"),
            ("worktree", "add", "--detach", ".claude/worktrees/a", "HEAD"),
            ("worktree", "add", "-d", ".claude/worktrees/a", "HEAD"),
            ("worktree", "add", "-f", ".claude/worktrees/a", "-b", "a"),
            ("worktree", "add", "--force", ".claude/worktrees/a", "-b", "a"),
            # 2 つ目の語がブランチ名と違えば、名前の違う行き先に出すか detached になる。
            ("worktree", "add", ".claude/worktrees/a", "HEAD"),
            ("worktree", "add", ".claude/worktrees/a", "other"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".claude", "worktrees", "a")))

    def test_the_rejection_says_how_to_write_it(self):
        stderr = self.assertRejected("worktree", "add", ".claude/worktrees/a", "-b", "b").stderr
        self.assertIn("-b b", stderr)
        self.assertIn("worktree add .claude/worktrees/b -b b", stderr)

    def test_matching_forms_pass(self):
        git(self.dir, "branch", "c")
        for args, name in (
            (("worktree", "add", ".claude/worktrees/a", "-b", "a", "HEAD"), "a"),
            (("worktree", "add", ".claude/worktrees/b"), "b"),
            (("worktree", "add", ".claude/worktrees/c", "c"), "c"),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                tree = os.path.join(self.dir, ".claude", "worktrees", name)
                self.assertEqual(name, git_out(tree, "branch", "--show-current"))


class ParentWorktreeSwitchTest(GitWrapperTest):
    """親のワークツリーでは別のブランチへ移らない（ADR-0093 の 3.1 の 10。段階 2b）。

    親のワークツリーは .claude/worktrees/<P> で、親の写しか提案（`ticket: <P>`、`parent:` なし）が
    あるもの。親のブランチの名前は識別子で、ワークツリーが別のブランチの上に居ると家族を引けなくなる。
    """

    def setUp(self):
        super().setUp()
        self.base = git_out(self.dir, "branch", "--show-current")
        self.parent = os.path.join(self.dir, ".claude", "worktrees", "i0001")
        git(self.dir, "worktree", "add", "-q", self.parent, "-b", "i0001")
        write_text(
            os.path.join(self.parent, "wip", "proposals", "todo", "i0001.md"),
            "---\nversion: 1\nticket: i0001\n---\n",
        )
        self.free = os.path.join(self.dir, ".claude", "worktrees", "free")
        git(self.dir, "worktree", "add", "-q", self.free, "-b", "free")

    def test_moving_away_from_the_parent_branch_is_rejected(self):
        before = git_out(self.parent, "rev-parse", "--abbrev-ref", "HEAD")
        for args in (
            ("checkout", self.base),
            ("switch", self.base),
            ("checkout", "-b", "other"),
            ("switch", "--create", "other"),
            ("switch", "--create=other"),
            ("checkout", "--orphan", "other"),
            ("checkout", "--detach"),
            ("switch", "--detach"),
            ("switch", "-d", "HEAD"),
            ("checkout", "-"),
            ("checkout", "HEAD~0"),
        ):
            with self.subTest(args=args):
                result = run_in(self.parent, *args)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertIn("親のワークツリー", result.stderr)
                self.assertIn("識別子", result.stderr)
        self.assertEqual(before, git_out(self.parent, "rev-parse", "--abbrev-ref", "HEAD"))
        self.assertEqual([], logs_of(self.dir), "拒否したのに git が走って記録が残っている")

    def test_staying_on_the_parent_branch_passes(self):
        for args in (("checkout", "i0001"), ("checkout", "HEAD", "tracked.txt")):
            with self.subTest(args=args):
                result = run_in(self.parent, *args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_other_worktrees_move_as_before(self):
        result = run_in(self.free, "checkout", "-b", "elsewhere")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_a_child_worktree_is_not_a_parent(self):
        child = os.path.join(self.dir, ".claude", "worktrees", "i0001-01")
        git(self.dir, "worktree", "add", "-q", child, "-b", "i0001-01")
        write_text(
            os.path.join(child, ".ccnavi", "approved", "doing", "i0001-01.md"),
            "---\nversion: 1\nticket: i0001-01\nparent: i0001\nphase: 1\n---\n",
        )
        result = run_in(child, "checkout", "-b", "elsewhere")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class FamilyRecordPushTest(GitWrapperTest):
    """push が通ったら親のブランチの家族の控えを作り、控えが gone なら送らない（ADR-0093 の 4.3）。

    控えは ワークスペースルートの logs/state/sync/self/families/<P>（1 行 1 項目）。
    """

    def setUp(self):
        super().setUp()
        self.bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", self.bare)
        self.tree = os.path.join(self.dir, ".claude", "worktrees", "i0001")
        git(self.dir, "worktree", "add", "-q", self.tree, "-b", "i0001")
        self.copy = os.path.join(self.tree, ".ccnavi", "approved", "doing", "i0001.md")
        write_text(self.copy, "---\nversion: 1\nticket: i0001\n---\n")
        git(self.tree, "add", ".ccnavi/approved/doing/i0001.md")
        git(self.tree, "commit", "-q", "-m", "copy")
        self.record = os.path.join(self.dir, "logs", "state", "sync", "self", "families", "i0001")

    def push(self, tree=None, branch="i0001"):
        return run_in(tree or self.tree, "push", "-u", "origin", branch)

    def fields(self):
        with open(self.record, encoding="utf-8") as f:
            return dict(line.rstrip("\n").split(" ", 1) for line in f if " " in line)

    def remote_sha(self, branch="i0001"):
        return subprocess.run(
            ["git", "--git-dir", self.bare, "rev-parse", "--verify", "-q", branch],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_the_first_push_writes_a_present_record(self):
        result = self.push()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        fields = self.fields()
        self.assertEqual("present", fields["state"])
        self.assertEqual("i0001", fields["branch"])
        self.assertEqual("origin", fields["remote"])
        self.assertEqual(git_out(self.tree, "rev-parse", "HEAD"), fields["sha"])
        self.assertTrue(fields["fetched_at"].isdigit())
        self.assertIn("家族の控えを作った", result.stdout)
        self.assertIn("ccnavi-sync.sh i0001", result.stdout)

    def test_uncommitted_store_changes_keep_the_family_out(self):
        with open(self.copy, "a", encoding="utf-8") as f:
            f.write("書きかけ\n")
        result = self.push()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.record))
        self.assertIn("未コミットの状態", result.stdout)
        self.assertIn(".ccnavi/approved/doing/i0001.md", result.stdout)

    def test_a_present_record_follows_the_new_head(self):
        self.assertEqual(0, self.push().returncode)
        write_text(os.path.join(self.tree, "more.txt"), "x\n")
        git(self.tree, "add", "more.txt")
        git(self.tree, "commit", "-q", "-m", "more")
        result = self.push()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(git_out(self.tree, "rev-parse", "HEAD"), self.fields()["sha"])
        self.assertNotIn("家族の控えを作った", result.stdout)

    def test_a_gone_family_is_not_pushed(self):
        write_text(
            self.record,
            "remote origin\nbranch i0001\nsha abc\nfetched_at 1\nstate gone\nreason x\n",
        )
        result = self.push()
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("消えた", result.stderr)
        self.assertIn("ccnavi-sync.sh i0001", result.stderr)
        self.assertEqual("", self.remote_sha())
        self.assertEqual("gone", self.fields()["state"])

    def test_other_trees_get_no_record(self):
        free = os.path.join(self.dir, ".claude", "worktrees", "free")
        git(self.dir, "worktree", "add", "-q", free, "-b", "free")
        result = self.push(free, "free")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(
            os.path.exists(
                os.path.join(self.dir, "logs", "state", "sync", "self", "families", "free")
            )
        )


class AllowListTest(GitWrapperTest):
    """オプションは許可リストで読む（ADR-0093 の段階 2b のレビュー。利用者の決定 A）。

    git の parse-options は長いオプションの略（`--force-c` → `--force-create`）を受けるので、止める
    名前を並べるやり方では止められずに通る。束ねた短いオプション（`-qbnew`）は 1 字ずつ読み、
    値を取る字の後ろは値として扱う。
    """

    def setUp(self):
        super().setUp()
        self.base = git_out(self.dir, "branch", "--show-current")
        git(self.dir, "branch", "topic")
        write_text(os.path.join(self.dir, ".ccnavi", "approved", "doing", "i0001.md"), "old\n")
        git(self.dir, "add", ".ccnavi/approved/doing/i0001.md")
        git(self.dir, "commit", "-q", "-m", "store")

    def test_abbreviated_and_bundled_forms_are_rejected(self):
        store = ".ccnavi/approved/doing/i0001.md"
        before = git_out(self.dir, "for-each-ref")
        for args in (
            ("switch", "--force-c", "topic", "HEAD"),
            ("switch", "--force-cr=topic", "HEAD"),
            ("switch", "--discard-ch", "topic"),
            ("switch", "--for", "topic"),
            ("checkout", "--for", "topic"),
            ("checkout", "-qB", "topic", "HEAD"),
            ("checkout", "-fq", "topic"),
            ("branch", "--mov", "topic", "renamed"),
            ("branch", "--forc", "-d", "topic"),
            ("branch", "-rM", "a", "b"),
            ("pull", "--rebas", "origin", "main"),
            ("pull", "-r", "origin", "main"),
            ("pull", "-Xours", "origin", "main"),
            ("fetch", "--prun", "origin"),
            ("fetch", "-p", "origin"),
            ("merge", "--strategy-o=ours", "topic"),
            ("merge", "--strategy-option", "theirs", "topic"),
            ("merge", "-Xours", "topic"),
            ("merge", "-sours", "topic"),
            ("merge", "--no-verif", "topic"),
            ("commit", "--no-verif", "-m", "x"),
            ("commit", "-anm", "x"),
            ("commit", "--amend", "-m", "x"),
            ("commit", "--amen", "-m", "x"),
            ("rm", "--forc", "tracked.txt"),
            ("rm", "-rf", "tracked.txt"),
            ("worktree", "remove", "--forc", "x"),
            ("worktree", "remove", "-ff", "x"),
            ("restore", "--sour=HEAD~1", store),
            ("restore", "-sHEAD~1", store),
            ("restore", "-Ss", "HEAD~1", store),
            ("cat-file", "--textc", "HEAD:tracked.txt"),
            ("cat-file", "--filters", "HEAD:tracked.txt"),
            ("grep", "-O", "line"),
            ("grep", "-iOvim", "line"),
            ("grep", "--open-f", "line"),
            ("log", "--outp=out.txt"),
            ("diff", "--ext"),
            ("show", "--textc"),
            ("fetch", "--upload-p=touch x", "origin"),
            ("checkout", "--unknown-option"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertEqual(before, git_out(self.dir, "for-each-ref"))

    def test_plain_forms_still_pass(self):
        for args in (
            ("checkout", "-b", "new1"),
            ("checkout", "-qbnew2", self.base),
            ("switch", "-cnew3"),
            ("switch", "--create", "new4", self.base),
            ("checkout", self.base),
            ("branch", "--list"),
            ("branch", "-vv"),
            ("branch", "-d", "new1"),
            ("commit", "--allow-empty", "-qm", "x"),
            ("cat-file", "-p", "HEAD:tracked.txt"),
            ("grep", "-e", "line 1", "--", "tracked.txt"),
            ("grep", "-C3", "line 5", "--", "tracked.txt"),
            ("diff", "--text", "HEAD"),
            ("restore", "--staged", "tracked.txt"),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_the_refusal_says_to_spell_it_out(self):
        stderr = self.assertRejected("switch", "--force-c", "topic").stderr
        self.assertIn("略さずに", stderr)


class ParentWorktreeValueBundleTest(ParentWorktreeSwitchTest):
    """親のワークツリーでは、値を束ねた綴り（`-bnew`・`-qbnew`・`--create=`）でも移れない。"""

    def test_moving_away_from_the_parent_branch_is_rejected(self):
        for args in (
            ("checkout", "-bnew1"),
            ("checkout", "-qbnew3"),
            ("switch", "-cbar"),
            ("switch", "-qcbar"),
            ("checkout", "--orphan=x"),
            ("checkout", "--track", "origin/x"),
            ("checkout", f"refs/heads/{self.base}"),
            ("switch", "--det"),
            ("checkout", "--orph", "x"),
        ):
            with self.subTest(args=args):
                result = run_in(self.parent, *args)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("i0001", git_out(self.parent, "branch", "--show-current"))

    def test_staying_on_the_parent_branch_passes(self):
        super().test_staying_on_the_parent_branch_passes()
        for args in (("checkout",), ("checkout", "-q", "HEAD")):
            with self.subTest(args=args):
                result = run_in(self.parent, *args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_other_worktrees_move_as_before(self):
        result = run_in(self.free, "checkout", "-qbelsewhere")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_a_child_worktree_is_not_a_parent(self):
        pass


class WorktreeDetachTest(GitWrapperTest):
    """2 つ目の語がタグ・sha だと detached になる。名前が揃っていても止める（軽 17）。"""

    def test_tags_and_shas_are_rejected(self):
        sha = git_out(self.dir, "rev-parse", "HEAD")
        git(self.dir, "tag", "v1")
        for args in (
            ("worktree", "add", ".claude/worktrees/v1", "v1"),
            ("worktree", "add", f".claude/worktrees/{sha}", sha),
        ):
            with self.subTest(args=args):
                result = self.assertRejected(*args)
                self.assertIn("detached", result.stderr)

    def test_a_remote_branch_of_the_same_name_passes(self):
        bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", bare)
        git(self.dir, "push", "-q", "origin", "HEAD:refs/heads/i0002")
        git(self.dir, "fetch", "-q", "origin")
        result = self.run_wrapper("worktree", "add", ".claude/worktrees/i0002", "i0002")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


class SymlinkedWorkspaceTest(GitWrapperTest):
    """リンクを経た作業場でも守りが有効（git の綴りとワークスペースの綴りを揃える。中 12）。"""

    def setUp(self):
        super().setUp()
        self.link = self.dir + "-link"
        try:
            os.symlink(self.dir, self.link)
        except (OSError, NotImplementedError):
            self.skipTest("リンクを作れない")
        self.addCleanup(os.unlink, self.link)
        self.bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", self.bare)
        tree = os.path.join(self.dir, ".claude", "worktrees", "i0001")
        git(self.dir, "worktree", "add", "-q", tree, "-b", "i0001")
        write_text(
            os.path.join(tree, ".ccnavi", "approved", "doing", "i0001.md"),
            "---\nversion: 1\nticket: i0001\n---\n",
        )
        git(tree, "add", ".ccnavi/approved/doing/i0001.md")
        git(tree, "commit", "-q", "-m", "copy")
        self.via_link = os.path.join(self.link, ".claude", "worktrees", "i0001")

    def test_the_parent_worktree_is_seen_through_the_link(self):
        result = run_in(self.via_link, "checkout", "-b", "other")
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("親のワークツリー", result.stderr)

    def test_the_family_record_is_written_through_the_link(self):
        result = run_in(self.via_link, "push", "-u", "origin", "i0001")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("家族の控えを作った", result.stdout)


class PushRemoteResolutionTest(FamilyRecordPushTest):
    """送り先は git と同じ順で解き、origin 以外へ送ったら控えを作らない（軽 18）。"""

    def test_a_push_remote_other_than_origin_writes_no_record(self):
        other = self.make_bare()
        git(self.dir, "remote", "add", "other", other)
        git(self.tree, "config", "branch.i0001.pushRemote", "other")
        result = run_in(self.tree, "push")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.record))

    def test_push_default_to_origin_writes_a_record(self):
        git(self.tree, "config", "remote.pushDefault", "origin")
        git(self.tree, "config", "push.default", "current")
        result = run_in(self.tree, "push")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("present", self.fields()["state"])


class SafeAdditionsTest(GitWrapperTest):
    """許可リストに足した、履歴を書き換えない形（ADR-0093 の段階 2b のレビューの相談 2）。

    足したもの: commit --fixup・--squash、fetch と pull の --depth・--deepen・--shallow-since・
    --unshallow、merge と pull の --autostash・--no-autostash、fetch の --show-forced-updates・
    --no-show-forced-updates・--write-fetch-head・--no-write-fetch-head。
    拒否されない（終了コード 2 にならない）ことを見る。git 自身が断る形（完全な clone への
    --unshallow など）は 1 で終わってよい。
    """

    def setUp(self):
        super().setUp()
        self.bare = self.make_bare()
        git(self.dir, "remote", "add", "origin", self.bare)
        self.branch = git_out(self.dir, "branch", "--show-current")
        git(self.dir, "push", "-q", "origin", self.branch)

    def test_added_forms_are_not_rejected(self):
        head = git_out(self.dir, "rev-parse", "HEAD")
        b = self.branch
        for args in (
            ("commit", "--allow-empty", f"--fixup={head}"),
            ("commit", "--allow-empty", "--fixup", head),
            ("commit", "--allow-empty", f"--squash={head}", "-m", "x"),
            ("fetch", "--depth=1", "origin", b),
            ("fetch", "--depth", "1", "origin", b),
            ("fetch", "--deepen=1", "origin", b),
            ("fetch", "--shallow-since=2000-01-01", "origin", b),
            ("fetch", "--unshallow", "origin", b),
            ("fetch", "--show-forced-updates", "origin", b),
            ("fetch", "--no-show-forced-updates", "origin", b),
            ("fetch", "--no-write-fetch-head", "origin", b),
            ("fetch", "--write-fetch-head", "origin", b),
            ("pull", "--depth=1", "origin", b),
            ("pull", "--autostash", "origin", b),
            ("pull", "--no-autostash", "origin", b),
            ("merge", "--autostash", "origin/" + b),
            ("merge", "--no-autostash", "origin/" + b),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertNotEqual(2, result.returncode, result.stdout + result.stderr)

    def test_risky_neighbours_stay_rejected(self):
        for args in (
            ("fetch", "--prune", "origin"),
            ("fetch", "--force", "origin"),
            ("fetch", "--refmap=x", "origin"),
            ("fetch", "--depth=1", "origin", "+main:main"),
            ("pull", "--rebase", "origin", self.branch),
            ("pull", "--rebase=merges", "origin", self.branch),
            ("commit", "--amend", "-m", "x"),
            ("commit", "--fixu=x"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)


class TagListOnlyTest(GitWrapperTest):
    """tag は一覧だけ。位置の引数は -l / --list のときの絞り込みだけ（相談 1）。"""

    def test_creating_a_tag_is_rejected(self):
        for args in (
            ("tag", "v1"),
            ("tag", "v1", "HEAD"),
            ("tag", "--contains", "HEAD", "v1"),
            ("tag", "-n", "v1"),
        ):
            with self.subTest(args=args):
                self.assertRejected(*args)
        self.assertEqual("", git_out(self.dir, "tag", "--list"))

    def test_listing_still_passes(self):
        git(self.dir, "tag", "v0")
        for args in (
            ("tag",),
            ("tag", "-l"),
            ("tag", "-l", "v*"),
            ("tag", "--list", "v*"),
            ("tag", "--contains", "HEAD"),
            ("tag", "--points-at", "HEAD"),
            ("tag", "--merged=HEAD"),
            ("tag", "-n", "-l", "v*"),
        ):
            with self.subTest(args=args):
                result = self.run_wrapper(*args)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
