"""保護済みの sh を呼ぶ形のうち、実行前に止める 2 つ（src/ccnavi/hook/wrapguard.py）。

止める・通すの判定は hook が持ち、sh の検査は 2 重目の確認にする。

1. sh の検査の材料を変える環境変数を、同じコマンド行で置いて保護済みの sh を呼ぶ形
2. 子チケットのワークツリーからの `ccnavi-git.sh push`。親エージェントが打っても止める

見本の表（CHILD_CASES）は、コピーする版の `ccnavi-git.sh` にも同じものを渡して、sh の検査が
2 重目として同じく止めることを確かめる（test_sh_agrees）。hook と sh の検査は同じことを
別の実装で持つので、食い違いはこの表で見つける。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.hook import wrapguard
from ccnavi.infra import shellread
from tests import GIT_ENV, ROOT, SH_SCRIPTS, common_path, common_sh
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
            "env -u CCNAVI_BIN_PATH sh .ccnavi/scripts/ccnavi-ticket.sh finish a",
            "export CCNAVI_WORKSPACE=/x; sh .ccnavi/scripts/ccnavi-git.sh push",
            "CCNAVI_STATE=/x; sh .ccnavi/scripts/ccnavi-review.sh confirm",
            "unset CCNAVI_TICKETS_PROPOSAL && sh .ccnavi/scripts/ccnavi-git.sh push",
            "sudo env CLAUDE_PROJECT_DIR=/x bash .ccnavi/scripts/ccnavi-review.sh confirm",
            "CCNAVI_NEW_THING=1 ../../../.ccnavi/scripts/ccnavi-git.sh status",
            "cd a && CCNAVI_BIN_PATH=f sh ../.ccnavi/scripts/ccnavi-agree.sh",
            # 関数の定義の本体。頭（`f()`・`function f`）を名前と読み違えると見落とす。
            "f() { CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
            "f(){ CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
            "function f { CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
            # 環境を丸ごと空にする。settings.json の env が書き出した置き場も消える。
            "env -i sh .ccnavi/scripts/ccnavi-git.sh push",
            "env --ignore-environment sh .ccnavi/scripts/ccnavi-git.sh push",
            # 値を取るオプションの後ろの代入。
            "env -C /tmp CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push",
            # ループの中では、後ろに書いた代入も次の回の呼び出しに届く。
            "for i in 1 2; do sh .ccnavi/scripts/ccnavi-git.sh push; CCNAVI_X=1; done",
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
            # 読み切れない形はここでは見ない。allow が当たらずに確認になる。
            "sh -c 'CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push'",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.names(command))

    def test_pass_through_is_listed_not_guessed(self):
        # 通すものを並べる形。sh があとから読むようになった変数は止まる側。
        self.assertFalse(wrapguard.blocked("CCNAVI_GIT_KEEP_LOGS"))
        self.assertTrue(wrapguard.blocked("CCNAVI_GIT_SOMETHING_NEW"))
        self.assertFalse(wrapguard.blocked("PATH"))


class EnvHookTest(unittest.TestCase):
    """環境変数の代入を hook の入口から通す。上の EnvTest は読みの部品を直接呼ぶので、
    判定の中で「ccnavi 自身を守る設定」で切れることと、ルールより先に止めることは見ない。"""

    COMMAND = "CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push"

    # 同じコマンドを通す allow を置く。ルールが先に当たるなら通ってしまう。
    RULES = {
        "version": 1,
        "allow": [{"id": "wrap", "match": "Bash", "regex": r"ccnavi-git\.sh"}],
    }

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-env-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        write(common_path(self.root, "rules"), json.dumps(self.RULES))

    def hook(self, command, **extra_env):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.root,
            "session_id": "s1",
            "permission_mode": "default",
            "tool_input": {"command": command},
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(extra_env)
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
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason") or ""

    def test_stops_before_an_allow_rule(self):
        decision, reason = self.hook(self.COMMAND)
        self.assertEqual(decision, "deny")
        self.assertIn(wrapguard.CODE_ENV, reason)
        self.assertIn("CCNAVI_WORKSPACE", reason)

    def test_the_same_call_without_the_assignment_reaches_the_rule(self):
        decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh push")
        # allow が当たって通る呼び出しは何も出さない。
        self.assertEqual(decision, "")
        self.assertNotIn(wrapguard.CODE_ENV, reason)

    def test_turned_off_with_the_core_files_guard(self):
        # ccnavi 自身を守る設定を切ると、この止め方も切れ、allow が当たる。
        decision, reason = self.hook(self.COMMAND, CCNAVI_GUARD_CORE_FILES="disable")
        self.assertEqual(decision, "")
        self.assertNotIn(wrapguard.CODE_ENV, reason)


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
        self.trees = {n: os.path.join(base, n) for n in ("i0001", "i0001-01-01", "plain")}
        for path in self.trees.values():
            os.makedirs(path)

    def place(self, where):
        parent = self.trees["i0001"]
        if where == "parent-doing":
            return write(
                os.path.join(parent, ".ccnavi/approved/doing/i0001-01-01.md"),
                ticket("i0001-01-01", "i0001"),
            )
        if where == "root-done":
            return write(
                os.path.join(self.root, ".ccnavi/approved/done/i0001-01-01.md"),
                ticket("i0001-01-01", "i0001"),
            )
        if where == "parent-review":
            return write(
                os.path.join(parent, "wip/proposals/review/i0001-01-01.md"),
                ticket("i0001-01-01", "i0001"),
            )
        if where == "broken":
            return write(
                os.path.join(parent, ".ccnavi/approved/doing/i0001-01-01.md"),
                "---\nparent: i0001\n: : [unclosed\n",
            )
        raise AssertionError(where)

    def cwd(self, where):
        return {
            "parent": self.trees["i0001"],
            "child": self.trees["i0001-01-01"],
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
            self.root, "cd .claude/worktrees/i0001-01-01 && " + " ".join(GIT + ["push"])
        )
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)
        # 親のツリーへ移ってから打つ形は通す。
        reason = self.hook(self.trees["i0001-01-01"], "cd ../i0001 && " + " ".join(GIT + ["push"]))
        self.assertNotIn(wrapguard.CODE_CHILD_PUSH, reason)

    def test_inside_a_function(self):
        self.place("parent-doing")
        command = "f() { " + " ".join(GIT + ["push"]) + "; }; f"
        reason = self.hook(self.trees["i0001-01-01"], command)
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)

    def test_unreadable_cd_stops(self):
        reason = self.hook(self.root, 'cd "$X" && ' + " ".join(GIT + ["push"]))
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)

    def test_refusal_names_the_builtin_rule(self):
        # 文面の先頭に、記録の `rules` と同じ識別子を出す。
        reason = self.hook(self.root, 'cd "$X" && ' + " ".join(GIT + ["push"]))
        self.assertIn(
            f"[ccnavi] {wrapguard.CODE_CHILD_PUSH} (builtin rule: {wrapguard.CHILD_PUSH_RULE_ID})",
            reason,
        )

    def test_symlinked_workspace(self):
        # 論理のパスと実際のパスを混ぜると外れる。cwd が symlink 越しでも止める。
        self.place("parent-doing")
        link = self.root + "-link"
        try:
            os.symlink(self.root, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink を作れない")
        self.addCleanup(os.remove, link)
        child = os.path.join(link, ".claude", "worktrees", "i0001-01-01")
        reason = self.hook(child, " ".join(GIT + ["push"]))
        self.assertIn(wrapguard.CODE_CHILD_PUSH, reason)


@unittest.skipIf(shutil.which("git") is None, "git が無い")
class ShAgreesTest(unittest.TestCase):
    """同じ見本をコピーする版の sh にも渡し、2 重目として同じく止めることを確かめる。

    `CCNAVI_SH_DIR` で sh の置き場を差し替えられる（コピーする版は wip/design/scripts/）。
    """

    ORIGINAL = os.path.join(ROOT, ".ccnavi", "scripts")
    SH_DIR = os.path.abspath(os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR") or ORIGINAL))

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-sh-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        scripts = os.path.join(self.root, ".ccnavi", "scripts")
        os.makedirs(scripts)
        # 共通部（入口と部品）は 1 か所からまとめて写す。入口と部品の版が混ざらないように。
        common_dir = (
            self.SH_DIR
            if os.path.isfile(os.path.join(self.SH_DIR, "ccnavi-common.sh"))
            else SH_SCRIPTS
        )
        for name in ("ccnavi-git.sh", *common_sh(common_dir)):
            source = os.path.join(self.SH_DIR, name)
            if not os.path.isfile(source):
                # コピーする版は変えた sh だけを置く。残りは実物を使う。
                source = os.path.join(ROOT, ".ccnavi", "scripts", name)
            shutil.copy(source, scripts)
        self.git("init", "--quiet", "-b", "main", cwd=self.root)
        write(os.path.join(self.root, ".gitignore"), ".claude/\nlogs/\n")
        self.git("add", "-A", cwd=self.root)
        self.git("commit", "--quiet", "-m", "init", cwd=self.root)
        self.trees = {}
        for name in ("i0001", "i0001-01-01", "plain"):
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
            "parent-doing": os.path.join(parent, ".ccnavi/approved/doing/i0001-01-01.md"),
            "root-done": os.path.join(self.root, ".ccnavi/approved/done/i0001-01-01.md"),
            "parent-review": os.path.join(parent, "wip/proposals/review/i0001-01-01.md"),
            "broken": os.path.join(parent, ".ccnavi/approved/doing/i0001-01-01.md"),
        }
        for where, sub, placed, stops in CHILD_CASES:
            if sub != "push":
                continue
            with self.subTest(where=where, placed=placed):
                text = (
                    "---\nparent: i0001\n: : [unclosed\n"
                    if placed == "broken"
                    else ticket("i0001-01-01", "i0001")
                )
                path = write(places[placed], text)
                try:
                    cwd = {
                        "parent": parent,
                        "child": self.trees["i0001-01-01"],
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
            os.path.join(self.trees["i0001"], ".ccnavi/approved/doing/i0001-01-01.md"),
            ticket("i0001-01-01", "i0001"),
        )
        result = self.run_sh(os.path.join(link, ".claude", "worktrees", "i0001-01-01"), "push")
        # 実際の sh も両辺をリンクを解いたパスで比べる。比べないとリンクを経た作業場で保護が外れる。
        stopped = "子チケットのワークツリー" in result.stderr
        self.assertTrue(stopped, result.stderr)


# （ラッパーに渡す引数, 止まるか）。居場所はワークスペースルート。
# ワークスペースには .ccnavi/scripts/ccnavi-git.sh・.ccnavi/common/rules.yml・src/a.py・
# projects/p/.ccnavi/config/rules.yml・.claude/settings.json がある（CorePathTest.FILES）。
CORE_CASES = (
    # 消す。ラッパー自身を消すと、生の git の組み込みの止め（ラッパーがあるときだけ）も外れる。
    ("rm .ccnavi/scripts/ccnavi-git.sh", True),
    ("rm -r .ccnavi/scripts", True),
    ("rm --cached .ccnavi/common/rules.yml", True),
    ("rm .claude/settings.json", True),
    ("rm -r projects", True),
    ("rm *.sh", True),
    ("rm :/.ccnavi", True),
    ("rm --pathspec-from-file=list.txt", True),
    ("rm src/a.py", False),
    ("rm src/*.py", False),
    # 何も消さない形と、ワークスペースの記録の置き場ではない logs。
    ("rm -n .claude/settings.json", False),
    ("rm .claude/settings.json --dry-run", False),
    ("rm -r tests/logs", False),
    # 別のコミットの中身に戻す。
    ("restore --source=HEAD~5 -- .ccnavi", True),
    ("restore -s HEAD .ccnavi/common/rules.yml", True),
    ("restore -sHEAD .claude/settings.json", True),
    ("restore .ccnavi/common/rules.yml --source HEAD", True),
    ("restore --source=HEAD -- ./", True),
    ("restore --source=HEAD --pathspec-from-file=list.txt", True),
    ("restore --source=HEAD -- src/a.py", False),
    ("checkout HEAD .ccnavi/common/rules.yml", True),
    ("checkout HEAD src/a.py", False),
    # 索引の中身に戻す形と、衝突の解決は通す。
    ("restore .ccnavi/common/rules.yml", False),
    ("restore --staged .ccnavi/common/rules.yml", False),
    ("restore --ours -- .ccnavi/common/rules.yml", False),
    ("restore --theirs .ccnavi/common/rules.yml", False),
    ("checkout main", False),
    ("checkout -b x main", False),
    ("add .ccnavi/common/rules.yml", False),
)


class CorePathTest(unittest.TestCase):
    """ラッパーで自己防衛の対象を消す・別のコミットの中身に戻す形（hook の側）。"""

    FILES = (
        ".ccnavi/scripts/ccnavi-git.sh",
        ".ccnavi/common/rules.yml",
        "src/a.py",
        "projects/p/.ccnavi/config/rules.yml",
        ".claude/settings.json",
        "tests/logs/a.txt",
        ".ccnavi/approved/phases/i0001/1.reviewed",
        "wip/proposals/review/i0002.md",
        "wip/scratchpad/a.md",
    )
    # 同じコマンドを通す allow を置く。ルールが先に当たるなら通ってしまう。
    RULES = {
        "version": 1,
        "allow": [{"id": "wrap", "match": "Bash", "regex": r"ccnavi-git\.sh"}],
    }

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-core-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in self.FILES:
            write(os.path.join(self.root, name), "x\n")
        write(common_path(self.root, "rules"), json.dumps(self.RULES))

    def hook(self, command, cwd="", **extra_env):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": cwd or self.root,
            "session_id": "s1",
            "permission_mode": "default",
            "tool_input": {"command": command},
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(extra_env)
        result = run_ccnavi(
            ["--root", self.root, "--log", "", "--state", os.path.join(self.root, "state")]
            + ["--mode", "enable"],
            input=json.dumps(payload),
            cwd=ROOT,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason") or ""

    def test_table(self):
        for args, stops in CORE_CASES:
            with self.subTest(args=args):
                decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh " + args)
                if stops:
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn(wrapguard.CODE_CORE_PATH, reason)
                else:
                    self.assertEqual(decision, "", reason)

    def test_refusal_names_the_way_out(self):
        _, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/scripts/ccnavi-git.sh")
        self.assertIn(
            f"[ccnavi] {wrapguard.CODE_CORE_PATH} (builtin rule: {wrapguard.CORE_PATH_RULE_ID})",
            reason,
        )
        # 代わりの手段（ユーザに依頼）と、通る衝突の解決を名指しする。実パスで書く。
        self.assertIn("ユーザに依頼", reason)
        self.assertIn(f"{os.path.realpath(self.root)}/.ccnavi/scripts/ccnavi-git.sh", reason)
        self.assertIn("restore --ours", reason)

    def test_follows_cd(self):
        # 居場所から解いたパスで見る。
        for command, stops in (
            ("cd .ccnavi && sh scripts/ccnavi-git.sh rm scripts/ccnavi-git.sh", True),
            ("cd src && sh ../.ccnavi/scripts/ccnavi-git.sh restore --source=HEAD -- ..", True),
            ("cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm ../.claude/settings.json", True),
            ("cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm a.py", False),
            # 居場所が読めない。上へ出る語は当たる先を決められない。
            ('cd "$X" && sh /x/.ccnavi/scripts/ccnavi-git.sh rm ../a.py', True),
        ):
            with self.subTest(command=command):
                decision, _ = self.hook(command)
                self.assertEqual(decision == "deny", stops, command)

    def test_hook_only_forms(self):
        """sh が別の理由で断る形と、sh が通してユーザに任せる形も、
        hook はエージェントから止める。
        """
        for args, kind_text in (
            # 長いオプションの省略形（sh は省略形を断る）。
            ("restore --sou=HEAD~1 .claude/settings.json", "ccnavi 自身を守る場所"),
            ("restore --so HEAD~1 .claude/settings.json", "ccnavi 自身を守る場所"),
            ("rm --pathspec-from-fi=list.txt", "ccnavi 自身を守る場所"),
            # 索引の中身に戻す checkout（sh は restore を案内して断る）。文面も restore を案内する。
            ("checkout -- .ccnavi/common/rules.yml", "restore <パス>"),
            # レビュー済みのマーカーを消すのはユーザが端末で打つ手順（sh は通す）。
            ("rm .ccnavi/approved/phases/i0001/1.reviewed", "ユーザに依頼"),
        ):
            with self.subTest(args=args):
                decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh " + args)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(wrapguard.CODE_CORE_PATH, reason)
                self.assertIn(kind_text, reason)

    def test_review_place(self):
        """レビュー待ちの置き場（wip/proposals/review/）は、承認済みチケットの置き場と同じく
        エージェントのラッパーの呼び出しを止める。置き場の親を丸ごと消す形も当たる。"""
        target = os.path.join(self.root, "wip", "proposals", "review", "i0002.md")
        for command, stops in (
            ("sh .ccnavi/scripts/ccnavi-git.sh rm wip/proposals/review/i0002.md", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm -r wip/proposals/review", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm --cached wip/proposals/review/i0002.md", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm -r wip", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm -r wip/proposals/", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm 'wip/*'", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm WIP/Proposals/Review/i0002.md", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh mv wip/proposals/review/i0002.md wip/x.md", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh restore --source=HEAD~1 wip/proposals/review", True),
            (
                "sh .ccnavi/scripts/ccnavi-git.sh checkout HEAD~1 wip/proposals/review/i0002.md",
                True,
            ),
            ("cd wip/proposals && sh ../../.ccnavi/scripts/ccnavi-git.sh rm review/i0002.md", True),
            ("cd wip && sh ../.ccnavi/scripts/ccnavi-git.sh rm -r proposals", True),
            (f"sh .ccnavi/scripts/ccnavi-git.sh rm {target}", True),
            # 下書きの置き場と、何も消さない・索引に戻す形は通す。
            ("sh .ccnavi/scripts/ccnavi-git.sh rm -r wip/scratchpad", False),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm wip/scratchpad/a.md", False),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm -n wip/proposals/review/i0002.md", False),
            (
                "sh .ccnavi/scripts/ccnavi-git.sh restore --staged wip/proposals/review/i0002.md",
                False,
            ),
            ("sh .ccnavi/scripts/ccnavi-git.sh add wip/proposals/review/i0002.md", False),
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision == "deny", stops, reason)
                if stops:
                    self.assertIn(wrapguard.CODE_CORE_PATH, reason)
                    self.assertIn("レビュー待ち", reason)
                    self.assertIn("ユーザに依頼", reason)

    def test_parent_of_an_empty_review_place(self):
        """親を閉じた後の案内（ops._close_parent の `rm -r wip`）は、レビュー待ちが空なら通す。"""
        os.remove(os.path.join(self.root, "wip", "proposals", "review", "i0002.md"))
        for args in ("rm -r wip", "rm -r wip/proposals"):
            with self.subTest(args=args):
                decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh " + args)
                self.assertEqual(decision, "", reason)
        shutil.rmtree(os.path.join(self.root, "wip", "proposals"))
        decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh rm -r wip")
        self.assertEqual(decision, "", reason)

    def test_restoring_the_parent_of_an_empty_review_place(self):
        """空の親を通すのは rm だけ。過去のコミットに戻すと、レビュー待ちの提案が戻りうる。"""
        os.remove(os.path.join(self.root, "wip", "proposals", "review", "i0002.md"))
        for args in (
            "restore --source HEAD~1 wip",
            "restore -s HEAD~1 wip/proposals",
            "checkout HEAD~1 -- wip",
            "checkout HEAD~1 wip/proposals",
        ):
            with self.subTest(args=args):
                decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh " + args)
                self.assertEqual(decision, "deny", reason)
                self.assertIn("レビュー待ち", reason)
        decision, reason = self.hook("sh .ccnavi/scripts/ccnavi-git.sh restore --staged wip")
        self.assertEqual(decision, "", reason)

    def test_unreadable_targets_stop(self):
        """対象を読み切れない形は、共通ルールの allow に落とさずに止める。"""
        for command, stops in (
            ("X=.ccnavi; sh .ccnavi/scripts/ccnavi-git.sh rm -r $X", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh rm $(printf .ccnavi/rules.yml)", True),
            ("pushd .ccnavi && sh scripts/ccnavi-git.sh rm rules.yml", True),
            ('cd "$D" && sh /x/.ccnavi/scripts/ccnavi-git.sh rm a.py', True),
            ("sh .ccnavi/scripts/ccnavi-git.sh restore --source=HEAD -- $P", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh checkout $B .ccnavi/common/rules.yml", True),
            # 起点だけを変数で渡す形と、索引に戻す形は、対象が読めるので通す。
            ("sh .ccnavi/scripts/ccnavi-git.sh restore --source $R src/a.py", False),
            ("sh .ccnavi/scripts/ccnavi-git.sh checkout $B", False),
            ("sh .ccnavi/scripts/ccnavi-git.sh restore $P", False),
            # 副命令が文字どおりの語でない（変数・置換・glob）。
            ("S=rm; sh .ccnavi/scripts/ccnavi-git.sh $S .ccnavi/approved/t/r", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh ${S:-rm} .ccnavi/approved/t/r", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh $(echo rm) .ccnavi/approved/t/r", True),
            ("sh .ccnavi/scripts/ccnavi-git.sh r? .ccnavi/approved/t/r", True),
            # 標準入力から読むシェルに流す文字列の中の呼び出し。
            ("echo 'sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/approved/t/r' | sh", True),
            ("echo 'sh .ccnavi/scripts/ccnavi-git.sh status; rm -rf .ccnavi' | sh", True),
            # 長い旗・値を取る旗を前に置いたシェルと、`env`・`command` 越しのシェルも読む。
            *(
                (
                    f"echo 'sh .ccnavi/scripts/ccnavi-git.sh rm wip/proposals/review/i0002.md'"
                    f" | {sh}",
                    True,
                )
                for sh in (
                    "bash --norc",
                    "bash --posix",
                    "bash --noprofile",
                    "bash --rcfile x",
                    "bash -o pipefail",
                    "bash -O extglob",
                    "bash -euo pipefail",
                    "env sh",
                    "env -i FOO=1 bash",
                    "command sh",
                )
            ),
            # 走査が読み切れず、ラッパーの名前の後ろに文字どおりの副命令が無い。
            ("echo rm .ccnavi/approved/t/r | xargs sh .ccnavi/scripts/ccnavi-git.sh", True),
            ("xargs -a f sh .ccnavi/scripts/ccnavi-git.sh", True),
            # 直接の呼び出しは通す。
            ('sh .ccnavi/scripts/ccnavi-git.sh commit -m "fix: $X"', False),
            ("sh .ccnavi/scripts/ccnavi-git.sh diff $F", False),
            ("sh .ccnavi/scripts/ccnavi-git.sh status", False),
        ):
            with self.subTest(command=command):
                decision, reason = self.hook(command)
                self.assertEqual(decision == "deny", stops, reason)
                if stops:
                    self.assertIn(wrapguard.CODE_CORE_PATH, reason)

    def test_restore_from_a_list_without_source(self):
        # 索引の中身に戻すだけなら、パスをファイルから読む形もここでは止めない。
        decision, reason = self.hook(
            "sh .ccnavi/scripts/ccnavi-git.sh restore --pathspec-from-file=list.txt"
        )
        self.assertNotIn(wrapguard.CODE_CORE_PATH, reason)

    def test_absolute_path(self):
        target = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        decision, _ = self.hook(f"sh .ccnavi/scripts/ccnavi-git.sh rm {target}")
        self.assertEqual(decision, "deny")

    def test_turned_off_with_the_core_files_guard(self):
        decision, reason = self.hook(
            "sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/scripts/ccnavi-git.sh",
            CCNAVI_GUARD_CORE_FILES="disable",
        )
        self.assertNotIn(wrapguard.CODE_CORE_PATH, reason)


@unittest.skipIf(shutil.which("git") is None, "git が無い")
class CorePathShAgreesTest(unittest.TestCase):
    """同じ見本を sh にも渡し、2 重目として同じく止めることを確かめる。"""

    SH_DIR = ShAgreesTest.SH_DIR

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-wrapguard-core-sh-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        scripts = os.path.join(self.root, ".ccnavi", "scripts")
        os.makedirs(scripts)
        common_dir = (
            self.SH_DIR
            if os.path.isfile(os.path.join(self.SH_DIR, "ccnavi-common.sh"))
            else SH_SCRIPTS
        )
        for name in ("ccnavi-git.sh", *common_sh(common_dir)):
            source = os.path.join(self.SH_DIR, name)
            if not os.path.isfile(source):
                source = os.path.join(ROOT, ".ccnavi", "scripts", name)
            shutil.copy(source, scripts)
        for name in CorePathTest.FILES:
            if not os.path.exists(os.path.join(self.root, name)):
                write(os.path.join(self.root, name), "x\n")
        write(os.path.join(self.root, ".gitignore"), "/logs/\n")
        git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
        for args in (["init", "--quiet", "-b", "main"], ["add", "-A"], ["commit", "-qm", "i"]):
            subprocess.run(git + args, cwd=self.root, check=True, capture_output=True)

    def run_sh(self, args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(GIT_ENV)
        env["PWD"] = self.root
        script = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        # 消した形は試すたびに戻す（--dry-run は自己防衛の検査を通るので、本当に打つ）。
        try:
            return subprocess.run(
                ["sh", script, *args.split()],
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
            )
        finally:
            subprocess.run(
                ["git", "reset", "-q", "--hard"], cwd=self.root, env=env, capture_output=True
            )

    def test_sh_agrees(self):
        for args, stops in CORE_CASES:
            with self.subTest(args=args):
                result = self.run_sh(args)
                if stops:
                    # ツリー全体（`./`）と承認済みチケットの置き場を含む指定は、sh の前からある
                    # 拒否（restore-whole-tree・restore-store-source）が先に当たる。
                    self.assertEqual(result.returncode, 2, result.stderr)
                    said = ("ccnavi 自身を守る場所", "ツリー全体", "承認済みチケットの置き場")
                    self.assertTrue(any(s in result.stderr for s in said), result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_user_removes_a_review_marker(self):
        """レビュー済みのマーカーを消す手順（phase._erase_steps）は、sh が通す。"""
        result = self.run_sh("rm .ccnavi/approved/phases/i0001/1.reviewed")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_review_place_is_left_to_the_user(self):
        """レビュー待ちの置き場の `rm` は sh では止めない（ユーザが端末で打つ手順を塞がない）。
        過去の中身に戻す形は、前からの置き場の検査（store_hit）が止める。"""
        for args in ("rm wip/proposals/review/i0002.md", "rm -r wip"):
            with self.subTest(args=args):
                result = self.run_sh(args)
                self.assertEqual(result.returncode, 0, result.stderr)
        for args in (
            "restore --source=HEAD wip/proposals/review/i0002.md",
            "checkout HEAD wip/proposals/review/i0002.md",
        ):
            with self.subTest(args=args):
                result = self.run_sh(args)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_records_of_the_workspace_only(self):
        """記録の置き場はワークスペースルートの logs だけ。ほかの階層の logs は守る場所ではない。"""
        result = self.run_sh("rm -r logs/state")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ccnavi 自身を守る場所", result.stderr)
        result = self.run_sh("rm -r tests/logs")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
