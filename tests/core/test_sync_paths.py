"""`ccnavi sync paths` の約束（ADR-0093 の 4.2・D30・D33。段階 2b）。

`ccnavi-sync.sh` は jq を使わず JSON も読まない。置き場の綴りと、`.claude/settings.local.json` の
`env` に書かれた統合先の名前を、実行ファイルが 1 行 1 項目（`<鍵> <値>`）で返す。
統合先の名前は環境変数からは読まない（sh が先に環境変数を見る）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from ccnavi import settings
from tests.inproc import run_ccnavi


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class SyncPathsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-sync-paths-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))

    def paths(self, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        result = run_ccnavi(["--root", self.ws, "sync", "paths"], env=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        return dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())

    def test_defaults(self):
        self.assertEqual(
            {
                "approved": ".ccnavi/approved",
                "proposals": "wip/proposals",
                "home": ".ccnavi",
                "integration": "",
            },
            self.paths(),
        )

    def test_places_follow_the_environment(self):
        found = self.paths(
            {
                "CCNAVI_TICKETS_APPROVED": "tickets/approved/",
                "CCNAVI_TICKETS_PROPOSAL": "tickets/proposals",
                "CCNAVI_PROJECT_HOME": ".nav",
            }
        )
        self.assertEqual("tickets/approved", found["approved"])
        self.assertEqual("tickets/proposals", found["proposals"])
        self.assertEqual(".nav", found["home"])

    def test_the_integration_branch_comes_from_settings_local_json_only(self):
        # 環境変数は読まない。
        self.assertEqual("", self.paths({"CCNAVI_INTEGRATION_BRANCH": "develop"})["integration"])
        write(
            os.path.join(self.ws, ".claude", "settings.local.json"),
            '{"env": {"CCNAVI_INTEGRATION_BRANCH": " develop "}}',
        )
        self.assertEqual("develop", self.paths()["integration"])

    def test_unreadable_settings_fall_back_to_the_default(self):
        for text in ("{", "[]", '{"env": []}', '{"env": {"CCNAVI_INTEGRATION_BRANCH": 3}}'):
            with self.subTest(text=text):
                write(os.path.join(self.ws, ".claude", "settings.local.json"), text)
                self.assertEqual("", self.paths()["integration"])

    def test_a_value_with_a_newline_is_not_used(self):
        # 1 行 1 項目の契約（D33）に合わない値は使わない。
        write(
            os.path.join(self.ws, ".claude", "settings.local.json"),
            '{"env": {"CCNAVI_INTEGRATION_BRANCH": "a\\nb"}}',
        )
        self.assertEqual("", settings.integration_local(self.ws))

    def test_works_with_ticket_control_disabled(self):
        found = self.paths({"CCNAVI_TICKET_CONTROL": "disable"})
        self.assertEqual(".ccnavi/approved", found["approved"])


if __name__ == "__main__":
    unittest.main()
