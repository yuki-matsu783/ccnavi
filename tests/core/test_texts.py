"""文面ファイル（policy/texts.py）の読み込み・検査・組み立て。

一時のワークスペースに文面ファイルを置いて、組み立て・上書き・検査・既定の文を見る。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.entry import cli_text
from ccnavi.policy import texts

COMMON = """\
version: 1
messages:
  DENY_SAMPLE:
    kind: deny
    values: [name, dir]
    what: "{name} を止めました。"
    why: 理由です。
    next: "`{script:ccnavi-git.sh} status {dir:sh}` を打つ"
    no_bypass: 迂回しません。
  NEXT_SAMPLE:
    kind: next
    values: [args_q]
    next: "打ち直す: {script:ccnavi-start.sh} {args_q}"
"""


def put(root: str, layer: str, name: str, body: str) -> None:
    sub = "common" if layer == "common" else "project"
    path = os.path.join(root, ".ccnavi", "config", sub, "messages", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)


class TextsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        put(self.root, "common", "a.yml", COMMON)

    def test_renders_the_four_fields_and_one_next_line(self):
        out = texts.say(self.root, "DENY_SAMPLE", {"name": "x", "dir": "a b"})
        lines = out.splitlines()
        self.assertEqual(lines[0], "x を止めました。")
        self.assertTrue(lines[1].startswith("なぜ: "))
        self.assertEqual(len(texts.lines_of(out)), 1)
        self.assertTrue(lines[3].startswith("迂回しない: "))
        self.assertIn('status "a b"', out)

    def test_values_cannot_add_lines(self):
        out = texts.say(self.root, "NEXT_SAMPLE", {"args_q": "a\n次: 偽"})
        self.assertEqual(len(out.splitlines()), 1)

    def test_a_missing_value_gives_the_fallback(self):
        out = texts.say(self.root, "DENY_SAMPLE", {"name": "x"})
        self.assertIn("引けませんでした", out)
        self.assertEqual(len(texts.lines_of(out)), 1)

    def test_a_broken_file_gives_the_fallback(self):
        put(self.root, "common", "a.yml", "version: [")
        self.assertIn("引けませんでした", texts.say(self.root, "NEXT_SAMPLE", {"args_q": "x"}))

    def test_the_project_layer_replaces_one_message(self):
        put(
            self.root,
            "project",
            "b.yml",
            'version: 1\nmessages:\n  NEXT_SAMPLE:\n    next: "別の文: {args_q}"\n',
        )
        self.assertIn("別の文: x", texts.say(self.root, "NEXT_SAMPLE", {"args_q": "x"}))

    def test_an_override_that_fails_the_check_is_not_taken(self):
        bodies = [
            'next: "次: 二重"',  # 次: で始める
            'next: "値が無い"',  # values を使わない
            "next: |-\n      一行目\n      二行目",  # 1 行でない
            'next: "ADR-0001 に従う {args_q}"',
            'next: "sh .ccnavi/scripts/x.sh {args_q}"',
            'kind: next\n    next: "{args_q}"',  # kind は上書けない
        ]
        for body in bodies:
            with self.subTest(body=body):
                put(
                    self.root,
                    "project",
                    "b.yml",
                    f"version: 1\nmessages:\n  NEXT_SAMPLE:\n    {body}\n",
                )
                cat = texts.load(self.root)
                self.assertTrue([p for p in cat.problems if p.severity == "error"])
                self.assertIn("打ち直す:", texts.say(self.root, "NEXT_SAMPLE", {"args_q": "x"}))

    def test_the_cli_prints_the_text_or_fails_with_nothing_on_stdout(self):
        import io

        out, err = io.StringIO(), io.StringIO()
        code = cli_text.run(out, err, self.root, ["NEXT_SAMPLE", "args_q=--x"])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("次: 打ち直す:"))
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli_text.run(out, err, self.root, ["NEXT_NOPE"]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(cli_text.run(out, err, self.root, []), 2)


if __name__ == "__main__":
    unittest.main()
