"""保護済みの sh を呼ぶ形のうち、実行前に止める 2 つ（ccnavi/wrapguard.py、ADR-0077）。

1. sh の検査の材料を変える環境変数を、同じコマンド行で置いて保護済みの sh を呼ぶ形
2. 子チケットのワークツリーからの `ccnavi-git.sh push`。親エージェントが打っても止める

見本の表（CHILD_CASES）は、写す版の `ccnavi-git.sh` にも同じものを渡して、sh の検査が
2 重目として同じく止めることを確かめる（test_sh_agrees）。hook と sh の検査は同じことを
別の実装で持つので、ずれはこの表で捕まえる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi import shellread, wrapguard
from tests import GIT_ENV, ROOT, common_path
from tests.inproc import run_ccnavi

GIT = ["sh", "../../../.ccnavi/scripts/ccnavi-git.sh"]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ticket(name, parent=""):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if parent:
        lines += [f"parent: {parent}", "phase: 1"]
    lines += ["title: t", "---", ""]
    return "\n".join(lines) + "\n"


class EnvTest(unittest.TestCase):
    """環境変数の代入。読みだけで決まるので、ワークスペースは要らない。"""

    def names(self, command):
        return wrapguard.check_env(command, shellread.read(command).reason)[1]

    def test_forms_that_set_a_blocked_variable(self):
        for command in (
            "CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push",
            "env CCNAVI_PROJECTS=/x sh .ccnavi/scripts/ccnavi-git.sh push",
            "env -u CCNAVI_BIN_PATH sh .ccnavi/scripts/ccnavi-ticket.sh done a",
            "export CCNAVI_WORKSPACE=/x; sh .ccnavi/scripts/ccnavi-git.sh push",
            "CCNAVI_STATE=/x; sh .ccnavi/scripts/ccnavi-review.sh check",
            "unset CCNAVI_TICKETS_PROPOSAL && sh .ccnavi/scripts/ccnavi-git.sh push",
            "sudo env CLAUDE_PROJECT_DIR=/x bash .ccnavi/scripts/ccnavi-review.sh check",
            "CCNAVI_NEW_THING=1 ../../../.ccnavi/scripts/ccnavi-git.sh status",
            "cd a && CCNAVI_BIN_PATH=f sh ../.ccnavi/scripts/ccnavi-approve.sh",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.names(command))

    def test_forms_that_pass(self):
        for command in (
            "sh .ccnavi/scripts/ccnavi-git.sh push",
            "CCNAVI_GIT_MAX_LINES=200 sh .ccnavi/scripts/ccnavi-git.sh log",
            "env CCNAVI_FETCH_TIMEOUT=5 sh .ccnavi/scripts/ccnavi-fetch.sh",
            # 保護済みの sh を呼ばないなら、ここの話ではない。
            "CCNAVI_BIN_PATH=x uv run python -m ccnavi --lint",
            "echo CCNAVI_BIN_PATH=x && sh .ccnavi/scripts/ccnavi-git.sh status",
            # 読み切れない形はここでは見ない。allow が当たらずに確認へ落ちる。
            "sh -c 'CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push'",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.names(command))

    def test_pass_through_is_listed_not_guessed(self):
        # 通すものを並べる形。sh があとから読むようになった変数は止まる側。
        self.assertFalse(wrapguard.blocked("CCNAVI_GIT_KEEP_LOGS"))
        self.assertTrue(wrapguard.blocked("CCNAVI_GIT_SOMETHING_NEW"))
        self.assertFalse(wrapguard.blocked("PATH"))


# （cwd の置き場, コマンド, チケットをどこに置くか, 止まるか）
# 置き場: "parent" = 親のワークツリー、"child" = 子のワークツリー、"root" = ワークスペース。
# チケットの置き場: "parent-doing" = 親のツリーの doing/、"root-done" = ワークスペースの done/、
# "parent-review" = 親のツリーの wip/proposals/review/、
# "broken" = 親のツリーの doing/ に置いた、YAML として読めないもの。
CHILD_CASES = (
    ("child", "push", "parent-doing", True),
    ("child", "push", "root-done", True),
    ("child", "push", "parent-review", True),
    ("child", "push", "broken", True),
    ("child", "status", "parent-doing", False),
    ("parent", "push", "parent-doing", False),
    ("plain", "push", "parent-doing", False),
)


class ChildPushTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        write(common_path(self.root, "rules"), json.dumps({"version": 1}))
        base = os.path.join(self.root, ".claude", "worktrees")
        # git に登録していないディレクトリで作る。sh の検査は登録を問わないので、hook も問わない。
        self.trees = {n: os.path.join(base, n) for n in ("i0001", "i0001-01", "plain")}
        for path in self.trees.values():
            os.makedirs(path)

    def place(self, where):
        parent = self.trees["i0001"]
        if where == "parent-doing":
            return write(
                os.path.join(parent, ".ccnavi/approved/doing/i0001-01.md"),
                ticket("i0001-01", "i0001"),
            )
        if where == "root-done":
            return write(
                os.path.join(self.root, ".ccnavi/approved/done/i0001-01.md"),
                ticket("i0001-01", "i0001"),
            )
        if where == "parent-review":
            return write(
                os.path.join(parent, "wip/proposals/review/i0001-01.md"),
                ticket("i0001-01", "i0001"),
            )
        if where == "broken":
            return write(
                os.path.join(parent, ".ccnavi/approved/doing/i0001-01.md"),
                "---\nparent: i0001\n: : [unclosed\n",
            )
        raise AssertionError(where)

    def cwd(self, where):
        return {
            "parent": self.trees["i0001"],
            "child": self.trees["i0001-01"],
            "plain": self.trees["plain"],
        }[where]

    def hook(self, cwd, command):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": {"command": command},
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            [
                "--root",
                self.root,
                "--log",
                "",
                "--state",
                os.path.join(self.root, "state"),
                "--mode",
                "enable",
            ],
            input=json.dumps(payload),
            cwd=ROOT,
            env=env,
        )
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or ""

    def test_table(self):
        for where, sub, placed, stops in CHILD_CASES:
            with self.subTest(where=where, sub=sub, placed=placed):
                path = self.place(placed)
                try:
                    reason = self.hook(self.cwd(where), " ".join(GIT + [sub]))
                    if stops:
                        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)
                        self.assertIn("親（", reason)
                    else:
                        self.assertNotIn(wrapguard.CODE_CHILD_PUSH, reason)
                finally:
                    os.remove(path)

    def test_parent_agent_cd_into_child(self):
        self.place("parent-doing")
        reason = self.hook(
            self.root, "cd .claude/worktrees/i0001-01 && " + " ".join(GIT + ["push"])
        )
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)
        # 親のツリーへ移ってから打つ形は通す。
        reason = self.hook(self.trees["i0001-01"], "cd ../i0001 && " + " ".join(GIT + ["push"]))
        self.assertNotIn(wrapguard.CODE_CHILD_PUSH, reason)

    def test_unreadable_cd_stops(self):
        reason = self.hook(self.root, 'cd "$X" && ' + " ".join(GIT + ["push"]))
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)

    def test_symlinked_workspace(self):
        # 論理のパスと実際のパスを混ぜると外れる。cwd が symlink 越しでも止める。
        self.place("parent-doing")
        link = self.root + "-link"
        try:
            os.symlink(self.root, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink を作れない")
        self.addCleanup(os.remove, link)
        child = os.path.join(link, ".claude", "worktrees", "i0001-01")
        reason = self.hook(child, " ".join(GIT + ["push"]))
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)


@unittest.skipIf(shutil.which("git") is None, "git が無い")
class ShAgreesTest(unittest.TestCase):
    """同じ見本を写す版の sh にも渡し、2 重目として同じく止めることを確かめる。

    `CCNAVI_SH_DIR` で sh の置き場を差し替えられる（写す版は wip/design/scripts/）。
    """

    ORIGINAL = os.path.join(ROOT, ".ccnavi", "scripts")
    SH_DIR = os.path.abspath(os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR") or ORIGINAL))

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-sh-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        scripts = os.path.join(self.root, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-git.sh", "ccnavi-common.sh"):
            source = os.path.join(self.SH_DIR, name)
            if not os.path.isfile(source):
                # 写す版は変えた sh だけを置く。残りは本物を使う。
                source = os.path.join(ROOT, ".ccnavi", "scripts", name)
            shutil.copy(source, scripts)
        self.git("init", "--quiet", "-b", "main", cwd=self.root)
        write(os.path.join(self.root, ".gitignore"), ".claude/\nlogs/\n")
        self.git("add", "-A", cwd=self.root)
        self.git("commit", "--quiet", "-m", "init", cwd=self.root)
        self.trees = {}
        for name in ("i0001", "i0001-01", "plain"):
            path = os.path.join(self.root, ".claude", "worktrees", name)
            self.git("worktree", "add", "--quiet", path, "-b", name, "main", cwd=self.root)
            self.trees[name] = path

    def git(self, *args, cwd):
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    def run_sh(self, cwd, sub):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(GIT_ENV)
        # シェルは起動時の PWD を論理の居場所として引き継ぐ。端末から symlink 越しに cd した形。
        env["PWD"] = cwd
        return subprocess.run(
            [
                "sh",
                os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh"),
                sub,
                "--dry-run",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_sh_agrees(self):
        parent = self.trees["i0001"]
        places = {
            "parent-doing": os.path.join(parent, ".ccnavi/approved/doing/i0001-01.md"),
            "root-done": os.path.join(self.root, ".ccnavi/approved/done/i0001-01.md"),
            "parent-review": os.path.join(parent, "wip/proposals/review/i0001-01.md"),
            "broken": os.path.join(parent, ".ccnavi/approved/doing/i0001-01.md"),
        }
        for where, sub, placed, stops in CHILD_CASES:
            if sub != "push":
                continue
            with self.subTest(where=where, placed=placed):
                text = (
                    "---\nparent: i0001\n: : [unclosed\n"
                    if placed == "broken"
                    else ticket("i0001-01", "i0001")
                )
                path = write(places[placed], text)
                try:
                    cwd = {
                        "parent": parent,
                        "child": self.trees["i0001-01"],
                        "plain": self.trees["plain"],
                    }[where]
                    result = self.run_sh(cwd, "push")
                    said = "子チケットのワークツリー" in result.stderr
                    self.assertEqual(stops, said, result.stderr)
                finally:
                    os.remove(path)

    def test_sh_symlinked_workspace(self):
        # ワークスペースを symlink 越しに開いたとき（macOS の /tmp → /private/tmp など）。
        # sh は論理のパスでワークスペースを、git の実際のパスでツリーを作るので、混ぜると外れる。
        link = self.root + "-link"
        try:
            os.symlink(self.root, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink を作れない")
        self.addCleanup(os.remove, link)
        write(
            os.path.join(self.trees["i0001"], ".ccnavi/approved/doing/i0001-01.md"),
            ticket("i0001-01", "i0001"),
        )
        result = self.run_sh(os.path.join(link, ".claude", "worktrees", "i0001-01"), "push")
        stopped = "子チケットのワークツリー" in result.stderr
        if self.SH_DIR == self.ORIGINAL:
            # 直した sh は wip/design/scripts/ にあり、人が写すまで本物は外れたまま。
            # 写したらここが落ちるので、この分岐を消して下の assert だけにする。
            self.assertFalse(stopped, "写したなら、この分岐を消す")
            return
        self.assertTrue(stopped, result.stderr)


if __name__ == "__main__":
    unittest.main()
