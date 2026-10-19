"""ccnavi-user-push-approved.sh の受入テスト（前の名前は ccnavi-push-approved.sh）。
使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

設計 wip/design/approve-carry.md 1 と 6.3。確かめるのは次のとおり。

17. 置き場（`.ccnavi/approved`）の変更だけをコミットし、同じツリーの他の未コミットはコミットしない
18. コミットするものが無ければ 0 で `コミットして push する承認済みチケットは無い。`
19. `main` の上のツリーはコミットして push しない（0、標準エラーにブランチ名）
20. push が失敗すると 1、コミットは残る
21. detached のツリーは飛ばす
22. `ccnavi-user-agree.sh` が承認のあとコミットして push する
23. `ccnavi-user-agree.sh` は並べた識別子を `user agree` の後ろに渡し、`-` で始まる語と
    空の語は断る

ワークスペースは一時ディレクトリに git と bare のリモートで作る。承認そのものは
ccnavi の実行ファイルの代わりに、承認済みチケットを 1 枚置くだけの sh（stub）で済ませる。
承認の中身は他のテストが見ていて、ここで見るのは sh のつなぎ方だけ。

読み返すのは終了コード・出力と、git に残ったものだけ。

`CCNAVI_SH_DIR` で、コピーする sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from ccnavi.entry import version
from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
PUSH_SCRIPTS = ("ccnavi-user-push-approved.sh", *common_sh(SH_DIR))
APPROVE_SCRIPTS = (*PUSH_SCRIPTS, "ccnavi-user-agree.sh")
APPROVED = ".ccnavi/approved/doing"
MESSAGE = "ccnavi: 承認済みチケットを更新"
NOTHING = "コミットして push する承認済みチケットはありません。"

# 承認の代わり。STUB_ARGS があれば受けた引数を 1 行ずつ書き、STUB_EXIT が 0 でなければ失敗し、
# STUB_TREE があればそこに承認済みチケットを置く。
STUB = """#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\\nfamily i0001\\ntarget no\\n'; exit 0 ;;
esac
[ -z "${STUB_ARGS:-}" ] || printf '%s\\n' "$@" > "$STUB_ARGS"
# 実行ファイルは書き込みに入ると、書けたパスの一覧を --placed-out のファイルへ書く。
# --record-writes のファイルには、書いたパス（ルートからの相対）を書く（書かなければ空）。
record=""
while [ $# -gt 0 ]; do
	if [ "$1" = --placed-out ] && [ -n "${STUB_REPORT:-}" ]; then
		printf '%s\\n' "$STUB_REPORT" > "$2"
	fi
	if [ "$1" = --record-writes ]; then
		record="$2"
		: > "$record"
	fi
	shift
done
[ "${STUB_EXIT:-0}" = 0 ] || exit "$STUB_EXIT"
[ -n "${STUB_TREE:-}" ] || exit 0
mkdir -p "$STUB_TREE/.ccnavi/approved/doing"
printf 'approved\\n' > "$STUB_TREE/.ccnavi/approved/doing/i0001.md"
[ -z "$record" ] || printf '%s/.ccnavi/approved/doing/i0001.md\\n' \\
	"${STUB_REL:-.claude/worktrees/i0001}" > "$record"
exit "${STUB_AFTER_EXIT:-0}"
"""


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args, check=True):
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done


def posix(path):
    return path.replace(os.sep, "/")


class Workspace(unittest.TestCase):
    """使い捨てのワークスペースと bare のリモート。テストは持たない（下の 2 つが継いで使う）。"""

    scripts = PUSH_SCRIPTS

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in self.scripts:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        git(self._tmp.name, "init", "-q", "-b", "main", self.ws)
        for key, value in (
            ("user.email", "t@example.invalid"),
            ("user.name", "t"),
            ("commit.gpgsign", "false"),
        ):
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), ".claude/worktrees/\n")
        write(os.path.join(self.ws, "README.md"), "seed\n")
        git(self.ws, "add", "--", ".gitignore", "README.md", ".ccnavi/scripts")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(self._tmp.name, "origin.git")
        git(self._tmp.name, "init", "-q", "--bare", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        # write-and-push の対象かを答えるだけの実行ファイルの代役（いつも対象外。
        # ここで見るのは対象外の道）
        self.write_and_push_stub = write(
            os.path.join(self._tmp.name, "write-and-push-bin", "ccnavi"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f"*\" --version \"*) printf 'compat: {version.COMPAT}\\n'; exit 0 ;;\n"
            '*" write-and-push family "*) printf \'write-and-push 1\\nfamily i0001\\ntarget '
            "no\\n'; exit 0 ;;\n"
            # 親か・承認済みか（push の関門が聞く）。STUB_PARENT があるときだけ答える。
            # STUB_PARENT=old は、新しい行（approved など）を知らない古い実行ファイル。
            '*" tree parent "*)\n'
            '  [ -n "${STUB_PARENT:-}" ] || exit 0\n'
            "  [ \"$STUB_PARENT\" = no ] && { printf 'tree 1\\nparent no\\n'; exit 0; }\n"
            "  printf 'tree 1\\nparent yes\\n'\n"
            '  [ "$STUB_PARENT" = old ] && exit 0\n'
            "  printf 'approved %s\\nrequested %s\\narchived %s\\nstarted %s\\nmr -\\n' "
            '"${STUB_APPROVED:-no}" "${STUB_REQUESTED:-no}" "${STUB_ARCHIVED:-no}" '
            '"${STUB_STARTED:-no}"\n'
            "  exit 0 ;;\n"
            "esac\nexit 0\n",
        )
        os.chmod(
            self.write_and_push_stub,
            os.stat(self.write_and_push_stub).st_mode | stat.S_IXUSR | stat.S_IXGRP,
        )

    # ---- 道具

    def worktree(self, name, detach=False):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        if detach:
            git(self.ws, "worktree", "add", "-q", "--detach", path, "main")
        else:
            git(self.ws, "worktree", "add", "-q", path, "-b", name, "main")
        return path

    def place(self, tree, ticket="i0001"):
        return write(os.path.join(tree, *APPROVED.split("/"), ticket + ".md"), "approved\n")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env["CCNAVI_BIN_PATH"] = posix(self.write_and_push_stub)
        env.update(extra)
        return env

    def run_sh(self, name, *args, cwd=None, env=None):
        script = posix(os.path.join(self.ws, ".ccnavi", "scripts", name))
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env or self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def push(self, *args, cwd=None, env=None):
        return self.run_sh("ccnavi-user-push-approved.sh", *args, cwd=cwd, env=env)

    def repository(self, path, branch):
        """ワークスペースの下に別のリポジトリを置く。bare のリモートを付けて返す。"""
        git(self._tmp.name, "init", "-q", "-b", "main", path)
        for key, value in (
            ("user.email", "t@example.invalid"),
            ("user.name", "t"),
            ("commit.gpgsign", "false"),
        ):
            git(path, "config", key, value)
        write(os.path.join(path, "README.md"), "project\n")
        git(path, "add", "--", "README.md")
        git(path, "commit", "-q", "-m", "seed")
        git(path, "checkout", "-q", "-b", branch)
        remote = os.path.join(self._tmp.name, os.path.basename(path) + ".git")
        git(self._tmp.name, "init", "-q", "--bare", remote)
        git(path, "remote", "add", "origin", remote)
        return remote

    def head_of(self, remote, branch):
        done = git(
            self._tmp.name,
            "--git-dir",
            remote,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/heads/{branch}",
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    def staged(self, tree):
        return git(tree, "diff", "--cached", "--name-only").stdout.strip()

    def head(self, tree):
        return git(tree, "rev-parse", "HEAD").stdout.strip()

    def subject(self, tree):
        return git(tree, "log", "-1", "--format=%s").stdout.strip()

    def committed(self, tree):
        out = git(tree, "show", "--name-only", "--format=", "HEAD").stdout
        return sorted(line for line in out.splitlines() if line)

    def dirty(self, tree, *paths):
        return git(tree, "status", "--porcelain", "--", *paths).stdout.strip()

    def remote_head(self, branch):
        done = git(
            self._tmp.name,
            "--git-dir",
            self.remote,
            "rev-parse",
            "--verify",
            "-q",
            f"refs/heads/{branch}",
            check=False,
        )
        return done.stdout.strip() if done.returncode == 0 else ""


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class PushApprovedTest(Workspace):
    # ---- 17. 置き場だけをコミットする

    def test_commits_only_the_approved_place_and_pushes(self):
        tree = self.worktree("i0001")
        self.place(tree)
        # 同じツリーの書きかけ。追跡しているファイルの変更と、未追跡のファイル。
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        write(os.path.join(tree, "src", "draft.py"), "x\n")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertTrue(self.dirty(tree, "README.md"))
        self.assertTrue(self.dirty(tree, "src"))
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertIn("i0001", result.stdout)
        self.assertNotIn(NOTHING, result.stdout)

    def test_does_not_carry_changes_someone_else_staged(self):
        """先にステージされていた他人の変更もコミットしない。パスを限るのはコミットまで。"""
        tree = self.worktree("i0001")
        self.place(tree)
        write(os.path.join(tree, "README.md"), "ステージ済みの書きかけ\n")
        git(tree, "add", "--", "README.md")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertTrue(self.dirty(tree, "README.md"))

    def test_leftover_flow_temp_files_are_not_carried(self):
        """ボードの保存が残した `flows/.<名前>.<番号>.tmp` はコミットせず、
        フローはコミットする（L-e）。
        """
        tree = self.worktree("i0001")
        flows = os.path.join(tree, ".ccnavi", "approved", "flows")
        write(os.path.join(flows, "i0001-01-01.yml"), "nodes: []\n")
        temp = write(os.path.join(flows, ".i0001-01-01.yml.123.abcdef.tmp"), "half")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), [".ccnavi/approved/flows/i0001-01-01.yml"])
        self.assertTrue(os.path.exists(temp))
        self.assertEqual(self.staged(tree), "")
        # 一時ファイルだけが残っていても、コミットするものは無い。
        again = self.push()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn(NOTHING, again.stdout)

    def test_a_removed_draft_is_carried_with_the_flow(self):
        """取り込んで消えた下書き（`wip/proposals/flows/`）は、
        フローと一緒にコミットして push する。
        書き直された下書き・追跡していない下書きには触れない。"""
        tree = self.worktree("i0001")
        drafts = "wip/proposals/flows"
        taken = f"{drafts}/i0001-01-01.yml"
        rewritten = f"{drafts}/i0001-01-02.yml"
        untracked = f"{drafts}/i0001-01-03.yml"
        for rel in (taken, rewritten):
            write(os.path.join(tree, *rel.split("/")), "nodes: []\n")
        git(tree, "add", "--", taken, rewritten)
        git(tree, "commit", "-q", "-m", "drafts")
        flow = ".ccnavi/approved/flows/i0001-01-01.yml"
        write(os.path.join(tree, *flow.split("/")), "nodes: []\n")
        os.remove(os.path.join(tree, *taken.split("/")))
        write(os.path.join(tree, *rewritten.split("/")), "nodes: [x]\n")
        write(os.path.join(tree, *untracked.split("/")), "nodes: []\n")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 同じ中身なので、改名として読まずに 2 つのパスで見る。
        out = git(tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow, taken]), result.stdout + result.stderr)
        self.assertTrue(self.dirty(tree, rewritten))
        self.assertTrue(self.dirty(tree, untracked))
        self.assertEqual(self.dirty(tree, taken), "")
        self.assertEqual(self.staged(tree), "")
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_drafts_alone_are_not_carried(self):
        """フローの保存が無ければコミットしない（置き場の変更が無いツリーには入らない）。"""
        tree = self.worktree("i0001")
        taken = "wip/proposals/flows/i0001-01-01.yml"
        write(os.path.join(tree, *taken.split("/")), "nodes: []\n")
        git(tree, "add", "--", taken)
        git(tree, "commit", "-q", "-m", "draft")
        os.remove(os.path.join(tree, *taken.split("/")))
        before = self.head(tree)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)
        self.assertEqual(self.head(tree), before)

    # ---- 18. コミットするものが無い

    def test_nothing_to_carry_says_so(self):
        tree = self.worktree("i0001")
        # 置き場の外の書きかけはコミットするものに数えない。
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        before = self.head(tree)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)
        self.assertEqual(self.head(tree), before)
        self.assertEqual(self.remote_head("i0001"), "")

    # ---- 19. 保護されたブランチ

    def test_main_is_committed_but_not_pushed(self):
        self.place(self.ws)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(self.ws), MESSAGE)
        self.assertEqual(self.committed(self.ws), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("main"), "")
        self.assertIn("main", result.stderr)

    # 固定のリストに無い名前の統合先。決め方は ccnavi_integration。

    def assertNotPushed(self, result, tree, branch):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head(branch), "")
        self.assertIn(f"統合先 {branch}", result.stderr)

    def test_the_branch_origin_head_points_to_is_committed_but_not_pushed(self):
        tree = self.worktree("develop-v1.0.0")
        ref = "refs/remotes/origin/develop-v1.0.0"
        git(self.ws, "update-ref", ref, "HEAD")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
        self.place(tree)
        self.assertNotPushed(self.push(), tree, "develop-v1.0.0")

    def test_an_undetermined_integration_keeps_the_fixed_list_only(self):
        """統合先が決まらない（origin/HEAD も origin/main・master も無い）なら今までどおり送る。"""
        tree = self.worktree("develop-v1.0.0")
        self.place(tree)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("develop-v1.0.0"), self.head(tree))

    def test_a_project_is_judged_by_its_own_integration(self):
        """projects/<名前>/ のツリーは、そのプロジェクトの origin/HEAD で統合先を決める。
        ワークスペースの origin/HEAD はプロジェクトの統合先を変えない（固定のリストに無い名前
        develop-v1.0.0 で止まる）。"""
        project = os.path.join(self.ws, "projects", "app")
        remote = self.repository(project, "develop-v1.0.0")
        self.place(project)
        # ワークスペース自身の origin/HEAD が同じ名前でも、プロジェクトの統合先は別（origin も空）。
        ref = "refs/remotes/origin/develop-v1.0.0"
        git(self.ws, "update-ref", ref, "HEAD")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.head_of(remote, "develop-v1.0.0"), self.head(project))

        # プロジェクト側の origin/HEAD が develop-v1.0.0 なら、そのプロジェクトでは止まる。
        write(os.path.join(project, *APPROVED.split("/"), "i0002.md"), "approved\n")
        before = self.head_of(remote, "develop-v1.0.0")
        git(project, "update-ref", ref, "HEAD")
        git(project, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("統合先 develop-v1.0.0", result.stderr)
        self.assertEqual(self.head_of(remote, "develop-v1.0.0"), before)
        self.assertNotEqual(self.head(project), before)

    # ---- 20. push が失敗する

    def test_failed_push_exits_1_and_keeps_the_commit(self):
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        tree = self.worktree("i0001")
        self.place(tree)
        result = self.push()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.dirty(tree, APPROVED), "")
        self.assertIn("i0001", result.stderr)

    # ---- 親のブランチの push の関門（ccnavi_push_gate）。
    # 実行ファイルの代役が `tree parent` に答える（STUB_PARENT）。答えなければ参照だけで決める。

    def sent_parent(self, name="i0001", started="yes", **extra):
        """送ったことのある親のワークツリー。ホストがブランチを消した（参照だけが残る）形にする。

        返すのは、ツリーと、関門に聞かれる実行ファイルの答えを入れた環境。
        承認済みの親チケットは置いてある（STUB_APPROVED=yes）。着手済み（STUB_STARTED）が形跡になる。
        """
        tree = self.worktree(name)
        git(tree, "push", "-q", "-u", "origin", name)
        git(self._tmp.name, "--git-dir", self.remote, "update-ref", "-d", f"refs/heads/{name}")
        self.place(tree)
        env = self.env(STUB_PARENT="1", STUB_APPROVED="yes", STUB_STARTED=started, **extra)
        return tree, env

    def test_a_parent_gone_from_the_remote_is_committed_but_not_pushed(self):
        tree, env = self.sent_parent()
        result = self.push(env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0001"), "")
        self.assertIn("親のブランチ i0001 がリモートに無い", result.stderr)
        self.assertIn("ccnavi-sync.sh i0001", result.stderr)
        self.assertIn("コミットはローカルに残っている", result.stderr)
        # ユーザが端末で元の名前に戻せば、打ち直して送れる。
        git(tree, "push", "-q", "origin", "i0001")
        write(os.path.join(tree, *APPROVED.split("/"), "i0002.md"), "approved\n")
        result = self.push(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_an_approved_parent_without_a_reference_is_still_guarded(self):
        # fetch.prune で参照が消えても、着手済みの親チケットがあれば、作り直さない。
        tree, env = self.sent_parent()
        git(self.ws, "update-ref", "-d", "refs/remotes/origin/i0001")
        result = self.push(env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), "")
        self.assertIn("リモートに無い", result.stderr)

    def test_a_request_marker_or_an_archive_also_counts_as_having_been_sent(self):
        for key in ("STUB_REQUESTED", "STUB_ARCHIVED"):
            with self.subTest(key=key):
                tree, env = self.sent_parent(name=f"i0001-{key[5:9].lower()}", started="no")
                git(self.ws, "update-ref", "-d", f"refs/remotes/origin/{os.path.basename(tree)}")
                env[key] = "yes"
                result = self.push(env=env)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("リモートに無い", result.stderr)

    def test_an_approved_parent_never_started_passes_as_the_first_push(self):
        # 直接作業で提案を書き、同名のワークツリーで承認して、最初に打つ承認の push。
        # 承認済みというだけでは形跡にしない。
        tree = self.worktree("i0001")
        self.place(tree)
        env = self.env(STUB_PARENT="1", STUB_APPROVED="yes", STUB_STARTED="no")
        result = self.push(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_the_first_push_of_a_parent_never_sent_passes(self):
        # 形跡が無く、承認済みの親チケットも依頼も退避も無い。最初の push として通す。
        tree = self.worktree("i0001")
        self.place(tree)
        env = self.env(STUB_PARENT="1", STUB_APPROVED="no")
        result = self.push(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_an_old_executable_without_the_new_rows_is_judged_by_the_reference_alone(self):
        tree = self.worktree("i0001")
        self.place(tree)
        env = self.env(STUB_PARENT="old")
        result = self.push(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_a_remote_that_cannot_be_asked_is_refused(self):
        tree, env = self.sent_parent()
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        result = self.push(env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("リモートにあるかを確かめられない", result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)

    def test_a_tree_that_is_not_a_parent_is_not_gated(self):
        # 実行ファイルが親でないと答えれば、参照が残っていてもリモートに無い名前を送る。
        tree, env = self.sent_parent()
        env["STUB_PARENT"] = "no"
        result = self.push(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    # ---- 21. detached

    def test_detached_tree_is_skipped_and_others_are_carried(self):
        loose = self.worktree("loose", detach=True)
        self.place(loose)
        before = self.head(loose)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")

        result = self.push()
        # 飛ばしたツリーがあるときの終了コードは設計（1.3）で決まっていないので見ない。
        self.assertIn("loose", result.stderr)
        self.assertEqual(self.head(loose), before)
        self.assertTrue(self.dirty(loose, APPROVED))
        # 飛ばしても、他のツリーはコミットして push する。
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    # ---- チケット approve-carry-04 の 11〜14

    def test_carries_a_tree_under_projects(self):
        """11. `projects/<名前>/` の下のツリーの置き場も、コミットして push する。"""
        project = os.path.join(self.ws, "projects", "app")
        remote = self.repository(project, "work")
        self.place(project)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(project), MESSAGE)
        self.assertEqual(self.committed(project), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.head_of(remote, "work"), self.head(project))
        self.assertNotIn(NOTHING, result.stdout)

    def test_does_not_read_the_place_variables(self):
        """12（改）. 置き場を動かす環境変数は読まない。既定の置き場だけをコミットして push する
        （置き場は固定。A9）。

        `CCNAVI_PROJECTS` / `CCNAVI_TICKETS_APPROVED` / `CCNAVI_TICKETS_PROPOSAL` を既定と違う
        値で入れても、`projects/` の下と既定の置き場（`.ccnavi/approved`）をコミットして
        push し、既定の提案の置き場（`wip/proposals/todo`）で消えた提案の削除を
        同じコミットに入れる。
        env が指した置き場はコミットしない。以前は 12（`CCNAVI_TICKETS_APPROVED` で置き場が動く）と
        パスの正規化の 2 本（`CCNAVI_PROJECTS=/`・`CCNAVI_TICKETS_APPROVED=.`）が見ていた。

        3 つのどれか 1 つでも読まれれば、下の確かめのどれかが失敗する。

        - `CCNAVI_PROJECTS`: `projects/app` がコミットされず、env が指す `x/stray` がコミットされる
        - `CCNAVI_TICKETS_APPROVED`: 既定の置き場の代わりに `approved/tickets` がコミットされる
        - `CCNAVI_TICKETS_PROPOSAL`: `wip/proposals/todo` の削除がコミットされず、env が指す
          `elsewhere/proposals/todo` の削除がコミットされる

        `.ccnavi/scripts/` が写す版（i0064-04 の `wip/design/scripts/`）になる前は失敗する。
        写す前の sh は 3 つの値を読むため。
        """
        project = os.path.join(self.ws, "projects", "app")
        remote = self.repository(project, "work")
        self.place(project)
        tree = self.worktree("i0001")
        # 承認で todo/ から動いた提案の形。既定の置き場と env が指す置き場に 1 枚ずつ
        # 追跡させてから消す。運ばれてよいのは既定の側の削除だけ。
        kept = "elsewhere/proposals/todo/x.md"
        gone = "wip/proposals/todo/x.md"
        for rel in (gone, kept):
            write(os.path.join(tree, *rel.split("/")), "proposal\n")
        git(tree, "add", "--", gone, kept)
        git(tree, "commit", "-q", "-m", "proposals")
        for rel in (gone, kept):
            os.remove(os.path.join(tree, *rel.split("/")))
        self.place(tree)
        # env が指す承認済みチケットの置き場。読まれたときにだけコミットされる。
        other = "approved/tickets"
        write(os.path.join(tree, *other.split("/"), "i0009.md"), "approved\n")
        # env が指すプロジェクトの置き場（`/x` は末尾の / を除くとルートの下の `x`）。
        # 読まれたときにだけ、その下のリポジトリが運ばれる。
        stray = os.path.join(self.ws, "x", "stray")
        stray_remote = self.repository(stray, "work")
        self.place(stray)
        stray_before = self.head(stray)

        result = self.push(
            env=self.env(
                CCNAVI_PROJECTS="/x",
                CCNAVI_TICKETS_APPROVED=other,
                CCNAVI_TICKETS_PROPOSAL="elsewhere/proposals",
            )
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # `projects/` の下のプロジェクトと、切ったワークツリーの既定の置き場を運ぶ。
        self.assertEqual(self.subject(project), MESSAGE, result.stderr)
        self.assertEqual(self.committed(project), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.head_of(remote, "work"), self.head(project))
        # ワークツリーは既定の置き場と、既定の提案の置き場で消えた提案を 1 つのコミットで運ぶ。
        self.assertEqual(self.subject(tree), MESSAGE, result.stderr)
        self.assertEqual(self.committed(tree), sorted([f"{APPROVED}/i0001.md", gone]))
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        # env が指した置き場は運ばない。承認済みチケットの置き場も、消えた提案も、
        # その下のリポジトリも。
        self.assertTrue(self.dirty(tree, other))
        self.assertTrue(self.dirty(tree, kept))
        self.assertEqual(self.head(stray), stray_before)
        self.assertEqual(self.head_of(stray_remote, "work"), "")

    # ---- チケット approve-carry-05 の 7・8

    def said(self, result, name):
        """sh 自身が標準エラーに name を名指ししたか。git のエラー文に紛れた名前は数えない。"""
        return any(
            line.startswith("ccnavi-user-push-approved:") and name in line
            for line in result.stderr.splitlines()
        )

    def test_a_failed_add_in_one_tree_does_not_stop_the_others(self):
        """7. 1 本のツリーで `git add` が失敗しても、もう 1 本はコミットして push する。

        終了コードは 1 で、失敗したツリーを標準エラーで名指しする。
        """
        locked = self.worktree("locked")
        self.place(locked)
        before = self.head(locked)
        # そのツリーのインデックスを他のプロセスが握っている形。`git add` が失敗する。
        gitdir = git(locked, "rev-parse", "--absolute-git-dir").stdout.strip()
        write(os.path.join(gitdir, "index.lock"))
        tree = self.worktree("i0002")
        self.place(tree, "i0002")

        result = self.push()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.said(result, "locked"), result.stderr)
        self.assertEqual(self.head(locked), before)
        self.assertEqual(self.remote_head("locked"), "")
        # 失敗したツリーのあとでも、他のツリーはコミットして push する。
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0002.md"])
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    def test_a_symlink_under_worktrees_is_not_followed(self):
        """8. `.claude/worktrees/` の下のシンボリックリンクは辿らない。標準エラーに言う。

        リンク先はワークスペースの外のリポジトリ。実際のワークツリーはコミットして push する。
        """
        outside = os.path.join(self._tmp.name, "outside")
        outside_remote = self.repository(outside, "work")
        self.place(outside)
        before = self.head(outside)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")
        link = os.path.join(self.ws, ".claude", "worktrees", "linked")
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"シンボリックリンクが作れない: {error}")

        result = self.push()
        # 飛ばしたリンクがあるときの終了コードはチケットで決まっていないので見ない。
        self.assertTrue(self.said(result, "linked"), result.stderr)
        self.assertEqual(self.head(outside), before)
        self.assertTrue(self.dirty(outside, APPROVED))
        self.assertEqual(self.head_of(outside_remote, "work"), "")
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    # ---- チケット approve-carry-07（3 回目の敵対的レビュー）

    def symlink_or_skip(self, target, link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"シンボリックリンクが作れない: {error}")

    def outside_repository(self, holder):
        """ワークスペースの外の holder/app にリポジトリを置き、承認済みチケットを 1 枚置く。"""
        app = os.path.join(self._tmp.name, holder, "app")
        remote = self.repository(app, "work")
        self.place(app)
        return app, remote

    def assert_not_carried(self, app, remote, before):
        self.assertEqual(self.head(app), before)
        self.assertTrue(self.dirty(app, APPROVED))
        self.assertEqual(self.head_of(remote, "work"), "")

    def test_projects_itself_as_a_symlink_is_not_followed(self):
        """`projects/` そのものがシンボリックリンクなら、その下のリポジトリにコミットしない。

        リンク先の中の 1 件ずつはリンクではないので、置き場の段で確かめないと辿ってしまう。
        飛ばしたことは標準エラーに言う。実際のワークツリーはコミットして push する。
        """
        app, remote = self.outside_repository("elsewhere-projects")
        before = self.head(app)
        tree = self.worktree("i0002")
        self.place(tree, "i0002")
        self.symlink_or_skip(os.path.dirname(app), os.path.join(self.ws, "projects"))

        result = self.push()
        self.assertTrue(self.said(result, "projects"), result.stderr)
        self.assert_not_carried(app, remote, before)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.remote_head("i0002"), self.head(tree))

    def test_worktrees_itself_as_a_symlink_is_not_followed(self):
        """`.claude/worktrees/` そのものがシンボリックリンクなら、その下にコミットしない。"""
        app, remote = self.outside_repository("elsewhere-worktrees")
        before = self.head(app)
        link = os.path.join(self.ws, ".claude", "worktrees")
        self.symlink_or_skip(os.path.dirname(app), link)

        result = self.push()
        self.assertTrue(self.said(result, "worktrees"), result.stderr)
        self.assert_not_carried(app, remote, before)

    def test_leaves_nothing_of_others_in_the_index(self):
        """13. 実行後、同じツリーの他人の変更がステージ（インデックス）に載っていない。

        書きかけは書きかけのまま（ステージしていない変更と未追跡のファイル）で残す。
        """
        tree = self.worktree("i0001")
        self.place(tree)
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        write(os.path.join(tree, "src", "draft.py"), "x\n")

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.staged(tree), "")
        self.assertEqual(self.dirty(tree, "README.md"), "M README.md")
        self.assertTrue(self.dirty(tree, "src").startswith("??"), self.dirty(tree, "src"))

    def test_only_a_detached_tree_is_skipped_without_saying_nothing(self):
        """14. detached のツリーにしか変更が無いときは 0 で終わる。

        「コミットして push する承認済みチケットは無い。」とは言わず、
        飛ばしたことを標準エラーに言う。
        """
        loose = self.worktree("loose", detach=True)
        self.place(loose)
        before = self.head(loose)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(NOTHING, result.stdout)
        self.assertNotIn(NOTHING, result.stderr)
        self.assertIn("loose", result.stderr)
        self.assertEqual(self.head(loose), before)
        self.assertTrue(self.dirty(loose, APPROVED))

    # ---- 1.2・1.3 引数と置き場

    def test_path_carries_only_the_named_paths(self):
        """`--path` を並べると、置き場の変更のうちそのパスだけをコミットして push する。"""
        tree = self.worktree("i0001")
        self.place(tree, "i0001")
        self.place(tree, "i0009")
        gone = write(os.path.join(tree, "wip", "proposals", "todo", "i0001.md"), "proposal\n")
        git(tree, "add", "--", gone)
        git(tree, "commit", "-q", "-m", "propose")
        os.remove(gone)
        review = write(os.path.join(tree, "wip", "proposals", "review", "i0008.md"), "review\n")
        other = self.worktree("i0002")
        self.place(other, "i0002")
        rel = ".claude/worktrees/i0001"
        result = self.push(
            "--path",
            f"{rel}/{APPROVED}/i0001.md",
            "--path",
            f"{rel}/wip/proposals/todo/i0001.md",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.committed(tree), [f"{APPROVED}/i0001.md", "wip/proposals/todo/i0001.md"]
        )
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertTrue(self.dirty(tree, f"{APPROVED}/i0009.md"))
        self.assertTrue(self.dirty(tree, review))
        self.assertEqual(self.remote_head("i0002"), "")
        self.assertTrue(self.dirty(other, f"{APPROVED}/i0002.md"))

    def test_path_refuses_absolute_paths_and_parent_ids_together(self):
        self.worktree("i0001")
        for args in (("--path",), ("--path", "/abs"), ("--path", "../x"), ("--path", "a", "i0001")):
            with self.subTest(args=args):
                result = self.push(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_takes_only_parent_ids_and_needs_the_workspace(self):
        self.assertEqual(self.push("--help").returncode, 0)
        for word in ("-x", "../i0001", "a/b", ""):
            wrong = self.push(word)
            self.assertEqual(wrong.returncode, 2, word + wrong.stdout + wrong.stderr)
        # 取り込み済みでない親子のチケットの名指しはコミットしない（今のまま、ユーザがコミットする）
        # 。
        tree = self.worktree("i0001")
        self.place(tree)
        named = self.push("i0001")
        self.assertEqual(named.returncode, 0, named.stdout + named.stderr)
        self.assertIn("取り込み済みの親子のチケットでない", named.stdout)
        self.assertTrue(self.dirty(tree, APPROVED))
        self.assertEqual(self.remote_head("i0001"), "")
        outside = os.path.join(self._tmp.name, "elsewhere")
        os.makedirs(outside)
        lost = self.push(cwd=outside)
        self.assertEqual(lost.returncode, 2, lost.stdout + lost.stderr)


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class TemplateDraftCarryTest(Workspace):
    """取り込んで消えたテンプレートの下書き（`wip/proposals/templates/`）は、フローの下書きと同じ形で
    コミットして push する。

    `.ccnavi/scripts/` の版で走らせる。この版は、消えた下書きを拾ってコミットに入れる。
    """

    drafts = "wip/proposals/templates"

    def track(self, tree, *rels):
        for rel in rels:
            write(os.path.join(tree, *rel.split("/")), "name: x\n")
        git(tree, "add", "--", *rels)
        git(tree, "commit", "-q", "-m", "drafts")

    def test_a_removed_template_draft_is_carried_with_the_approved_place(self):
        tree = self.worktree("i0001")
        taken = f"{self.drafts}/feature.yml"
        rewritten = f"{self.drafts}/research.yml"
        untracked = f"{self.drafts}/local.yml"
        self.track(tree, taken, rewritten)
        os.remove(os.path.join(tree, *taken.split("/")))
        write(os.path.join(tree, *rewritten.split("/")), "name: x\nplan: [y]\n")
        write(os.path.join(tree, *untracked.split("/")), "name: x\n")
        self.place(tree)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 同じ中身の削除と新規が改名に見えないよう、2 つのパスで見る。
        out = git(tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([f"{APPROVED}/i0001.md", taken]))
        # 書き直された下書き・追跡していない下書きには触れない。
        self.assertTrue(self.dirty(tree, rewritten))
        self.assertTrue(self.dirty(tree, untracked))
        self.assertEqual(self.dirty(tree, taken), "")
        self.assertEqual(self.staged(tree), "")
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_flow_and_template_drafts_are_carried_together(self):
        tree = self.worktree("i0001")
        flow = "wip/proposals/flows/i0001-01-01.yml"
        template = f"{self.drafts}/feature.yml"
        self.track(tree, flow, template)
        os.remove(os.path.join(tree, *flow.split("/")))
        os.remove(os.path.join(tree, *template.split("/")))
        self.place(tree)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = git(tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([f"{APPROVED}/i0001.md", flow, template]))

    def test_template_drafts_alone_are_not_carried(self):
        """承認済みチケットの置き場に変更が無ければ、下書きの削除だけではコミットしない。"""
        tree = self.worktree("i0001")
        taken = f"{self.drafts}/feature.yml"
        self.track(tree, taken)
        os.remove(os.path.join(tree, *taken.split("/")))
        before = self.head(tree)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)
        self.assertEqual(self.head(tree), before)

    def test_the_places_of_other_trees_are_not_touched(self):
        """置き場の外（`wip/proposals/` の下の別の場所）で消えたものは運ばない。"""
        tree = self.worktree("i0001")
        other = "wip/proposals/notes/x.md"
        template = f"{self.drafts}/feature.yml"
        self.track(tree, other, template)
        os.remove(os.path.join(tree, *other.split("/")))
        os.remove(os.path.join(tree, *template.split("/")))
        self.place(tree)

        result = self.push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), sorted([f"{APPROVED}/i0001.md", template]))
        self.assertTrue(self.dirty(tree, other))


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class ApproveCarriesTest(Workspace):
    """22. `ccnavi-user-agree.sh` は承認のあと `ccnavi-user-push-approved.sh` で
    コミットして push する。
    """

    scripts = APPROVE_SCRIPTS

    def setUp(self):
        super().setUp()
        self.stub = write(
            os.path.join(self._tmp.name, "bin", "ccnavi"),
            STUB.replace("{compat}", str(version.COMPAT)),
        )
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR | stat.S_IXGRP)

    def approve(self, *ids, **extra):
        env = self.env(
            CCNAVI_BIN_PATH=posix(self.stub),
            **{k: posix(v) if k in ("STUB_TREE", "STUB_ARGS") else v for k, v in extra.items()},
        )
        return self.run_sh("ccnavi-user-agree.sh", *ids, env=env)

    def received(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_approve_passes_ids_after_approve(self):
        """23. 並べた識別子だけを承認の対象にする。

        同じ親の承認待ちのうち 1 本だけを先に承認する形（#31）。
        """
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve("i0002-01-03", "i0002-01-04", STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        got = self.received(args)
        self.assertEqual(got[:1], ["--root"], got)
        # 実行ファイルには、書いたパスの一覧（--record-writes）と、書けたパスの報告の書き出し先
        # （--placed-out）も渡す
        self.assertEqual(got[2], "--record-writes", got)
        # 承認は実行ファイルのユーザ専用の入口（`user agree`）で呼ぶ
        self.assertEqual(got[4:7], ["user", "agree", "--placed-out"], got)
        self.assertEqual(got[8:], ["i0002-01-03", "i0002-01-04"], got)

    def test_approve_without_ids_takes_all_pending(self):
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve(STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        got = self.received(args)
        self.assertEqual(got[4:7], ["user", "agree", "--placed-out"], got)
        self.assertEqual(got[8:], [])

    def test_approve_refuses_words_that_are_not_ids(self):
        """識別子でない語は断り、実行ファイルを呼ばない。

        `--yes` や `--root` を混ぜると、端末の y/N を経ない経路や別のワークスペースになってしまう。
        """
        tree = self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        for ids in (("--yes", "i0002"), ("i0002", "--root", "/elsewhere"), ("-x",), ("",)):
            with self.subTest(ids=ids):
                result = self.approve(*ids, STUB_ARGS=args, STUB_TREE=tree)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("チケットID以外の引数", result.stderr)
                self.assertFalse(os.path.exists(args))
                self.assertEqual(self.remote_head("i0001"), "")

    def test_approve_help_still_shows_usage(self):
        for word in ("-h", "--help", "help"):
            with self.subTest(word=word):
                result = self.approve(word)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("<識別子>", result.stdout)

    def test_approve_help_next_to_ids_is_an_id(self):
        """使い方を出すのは語が 1 つのときだけ。識別子と並んだ `help` を何も言わずに捨てない。"""
        self.worktree("i0001")
        args = os.path.join(self._tmp.name, "args")
        result = self.approve("help", "i0002-01-01", STUB_ARGS=args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        got = self.received(args)
        self.assertEqual(got[4:7], ["user", "agree", "--placed-out"])
        self.assertEqual(got[8:], ["help", "i0002-01-01"])
        refused = self.approve("--help", "i0002-01-01")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)

    def test_approve_carries_after_approval(self):
        tree = self.worktree("i0001")
        result = self.approve(STUB_TREE=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("i0001"), self.head(tree))

    def test_approve_says_when_there_is_nothing_to_carry(self):
        """コミットと push の段は ccnavi-user-push-approved.sh に任せる。
        コミットするものが無ければその 1 行が出る。
        """
        self.worktree("i0001")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(NOTHING, result.stdout)

    def test_failed_approval_carries_nothing(self):
        """何も置かれなかった承認の失敗では、無関係な未コミットの承認済みチケットも含めて送らない。"""
        tree = self.worktree("i0001")
        self.place(tree)
        before = self.head(tree)
        result = self.approve(STUB_EXIT="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.head(tree), before)
        self.assertEqual(self.remote_head("i0001"), "")

    def test_partial_approval_still_carries_what_was_placed(self):
        """一部だけ通って非 0 で終わっても、置かれた分はコミットして push する。終了コードは 1。

        親は承認済みとして残り、子の書き込みだけ失敗した形。送らないと他のマシンに届かない。
        """
        tree = self.worktree("i0001")
        result = self.approve(STUB_TREE=tree, STUB_AFTER_EXIT="4")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertIn("承認の一部だけが通りました", result.stderr)

    def test_partial_approval_reports_the_written_and_the_failed(self):
        """親は書けて子だけ書けなかった承認。親は push され、書けた分と子の失敗が報告される。"""
        tree = self.worktree("i0001")
        report = (
            '{"version":1,"placed":["/w/.ccnavi/approved/doing/i0001.md"],'
            '"failed":[{"ticket":"i0001-01-01","reason":"保存できませんでした"}],'
            '"unwritten":[],"rejected":[]}'
        )
        result = self.approve(STUB_TREE=tree, STUB_AFTER_EXIT="4", STUB_REPORT=report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertIn("/w/.ccnavi/approved/doing/i0001.md", result.stderr)
        self.assertIn("i0001-01-01", result.stderr)
        self.assertIn("保存できませんでした", result.stderr)
        self.assertNotIn("未送信", result.stderr)

    def test_partial_approval_whose_push_fails_names_the_unsent_paths(self):
        """一部だけ書けて、push も失敗したときは、未送信のパスを名指しする。"""
        tree = self.worktree("i0001")
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        result = self.approve(STUB_TREE=tree, STUB_AFTER_EXIT="4", STUB_REPORT='{"placed":[]}')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)
        self.assertIn("未送信", result.stderr)
        self.assertIn(f"{APPROVED}/i0001.md", result.stderr)

    def test_approve_carries_only_what_the_approval_wrote(self):
        """同じ置き場にある承認と無関係な変更（別の承認済みチケット、review/、別のツリー）は送らない。"""
        tree = self.worktree("i0001")
        self.place(tree, "i0009")
        review = write(os.path.join(tree, "wip", "proposals", "review", "i0008.md"), "review\n")
        other = self.worktree("i0002")
        self.place(other, "i0002")
        result = self.approve(STUB_TREE=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.committed(tree), [f"{APPROVED}/i0001.md"])
        self.assertEqual(self.remote_head("i0001"), self.head(tree))
        self.assertTrue(self.dirty(tree, f"{APPROVED}/i0009.md"), "無関係な承認済みチケットは残す")
        self.assertTrue(self.dirty(tree, review), "review/ の変更は残す")
        self.assertEqual(self.remote_head("i0002"), "", "別のツリーは送らない")
        self.assertTrue(self.dirty(other, f"{APPROVED}/i0002.md"))
        self.assertNotIn("済んでいない", result.stderr)

    def test_a_failed_approval_still_shows_why(self):
        """何も置かずに失敗した（4 以外の非 0）ときも、書けなかった理由の報告は見せる。"""
        self.worktree("i0001")
        report = (
            '{"version":1,"placed":[],"written":[],'
            '"failed":[{"ticket":"i0001","reason":"提案の内容が更新されました"}],'
            '"unwritten":[],"rejected":[]}'
        )
        result = self.approve(STUB_EXIT="1", STUB_REPORT=report)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("提案の内容が更新されました", result.stderr)

    def test_source_mode_starts_uv_at_the_workspace_root(self):
        """ソースで動かすとき、uv はワークスペースルートで起こす（cwd が中の別の場所でも）。"""
        write(os.path.join(self.ws, "src", "ccnavi", "__main__.py"), "")
        where = os.path.join(self._tmp.name, "uv-cwd")
        uv = write(
            os.path.join(self._tmp.name, "uvbin", "uv"),
            "#!/bin/sh\n"
            f"pwd > '{posix(where)}'\n"
            'while [ $# -gt 0 ]; do [ "$1" != --record-writes ] || : > "$2"; shift; done\n',
        )
        os.chmod(uv, 0o755)
        inner = os.path.join(self.ws, "docs")
        os.makedirs(inner)
        env = self.env(CCNAVI_BIN_PATH=posix(os.path.join(self._tmp.name, "none")))
        env["PATH"] = os.pathsep.join([os.path.dirname(uv), env.get("PATH", "")])
        result = self.run_sh("ccnavi-user-agree.sh", cwd=inner, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(where, encoding="utf-8") as f:
            self.assertEqual(os.path.realpath(f.read().strip()), os.path.realpath(self.ws))

    def test_failed_push_does_not_fail_the_approval(self):
        """コミットや push の失敗で承認が失敗に見えないように、承認が通れば 0。コミットは残る。"""
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "missing.git"))
        tree = self.worktree("i0001")
        result = self.approve(STUB_TREE=tree)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.subject(tree), MESSAGE)


if __name__ == "__main__":
    unittest.main()
