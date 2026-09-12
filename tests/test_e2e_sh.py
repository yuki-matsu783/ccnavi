"""モード B の受入テスト。本物のワークスペースを組み立てて sh を外から叩く。

モード B は、道具を持つワークスペースの下の `projects/<名前>/` に別々のリポジトリを
clone する形（設計 §25）。ここで確かめるのは、保護済み sh が「自分の根」を
ワークスペースルートとして正しく取れること、その結果として記録・実行ファイル・
状態の置き場がワークスペース側に揃うこと、そしてモード A（`projects/` が無い形）が
退行しないこと。

in-process のテストでは掛からない理由が 3 つある。sh は実行ファイルではなく別の
プロセスで動く。git の作業ツリーの実物（`.git` ファイルの `gitdir:`）が要る。
Windows のパスの綴りが実物でしか出ない。

**重い。** git init と worktree add を何度も行い、実行ファイルを 18MB 写す。毎ターン
走らせる意味は無いので、`CCNAVI_E2E` が設定されているときだけ走る。

    CCNAVI_E2E=1 uv run python -m unittest tests.test_e2e_sh -v

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。実装フェーズの成果物は
`wip/design/scripts/` に置かれ、人が写すまで `.claude/scripts/` には入らない。
写す前に新しい sh を測るときは、そこを指す。

    CCNAVI_E2E=1 CCNAVI_SH_DIR=wip/design/scripts uv run python -m unittest tests.test_e2e_sh

読み返すのは標準出力・標準エラー・終了コードと、ファイルシステムに出たものだけ。
スクリプトの中の変数も関数も見ない。

**ここで確かめないもの。** `ccnavi-review.sh wrapup` のブランチ名にスラッシュがある場合
（`feature/x` で存在しないディレクトリを指す）は、リモートと `gh` が要るのでここには
入れない。実装とレビューで確かめる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = shutil.which("sh") or shutil.which("bash")
E2E = os.environ.get("CCNAVI_E2E", "")

# 写す sh の出どころ。既定はワークスペースに配られている版。
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", os.path.join(".claude", "scripts")))
HOOK_DIR = os.path.join(ROOT, ".claude", "hooks")


def find_dist():
    """組み立てた実行ファイルの置き場を探す。

    作業ツリーには `dist/` が無い（追跡外なので checkout されない）。実物は
    ワークスペースルートにあるので、そこまで上へ歩く。`CCNAVI_DIST` があれば
    それを使う。
    """
    named = os.environ.get("CCNAVI_DIST", "")
    if named:
        return os.path.abspath(named)
    here = ROOT
    while True:
        candidate = os.path.join(here, "dist", "ccnavi")
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.join(ROOT, "dist", "ccnavi")
        here = parent


DIST = find_dist()


def git(cwd, *args):
    """素の git。見本を組み立てるためだけに使う。"""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(path):
    """コミットが 1 件ある使い捨てのリポジトリ。"""
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    write(os.path.join(path, "seed.txt"), "seed\n")
    git(path, "add", "seed.txt")
    git(path, "commit", "-q", "-m", "seed")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def files_under(path):
    """そのディレクトリの下にあるファイルの相対パス。無ければ空。"""
    if not os.path.isdir(path):
        return []
    out = []
    for base, _dirs, names in os.walk(path):
        for name in names:
            out.append(os.path.relpath(os.path.join(base, name), path))
    return sorted(out)


def assertGotPastTheRoot(case, result):
    """根の導出より先へ進んだこと。

    素通りで合格するのを防ぐ。根の導出が誤っていると、スクリプトは実行ファイルを
    プロジェクトの中に探して落ちる。その手前で終わった実行を「漏れなかった」
    「作らなかった」と数えると、テストが嘘をつく。
    """
    blob = (result.stdout or "") + (result.stderr or "")
    case.assertNotIn(
        "実行ファイルが無い",
        blob,
        "根の導出で落ちている。この実行は中身を確かめていない",
    )


def reason_to_skip():
    """走らせられない理由。無ければ空。"""
    if not E2E:
        return "CCNAVI_E2E が設定されていない（重いので既定では走らせない）"
    if not SHELL:
        return "sh も bash も見つからない"
    if not shutil.which("git"):
        return "git が見つからない"
    if not os.path.isdir(SH_DIR):
        return f"sh の出どころが無い ({SH_DIR})"
    if not os.path.isdir(DIST):
        return f"実行ファイルが組み立てられていない ({DIST})。build.py で作る"
    return ""


SKIP = reason_to_skip()


@unittest.skipIf(SKIP, SKIP)
class WorkspaceTest(unittest.TestCase):
    """ワークスペース 1 つ、プロジェクト 2 つ、作業ツリー 2 つ。

    組み立てが重いのでクラスで 1 度だけ作る。記録は各テストの前に消す。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ccnavi-e2e-")
        cls.ws = os.path.join(cls.tmp, "ws")
        cls.build_workspace(cls.ws, projects=("p1", "p2"))
        # p1 から切った作業ツリーと、ワークスペースから切った作業ツリー。
        cls.wp1 = os.path.join(cls.ws, ".claude", "worktrees", "wp1")
        cls.w0 = os.path.join(cls.ws, ".claude", "worktrees", "w0")
        git(os.path.join(cls.ws, "projects", "p1"), "worktree", "add", "-q", cls.wp1, "-b", "wp1")
        git(cls.ws, "worktree", "add", "-q", cls.w0, "-b", "w0")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def build_workspace(cls, ws, projects=()):
        """道具を持つワークスペースを作る。実行ファイルと sh を実物で置く。"""
        make_repo(ws)
        scripts = os.path.join(ws, ".claude", "scripts")
        os.makedirs(scripts, exist_ok=True)
        for name in sorted(os.listdir(SH_DIR)):
            if name.endswith(".sh"):
                shutil.copy2(os.path.join(SH_DIR, name), os.path.join(scripts, name))
        hooks = os.path.join(ws, ".claude", "hooks")
        os.makedirs(hooks, exist_ok=True)
        for name in ("test-py.sh",):
            # 写す版があればそちらを優先する。CCNAVI_SH_DIR で写す前の版を
            # 指しているとき、hook だけ古い版を測ってしまうのを防ぐ。
            src = os.path.join(SH_DIR, name)
            if not os.path.isfile(src):
                src = os.path.join(HOOK_DIR, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(hooks, name))
        shutil.copytree(DIST, os.path.join(ws, "dist", "ccnavi"))
        os.makedirs(os.path.join(ws, ".claude", "ccnavi", "tickets"), exist_ok=True)
        write(os.path.join(ws, ".gitignore"), "/logs/\n/projects/\n/dist/\n/.claude/worktrees/\n")
        git(ws, "add", ".gitignore", ".claude/scripts", ".claude/hooks")
        git(ws, "commit", "-q", "-m", "tools")
        for name in projects:
            make_repo(os.path.join(ws, "projects", name))
        return ws

    def setUp(self):
        # 記録を毎回まっさらにする。どのテストが何を残したかを取り違えないため。
        for place in (
            self.ws,
            os.path.join(self.ws, "projects", "p1"),
            os.path.join(self.ws, "projects", "p2"),
            self.wp1,
            self.w0,
        ):
            shutil.rmtree(os.path.join(place, "logs"), ignore_errors=True)

    # ---- 道具

    def script(self, name):
        return os.path.join(self.ws, ".claude", "scripts", name)

    def run_sh(self, name, *args, cwd=None, env=None):
        environment = dict(os.environ)
        environment.pop("CCNAVI_WORKSPACE", None)
        environment.update(env or {})
        return subprocess.run(
            [SHELL, self.script(name), *args],
            cwd=cwd or self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def assertLogsIn(self, relative, result):
        """記録がワークスペースの下の狙った場所に出たこと。"""
        place = os.path.join(self.ws, *relative.split("/"))
        found = [n for n in files_under(place) if n.endswith(".log")]
        self.assertTrue(
            found, f"{relative} に記録が無い。stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def assertNoLogsIn(self, absolute):
        found = [n for n in files_under(os.path.join(absolute, "logs")) if n.endswith(".log")]
        self.assertEqual(
            [], found, f"{absolute}/logs に記録が出ている（ワークスペースの外に漏れた）"
        )


class LogPlacementTest(WorkspaceTest):
    """記録はワークスペースの logs/<プロジェクト>/ に出る（REQ-MLT-14、設計 4.1）。"""

    def test_inside_a_project(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-git.sh", "status", cwd=p1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLogsIn("logs/p1", result)
        self.assertNoLogsIn(p1)

    def test_inside_a_worktree_cut_from_a_project(self):
        result = self.run_sh("ccnavi-git.sh", "status", cwd=self.wp1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLogsIn("logs/p1", result)
        self.assertNoLogsIn(self.wp1)

    def test_inside_a_worktree_cut_from_the_workspace(self):
        result = self.run_sh("ccnavi-git.sh", "status", cwd=self.w0)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLogsIn("logs", result)
        self.assertNoLogsIn(self.w0)

    def test_at_the_workspace_root(self):
        result = self.run_sh("ccnavi-git.sh", "status", cwd=self.ws)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLogsIn("logs", result)

    def test_the_returned_path_can_be_opened_from_anywhere(self):
        """返す綴りは、cwd がプロジェクトでも開ける形であること（設計 4.1）。"""
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-git.sh", "status", cwd=p1)
        shown = result.stdout
        found = [n for n in files_under(os.path.join(self.ws, "logs", "p1")) if n.endswith(".log")]
        self.assertTrue(found)
        name = os.path.basename(found[0])
        self.assertIn(name, shown, "記録の名前が返っていない")
        opened = False
        for piece in shown.replace("\n", " ").split():
            candidate = piece.strip("()")
            # ラッパは `log=<綴り>` の形で返す。接頭辞を落としてから開く。
            if "=" in candidate:
                candidate = candidate.split("=", 1)[1]
            if not candidate.endswith(".log"):
                continue
            # cwd（プロジェクトの中）から開けるか、絶対で開けるかを見る。
            # ワークスペースからしか開けない綴りは、モード B では届かない。
            if os.path.isfile(os.path.join(p1, candidate)) or os.path.isfile(candidate):
                opened = True
        self.assertTrue(opened, f"返された綴りが cwd から開けない: {shown!r}")


class BinaryDiscoveryTest(WorkspaceTest):
    """実行ファイルはワークスペースの dist/ にある（設計 4.2、4.3）。"""

    def test_ticket_script_finds_the_binary_from_inside_a_project(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-ticket.sh", "cancel", "nope", "--reason", "x", cwd=p1)
        self.assertNotIn("実行ファイルが無い", result.stderr, "プロジェクトの中を探しに行っている")

    def test_review_script_does_not_create_dot_claude_in_a_project(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-review.sh", "origin", cwd=p1)
        assertGotPastTheRoot(self, result)
        self.assertFalse(
            os.path.exists(os.path.join(p1, ".claude")),
            "プロジェクトに .claude/ を作った（--lint が禁じている形）",
        )


class PushGuardTest(WorkspaceTest):
    """子チケットの作業ツリーからは送らない（設計 4.1）。"""

    def test_a_child_worktree_cut_from_a_project_cannot_push(self):
        write(
            os.path.join(self.ws, ".claude", "ccnavi", "tickets", "wp1.md"),
            "---\nversion: 1\nticket: wp1\nparent: oya\n---\n本文\n",
        )
        self.addCleanup(
            lambda: (
                os.path.exists(os.path.join(self.ws, ".claude", "ccnavi", "tickets", "wp1.md"))
                and os.remove(os.path.join(self.ws, ".claude", "ccnavi", "tickets", "wp1.md"))
            )
        )
        bare = os.path.join(self.tmp, "origin.git")
        if not os.path.isdir(bare):
            git(self.tmp, "init", "-q", "--bare", bare)
            git(os.path.join(self.ws, "projects", "p1"), "remote", "add", "origin", bare)
        result = self.run_sh("ccnavi-git.sh", "push", "-u", "origin", "wp1", cwd=self.wp1)
        self.assertEqual(2, result.returncode, f"送れてしまった: stdout={result.stdout!r}")
        self.assertIn("子チケット", result.stderr)


class WorktreeAddTest(WorkspaceTest):
    """作業ツリーはワークスペースの .claude/worktrees/ の下に切る（設計 4.1）。"""

    def cleanup_worktree(self, name):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        self.addCleanup(
            lambda: subprocess.run(
                ["git", "worktree", "prune"],
                cwd=os.path.join(self.ws, "projects", "p1"),
                capture_output=True,
            )
        )
        return path

    def test_a_relative_destination_inside_a_project_is_rejected(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh(
            "ccnavi-git.sh", "worktree", "add", ".claude/worktrees/x", "-b", "x", cwd=p1
        )
        self.assertEqual(2, result.returncode, f"通ってしまった: {result.stdout!r}")
        self.assertFalse(
            os.path.exists(os.path.join(p1, ".claude", "worktrees", "x")),
            "プロジェクトの中に作業ツリーができた",
        )
        self.assertIn("../../.claude/worktrees/", result.stderr, "正しい綴りを案内していない")

    def test_the_correct_spelling_from_inside_a_project_works(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        path = self.cleanup_worktree("x2")
        result = self.run_sh(
            "ccnavi-git.sh", "worktree", "add", "../../.claude/worktrees/x2", "-b", "x2", cwd=p1
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(os.path.isdir(path), "ワークスペースの下に作業ツリーができていない")

    def test_an_unknown_option_is_rejected(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        self.cleanup_worktree("x3")
        result = self.run_sh(
            "ccnavi-git.sh",
            "worktree",
            "add",
            "--no-such-option",
            "../../.claude/worktrees/x3",
            "-b",
            "x3",
            cwd=p1,
        )
        self.assertEqual(2, result.returncode, f"知らないオプションが通った: {result.stdout!r}")


class CredentialTest(WorkspaceTest):
    """origin に埋まった資格情報は、どの出口でも出さない（設計 4.3）。"""

    def set_origin(self, url):
        p1 = os.path.join(self.ws, "projects", "p1")
        subprocess.run(["git", "remote", "remove", "origin"], cwd=p1, capture_output=True)
        git(p1, "remote", "add", "origin", url)
        self.addCleanup(
            lambda: subprocess.run(
                ["git", "remote", "remove", "origin"], cwd=p1, capture_output=True
            )
        )
        return p1

    def assertNoSecret(self, result, *secrets):
        """伏せた結果を目で比べない。元のトークンの断片で探す。

        「消えているつもりで残っている」形は、綴りを見比べると見落とす。
        origin を読む処理まで届いたことも確かめる。届く前に落ちた実行を
        「漏れなかった」と数えると、穴が開いたままテストが緑になる。
        """
        assertGotPastTheRoot(self, result)
        blob = (result.stdout or "") + (result.stderr or "")
        self.assertTrue(
            "origin" in blob,
            f"origin を読む処理まで届いていない: {blob!r}",
        )
        for secret in secrets:
            self.assertNotIn(secret, blob, f"資格情報が出ている: {blob!r}")

    def test_an_at_sign_inside_the_token_does_not_survive(self):
        p1 = self.set_origin("ssh://oauth2:glpat-AAA@BBB@example.invalid:2222/g/p.git")
        result = self.run_sh("ccnavi-review.sh", "origin", cwd=p1)
        self.assertNoSecret(result, "glpat-AAA", "BBB")

    def test_an_unreadable_spelling_does_not_leak_on_failure(self):
        p1 = self.set_origin("weird://oauth2:glpat-CCC@example.invalid/g/p.git")
        result = self.run_sh("ccnavi-review.sh", "origin", cwd=p1)
        self.assertNoSecret(result, "glpat-CCC")


class WorkspaceDiscoveryTest(WorkspaceTest):
    """ワークスペースルートの探し方（設計 2）。"""

    def test_the_override_is_honoured(self):
        other = os.path.join(self.tmp, "ws2")
        if not os.path.isdir(other):
            self.build_workspace(other)
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-git.sh", "status", cwd=p1, env={"CCNAVI_WORKSPACE": other})
        self.assertEqual(0, result.returncode, result.stderr)
        found = [n for n in files_under(os.path.join(other, "logs")) if n.endswith(".log")]
        self.assertTrue(found, "CCNAVI_WORKSPACE の指す先に記録が出ていない")

    def test_an_override_without_the_marker_fails(self):
        empty = os.path.join(self.tmp, "not-a-workspace")
        os.makedirs(empty, exist_ok=True)
        p1 = os.path.join(self.ws, "projects", "p1")
        result = self.run_sh("ccnavi-git.sh", "status", cwd=p1, env={"CCNAVI_WORKSPACE": empty})
        self.assertNotEqual(0, result.returncode, "印の無い場所を黙って受けた")

    def test_outside_any_workspace_it_stops_and_says_how(self):
        stray = os.path.join(self.tmp, "stray")
        if not os.path.isdir(stray):
            make_repo(stray)
        result = self.run_sh("ccnavi-git.sh", "status", cwd=stray)
        self.assertNotEqual(0, result.returncode, "ワークスペースの外で動いた")
        self.assertIn("CCNAVI_WORKSPACE", result.stderr, "抜け道を案内していない")


class HookTest(WorkspaceTest):
    """tests/ を持たないツリーでターンを止めない（設計 4.4）。"""

    def test_a_tree_without_tests_is_skipped(self):
        p1 = os.path.join(self.ws, "projects", "p1")
        hook = os.path.join(self.ws, ".claude", "hooks", "test-py.sh")
        if not os.path.isfile(hook):
            self.skipTest("test-py.sh が写されていない")
        result = subprocess.run(
            [SHELL, hook],
            cwd=p1,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="{}",
        )
        self.assertEqual(
            0,
            result.returncode,
            f"tests/ の無いツリーで止まった: {result.stdout!r} {result.stderr!r}",
        )
        self.assertNotIn("Start directory is not importable", result.stdout + result.stderr)


@unittest.skipIf(SKIP, SKIP)
class ModeATest(unittest.TestCase):
    """projects/ が無いワークスペースで、§25 の前と同じに動くこと（REQ-MLT-15）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ccnavi-e2e-modea-")
        cls.ws = os.path.join(cls.tmp, "ws")
        WorkspaceTest.build_workspace.__func__(WorkspaceTest, cls.ws)
        cls.w0 = os.path.join(cls.ws, ".claude", "worktrees", "w0")
        git(cls.ws, "worktree", "add", "-q", cls.w0, "-b", "w0")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_sh(self, name, *args, cwd=None):
        environment = dict(os.environ)
        environment.pop("CCNAVI_WORKSPACE", None)
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".claude", "scripts", name), *args],
            cwd=cwd or self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def setUp(self):
        for place in (self.ws, self.w0):
            shutil.rmtree(os.path.join(place, "logs"), ignore_errors=True)

    def test_logs_stay_at_the_workspace_root(self):
        result = self.run_sh("ccnavi-git.sh", "status")
        self.assertEqual(0, result.returncode, result.stderr)
        found = [n for n in files_under(os.path.join(self.ws, "logs")) if n.endswith(".log")]
        self.assertTrue(found, "logs/ に出ていない")
        self.assertFalse(
            os.path.isdir(os.path.join(self.ws, "logs", "projects")),
            "プロジェクトの区画ができている",
        )

    def test_a_worktree_still_writes_to_the_workspace(self):
        result = self.run_sh("ccnavi-git.sh", "status", cwd=self.w0)
        self.assertEqual(0, result.returncode, result.stderr)
        found = [n for n in files_under(os.path.join(self.ws, "logs")) if n.endswith(".log")]
        self.assertTrue(found, "作業ツリーからの記録がワークスペースに出ていない")

    def test_an_empty_placement_dir_changes_nothing(self):
        os.makedirs(os.path.join(self.ws, "projects"), exist_ok=True)
        self.addCleanup(shutil.rmtree, os.path.join(self.ws, "projects"), ignore_errors=True)
        result = self.run_sh("ccnavi-git.sh", "status")
        self.assertEqual(0, result.returncode, result.stderr)
        found = [n for n in files_under(os.path.join(self.ws, "logs")) if n.endswith(".log")]
        self.assertTrue(found, "空の置き場があるだけで置き場が変わった")


if __name__ == "__main__":
    unittest.main()
