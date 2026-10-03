"""sh の診断ログ（ccnavi-common.sh の log_*）と、3 つの言語で行が揃うこと。

見るのは 2 組。

1. sh の logger そのもの。行の形・置き場・出どころの決め方・レベルの絞り込み・logfmt の
   エスケープの仕方・URL と scp 形の資格情報の伏せ字。`set -eu` の下でも書けないときに 0 で戻り、
   標準出力にも標準エラーにも何も出さないこと。出さないレベルでは date を起こさないこと。
   リンク（logs・logs/diag・書き先）を辿らないこと、使えない字の出どころでは書かないこと、
   新しいファイルが 0600 になること
2. 同じ入力から、sh・Python（ccnavi/diaglog.py）・拡張（src/log.ts を node が型を取り除いて読む）が
   同じ行を出すこと。時刻と pid は除いて比べ、時刻は 3 つとも同じ形であることだけを見る
3. ccnavi-git.sh の reject と ccnavi-review.sh の fail が、拒否の文面ではなく識別子だけを
   診断ログに残すこと（文面は標準エラーの契約で、そちらは変わらない）

使い捨てのワークスペースに ccnavi-common.sh を置き、それを読む sh を書いて走らせる。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from ccnavi import diaglog
from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
NODE = shutil.which("node")
COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
LOG_TS = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "src", "log.ts")

HEAD = re.compile(
    r"^(?P<when>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) "
    r"(?P<level>DEBUG|INFO |WARN |ERROR) (?P<name>[^\[\s]+)\[(?P<pid>\d+)\] "
)

# 3 つの言語に同じものを渡す。(レベル, 本文, 値の組)。値は渡した順に並ぶ。
CASES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("INFO", "push を拒否した", [("reason", "unapproved"), ("ticket", "T-12")]),
    ("WARN", "値の逃がし方", [("v", "a b"), ("q", 'say "hi"\\x'), ("eq", "k=v"), ("tab", "a\tb")]),
    ("ERROR", "改行を\n含む本文", [("n", "l1\nl2\r\nl3\rl4"), ("empty", ""), ("bs", "a\\b")]),
    ("INFO", "値の無い行", []),
    ("DEBUG", "判定の材料", [("sub", "push"), ("path", "/tmp/x y/z")]),
    (
        "INFO",
        "clone https://u:p@h/x を読んだ",
        [
            ("url", "https://user:glpat-SECRET@gitlab.example/x.git"),
            ("scp", "oauth2:tok@gitlab.example:org/r.git"),
            ("plain", "git@github.com:org/r.git"),
            ("said", "push to ssh://git@h:22/p failed"),
        ],
    ),
]


def base_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.update(extra)
    return env


def body(line: str) -> tuple[str, str, str]:
    """時刻と pid を除いた (レベル, 出どころ, 残り)。"""
    m = HEAD.match(line)
    assert m is not None, line
    return m.group("level"), m.group("name"), line[m.end() :]


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class _Workspace(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-diaglog-sh-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        shutil.copy(COMMON, scripts)

    def run_sh(self, calls: str, name: str = "probe.sh", **env: str):
        """ccnavi-common.sh を読み、calls を走らせる sh を書いて起動する。"""
        path = os.path.join(self.ws, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write('set -eu\n. "$(dirname "$0")/.ccnavi/scripts/ccnavi-common.sh"\n')
            f.write(calls)
            f.write("\nprintf 'done\\n'\n")
        return subprocess.run(
            [SHELL, name],
            cwd=self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env(**env),
            check=False,
        )

    def lines(self, name: str = "probe") -> list[str]:
        try:
            with open(os.path.join(self.ws, "logs", "diag", f"{name}.log"), encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


def sh_call(level: str, msg: str, fields: list[tuple[str, str]]) -> str:
    words = [f"log_{level.lower()}", shlex.quote(msg)]
    if fields:
        words.append("--")
        words += [shlex.quote(f"{k}={v}") for k, v in fields]
    return " ".join(words)


class ShLoggerTest(_Workspace):
    def test_the_line_shape(self):
        result = self.run_sh("log_info push を拒否した -- reason=unapproved ticket=T-12")
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))
        (line,) = self.lines()
        self.assertEqual(
            ("INFO ", "probe", "push を拒否した reason=unapproved ticket=T-12"), body(line)
        )

    def test_message_words_are_joined_with_spaces(self):
        self.run_sh("log_warn 1 つ目 '2 つ目' -- a=1")
        (line,) = self.lines()
        self.assertEqual("1 つ目 2 つ目 a=1", body(line)[2])

    def test_a_field_without_equals_is_an_empty_value(self):
        self.run_sh("log_info m -- flag")
        self.assertEqual("m flag=", body(self.lines()[0])[2])

    def test_the_name_comes_from_the_script_or_ccnavi_log_name(self):
        self.run_sh("log_info x", name="other.sh")
        self.assertEqual(1, len(self.lines("other")))
        self.run_sh("log_info x", CCNAVI_LOG_NAME="named")
        self.assertEqual(1, len(self.lines("named")))

    def test_the_workspace_is_found_from_a_subdirectory(self):
        sub = os.path.join(self.ws, "a", "b")
        os.makedirs(sub)
        script = os.path.join(self.ws, "probe.sh")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(f". {shlex.quote(COMMON)}\nlog_info x\n")
        subprocess.run(
            [SHELL, script],
            cwd=sub,
            env=base_env(CCNAVI_WORKSPACE=self.ws),
            check=True,
            capture_output=True,
        )
        self.assertEqual(1, len(self.lines()))

    def test_levels_are_filtered(self):
        calls = "log_debug d\nlog_info i\nlog_warn w\nlog_error e"
        self.run_sh(calls)
        self.assertEqual(["INFO ", "WARN ", "ERROR"], [body(x)[0] for x in self.lines()])
        os.remove(os.path.join(self.ws, "logs", "diag", "probe.log"))
        self.run_sh(calls, CCNAVI_LOG_LEVEL="debug")
        self.assertEqual(4, len(self.lines()))
        os.remove(os.path.join(self.ws, "logs", "diag", "probe.log"))
        self.run_sh(calls, CCNAVI_LOG_LEVEL="Warn")
        self.assertEqual(["WARN ", "ERROR"], [body(x)[0] for x in self.lines()])
        os.remove(os.path.join(self.ws, "logs", "diag", "probe.log"))
        self.run_sh(calls, CCNAVI_LOG_LEVEL="loud")
        self.assertEqual(3, len(self.lines()))

    def test_a_dropped_level_does_not_start_date(self):
        # PATH の先頭に、呼ばれたら印を残す date を置く。
        bin_dir = os.path.join(self.ws, "fakebin")
        os.makedirs(bin_dir)
        mark = os.path.join(self.ws, "date-was-called")
        fake = os.path.join(bin_dir, "date")
        with open(fake, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"#!/bin/sh\n: >{shlex.quote(mark)}\nprintf '2026-09-27T10:15:03+0900\\n'\n")
        os.chmod(fake, 0o755)
        path = bin_dir + os.pathsep + os.environ.get("PATH", "")
        self.run_sh("log_debug x -- a=1\nlog_info y", CCNAVI_LOG_LEVEL="WARN", PATH=path)
        self.assertFalse(os.path.exists(mark), "出さないレベルで date が起きた")
        self.assertEqual([], self.lines())
        self.run_sh("log_warn z", CCNAVI_LOG_LEVEL="WARN", PATH=path)
        self.assertTrue(os.path.exists(mark))
        (line,) = self.lines()
        self.assertTrue(line.startswith("2026-09-27T10:15:03+09:00 WARN  probe["), line)

    def test_an_unwritable_place_is_dropped_under_set_eu(self):
        with open(os.path.join(self.ws, "logs"), "w", encoding="utf-8") as f:
            f.write("x")
        result = self.run_sh("log_error 書けない -- a=1\nlog_info もう 1 行")
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))

    def test_no_workspace_is_dropped(self):
        os.remove(os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-common.sh"))
        script = os.path.join(self.ws, "probe.sh")
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"set -eu\n. {shlex.quote(COMMON)}\nlog_error x\nprintf 'done\\n'\n")
        result = subprocess.run(
            [SHELL, script], cwd=self.ws, env=base_env(), capture_output=True, text=True
        )
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "logs")))

    def test_nothing_goes_to_stdout_or_stderr(self):
        calls = "\n".join(sh_call(level, msg, fields) for level, msg, fields in CASES)
        result = self.run_sh(calls, CCNAVI_LOG_LEVEL="DEBUG")
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual(len(CASES), len(self.lines()))

    def test_credentials_in_urls_are_masked(self):
        self.run_sh(
            "log_info m -- 'reason=push to https://user:glpat-SECRET@gitlab.example/x.git failed'"
        )
        line = self.lines()[0]
        self.assertNotIn("SECRET", line)
        self.assertIn("https://***@gitlab.example/x.git", line)

    def test_the_message_is_masked_too(self):
        self.run_sh("log_info 'clone https://u:SECRET@h/x と' 'oauth2:SECRET@h:org/r.git'")
        self.assertEqual("clone https://***@h/x と ***@h:org/r.git", body(self.lines()[0])[2])

    def test_a_linked_log_file_is_not_followed(self):
        diag = os.path.join(self.ws, "logs", "diag")
        os.makedirs(diag)
        victim = os.path.join(self.ws, "logs", "decisions.jsonl")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("{}\n")
        if not symlink("../decisions.jsonl", os.path.join(diag, "probe.log")):
            self.skipTest("リンクを作れない")
        result = self.run_sh("log_error x -- a=1")
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))
        with open(victim, encoding="utf-8") as f:
            self.assertEqual("{}\n", f.read())

    def test_a_linked_place_is_not_followed(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-diaglog-out-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        # logs/diag がリンク
        os.makedirs(os.path.join(self.ws, "logs"))
        if not symlink(outside, os.path.join(self.ws, "logs", "diag")):
            self.skipTest("リンクを作れない")
        self.run_sh("log_error x")
        self.assertEqual([], os.listdir(outside))
        # logs そのものがリンク
        shutil.rmtree(os.path.join(self.ws, "logs"))
        os.makedirs(os.path.join(outside, "diag"))
        symlink(outside, os.path.join(self.ws, "logs"))
        result = self.run_sh("log_error x")
        self.assertEqual((0, "done\n", ""), (result.returncode, result.stdout, result.stderr))
        self.assertEqual([], os.listdir(os.path.join(outside, "diag")))

    def test_a_name_with_other_characters_is_not_written(self):
        for name in ("../escape", "a.b", "a b", "a/b"):
            with self.subTest(name=name):
                result = self.run_sh("log_error x", CCNAVI_LOG_NAME=name)
                self.assertEqual(
                    (0, "done\n", ""), (result.returncode, result.stdout, result.stderr)
                )
                self.assertFalse(os.path.exists(os.path.join(self.ws, "logs")))
                self.assertFalse(os.path.exists(os.path.join(self.ws, "escape.log")))

    @unittest.skipIf(os.name == "nt", "権限のビットは POSIX だけ")
    def test_a_new_file_is_owner_only(self):
        self.run_sh("umask 022\nlog_info x")
        mode = os.stat(os.path.join(self.ws, "logs", "diag", "probe.log")).st_mode & 0o777
        self.assertEqual(0o600, mode)

    def test_replace_with_an_empty_needle_returns(self):
        result = subprocess.run(
            [
                SHELL,
                "-c",
                f". {shlex.quote(COMMON)}; ccnavi_log_replace abc '' x;"
                " printf '%s' \"$ccnavi_log_out\"",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual((0, "abc"), (result.returncode, result.stdout))

    def test_a_given_root_is_used_without_searching(self):
        # ccnavi-common.sh を持たない場所でも、渡されたルートに書く（探し直さない）。
        other = tempfile.mkdtemp(prefix="ccnavi-diaglog-root-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        self.run_sh(f"ccnavi_log_root={shlex.quote(other)}\nlog_info x")
        self.assertTrue(os.path.isfile(os.path.join(other, "logs", "diag", "probe.log")))
        self.assertEqual([], self.lines())


def symlink(target: str, link: str) -> bool:
    """リンクを張る。張れない環境（Windows の権限）なら偽。"""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        return False
    return True


class GuardScriptTest(_Workspace):
    """reject / fail は識別子だけを診断ログに残す。文面と終了コードは契約のまま。"""

    def setUp(self):
        super().setUp()
        for name in ("ccnavi-git.sh", "ccnavi-review.sh"):
            shutil.copy(
                os.path.join(ROOT, ".ccnavi", "scripts", name),
                os.path.join(self.ws, ".ccnavi", "scripts"),
            )

    def run_script(self, name: str, *args: str):
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", name), *args],
            cwd=self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env(CCNAVI_WORKSPACE=self.ws),
            check=False,
        )

    def test_every_reject_and_fail_names_an_identifier(self):
        for name, word in (("ccnavi-git.sh", "reject"), ("ccnavi-review.sh", "fail")):
            with open(os.path.join(ROOT, ".ccnavi", "scripts", name), encoding="utf-8") as f:
                calls = [
                    x
                    for x in f
                    if re.search(rf"(^|[\s;|&(]){word} ", x) and not x.lstrip().startswith("#")
                ]
            self.assertTrue(calls, name)
            for line in calls:
                with self.subTest(script=name, line=line.strip()[:60]):
                    self.assertRegex(line, rf"(^|[\s;|&(]){word} [a-z][a-z-]* \"")

    def test_reject_logs_the_identifier_not_the_text(self):
        result = self.run_script("ccnavi-git.sh", "log", "--upload-pack=https://u:SECRET@h/x")
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            "ccnavi-git: --upload-pack=https://u:SECRET@h/x は、"
            "読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。"
            "出力を保存したいなら、このラッパースクリプトが logs/ に全量を残すので"
            "そちらを読んでください。\n",
            result.stderr,
        )
        (line,) = self.lines("ccnavi-git")
        self.assertEqual("拒否した sub=log reason=output-or-exec", body(line)[2])

    def test_reject_does_not_follow_a_linked_log(self):
        """再現: logs/diag/ccnavi-git.log を判定の記録へのリンクにしても、リンク先に書かない。"""
        diag = os.path.join(self.ws, "logs", "diag")
        os.makedirs(diag)
        victim = os.path.join(self.ws, "logs", "decisions.jsonl")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("{}\n")
        if not symlink("../decisions.jsonl", os.path.join(diag, "ccnavi-git.log")):
            self.skipTest("リンクを作れない")
        result = self.run_script("ccnavi-git.sh", "reset")
        self.assertEqual(2, result.returncode)
        self.assertTrue(
            result.stderr.startswith("ccnavi-git: reset は作業中の変更やコミットを消します。")
        )
        with open(victim, encoding="utf-8") as f:
            self.assertEqual("{}\n", f.read())

    def test_fail_logs_the_identifier_not_the_text(self):
        result = self.run_script("ccnavi-review.sh", "https://u:SECRET@h")
        self.assertEqual(2, result.returncode)
        self.assertEqual(
            "ccnavi-review: https://u:SECRET@h は通しません。"
            "使えるのは request / confirm / comment / decide / ready / close-early / chat / "
            "config-synced / fetch / origin / merged です。\n",
            result.stderr,
        )
        (line,) = self.lines("ccnavi-review")
        self.assertEqual("止めた sub=https://***@h exit=2 reason=unknown-sub", body(line)[2])


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class SameLineTest(_Workspace):
    """同じ入力から、3 つの言語が同じ行を出す（時刻と pid を除く）。"""

    def sh_lines(self) -> list[str]:
        calls = "\n".join(sh_call(level, msg, fields) for level, msg, fields in CASES)
        result = self.run_sh(calls, CCNAVI_LOG_LEVEL="DEBUG")
        self.assertEqual("", result.stderr)
        return self.lines()

    def python_lines(self) -> list[str]:
        root = tempfile.mkdtemp(prefix="ccnavi-diaglog-py-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        saved = os.environ.get(diaglog.LEVEL_ENV)
        os.environ[diaglog.LEVEL_ENV] = "DEBUG"
        try:
            log = diaglog.get("probe", root)
            for level, msg, fields in CASES:
                getattr(log, level.lower())(msg, **dict(fields))
        finally:
            if saved is None:
                os.environ.pop(diaglog.LEVEL_ENV, None)
            else:
                os.environ[diaglog.LEVEL_ENV] = saved
        with open(os.path.join(root, "logs", "diag", "probe.log"), encoding="utf-8") as f:
            return f.read().splitlines()

    def node_lines(self) -> list[str]:
        root = tempfile.mkdtemp(prefix="ccnavi-diaglog-ts-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        driver = os.path.join(root, "driver.mts")
        cases = [[level, msg, dict(fields)] for level, msg, fields in CASES]
        with open(driver, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                f"import * as log from {json.dumps(LOG_TS.replace(os.sep, '/'))};\n"
                f"const logger = log.get('probe', {json.dumps(root)});\n"
                f"for (const [level, msg, fields] of {json.dumps(cases)}) {{\n"
                "  (logger as any)[level.toLowerCase()](msg, fields);\n"
                "}\n"
            )
        result = subprocess.run(
            [NODE or "node", "--experimental-strip-types", "--no-warnings", driver],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=base_env(CCNAVI_LOG_LEVEL="DEBUG"),
            check=False,
        )
        if result.returncode != 0 and "strip-types" in result.stderr:
            self.skipTest("この node は型を剥がして読めない")
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))
        with open(os.path.join(root, "logs", "diag", "probe.log"), encoding="utf-8") as f:
            return f.read().splitlines()

    def test_sh_and_python_write_the_same_lines(self):
        sh = [body(x) for x in self.sh_lines()]
        py = [body(x) for x in self.python_lines()]
        self.assertEqual(len(CASES), len(sh))
        self.assertEqual(sh, py)

    @unittest.skipIf(not NODE, "node が見つからない")
    def test_node_writes_the_same_lines_as_python(self):
        ts = [body(x) for x in self.node_lines()]
        py = [body(x) for x in self.python_lines()]
        self.assertEqual(len(CASES), len(ts))
        self.assertEqual(py, ts)

    def test_the_expected_lines(self):
        """揃っているだけでなく、決めた形そのものであること。"""
        self.assertEqual(
            [
                ("INFO ", "probe", "push を拒否した reason=unapproved ticket=T-12"),
                (
                    "WARN ",
                    "probe",
                    '値の逃がし方 v="a b" q="say \\"hi\\"\\\\x" eq="k=v" tab="a\tb"',
                ),
                (
                    "ERROR",
                    "probe",
                    '改行を\\n含む本文 n="l1\\nl2\\nl3\\nl4" empty= bs=a\\b',
                ),
                ("INFO ", "probe", "値の無い行"),
                ("DEBUG", "probe", '判定の材料 sub=push path="/tmp/x y/z"'),
                (
                    "INFO ",
                    "probe",
                    "clone https://***@h/x を読んだ url=https://***@gitlab.example/x.git"
                    " scp=***@gitlab.example:org/r.git plain=git@github.com:org/r.git"
                    ' said="push to ssh://***@h:22/p failed"',
                ),
            ],
            [body(x) for x in self.sh_lines()],
        )


if __name__ == "__main__":
    unittest.main()
