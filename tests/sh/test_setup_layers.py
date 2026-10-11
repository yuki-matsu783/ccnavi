"""導入スクリプト（`scripts/ccnavi-setup.sh`）が配る設定のひな形の置き場。

rules と risk は汎用なので共通レイヤー（`.ccnavi/common/`）へ、phases はワークスペースの
レイアウトに依存するので自身のレイヤー（`.ccnavi/config/`）へ配る。配らないときは
3 本とも「まだ無いもの」に名前が出る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import platformtag
from tests import ROOT

SETUP = os.path.join(ROOT, "scripts", "ccnavi-setup.sh")
# 振り分けの sh の原本。コピーする前は CCNAVI_TEST_LAUNCHER で名指しできる
# （tests/sh/test_launcher.py と同じ）。
LAUNCHER = os.path.join(
    ROOT,
    os.environ.get("CCNAVI_TEST_LAUNCHER")
    or os.path.join(".ccnavi", "scripts", "ccnavi-launcher.sh"),
)
SHELL = shutil.which("sh") or shutil.which("bash")
HAS_JQ = shutil.which("jq") is not None
HOME = ".ccnavi"

# 配布元に置くひな形。導入スクリプトは中身を読まずに写すので、形だけ整えてある。
COMMON_RISK = "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors: {}\n"
COMMON_PHASES = "version: 1\nphases: {}\n"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
@unittest.skipUnless(HAS_JQ, "jq が見つからない")
class SetupTest(unittest.TestCase):
    """設定のひな形を配る先。tests/sh/test_setup.py の作りに合わせる。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-union-setup-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_setup(self, *args):
        return subprocess.run(
            [SHELL, SETUP, self.dir, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def make_source(self):
        """配布元のふり。実行ファイルと、共通レイヤーの rules / risk と、自身のレイヤーの phases。

        代わりに通る sh 3 本は起動して最初に共通部（ccnavi-common.sh と部品の
        ccnavi-common-*.sh）を読むので、配布元にも
        それを置く。無いと「配布元に無くて配れないもの」として名指しされる。
        """
        src = tempfile.mkdtemp(prefix="ccnavi-union-source-")
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        binary = os.path.join(src, "dist", "ccnavi", "ccnavi")
        os.makedirs(os.path.join(src, "dist", "ccnavi", "_internal"))
        write(binary, "#!/bin/sh\nexit 0\n")
        os.chmod(binary, 0o755)
        # 置き場の名前になる目印と、hook が起動する振り分けの sh。sh は `.ccnavi/scripts/` に置き、
        # 実行ファイルは隣ではなく `../bin/<os>-<arch>/` に探す。
        write(os.path.join(src, "dist", "ccnavi.target"), platformtag.host_target() + "\n")
        os.makedirs(os.path.join(src, ".ccnavi", "scripts"))
        shutil.copy(LAUNCHER, os.path.join(src, ".ccnavi", "scripts", "ccnavi-launcher.sh"))
        write(os.path.join(src, ".ccnavi", "common", "rules.yml"), "deny: []\n")
        write(os.path.join(src, ".ccnavi", "common", "risks.yml"), COMMON_RISK)
        write(os.path.join(src, HOME, "config", "phases.yml"), COMMON_PHASES)
        # 承認の push の sh と、端末で承認する sh も配るもの。
        # 無いと「まだ無いもの」に名前が出る。
        for name in (
            "ccnavi-ticket.sh",
            "ccnavi-review.sh",
            "ccnavi-git.sh",
            "ccnavi-common-state.sh",
            "ccnavi-common-lock.sh",
            "ccnavi-common-c1.sh",
            "ccnavi-common-host.sh",
            "ccnavi-common-log.sh",
            "ccnavi-common.sh",
            "ccnavi-push-approved.sh",
            "ccnavi-agree.sh",
            "ccnavi-fetch.sh",
            "ccnavi-sync.sh",
            "ccnavi-clean.sh",
            "ccnavi-clean.js",
            "ccnavi-branches.sh",
            "ccnavi-start.sh",
            "ccnavi-stage.sh",
            "ccnavi-worktree.sh",
        ):
            write(os.path.join(src, ".ccnavi", "scripts", name), f"# {name}\n")
        return src

    def settings(self):
        with open(os.path.join(self.dir, ".claude", "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_deploy_copies_common_rules_and_risk_and_own_phases(self):
        """共通レイヤーに rules.yml と risks.yml、
        自身のレイヤーに phases.yml のひな形を配る。"""
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue(os.path.isfile(os.path.join(self.dir, ".ccnavi", "common", "rules.yml")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, ".ccnavi", "common", "risks.yml")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, HOME, "config", "phases.yml")))
        self.assertNotIn("まだ無いもの", result.stdout)

    def test_missing_templates_are_named(self):
        """3 本とも「まだ無いもの」の点検に数える。"""
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn(".ccnavi/common/rules.yml", result.stdout)
        self.assertIn(".ccnavi/common/risks.yml", result.stdout)
        self.assertIn(".ccnavi/config/phases.yml", result.stdout)


if __name__ == "__main__":
    unittest.main()
