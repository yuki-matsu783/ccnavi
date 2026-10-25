"""ccnavi-stage.sh の階層名の読み替え（`.claude` → `handoff-.claude`）。

見るのは次のとおり。見本は tests/handoff_names_samples.py の表（`SAMPLES`）を使う。

1. 表に仮の 1 行を足した場合も含め、sh と Python が同じ変換結果を返す（同じ見本を両方に通す）
2. 元のパスに予約名（`handoff-.claude`）の階層があれば、stage が断る
3. stage → 編集 → `--commands` で、差し替えの mv が、読み替えた下書きを元の場所へ戻す
4. 表のファイルが無い・読めない行があれば、stage は何も作らずに断る
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from ccnavi.tickets import handoff, handoff_names
from tests import ROOT, common_sh
from tests.handoff_names_samples import RESERVED, SAMPLES

SHELL = shutil.which("sh") or shutil.which("bash")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
NAMES = "ccnavi-handoff-names.txt"


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(SHELL, "sh が無い")
class StageNamesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.count = 0

    def workspace(self, extra=""):
        """使い捨てのワークスペース。表のファイルは、配布元の写しに `extra`（1 行以上）を
        足したものにする。"""
        self.count += 1
        ws = os.path.join(self._tmp.name, f"ws{self.count}")
        scripts = os.path.join(ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-stage.sh", NAMES, *common_sh(SH_DIR)):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        if extra:
            with open(os.path.join(scripts, NAMES), "a", encoding="utf-8", newline="\n") as f:
                f.write(extra)
        return ws

    def stage(self, ws, *args):
        env = dict(os.environ)
        env.pop("CCNAVI_WORKSPACE", None)
        script = os.path.join(ws, ".ccnavi", "scripts", "ccnavi-stage.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def draft_of(self, ws, rel):
        """stage したあと、`wip/handoff/` の下で実際にできたファイルの相対パス。"""
        base = os.path.join(ws, "wip", "handoff")
        found = []
        for parent, _, files in os.walk(base):
            for name in files:
                if not name.endswith(handoff.ORIG_SUFFIX):
                    found.append(os.path.relpath(os.path.join(parent, name), base))
        self.assertEqual(1, len(found), (rel, found))
        return found[0].replace(os.sep, "/")

    def check_samples(self, samples, extra=""):
        for orig, expected in samples:
            with self.subTest(orig=orig):
                ws = self.workspace(extra)
                write(os.path.join(ws, *orig.split("/")), "x\n")
                result = self.stage(ws, orig)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, self.draft_of(ws, orig), "sh の変換")
                self.assertEqual(expected, handoff_names.to_draft(orig), "Python の変換")

    def test_sh_and_python_agree_on_every_sample(self):
        self.check_samples(SAMPLES)

    def test_a_row_added_to_the_table_works_in_both(self):
        """sh は写しの表を、Python は同じ文面を `parse` した表を使う。足す行は 1 行だけ。"""
        extra = ".vscode handoff-.vscode\n"
        table = handoff_names.parse(read(os.path.join(SH_DIR, NAMES)) + extra)
        samples = [
            (".vscode/settings.json", "handoff-.vscode/settings.json"),
            ("a/.VSCode/tasks.json", "a/handoff-.vscode/tasks.json"),
            (".claude/settings.json", "handoff-.claude/settings.json"),
            (".github/x.yml", ".github/x.yml"),
        ]
        with mock.patch.dict(handoff_names.ALIASES, table, clear=True):
            self.check_samples(samples, extra)
            ws = self.workspace(extra)
            write(os.path.join(ws, "handoff-.vscode", "x.json"), "x\n")
            result = self.stage(ws, "handoff-.vscode/x.json")
            self.assertEqual(2, result.returncode)
            self.assertIn("予約", result.stderr)
            self.assertIn("予約", handoff_names.reserved_problem("handoff-.vscode/x.json"))

    def test_an_original_path_with_the_reserved_name_is_refused(self):
        for rel in RESERVED:
            with self.subTest(rel=rel):
                ws = self.workspace()
                write(os.path.join(ws, *rel.split("/")), "x\n")
                result = self.stage(ws, rel)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn("予約", result.stderr)
                self.assertFalse(os.path.exists(os.path.join(ws, "wip")), "何も作らない")

    def test_commands_point_the_mv_from_the_renamed_draft_to_the_original(self):
        ws = self.workspace()
        orig = os.path.join(ws, ".claude", "settings.json")
        write(orig, '{"a": 1}\n')
        self.assertEqual(0, self.stage(ws, ".claude/settings.json").returncode)
        draft = os.path.join(ws, "wip", "handoff", "handoff-.claude", "settings.json")
        self.assertEqual('{"a": 1}\n', read(draft))
        write(draft, '{"a": 2}\n')
        result = self.stage(ws, "--commands", ".claude/settings.json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"mv '{draft}' '{orig}'", result.stdout)
        self.assertNotIn("wip/handoff/.claude", result.stdout)

    def test_a_missing_table_stops_before_anything_is_made(self):
        ws = self.workspace()
        os.remove(os.path.join(ws, ".ccnavi", "scripts", NAMES))
        write(os.path.join(ws, ".claude", "settings.json"), "x\n")
        result = self.stage(ws, ".claude/settings.json")
        self.assertEqual(2, result.returncode)
        self.assertIn(NAMES, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(ws, "wip")))

    def test_an_unreadable_row_stops_too(self):
        ws = self.workspace("only-one-word\n")
        write(os.path.join(ws, ".claude", "settings.json"), "x\n")
        result = self.stage(ws, ".claude/settings.json")
        self.assertEqual(2, result.returncode)
        self.assertIn("only-one-word", result.stderr)


if __name__ == "__main__":
    unittest.main()
