"""保護済み sh が起動する実行ファイルを探す順（.ccnavi/scripts/ccnavi-common.sh の ccnavi_bin）。

人が端末から `ccnavi-ticket.sh` などを打つ場面では settings.json の env が効かず、
CCNAVI_BIN_PATH は無いのが普通。配布先には `dist/` もソースも無く、実行ファイルは
`.ccnavi/bin/<os>-<arch>/` にしか無い（ADR-0044）。そこを見ないと、配布先では必ず
「実行ファイルが無い」で止まる。

ワークスペースを使い捨てで組み、実行ファイルの代わりに受け取った引数を書くだけの sh を置く。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi import platformtag
from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")

# 呼ばれたら、どれが呼ばれたかと引数を 1 行で出す。
STUB = '#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*"\n'


def write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class BinLookupTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-bin-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        shutil.copytree(SCRIPTS, os.path.join(self.ws, ".ccnavi", "scripts"))
        os.chmod(os.path.join(self.ws, ".ccnavi", "scripts", platformtag.LAUNCHER_NAME), 0o755)
        self.target = platformtag.host_target()

    def run_ticket(self, **env):
        full = {k: v for k, v in os.environ.items() if k != "CCNAVI_BIN_PATH"}
        full.update(env)
        full.pop("CCNAVI_WORKSPACE", None)
        return subprocess.run(
            [SHELL, os.path.join(".ccnavi", "scripts", "ccnavi-ticket.sh"), "start", "x"],
            cwd=self.ws,
            env=full,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def put_machine_build(self):
        write(
            os.path.join(self.ws, ".ccnavi", "bin", self.target, "ccnavi"), STUB.format(name="bin")
        )

    def test_a_deployed_workspace_runs_the_machine_build_without_the_env(self):
        self.put_machine_build()
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("bin "), result.stdout)
        self.assertIn("ticket start x", result.stdout)

    def test_the_local_build_wins_over_the_machine_build(self):
        self.put_machine_build()
        write(os.path.join(self.ws, "dist", "ccnavi", "ccnavi"), STUB.format(name="dist"))
        result = self.run_ticket()
        self.assertTrue(result.stdout.startswith("dist "), result.stdout + result.stderr)

    def test_the_env_wins_when_it_is_set(self):
        self.put_machine_build()
        write(os.path.join(self.ws, "other", "ccnavi"), STUB.format(name="env"))
        result = self.run_ticket(CCNAVI_BIN_PATH="other/ccnavi")
        self.assertTrue(result.stdout.startswith("env "), result.stdout + result.stderr)

    def test_nothing_to_run_says_where_it_looked(self):
        result = self.run_ticket()
        self.assertEqual(result.returncode, 2)
        self.assertIn("実行ファイルが無い", result.stderr)
        self.assertIn(".ccnavi/bin/", result.stderr)


if __name__ == "__main__":
    unittest.main()
