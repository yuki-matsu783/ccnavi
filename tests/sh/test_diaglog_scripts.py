"""診断ログを使い始めた sh（承認・チケット・片付け・運ぶ・取ってくる・開発用の hook）の検査。

見るのは 3 つ。中身の形は logger 自体のテスト（tests/sh/test_diaglog_sh.py）が固定しているので、
ここでは「識別子の綴り」と「契約の文面が変わっていないこと」だけを見る。

1. 止める理由の識別子。`fail` の 1 つ目の引数と、log_* に渡す `reason=` の値が、どれも
   kebab-case の固定の語であること（変数や文面を入れない）
2. 標準出力・標準エラー・終了コードが前と同じ文面のままで、診断ログには識別子だけが残ること。
   exec をやめて子として走らせた ccnavi-ticket.sh と ccnavi-clean.sh が、子の終了コードを
   そのまま返すこと
3. 開発用の hook（.claude/hooks/）は、ワークスペースの共通部を読めないときは何も書かず、
   読めるときも応答（標準エラー・終了コード）と logs/session/ の状態を変えないこと
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")
HOOKS = os.path.join(ROOT, ".claude", "hooks")

# 診断ログを使い始めたもの。fail を持つものは 2 つ目に True。
LOGGED = (
    (os.path.join(SCRIPTS, "ccnavi-approve.sh"), True),
    (os.path.join(SCRIPTS, "ccnavi-ticket.sh"), True),
    (os.path.join(SCRIPTS, "ccnavi-clean.sh"), True),
    (os.path.join(SCRIPTS, "ccnavi-push-approved.sh"), False),
    (os.path.join(SCRIPTS, "ccnavi-fetch.sh"), False),
    (os.path.join(HOOKS, "lint-py.sh"), False),
    (os.path.join(HOOKS, "test-py.sh"), False),
    (os.path.join(HOOKS, "test-ext.sh"), False),
    (os.path.join(HOOKS, "mark-ext.sh"), False),
)

HEAD = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} "
    r"(?P<level>DEBUG|INFO |WARN |ERROR) (?P<name>[^\[\s]+)\[\d+\] "
)

WS_MISSING = (
    "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から"
    "上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの"
    "絶対パスを渡してください。"
)
NO_BIN = (
    "ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ の"
    "どれにも無い）。"
    "build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。"
)


def code_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [x for x in f if not x.lstrip().startswith("#")]


def base_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(extra)
    return env


def rest(line: str) -> str:
    """時刻・レベル・出どころ[pid] を除いた残り。"""
    m = HEAD.match(line)
    assert m is not None, line
    return line[m.end() :]


class IdentifierTest(unittest.TestCase):
    """識別子は全部の呼び出しに付き、kebab-case の固定の語。"""

    def test_every_fail_names_an_identifier(self):
        for path, has_fail in LOGGED:
            if not has_fail:
                continue
            calls = [x for x in code_lines(path) if re.search(r"(^|[\s;|&(])fail ", x)]
            self.assertTrue(calls, path)
            for line in calls:
                with self.subTest(script=os.path.basename(path), line=line.strip()[:60]):
                    self.assertRegex(line, r"(^|[\s;|&(])fail [a-z][a-z-]* \"")

    def test_every_reason_is_a_fixed_kebab_word(self):
        for path, _ in LOGGED:
            lines = [
                x for x in code_lines(path) if re.search(r"\blog_(debug|info|warn|error)\b", x)
            ]
            self.assertTrue(lines, path)
            for line in lines:
                if "reason=" not in line:
                    continue
                with self.subTest(script=os.path.basename(path), line=line.strip()[:60]):
                    if "reason=$1" in line:
                        # fail の中。識別子は呼ぶ側が固定の語で渡す（上の検査）。
                        continue
                    self.assertRegex(line, r"\"reason=[a-z][a-z-]*\"")

    def test_the_logger_is_not_given_the_text(self):
        """fail は文面（$2）を診断ログに渡さない。"""
        for path, has_fail in LOGGED:
            if not has_fail:
                continue
            with open(path, encoding="utf-8") as f:
                body = f.read()
            m = re.search(r"^fail\(\) \{\n(.*?)^\}", body, re.S | re.M)
            assert m is not None, path
            logs = [x for x in m.group(1).splitlines() if "log_" in x]
            with self.subTest(script=os.path.basename(path)):
                self.assertEqual(1, len(logs))
                self.assertNotIn("$2", logs[0])


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class _Workspace(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-diaglog-scripts-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(self.scripts)
        for name in os.listdir(SCRIPTS):
            if name.endswith((".sh", ".js")):
                shutil.copy(os.path.join(SCRIPTS, name), self.scripts)

    def run_script(self, name: str, *args: str, cwd: str = "", **env: str):
        return subprocess.run(
            [SHELL, os.path.join(self.scripts, name), *args],
            cwd=cwd or self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env(**{"CCNAVI_WORKSPACE": self.ws, **env}),
            check=False,
        )

    def lines(self, name: str) -> list[str]:
        try:
            with open(os.path.join(self.ws, "logs", "diag", f"{name}.log"), encoding="utf-8") as f:
                return [rest(x) for x in f.read().splitlines()]
        except FileNotFoundError:
            return []

    def stub_bin(self, rc: int) -> str:
        """--version に互換の版を答え、ほかは 1 行出して rc で終わる実行ファイル。"""
        path = os.path.join(self.ws, "stub-bin")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "#!/bin/sh\n"
                'if [ "${1:-}" = --version ]; then echo "compat: 1"; exit 0; fi\n'
                'echo "stub $*"\n'
                'echo "stub-err" >&2\n'
                f"exit {rc}\n"
            )
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path


class ApproveTest(_Workspace):
    def test_a_non_identifier_keeps_the_text_and_logs_the_identifier(self):
        result = self.run_script("ccnavi-approve.sh", "--yes")
        self.assertEqual(2, result.returncode)
        self.assertTrue(
            result.stderr.startswith("ccnavi-approve: 識別子でない引数は取りません (--yes)。\n")
        )
        self.assertEqual(["止めた exit=2 reason=not-an-id"], self.lines("ccnavi-approve"))

    def test_no_bin_keeps_the_text(self):
        result = self.run_script("ccnavi-approve.sh")
        self.assertEqual((2, ""), (result.returncode, result.stdout))
        self.assertEqual(f"ccnavi-approve: {NO_BIN}\n", result.stderr)
        self.assertEqual(
            ["受け付けた ids=0", "止めた exit=2 reason=no-bin"], self.lines("ccnavi-approve")
        )

    def test_a_refused_approval_exits_1(self):
        result = self.run_script("ccnavi-approve.sh", "i0001", CCNAVI_BIN_PATH=self.stub_bin(1))
        self.assertEqual(1, result.returncode)
        self.assertIn("--approve i0001", result.stdout)
        self.assertEqual(
            ["受け付けた ids=1", "終わった exit=1 reason=not-approved"],
            self.lines("ccnavi-approve"),
        )

    def test_no_workspace_keeps_the_text(self):
        other = tempfile.mkdtemp(prefix="ccnavi-diaglog-nows-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        result = self.run_script("ccnavi-approve.sh", CCNAVI_WORKSPACE=other)
        self.assertEqual(2, result.returncode)
        self.assertEqual(f"ccnavi-approve: {WS_MISSING}\n", result.stderr)


class TicketTest(_Workspace):
    def test_an_unknown_sub_keeps_the_text_and_logs_the_identifier(self):
        result = self.run_script("ccnavi-ticket.sh", "https://u:SECRET@h", "x")
        self.assertEqual((2, ""), (result.returncode, result.stdout))
        self.assertEqual(
            "ccnavi-ticket: https://u:SECRET@h は通しません。"
            "使えるのは start / finish / cancel / record-risk です。\n",
            result.stderr,
        )
        self.assertEqual(["止めた sub= exit=2 reason=unknown-sub"], self.lines("ccnavi-ticket"))

    def test_missing_args_print_usage_and_exit_2(self):
        result = self.run_script("ccnavi-ticket.sh", "start")
        self.assertEqual(2, result.returncode)
        self.assertIn("record-risk", result.stdout)
        self.assertEqual(["止めた exit=2 reason=missing-args"], self.lines("ccnavi-ticket"))

    def test_no_bin_keeps_the_text(self):
        result = self.run_script("ccnavi-ticket.sh", "start", "i0001")
        self.assertEqual(2, result.returncode)
        self.assertEqual(f"ccnavi-ticket: {NO_BIN}\n", result.stderr)
        self.assertEqual(
            ["受け付けた sub=start args=2", "止めた sub=start exit=2 reason=no-bin"],
            self.lines("ccnavi-ticket"),
        )

    def test_the_child_exit_code_and_output_pass_through(self):
        """exec をやめても、実行ファイルの出力と終了コードはそのまま。"""
        result = self.run_script(
            "ccnavi-ticket.sh", "finish", "i0001", CCNAVI_BIN_PATH=self.stub_bin(5)
        )
        self.assertEqual(5, result.returncode)
        self.assertEqual(f"stub --root {self.ws} ticket finish i0001\n", result.stdout)
        self.assertEqual("stub-err\n", result.stderr)
        self.assertEqual(
            ["受け付けた sub=finish args=2", "終わった sub=finish exit=5"],
            self.lines("ccnavi-ticket"),
        )


class CleanTest(_Workspace):
    def test_refusals_keep_the_text_and_log_the_identifier(self):
        cases = (
            (
                ("--force",),
                "--force は通しません。使えるのは --dry-run だけです。",
                "unknown-option",
            ),
            (("a", "b"), "ワークツリーは 1 回に 1 本だけ指定します（a と b）。", "two-names"),
            (
                ("a/b",),
                "名前にパスは書けません（a/b）。"
                ".claude/worktrees/ の直下の名前だけを渡してください。",
                "path-in-name",
            ),
            (
                ("nope",),
                f"{self.ws}/.claude/worktrees/nope がありません。",
                "no-tree",
            ),
        )
        for args, text, reason in cases:
            with self.subTest(reason=reason):
                log = os.path.join(self.ws, "logs", "diag", "ccnavi-clean.log")
                if os.path.exists(log):
                    os.remove(log)
                result = self.run_script("ccnavi-clean.sh", *args)
                self.assertEqual((2, ""), (result.returncode, result.stdout))
                self.assertEqual(f"ccnavi-clean: {text}\n", result.stderr)
                self.assertEqual(f"止めた exit=2 reason={reason}", self.lines("ccnavi-clean")[-1])

    def test_the_result_is_logged_with_the_same_exit_code(self):
        tree = os.path.join(self.ws, ".claude", "worktrees", "t1")
        os.makedirs(os.path.join(tree, "node_modules"))
        result = self.run_script("ccnavi-clean.sh", "t1", "--dry-run")
        self.assertEqual(0, result.returncode)
        self.assertIn("node_modules", result.stdout)
        logged = self.lines("ccnavi-clean")
        self.assertEqual("受け付けた tree=t1 dry_run=1", logged[0])
        self.assertRegex(logged[-1], r"^終わった tree=t1 via=(node|sh) exit=0$")


class PushApprovedTest(_Workspace):
    def test_an_argument_keeps_the_text(self):
        result = self.run_script("ccnavi-push-approved.sh", "x")
        self.assertEqual(2, result.returncode)
        self.assertTrue(result.stderr.startswith("ccnavi-push-approved: 引数は取りません。\n"))
        self.assertEqual(
            ["止めた exit=2 reason=unexpected-arg"], self.lines("ccnavi-push-approved")
        )

    def test_nothing_to_carry_keeps_the_text_and_logs_the_result(self):
        result = self.run_script("ccnavi-push-approved.sh")
        got = (result.returncode, result.stdout, result.stderr)
        self.assertEqual((0, "運ぶ承認済みチケットは無い。\n", ""), got)
        self.assertEqual(["受け付けた", "終わった exit=0"], self.lines("ccnavi-push-approved"))


class FetchTest(_Workspace):
    def test_stdout_stays_empty_and_the_result_is_logged(self):
        result = self.run_script("ccnavi-fetch.sh")
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(
            ["受け付けた timeout=15", "終わった exit=0 reported=no"], self.lines("ccnavi-fetch")
        )


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class HookTest(unittest.TestCase):
    """開発用の hook。共通部を読めなければ何も書かず、読めても応答と状態は変わらない。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-diaglog-hooks-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def with_common(self) -> None:
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        shutil.copy(os.path.join(SCRIPTS, "ccnavi-common.sh"), scripts)

    def run_hook(self, name: str, payload: str, **env: str):
        return subprocess.run(
            [SHELL, os.path.join(HOOKS, name)],
            cwd=self.ws,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env(CLAUDE_PROJECT_DIR=self.ws, **env),
            check=False,
        )

    def diag(self, name: str) -> list[str]:
        try:
            with open(os.path.join(self.ws, "logs", "diag", f"{name}.log"), encoding="utf-8") as f:
                return [rest(x) for x in f.read().splitlines()]
        except FileNotFoundError:
            return []

    def mark(self, **env: str):
        payload = (
            '{"session_id":"s1","tool_input":{"file_path":"'
            + self.ws
            + '/vscode-extension/ccnavi-board/src/a.ts"}}'
        )
        return self.run_hook("mark-ext.sh", payload, **env)

    def marks(self) -> str:
        with open(os.path.join(self.ws, "logs", "session", "s1.ext-files"), encoding="utf-8") as f:
            return f.read()

    def test_without_the_common_part_nothing_is_logged(self):
        result = self.mark(CCNAVI_LOG_LEVEL="DEBUG")
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(f"{self.ws}/vscode-extension/ccnavi-board/src/a.ts\n", self.marks())
        self.assertFalse(os.path.exists(os.path.join(self.ws, "logs", "diag")))

    def test_mark_ext_writes_debug_only(self):
        self.with_common()
        result = self.mark()
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual([], self.diag("mark-ext"))
        result = self.mark(CCNAVI_LOG_LEVEL="DEBUG")
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(
            [f"印を付けた file={self.ws}/vscode-extension/ccnavi-board/src/a.ts"],
            self.diag("mark-ext"),
        )
        # 状態のファイルは前と同じ（1 回に 1 行）。
        line = f"{self.ws}/vscode-extension/ccnavi-board/src/a.ts\n"
        self.assertEqual(line * 2, self.marks())

    def test_test_ext_without_marks_keeps_silent(self):
        self.with_common()
        result = self.run_hook("test-ext.sh", '{"session_id":"s1"}', CCNAVI_LOG_LEVEL="DEBUG")
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(["回さなかった reason=no-marks"], self.diag("test-ext"))

    def test_test_ext_without_a_runnable_tree_keeps_the_text(self):
        self.with_common()
        self.mark()
        result = self.run_hook("test-ext.sh", '{"session_id":"s1"}')
        if shutil.which("node") is None:
            self.assertEqual(
                "ccnavi: node が無いので拡張のテストを回していません（要るのは Node 22）。\n",
                result.stderr,
            )
            self.assertEqual(["回さなかった exit=0 reason=no-node"], self.diag("test-ext"))
            return
        self.assertEqual(0, result.returncode)
        self.assertEqual(
            f"ccnavi: {self.ws}/vscode-extension/ccnavi-board にテストの入口が無いので"
            "回していません。\n"
            "ccnavi: 拡張を触った印はありますが、回せるツリーが見つかりませんでした。\n",
            result.stderr,
        )
        self.assertEqual(
            [
                f"回さなかった tree={self.ws}/vscode-extension/ccnavi-board reason=no-entry",
                "回さなかった exit=0 reason=no-runnable-tree",
            ],
            self.diag("test-ext"),
        )


if __name__ == "__main__":
    unittest.main()
