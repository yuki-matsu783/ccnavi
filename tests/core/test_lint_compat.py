"""`--lint` が `.ccnavi/scripts/` の sh の互換の版の食い違いを言うこと。

sh は `ccnavi-common.sh` の `CCNAVI_COMPAT=<数>` で互換の版を名乗る。実行ファイルの
`src/ccnavi/entry/version.py` の COMPAT と違えば `(version)` の warn で言い、直し方（ccnavi の
リポジトリなら組み立て直し、配布先なら配り直し）を名指しする。sh の無いワークスペースでは言わない。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest

from ccnavi.entry import version
from tests import ROOT
from tests.inproc import run_ccnavi


def ccnavi(*args: str, root: str) -> subprocess.CompletedProcess:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    return run_ccnavi(["--root", root, *args], input="", cwd=ROOT, env=environment)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class LintShCompatTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def put_sh(self, line: str) -> None:
        write(
            os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-common.sh"),
            f"# ccnavi-common\n{line}\nccnavi_abs() {{ :; }}\n",
        )

    def said(self) -> list[dict]:
        result = ccnavi("--lint", "--json", "--mode", "enable", root=self.root)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        problems = json.loads(result.stdout)["problems"]
        return [p for p in problems if p["where"] == "(version)"]

    def test_v20_a_matching_sh_says_nothing(self):
        """V20"""
        self.put_sh(f"CCNAVI_COMPAT={version.COMPAT}")
        self.assertEqual(self.said(), [])

    def test_v21_no_sh_says_nothing(self):
        """V21 sh の無いワークスペース（試しの置き場）では言わない。"""
        self.assertEqual(self.said(), [])

    def test_v22_a_different_compat_is_a_warn_naming_the_redistribution(self):
        """V22 配布先（build.py もソースも無い）では配り直しを名指しする。"""
        self.put_sh(f"CCNAVI_COMPAT={version.COMPAT + 1}")
        said = self.said()
        self.assertEqual([p["severity"] for p in said], ["warn"], said)
        self.assertIn(f"互換 {version.COMPAT + 1}", said[0]["detail"])
        self.assertIn(f"実行ファイルは互換 {version.COMPAT}", said[0]["detail"])
        self.assertIn("scripts/ccnavi-setup.sh", said[0]["detail"])

    def test_v23_an_sh_without_the_line_is_old(self):
        """V23 互換の版を名乗らない sh は、互換の版を持つ前の古い sh。"""
        self.put_sh("# nothing")
        said = self.said()
        self.assertEqual([p["severity"] for p in said], ["warn"], said)
        self.assertIn("CCNAVI_COMPAT", said[0]["detail"])

    def test_v24_in_the_ccnavi_repository_the_fix_is_a_rebuild(self):
        """V24"""
        self.put_sh(f"CCNAVI_COMPAT={version.COMPAT + 1}")
        write(os.path.join(self.root, "build.py"), "")
        write(os.path.join(self.root, "src", "ccnavi", "__main__.py"), "")
        said = self.said()
        self.assertEqual(len(said), 1, said)
        self.assertIn("build.py を実行して組み立て直して", said[0]["detail"])
        self.assertNotIn("ccnavi-setup.sh", said[0]["detail"])

    def test_v25_this_repository_is_in_step(self):
        """V25 このリポジトリの sh と実行ファイルは揃っている。"""
        result = ccnavi("--lint", "--json", "--mode", "enable", root=ROOT)
        problems = json.loads(result.stdout)["problems"]
        self.assertEqual([p for p in problems if p["where"] == "(version)"], [])
        common = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
        with open(common, encoding="utf-8") as f:
            self.assertRegex(f.read(), re.compile(rf"(?m)^CCNAVI_COMPAT={version.COMPAT}$"))


if __name__ == "__main__":
    unittest.main()
