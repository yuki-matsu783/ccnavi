"""導入スクリプト（`scripts/ccnavi-setup.sh`）が配る設定のひな形の置き場。

rules・phases・risk のひな形は、どれも共通レイヤー（`.ccnavi/config/common/`）へ配る。
自身のレイヤー（`.ccnavi/config/project/`）の phases.yml は配らない（レイアウトに依存する定義は
配布先のユーザが書く。既にあれば触らない）。配らないときは、rules と risk と、phases が共通にも
自身のレイヤーにも無いことが「まだ無いもの」に名前が出る。
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

# 配布元に置くひな形。導入スクリプトは中身を読まずにコピーするので、形だけ整えてある。
COMMON_RISK = "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors: {}\n"
COMMON_PHASES = (
    "version: 1\nphases:\n  implement:\n    kind: work\n    title: 実装とテスト\n"
    '    review: mr\n    scope: ["src/*", "tests/*"]\n'
)
OWN_PHASES = (
    "version: 1\nphases:\n  docs:\n    kind: work\n    title: 文書\n"
    '    review: mr\n    scope: ["docs/*"]\n'
)


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
        """配布元のふり。実行ファイルと、共通レイヤーの rules / phases / risk と、
        自身のレイヤーの phases。

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
        # 実行ファイルは `../bin/<os>-<arch>/` に探す。
        write(os.path.join(src, "dist", "ccnavi.target"), platformtag.host_target() + "\n")
        os.makedirs(os.path.join(src, ".ccnavi", "scripts"))
        shutil.copy(LAUNCHER, os.path.join(src, ".ccnavi", "scripts", "ccnavi-launcher.sh"))
        write(os.path.join(src, ".ccnavi", "config", "common", "rules.yml"), "deny: []\n")
        write(os.path.join(src, ".ccnavi", "config", "common", "risks.yml"), COMMON_RISK)
        write(os.path.join(src, ".ccnavi", "config", "common", "phases.yml"), COMMON_PHASES)
        write(os.path.join(src, HOME, "config", "project", "phases.yml"), OWN_PHASES)
        # セッションの開始で渡す作業の決まりと、使い方の文書も配るもの。
        write(os.path.join(src, ".ccnavi", "CCNAVI.md"), "# ccnavi の作業の決まり\n")
        write(os.path.join(src, ".ccnavi", "docs", "worktree.md"), "# ワークツリー\n")
        # 承認の push の sh と、ターミナルで承認する sh も配るもの。
        # 無いと「まだ無いもの」に名前が出る。
        for name in (
            "ccnavi-ticket.sh",
            "ccnavi-review.sh",
            "ccnavi-git.sh",
            "ccnavi-common-state.sh",
            "ccnavi-common-lock.sh",
            "ccnavi-common-write-and-push.sh",
            "ccnavi-common-host.sh",
            "ccnavi-common-log.sh",
            "ccnavi-common-review.sh",
            "ccnavi-common.sh",
            "ccnavi-user-lib-review.sh",
            "ccnavi-user-push-approved.sh",
            "ccnavi-user-agree.sh",
            "ccnavi-user-forget.sh",
            "ccnavi-user-decide.sh",
            "ccnavi-user-close-early.sh",
            "ccnavi-user-clean-branch.sh",
            "ccnavi-user-handoff.sh",
            "ccnavi-user-mirror.sh",
            "ccnavi-user-prune.sh",
            "ccnavi-fetch.sh",
            "ccnavi-dirs.sh",
            "ccnavi-session-start.sh",
            "ccnavi-sync.sh",
            "ccnavi-clean.sh",
            "ccnavi-clean.js",
            "ccnavi-branches.sh",
            "ccnavi-start.sh",
            "ccnavi-stage.sh",
            "ccnavi-handoff-names.txt",
            "ccnavi-worktree.sh",
            "ccnavi-idle.sh",
        ):
            write(os.path.join(src, ".ccnavi", "scripts", name), f"# {name}\n")
        return src

    def settings(self):
        with open(os.path.join(self.dir, ".claude", "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_deploy_copies_common_rules_and_risk_and_phases(self):
        """共通レイヤーに rules.yml・risks.yml・phases.yml のひな形を配る。

        自身のレイヤーの phases.yml は、配布元にあっても配らない。"""
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue(
            os.path.isfile(os.path.join(self.dir, ".ccnavi", "config", "common", "rules.yml"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.dir, ".ccnavi", "config", "common", "risks.yml"))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(self.dir, ".ccnavi", "config", "common", "phases.yml"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, HOME, "config", "project", "phases.yml"))
        )
        self.assertNotIn(".ccnavi/config/project/phases.yml", result.stdout)
        self.assertNotIn("まだ無いもの", result.stdout)

    def test_deploy_copies_common_phases(self):
        """共通レイヤーの phases.yml を配る。中身は配布元のまま。

        配布先に既にある自身のレイヤーの phases.yml は、--force でも触らない。"""
        src = self.make_source()
        mine = "version: 1\nphases:\n  mine:\n    kind: work\n    title: 自分の\n"
        own = write(os.path.join(self.dir, HOME, "config", "project", "phases.yml"), mine)
        result = self.run_setup("--mode", "enable", "--deploy", src, "--force")
        self.assertEqual(result.returncode, 0, result.stderr)

        common = os.path.join(self.dir, ".ccnavi", "config", "common", "phases.yml")
        with open(common, encoding="utf-8") as f:
            self.assertEqual(f.read(), COMMON_PHASES)
        with open(own, encoding="utf-8") as f:
            self.assertEqual(f.read(), mine)
        self.assertIn(".ccnavi/config/common/phases.yml", result.stdout)
        self.assertNotIn(".ccnavi/config/project/phases.yml", result.stdout)

    def test_existing_common_phases_is_kept_without_force(self):
        """配布先に共通レイヤーの phases.yml があれば、--force の無い配布では入れ替えない。"""
        src = self.make_source()
        mine = "version: 1\nphases:\n  mine:\n    kind: work\n    title: 自分の\n"
        common = write(os.path.join(self.dir, ".ccnavi", "config", "common", "phases.yml"), mine)
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(common, encoding="utf-8") as f:
            self.assertEqual(f.read(), mine)

    def test_phases_only_in_common_is_not_missing(self):
        """フェーズ定義が共通レイヤーにだけあれば、

        自身のレイヤーの phases.yml が無くても「まだ無いもの」に出さない。"""
        write(os.path.join(self.dir, ".ccnavi", "config", "common", "phases.yml"), COMMON_PHASES)
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(".ccnavi/config/project/phases.yml", result.stdout)
        self.assertNotIn(".ccnavi/config/common/phases.yml", result.stdout)

    def test_missing_templates_are_named(self):
        """rules と risk と、どこにも無いフェーズ定義を「まだ無いもの」の点検に数える。"""
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn(".ccnavi/config/common/rules.yml", result.stdout)
        self.assertIn(".ccnavi/config/common/risks.yml", result.stdout)
        self.assertIn(".ccnavi/config/common/phases.yml", result.stdout)


if __name__ == "__main__":
    unittest.main()
