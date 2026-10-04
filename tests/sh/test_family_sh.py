"""sh が識別子だけから親子のチケットの親を割り出すこと（ccnavi-common.sh の ccnavi_c1_family）。

子の識別子は `<親>-<2 桁のフェーズ番号>-<2 桁の連番>`。sh も Python（`ticket.child_pattern`）と
同じく右から 2 段を剥がして親にする。親が `-` や数字を含んでも
（`web-i0012-05-01` の親は `web-i0012`）割り出し方は 1 通りに決まる。

取り込み状態の記録が無ければ、sh は実行ファイルに聞かずに割り出した親を `ccnavi_c1_family_id` に
入れて対象外（`no`）と答える。記録があれば、その親の名前で記録を探す。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SCRIPT = (
    '. "$0"; ccnavi_c1_root=$1; ccnavi_c1_family "$2"; '
    'printf "%s %s\\n" "$ccnavi_c1_family_id" "$ccnavi_c1_target"'
)


class FamilyShTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-family-sh-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def family(self, ident):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env["CCNAVI_BIN"] = os.path.join(self.root, "no-such-ccnavi")
        result = subprocess.run(
            [SHELL, "-c", SCRIPT, COMMON, self.root, ident],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        family, target = result.stdout.strip().splitlines()[-1].split(" ", 1)
        return family, target

    def test_two_steps_are_stripped_from_the_right(self):
        for ident, parent in (
            ("web-i0012-05-01", "web-i0012"),
            ("i0001-01-01", "i0001"),
            ("i0001-00-12", "i0001"),
            ("abc-01-02-03", "abc-01"),
            ("i0001", "i0001"),
            ("web-i0012", "web-i0012"),
            # 旧い形は子として扱わない（自身が親）
            ("i0012-01", "i0012-01"),
        ):
            with self.subTest(ident=ident):
                self.assertEqual(self.family(ident), (parent, "no"))

    def test_the_record_is_looked_up_by_the_parent(self):
        families = os.path.join(self.root, "logs", "state", "sync", "ws", "families")
        os.makedirs(families)
        with open(os.path.join(families, "web-i0012"), "w", encoding="utf-8") as f:
            f.write("")
        family, target = self.family("web-i0012-05-01")
        self.assertEqual(family, "web-i0012")
        # 記録があるので実行ファイルに聞く。ここでは実行ファイルが無いので止める側に倒れる。
        self.assertNotEqual(target, "no")


if __name__ == "__main__":
    unittest.main()
