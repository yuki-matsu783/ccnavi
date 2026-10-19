"""`ccnavi sync paths` の約束。

`ccnavi-sync.sh` は jq を使わず JSON も読まない。置き場のパスを、
実行ファイルが 1 行 1 項目（`<鍵> <値>`）で返す。
統合先の名前は返さない（sh が各リポジトリの `origin/HEAD` で決める。
環境変数も `settings.local.json` も読まない）。
置き場は固定で、env では動かない（設計 wip/design/i0064-fixed-places.md）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

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
            },
            self.paths(),
        )

    def test_places_do_not_follow_the_environment(self):
        # 置き場を動かしていた 6 つの env を、既定と違う綴り（相対・絶対・末尾 `/` 付き）で
        # 全部入れても、返すのは既定の置き場。項目ごとではなく辞書全体で見る。
        elsewhere = os.path.join(tempfile.gettempdir(), "ccnavi-elsewhere")
        found = self.paths(
            {
                "CCNAVI_TICKETS_APPROVED": "tickets/approved/",
                "CCNAVI_TICKETS_PROPOSAL": os.path.join(elsewhere, "proposals"),
                "CCNAVI_PROJECT_HOME": ".nav",
                "CCNAVI_PROJECTS": "repos/",
                "CCNAVI_LOG": os.path.join(elsewhere, "log.jsonl"),
                "CCNAVI_STATE": "state",
            }
        )
        self.assertEqual(
            {
                "approved": ".ccnavi/approved",
                "proposals": "wip/proposals",
                "home": ".ccnavi",
            },
            found,
        )

    def test_no_integration_line_is_returned(self):
        # 統合先は各リポジトリの origin/HEAD で決まる。環境変数も settings.local.json も読まず、
        # 行も返さない。
        write(
            os.path.join(self.ws, ".claude", "settings.local.json"),
            '{"env": {"CCNAVI_INTEGRATION_BRANCH": "develop"}}',
        )
        found = self.paths({"CCNAVI_INTEGRATION_BRANCH": "develop"})
        self.assertNotIn("integration", found)
        self.assertEqual({"approved", "proposals", "home"}, set(found))

    def test_works_with_ticket_control_disabled(self):
        found = self.paths({"CCNAVI_TICKET_CONTROL": "disable"})
        self.assertEqual(".ccnavi/approved", found["approved"])

    def test_place_flags_are_ignored_outside_diagnosis(self):
        # sh は受け取った引数を `"$@"` で渡すので、
        # サブコマンドに置き場のフラグを足されても効かない。
        # 無視したことは標準エラーに出す。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        elsewhere = os.path.join(self.ws, "elsewhere")
        result = run_ccnavi(
            [
                "--root",
                self.ws,
                "--approved",
                "x/approved",
                "--tickets",
                "x/proposals",
                "--state",
                elsewhere,
                "--log",
                "",
                "sync",
                "paths",
            ],
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        found = dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())
        self.assertEqual(".ccnavi/approved", found["approved"])
        self.assertEqual("wip/proposals", found["proposals"])
        for flag in ("--approved", "--tickets", "--state", "--log"):
            self.assertIn(f"ccnavi: {flag} は診断", result.stderr)


class SyncRetiredCommandTest(unittest.TestCase):
    """`ccnavi sync retired` は答えの形だけをここで見る（中身は tests/ticket/test_retire.py）。"""

    def test_the_head_comes_first_without_any_note(self):
        ws = tempfile.mkdtemp(prefix="ccnavi-sync-retired-")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        os.makedirs(os.path.join(ws, ".claude"))
        result = run_ccnavi(["--root", ws, "sync", "retired"])
        self.assertEqual((0, "retired 1\n"), (result.returncode, result.stdout))


if __name__ == "__main__":
    unittest.main()
