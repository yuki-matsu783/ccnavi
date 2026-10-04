"""YAML の読み手（yamlread）の受入テスト。

見るのは 4 つ。

1. C の読み手（libyaml）でも純 Python の読み手でも、`yaml.safe_load` と同じ値を返す
2. libyaml の無い PyYAML（`CSafeLoader` が無い）でも、純 Python の読み手に戻って読める
3. 任意の Python の型を作るタグは、どちらの読み手でも断る（safe のまま）
4. 入れ子が深い文書でプロセスが落ちない。C の読み手は組み立ての再帰が C のスタックに乗るので、
   深い文書は純 Python の読み手に回り、以前と同じく `RecursionError` で断られる
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest

import yaml

from ccnavi.infra import yamlread
from tests import ROOT

DOCS = [
    "",
    "a: 1\nb: [x, y]\nc: {d: null, e: true}\n",
    "title: 日本語\nwhen: 2026-10-04\nnum: 0o17\nf: 1.5e3\n",
    "list:\n  - a\n  - b: c\n    d: >-\n      折り返し\n      の行\n",
    "base: &b {x: 1}\nuse: *b\n",
]

LOADERS = [yaml.SafeLoader] + ([yaml.CSafeLoader] if hasattr(yaml, "CSafeLoader") else [])


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


class FallbackTest(unittest.TestCase):
    def test_without_libyaml(self):
        """`CSafeLoader` の無い PyYAML でも読める。"""
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
        self.assertIs(yamlread.LOADER, saved or yaml.SafeLoader)


class DeepNestingTest(unittest.TestCase):
    def test_moderate_depth_reads(self):
        """`DEPTH_LIMIT` を少し超える程度なら、純 Python の読み手で読めて値も同じ。"""
        n = yamlread.DEPTH_LIMIT + 20
        doc = "[" * n + "]" * n
        self.assertEqual(yamlread.safe_load(doc), yaml.safe_load(doc))

    def test_very_deep_does_not_crash(self):
        """数万段の入れ子でもプロセスが落ちず、`RecursionError` で断られる。

        落ちると親のテストまで巻き込むので、別のプロセスで読む。
        """
        code = (
            "import sys\n"
            "from ccnavi.infra import yamlread\n"
            "n = 100000\n"
            "try:\n"
            "    yamlread.safe_load('[' * n + ']' * n)\n"
            "except RecursionError:\n"
            "    sys.exit(3)\n"
        )
        env = dict(os.environ, PYTHONPATH=ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=120
        )
        self.assertEqual(result.returncode, 3, result.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
