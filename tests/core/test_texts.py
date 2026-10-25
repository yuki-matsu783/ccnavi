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
    # common は読まれる置き場（`.ccnavi/messages/`）。project は読まれない旧来のレイヤーの置き場。
    where = ("messages",) if layer == "common" else ("config", "project", "messages")
    path = os.path.join(root, ".ccnavi", *where, name)
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

    def test_the_project_layer_is_not_read(self):
        bodies = [
            'next: "別の文: {args_q}"',
            'next: "次: 二重"',  # 共通レイヤーなら検査で落ちる形も、読まないので何も言わない
            'kind: next\n    next: "{args_q}"',
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
                self.assertEqual([], cat.problems)
                out = texts.say(self.root, "NEXT_SAMPLE", {"args_q": "x"})
                self.assertNotIn("別の文", out)
                self.assertIn("ccnavi-start.sh", out)  # 共通レイヤーの文のまま

    def test_a_non_string_kind_or_a_line_separator_is_an_error_not_a_crash(self):
        bad = [
            "  DENY_X:\n    kind: [deny]\n",
            '  NEXT_X:\n    kind: next\n    values: []\n    next: "a\\u2028b"\n',
        ]
        for body in bad:
            with self.subTest(body=body):
                put(self.root, "common", "c.yml", "version: 1\nmessages:\n" + body)
                cat = texts.load(self.root)
                self.assertTrue([p for p in cat.problems if p.severity == "error"])

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
