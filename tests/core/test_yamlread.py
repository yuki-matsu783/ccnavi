"""YAML の読み手（yamlread）の受入テスト。

見るのは 7 つ。

1. C の読み手（libyaml）でも純 Python の読み手でも、`yaml.safe_load` と同じ値を返す
2. libyaml の無い PyYAML（`CSafeLoader` が無い）でも、純 Python の読み手に戻って読める
3. 任意の Python の型を作るタグは、どちらの読み手でも断る（safe のまま）
4. 入れ子が深い文書でプロセスが落ちない。C の読み手は組み立ての再帰が C のスタックに乗るので、
   深い文書は純 Python の読み手に回り、`yaml.YAMLError` で断られる
5. 値を組み立てる途中の失敗（`!!int` の空・ありえない日付など）も、素の例外ではなく
   `yaml.YAMLError` で上がる。呼び手はそれしか捕まえない
6. タブ・BOM・`!` 単体など、二つの読み手で結果が分かれる文書でも、C を使ったときの結果は
   純 Python の読み手と同じ
7. 壊れたチケットがあっても、hook は落ちずに同じ判定を返す

C の読み手が無い環境では、C の側の確かめは飛ばしたと明示する（黙って純 Python だけで通さない）。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from ccnavi.infra import yamlread
from tests import ROOT, SRC, common_path

DOCS = [
    "",
    "a: 1\nb: [x, y]\nc: {d: null, e: true}\n",
    "title: 日本語\nwhen: 2026-10-04\nnum: 0o17\nf: 1.5e3\n",
    "list:\n  - a\n  - b: c\n    d: >-\n      折り返し\n      の行\n",
    "base: &b {x: 1}\nuse: *b\n",
]

LOADERS = [yaml.SafeLoader] + ([yaml.CSafeLoader] if hasattr(yaml, "CSafeLoader") else [])
C_LOADER = getattr(yaml, "CSafeLoader", None)
NO_C = "この PyYAML には libyaml（CSafeLoader）が無い"

# 値を組み立てる途中で PyYAML が素の例外を上げる文書。どれも yaml.YAMLError で上がるはず。
UNBUILDABLE = {
    "int-tab": "ticket: !!int \t\n",  # C はタブを受け入れて int('') で IndexError
    "int-empty": "ticket: !!int\n",  # IndexError
    "int-word": "ticket: !!int abc\n",  # ValueError
    "bool-tab": "ticket: !!bool \t\n",  # C は KeyError
    "bool-word": "ticket: !!bool maybe\n",  # KeyError
    "float-word": "x: !!float abc\n",  # ValueError
    "timestamp": "when: !!timestamp 2020-13-45\n",  # ValueError
    "tz-offset": "when: !!timestamp 2001-12-14t21:59:43.10+99:00\n",  # ValueError
    "surrogate": "title: \ud800\n",  # C は UnicodeEncodeError
}

# 二つの読み手で結果（値か、読めないか）が分かれる書き方。yamlread は純 Python に回して揃える。
SPLIT = {
    "tab-eol": "a: 1\t\nb: 2\n",
    "tab-after-colon": "a:\t1\n",
    "tab-after-quote": "a: 'x'\t\n",
    "tab-in-value": "a: x\ty\n",
    "bang-alone": "title: !\n",
    "bang-in-flow": "a: [!, x]\n",
    "bang-end": "a: !",
    "bom-middle": "title: x\n\ufeffticket: T-1\n",
    "bom-in-value": "title: a\ufeffb\n",
    "bom-head": "\ufefftitle: x\n",
    "block-hash": "a: |#\n",
    "directive": "%foo bar\n---\na: 1\n",
    "flow-colon": "{a:}\n",
    "flow-question": "[? ]\n",
    "flow-plain-question": "paths: [src/a?.py]\n",
    "flow-url-query": "k: [http://x/?q=1]\n",
    "flow-key-question": "k: {a?b: 1}\n",
    "flow-question-at": "[x?@]\n",
    "surrogate-escape": 'a: "\\uD800"\n',
    "surrogate-escape-long": 'a: "\\U0000DC00"\n',
    "surrogate-pair": 'a: "\\uD83D\\uDE00"\n',
}


def outcome(text: str, loader: type) -> tuple[str, object]:
    """読めたら ("ok", 値)、読めなければ ("error", None)。素の例外はそのまま上げる。"""
    try:
        return "ok", yamlread.safe_load(text, loader)
    except yaml.YAMLError:
        return "error", None


class SameValueTest(unittest.TestCase):
    def test_matches_safe_load(self):
        for loader in LOADERS:
            for doc in DOCS:
                with self.subTest(loader=loader.__name__, doc=doc):
                    self.assertEqual(yamlread.safe_load(doc, loader), yaml.safe_load(doc))

    def test_syntax_error_is_yaml_error(self):
        for loader in LOADERS:
            with self.subTest(loader=loader.__name__), self.assertRaises(yaml.YAMLError):
                yamlread.safe_load("a: [1, 2\n", loader)

    def test_python_tags_refused(self):
        doc = "x: !!python/object/apply:os.system ['echo hi']\n"
        for loader in LOADERS:
            with self.subTest(loader=loader.__name__), self.assertRaises(yaml.YAMLError):
                yamlread.safe_load(doc, loader)

    def test_loader_is_safe(self):
        self.assertIn(yamlread.LOADER.__name__, {"CSafeLoader", "SafeLoader"})


class UnbuildableTest(unittest.TestCase):
    """組み立ての途中の失敗も yaml.YAMLError で上がる。"""

    def check(self, loader: type) -> None:
        for name, doc in UNBUILDABLE.items():
            with self.subTest(loader=loader.__name__, case=name), self.assertRaises(yaml.YAMLError):
                yamlread.safe_load(doc, loader)

    def test_pure_python(self):
        self.check(yaml.SafeLoader)

    def test_libyaml(self):
        if C_LOADER is None:
            self.skipTest(NO_C)
        self.check(C_LOADER)

    def test_keeps_the_cause(self):
        """包んだ例外の元は `__cause__` に残る（原因を追えるように）。"""
        with self.assertRaises(yamlread.LoadError) as caught:
            yamlread.safe_load("ticket: !!int abc\n", yaml.SafeLoader)
        self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_load_wraps_other_safe_loaders(self):
        """yamlread を通さない読み手（別名を拒む子など）も `load` を通せば同じく包まれる。"""

        class Child(yaml.SafeLoader):
            pass

        with self.assertRaises(yaml.YAMLError):
            yamlread.load("ticket: !!int\n", Child)


class SameOutcomeTest(unittest.TestCase):
    """二つの読み手で分かれる書き方でも、C を使ったときの結果は純 Python と同じ。"""

    def test_libyaml_matches_pure_python(self):
        if C_LOADER is None:
            self.skipTest(NO_C)
        cases = {**SPLIT, **UNBUILDABLE, **{repr(d): d for d in DOCS}}
        for name, doc in cases.items():
            with self.subTest(case=name):
                self.assertEqual(outcome(doc, C_LOADER), outcome(doc, yaml.SafeLoader))

    def test_split_cases_really_differ_in_raw_loaders(self):
        """見本が見本として効いている（素の二つの読み手では本当に分かれる）。"""
        if C_LOADER is None:
            self.skipTest(NO_C)

        def raw(doc: str, loader: type) -> tuple[str, object]:
            try:
                return "ok", yaml.load(doc, Loader=loader)  # noqa: S506  safe な読み手だけ
            except yaml.YAMLError:
                return "error", None

        differ = [n for n, d in SPLIT.items() if raw(d, C_LOADER) != raw(d, yaml.SafeLoader)]
        # BOM が先頭のもの・値の中のタブなど、念のため回すだけで分かれないものもある
        self.assertGreaterEqual(len(differ), 8, differ)

    def test_question_outside_flow_plain_stays_on_c(self):
        """`?` があっても、クォートした値・ブロックの値なら C のまま読む。

        純 Python に回すのは、フローの中のクォートしない値に `?` があるときだけ。
        """
        if C_LOADER is None:
            self.skipTest(NO_C)
        for doc in ('["a?b"]\n', "- a?b\n", "k: http://x/?q=1\n", "{'a?': \"b?\"}\n"):
            with self.subTest(doc=doc):
                self.assertIsNone(yamlread._SPLIT.search(doc))
                self.assertTrue(yamlread._c_ok(doc, C_LOADER))
        self.assertFalse(yamlread._c_ok(SPLIT["flow-plain-question"], C_LOADER))


class FallbackTest(unittest.TestCase):
    def test_without_libyaml(self):
        """`CSafeLoader` の無い PyYAML でも読める。"""
        before = yamlread.LOADER
        saved = getattr(yaml, "CSafeLoader", None)
        if saved is not None:
            delattr(yaml, "CSafeLoader")
        try:
            reloaded = importlib.reload(yamlread)
            self.assertIs(reloaded.LOADER, yaml.SafeLoader)
            self.assertEqual(reloaded.safe_load(DOCS[1]), yaml.safe_load(DOCS[1]))
        finally:
            if saved is not None:
                yaml.CSafeLoader = saved
            importlib.reload(yamlread)
        self.assertIs(yamlread.LOADER, before)

    def test_unverified_libyaml_version(self):
        """libyaml の版が見比べた版でなければ C を使わない。"""
        if C_LOADER is None:
            self.skipTest(NO_C)
        from yaml import _yaml

        before = yamlread.LOADER
        saved = _yaml.get_version_string
        try:
            for version in ("0.2.4", "0.2.6", "0.3.0", "1.0.0"):
                _yaml.get_version_string = lambda v=version: v
                with self.subTest(version=version):
                    reloaded = importlib.reload(yamlread)
                    self.assertIs(reloaded.LOADER, yaml.SafeLoader)
                    self.assertEqual(reloaded.safe_load(DOCS[1]), yaml.safe_load(DOCS[1]))
            _yaml.get_version_string = saved
            for version in yamlread.VERIFIED_LIBYAML:
                _yaml.get_version_string = lambda v=version: v
                with self.subTest(version=version):
                    self.assertIs(importlib.reload(yamlread).LOADER, C_LOADER)
        finally:
            _yaml.get_version_string = saved
            importlib.reload(yamlread)
        self.assertIs(yamlread.LOADER, before)


class DeepNestingTest(unittest.TestCase):
    def test_moderate_depth_reads(self):
        """`DEPTH_LIMIT` を少し超える程度なら、純 Python の読み手で読めて値も同じ。"""
        n = yamlread.DEPTH_LIMIT + 20
        doc = "[" * n + "]" * n
        self.assertEqual(yamlread.safe_load(doc), yaml.safe_load(doc))

    def test_very_deep_does_not_crash(self):
        """数万段の入れ子でもプロセスが落ちず、`yaml.YAMLError` で断られる。

        落ちると親のテストまで巻き込むので、別のプロセスで読む。C の読み手（既定）と
        純 Python の読み手の両方で見る。
        """
        for loader in LOADERS:
            code = (
                "import sys, yaml\n"
                "from ccnavi.infra import yamlread\n"
                "n = 100000\n"
                "try:\n"
                f"    yamlread.safe_load('[' * n + ']' * n, yaml.{loader.__name__})\n"
                "except yaml.YAMLError:\n"
                "    sys.exit(3)\n"
            )
            env = dict(os.environ, PYTHONPATH=SRC)
            with self.subTest(loader=loader.__name__):
                result = subprocess.run(
                    [sys.executable, "-c", code],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 3, result.stderr.decode("utf-8", "replace"))
        if C_LOADER is None:
            self.skipTest(NO_C)


# hook を本物のプロセスで動かす。`pure` は libyaml を外してから動かす。
_HOOK = (
    "import runpy, sys, yaml\n"
    "if sys.argv.pop(1) == 'pure' and hasattr(yaml, 'CSafeLoader'):\n"
    "    del yaml.CSafeLoader\n"
    "sys.argv[0] = 'ccnavi'\n"
    "runpy.run_module('ccnavi', run_name='__main__', alter_sys=True)\n"
)


class BrokenTicketHookTest(unittest.TestCase):
    """壊れたチケットがあっても、PreToolUse は落ちずに、無いときと同じ判定を返す。

    hook が非 0 で終わると Claude Code は判定を読まないので、止めるはずの呼び出しが通る。
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = os.path.join(self.dir.name, "state")

    def workspace(self, name: str, ticket: str | None) -> str:
        root = os.path.join(self.dir.name, name)
        rules = common_path(root, "rules")
        os.makedirs(os.path.dirname(rules))
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", "rules.yml"), rules)
        if ticket is not None:
            review = os.path.join(root, "wip", "proposals", "review")
            os.makedirs(review)
            with open(os.path.join(review, "T-1.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\nticket: {ticket}\ntitle: x\n"
                    "ccnavi_approved:\n  approved_at: 2026-01-01\n---\nbody\n"
                )
        return root

    def hook(self, root: str, mode: str) -> tuple[int, str, str]:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "session_id": "s1",
            "cwd": root,
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(CCNAVI_GUARD_CORE_FILES="disable", PYTHONPATH=SRC)
        args = ["--root", root, "--log", "", "--state", self.state, "--mode", "enable"]
        done = subprocess.run(
            [sys.executable, "-c", _HOOK, mode, *args],
            input=json.dumps(payload).encode(),
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=120,
        )
        stderr = done.stderr.decode("utf-8", "replace")
        self.assertEqual(done.returncode, 0, stderr)
        out = json.loads(done.stdout)["hookSpecificOutput"]
        return done.returncode, out.get("permissionDecision"), stderr

    def test_git_push_is_still_denied(self):
        clean = self.workspace("clean", None)
        for mode in ("c", "pure"):
            if mode == "c" and C_LOADER is None:
                continue
            want = self.hook(clean, mode)[1]
            self.assertEqual(want, "deny")
            for name, ticket in (("int-tab", "!!int \t"), ("int-empty", "!!int")):
                with self.subTest(loader=mode, ticket=name):
                    root = self.workspace(f"{mode}-{name}", ticket)
                    self.assertEqual(self.hook(root, mode)[1], want)
        if C_LOADER is None:
            self.skipTest(NO_C)


if __name__ == "__main__":
    unittest.main()
