"""導入スクリプトの受入テスト。外からスクリプトを動かす。

読み返すのは、書かれた `.claude/settings.json` と標準出力・終了コードだけ。
中の関数も変数も見ない。

使い捨てのディレクトリを毎回作る。このリポジトリ自身に打つと、テストが
「コードのこと」ではなく「走った機械の設定ファイルのこと」を報告するし、
走らせるたびに自分の hook の登録が書き換わる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "ccnavi-setup.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
HAS_JQ = shutil.which("jq") is not None

# 登録されていることを確かめる 7 つ。README「設定」の表と対になる。
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStart",
    "SubagentStop",
)
REQUIRED_ENV = ("CCNAVI_MODE", "CCNAVI_RULES", "CCNAVI_LOG", "CCNAVI_BIN_PATH")


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
@unittest.skipUnless(HAS_JQ, "jq が見つからない")
class SetupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-setup-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_setup(self, *args):
        return subprocess.run(
            [SHELL, SCRIPT, self.dir, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def settings_path(self):
        return os.path.join(self.dir, ".claude", "settings.json")

    def write_settings(self, data):
        os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
        with open(self.settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read_settings(self):
        with open(self.settings_path(), encoding="utf-8") as f:
            return json.load(f)

    def commands_of(self, data, event):
        out = []
        for entry in data.get("hooks", {}).get(event, []):
            for hook in entry.get("hooks", []):
                out.append(hook.get("command", ""))
        return out

    def test_writes_env_and_all_seven_hooks(self):
        """設定ファイルが無いところに、env と 7 つの hook を作る。"""
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)

        data = self.read_settings()
        for name in REQUIRED_ENV:
            self.assertIn(name, data["env"])
        for event in EVENTS:
            commands = self.commands_of(data, event)
            self.assertTrue(
                any("ccnavi" in c.lower() for c in commands),
                f"{event} に ccnavi が登録されていない",
            )

    def test_bin_path_is_spelled_without_the_exe_suffix(self):
        """実行ファイルの綴りは 3 つの環境で 1 行のまま。

        `.exe` を書き足すのは PyInstaller のほうで、設定の側は書かない。
        設定に `.exe` が入ると、その 1 行が Windows でしか当たらなくなる。
        """
        self.run_setup()
        self.assertEqual(self.read_settings()["env"]["CCNAVI_BIN_PATH"], "dist/ccnavi/ccnavi")

    def test_running_twice_changes_nothing(self):
        """2 回目は同じ形に落ち着き、hook が二重にならない。"""
        self.run_setup()
        first = self.read_settings()

        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("変えるところがありません", result.stdout)
        self.assertEqual(self.read_settings(), first)
        self.assertEqual(len(self.commands_of(first, "PreToolUse")), 1)

    def test_keeps_settings_that_have_nothing_to_do_with_ccnavi(self):
        """ccnavi と関係のない hook・env・権限を消さない。

        導入は既にあるプロジェクトに対して打つものなので、消してしまうと
        入れた瞬間に、そのプロジェクトの他の道具が止まる。
        """
        self.write_settings(
            {
                "env": {"PYTHONUTF8": "1"},
                "permissions": {"deny": ["Read(**/.env*)"]},
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Write",
                            "hooks": [{"type": "command", "command": "sh lint.sh"}],
                        }
                    ]
                },
            }
        )
        self.run_setup()

        data = self.read_settings()
        self.assertEqual(data["env"]["PYTHONUTF8"], "1")
        self.assertEqual(data["permissions"]["deny"], ["Read(**/.env*)"])
        self.assertIn("sh lint.sh", self.commands_of(data, "PostToolUse"))
        self.assertEqual(len(self.commands_of(data, "PostToolUse")), 2)

    def test_does_not_add_a_second_registration_to_an_event_that_has_one(self):
        """すでに ccnavi が登録されているイベントには足さない。

        綴りは決め打ちにできないので、名前が入っているかどうかで見る。
        別の綴りで登録してあるプロジェクトに 2 本目を足すと、
        すべての呼び出しで判定が 2 回走る。
        """
        self.write_settings(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "", "hooks": [{"type": "command", "command": "bin/CCNAVI"}]}
                    ]
                }
            }
        )
        self.run_setup()
        self.assertEqual(self.commands_of(self.read_settings(), "PreToolUse"), ["bin/CCNAVI"])

    def test_keeps_an_existing_value_unless_force(self):
        """既にある env の値は、--force を付けたときだけ置き換わる。"""
        self.write_settings({"env": {"CCNAVI_MODE": "enable"}})

        self.run_setup("--mode", "dry-run")
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "enable")

        self.run_setup("--mode", "dry-run", "--force")
        self.assertEqual(self.read_settings()["env"]["CCNAVI_MODE"], "dry-run")

    def test_keeps_a_copy_of_what_it_replaced(self):
        """置き換える前の内容が .bak に残る。"""
        self.write_settings({"env": {"CCNAVI_MODE": "enable"}})
        self.run_setup("--mode", "dry-run", "--force")

        with open(self.settings_path() + ".bak", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["env"]["CCNAVI_MODE"], "enable")

    def test_all_writes_the_settings_that_have_defaults(self):
        """--all は、既定と同じ値のつまみも設定ファイルに並べる。"""
        self.run_setup("--all")
        env = self.read_settings()["env"]
        self.assertEqual(env["CCNAVI_TICKETS"], "wip/tickets")
        self.assertEqual(env["CCNAVI_GUARD_CLI"], "enable")

    def test_check_reports_without_writing(self):
        """--check は書かない。不足があれば終了コードで言う。"""
        result = self.run_setup("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CCNAVI_BIN_PATH", result.stdout)
        self.assertIn("SubagentStop", result.stdout)
        self.assertFalse(os.path.exists(self.settings_path()))

        self.run_setup()
        result = self.run_setup("--check")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_refuses_disable(self):
        """`disable` は書かない。

        監視される側が書けるファイルから監視を止める形になる。設定lint が
        error として報告する記述を、導入の側が作ってはいけない。
        """
        result = self.run_setup("--mode", "disable")
        self.assertEqual(result.returncode, 2)
        self.assertIn("disable", result.stderr)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_an_absolute_bin_path(self):
        """実行ファイルの綴りは相対だけ。

        `env` では `${CLAUDE_PROJECT_DIR}` が展開されないので、絶対で書くと
        3 つの環境で同じ 1 行が使えなくなる。
        """
        result = self.run_setup("--bin", "/opt/ccnavi/ccnavi")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_refuses_to_write_over_a_settings_file_it_cannot_read(self):
        """読めない設定ファイルには書かない。

        壊れた JSON を空のオブジェクトとして扱うと、書き損じたカンマ 1 つで
        そのプロジェクトの設定が丸ごと消える。
        """
        os.makedirs(os.path.dirname(self.settings_path()), exist_ok=True)
        with open(self.settings_path(), "w", encoding="utf-8") as f:
            f.write('{"env": {,}')

        result = self.run_setup()
        self.assertEqual(result.returncode, 2)
        with open(self.settings_path(), encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"env": {,}')

    def test_says_what_is_still_missing(self):
        """登録しただけでは動かないので、人が置くものを挙げる。"""
        result = self.run_setup()
        self.assertIn("dist/ccnavi/ccnavi", result.stdout)
        self.assertIn("rules.yml", result.stdout)
        self.assertIn("ccnavi-git.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
