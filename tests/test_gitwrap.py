"""git ラッパの受入テスト。外からスクリプトを動かす。

読み返すのは標準出力・標準エラー・終了コードと、logs/ に残った記録だけ。
中の関数も変数も見ないので、書き方が変わってもテストは真であり続ける。

使い捨ての git リポジトリを毎回作る。このリポジトリ自身で走らせると、
テストが「コードのこと」ではなく「走った機械の作業ツリーのこと」を報告する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".claude", "scripts", "ccnavi-git.sh")
SHELL = shutil.which("sh") or shutil.which("bash")


def git(cwd, *args):
    """素の git。ラッパを通さずに見本のリポジトリを組み立てるために使う。"""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(cwd):
    """コミットが 1 件あり、追跡外のファイルが 1 件ある使い捨てのリポジトリ。"""
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
    names = sorted(os.listdir(directory), reverse=True)
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

    def assertRejected(self, *args):
        """拒否は終了コード 2 で、git を 1 度も動かさない。"""
        result = self.run_wrapper(*args)
        self.assertEqual(2, result.returncode, f"stdout={result.stdout!r}")
        self.assertIn("ccnavi-git:", result.stderr)
        self.assertEqual("", result.stdout)
        self.assertEqual([], logs_of(self.dir), "拒否したのに git が走って記録が残っている")
        return result


class RejectTest(GitWrapperTest):
    def test_push_is_rejected_and_names_the_user(self):
        result = self.assertRejected("push", "origin", "main")
        self.assertIn("利用者", result.stderr)

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

    def test_the_alternative_it_names_is_not_a_denied_form(self):
        # 生の git は PreToolUse で止まる。案内が `git stash push -u` と書くと、
        # 案内された先でもう 1 度拒否される。代わりの手段が拒否される案内は、
        # 案内が無いのとほとんど同じ。名乗るならラッパの形で名乗る。
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
        # 短いオプションの束 (-rd) を 1 文字ずつ見る判定の巻き添え。
        result = self.run_wrapper("branch", "--contains=feature/dev")
        self.assertNotEqual(2, result.returncode, result.stderr)


class PassTest(GitWrapperTest):
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

    def test_checkout_moves_between_branches(self):
        result = self.run_wrapper("checkout", "-b", "topic")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        head = self.run_wrapper("rev-parse", "--abbrev-ref", "HEAD")
        self.assertIn("topic", head.stdout)


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
    # 引数で `-c` を弾いても、環境変数から同じ設定を差し込めるなら穴は開いたまま。
    INJECT = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "status.showUntrackedFiles",
        "GIT_CONFIG_VALUE_0": "no",
    }

    def test_the_injection_works_on_plain_git(self):
        """見本が効くことを先に確かめる。効かない見本では次のテストが空振りする。"""
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


if __name__ == "__main__":
    unittest.main()
