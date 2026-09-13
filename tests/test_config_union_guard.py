"""設定 3 本の和の受入テスト。守る面（設計 §11.6）と導入スクリプト（§11.9 末尾）。

selfguard のコアに、各層の `.ccnavi/config/` の 3 本と共通層の phases / risk が入る。
Write / Edit の拒否、シェルからの書き込みの拒否、控えと復元の 3 つとも、今 rules.yml に
掛けているものをそのまま掛ける。切り元から切った作業ツリー側の設定も対象。

ルールファイルは何でも通す 1 本にしてある。止まるなら、それはルールの外の組み込み。

実装はまだ無い。このテストは実装フェーズが緑にする。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi import platformtag
from tests.test_config_union import (
    COMMON_PHASES,
    COMMON_RISK,
    HOME,
    ConfigUnionHarness,
    layer_path,
    read,
    write,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(ROOT, "scripts", "ccnavi-setup.sh")
LAUNCHER = os.path.join(ROOT, "scripts", "ccnavi-launcher.sh")
SHELL = shutil.which("sh") or shutil.which("bash")
HAS_JQ = shutil.which("jq") is not None

# 何でも通す。deny が 1 件も無いので、止まったものはルールの外で止まっている。
OPEN_RULES = {
    "version": 3,
    "allow": [{"id": "anything", "match": "Bash|Read|Write|Edit|NotebookEdit", "regex": "."}],
}


class GuardHarness(ConfigUnionHarness):
    """守る面を enable にして動かす道具。"""

    def setUp(self):
        super().setUp()
        write(self.rules, json.dumps(OPEN_RULES))

    def guarded_hook(self, tool, cwd, *, event="PreToolUse", **tool_input):
        return self.hook(tool, cwd, event=event, guard="enable", **tool_input)

    def run_hook(self, event, command="ls"):
        """Bash 1 回。実行前で控えを取り、実行後で戻す。"""
        return self.guarded_hook("Bash", self.ws, event=event, command=command)

    def break_and_restore(self, path, broken="version: 3\ndeny: []\n"):
        """控えを取らせ、壊し、実行後に戻ったかを返す。"""
        before = read(path)
        self.run_hook("PreToolUse")
        write(path, broken)
        result = self.run_hook("PostToolUse")
        return before, read(path), result

    def said(self, result, note=""):
        """落ちたときに何が起きたかを添える。標準エラーを見ないと理由が出ない。"""
        head = f"{note}\n" if note else ""
        return f"{head}stdout: {result.stdout}\nstderr: {result.stderr}"


class RestoreTest(GuardHarness):
    """控えと復元（§11.6、REQ-MLT-08 の変更）。"""

    def test_project_layer_files_are_restored(self):
        """§11.6: プロジェクトの層の 3 本が控えと復元の対象。"""
        for kind in ("rules", "phases", "risk"):
            with self.subTest(kind=kind):
                path = layer_path(self.lib, kind)
                before, after, result = self.break_and_restore(path)
                self.assertEqual(after, before, self.said(result, kind))
                self.assertIn("restored", result.stdout, self.said(result, kind))
                self.assertIn(f"{kind}.yml", result.stdout, self.said(result, kind))

    def test_own_layer_files_are_restored(self):
        """§11.6: 自身の層 `<ワークスペースルート>/.ccnavi/config/` の 3 本も対象。"""
        write(layer_path(self.ws, "risk"), COMMON_RISK)
        for kind in ("rules", "phases", "risk"):
            with self.subTest(kind=kind):
                path = layer_path(self.ws, kind)
                before, after, result = self.break_and_restore(path)
                self.assertEqual(after, before, self.said(result, kind))
                self.assertIn("restored", result.stdout, self.said(result, kind))

    def test_common_phases_and_risk_are_restored(self):
        """§11.6: 共通層の phases.yml / risk.yml を足す（既存の穴の修正）。"""
        for path in (self.phases, self.risk):
            name = os.path.basename(path)
            with self.subTest(path=name):
                before, after, result = self.break_and_restore(path)
                self.assertEqual(after, before, self.said(result, name))
                self.assertIn("restored", result.stdout, self.said(result, name))

    def test_copies_in_a_worktree_cut_from_a_project_are_restored(self):
        """§11.6: 切り元のプロジェクトから切った作業ツリー側の設定も対象。"""
        tree = self.worktree(self.lib, "w1")
        copy = layer_path(tree, "rules")
        self.assertTrue(
            os.path.exists(copy), "lib の .ccnavi/ は追跡されているので作業ツリー側の設定がある"
        )

        before, after, result = self.break_and_restore(copy)
        self.assertEqual(after, before, self.said(result))
        self.assertIn("restored", result.stdout, self.said(result))
        self.assertIn("統合すれば", result.stdout, self.said(result))

    def test_copies_in_a_worktree_cut_from_the_workspace_are_restored(self):
        """§11.6: ワークスペースから切った作業ツリー側の、自身の層の設定も対象。"""
        tree = self.worktree(self.ws, "w2")
        copy = layer_path(tree, "phases")
        before, after, result = self.break_and_restore(copy, broken="version: 1\nphases: {}\n")
        self.assertEqual(after, before, self.said(result))

    def test_copies_of_the_common_layer_in_a_worktree_are_restored(self):
        """§11.6: ワークスペースから切った作業ツリー側の、共通層の設定も対象（既存の穴）。

        共通層の 3 本はワークスペースの git が追跡しているので、作業ツリー側の設定もできる。
        今はそこがルールの allow `worktrees` に当たって書けてしまい、戻りもしない。
        """
        tree = self.worktree(self.ws, "w3")
        for name in ("rules.yml", "phases.yml", "risk.yml"):
            with self.subTest(name=name):
                copy = os.path.join(tree, ".ccnavi", "common", name)
                self.assertTrue(
                    os.path.exists(copy), "共通層は追跡されているので作業ツリー側の設定がある"
                )
                broken = "version: 3\ndeny: []\n" if name == "rules.yml" else "version: 1\n"
                before, after, result = self.break_and_restore(copy, broken=broken)
                self.assertEqual(after, before, self.said(result, name))
                self.assertIn("restored", result.stdout, self.said(result, name))
                self.assertIn("統合すれば", result.stdout, self.said(result, name))

    def test_deleted_layer_file_comes_back_from_the_project_git(self):
        """§11.6 / REQ-SLF: 控えが無ければ、その層の git（プロジェクト自身）から戻る。"""
        path = layer_path(self.lib, "rules")
        expected = read(path)
        os.remove(path)

        result = self.run_hook("PreToolUse")

        self.assertTrue(os.path.exists(path), self.said(result))
        self.assertEqual(read(path), expected, self.said(result))

    def test_missing_layer_files_are_not_reported(self):
        """§11.6 / REQ-SLF-03: 無いものは対象から外れる。app の層が無いことは言わない。"""
        result = self.run_hook("PreToolUse")
        self.assertNotIn("app", result.stdout, self.said(result))
        self.assertEqual(result.stderr, "", self.said(result))

    def test_record_names_the_layer_that_was_restored(self):
        """§11.6: 記録の `guarded` に、層の名前付きの鍵で何をしたかが残る。"""
        self.break_and_restore(layer_path(self.lib, "phases"))
        with open(self.log, encoding="utf-8") as f:
            line = json.loads([ln for ln in f if ln.strip()][-1])
        self.assertIn("guarded", line)
        self.assertTrue(
            any("phases" in g and "lib" in g and "restored" in g for g in line["guarded"]),
            line["guarded"],
        )


class DenyTest(GuardHarness):
    """`.ccnavi/` 全体への組み込みの deny（§11.6）。ルールに宣言が無くても止まる。"""

    def test_named_tool_writes_into_project_home_are_denied(self):
        """§11.6: `*/.ccnavi/*` への Write / Edit は `builtin-guard-project-home` で止まる。"""
        targets = (
            layer_path(self.lib, "rules"),
            os.path.join(self.lib, HOME, "scripts", "count.sh"),
            layer_path(self.ws, "phases"),
            os.path.join(self.app, HOME, "config", "rules.yml"),
        )
        for path in targets:
            for tool in ("Write", "Edit"):
                with self.subTest(tool=tool, path=os.path.relpath(path, self.ws)):
                    result = self.guarded_hook(tool, self.ws, file_path=path)
                    self.assert_denied(result, "builtin-guard-project-home")

    def test_project_home_deny_follows_the_env(self):
        """§11.6: 綴りは `CCNAVI_PROJECT_HOME` の値で組む。"""
        moved = os.path.join(self.lib, ".navi", "config", "rules.yml")
        result = self.hook(
            "Write", self.ws, guard="enable", env={"CCNAVI_PROJECT_HOME": ".navi"}, file_path=moved
        )
        self.assert_denied(result, "builtin-guard-project-home")

    def test_shell_writes_into_project_home_are_denied(self):
        """§11.6: シェルからの書き込みは `builtin-guard-setting-files` の 1 本で止まる。"""
        for command in (
            "echo x > projects/lib/.ccnavi/scripts/count.sh",
            "echo x >> .ccnavi/config/rules.yml",
            "sed -i s/deny/allow/ projects/lib/.ccnavi/config/rules.yml",
            "cp /tmp/x .ccnavi/config/phases.yml",
            "cd projects/lib && echo x > .ccnavi/config/risk.yml",
        ):
            with self.subTest(command=command):
                result = self.guarded_hook("Bash", self.ws, command=command)
                self.assert_denied(result, "builtin-guard-setting-files")

    def test_reading_project_home_is_allowed(self):
        """§11.6: 読むだけなら通る。名前が出ただけでは止めない。"""
        for command in (
            "cat projects/lib/.ccnavi/config/rules.yml",
            "grep -n deny .ccnavi/config/rules.yml",
        ):
            with self.subTest(command=command):
                self.assert_not_denied(self.guarded_hook("Bash", self.ws, command=command))
        self.assert_not_denied(
            self.guarded_hook("Read", self.ws, file_path=layer_path(self.lib, "rules"))
        )

    def test_disable_does_not_add_the_deny(self):
        """§11.6: 守る面を disable にすれば組み込みの deny も足さない。"""
        result = self.hook("Write", self.ws, file_path=layer_path(self.lib, "rules"))
        self.assertNotIn("builtin-guard-project-home", self.reason(result))
        result = self.hook("Write", self.ws, file_path=self.phases)
        self.assertNotIn("builtin-guard-common-layer", self.reason(result))

    def test_named_tool_writes_into_the_common_layer_are_denied(self):
        """§11.6: 共通層の 3 本への Write / Edit は、ルールに宣言が無くても組み込みで止まる。

        土台の共通層は既定の置き場（`.ccnavi/common/`）にある。ルールの 1 行に任せると、
        書き換えたルールファイルのもとでは通ってしまう（issue #14）。
        """
        for path in (self.rules, self.phases, self.risk):
            for tool in ("Write", "Edit"):
                with self.subTest(tool=tool, path=os.path.basename(path)):
                    result = self.guarded_hook(tool, self.ws, file_path=path)
                    self.assert_denied(result, "builtin-guard-common-layer")

    def test_common_layer_deny_holds_when_the_project_home_is_moved(self):
        """§11.6: ccnavi ディレクトリの名前を動かしても、既定の置き場の共通層は止まる。

        共通層の置き場は ccnavi ディレクトリの名前に付いて動かないので、`*/.navi/*` からは外れる。
        """
        for path in (self.rules, self.phases, self.risk):
            with self.subTest(path=os.path.basename(path)):
                result = self.hook(
                    "Write",
                    self.ws,
                    guard="enable",
                    env={"CCNAVI_PROJECT_HOME": ".navi"},
                    file_path=path,
                )
                self.assert_denied(result, "builtin-guard-common-layer")

    def test_common_layer_copies_in_a_worktree_are_denied(self):
        """§11.6: ワークスペースから切った作業ツリー側の共通層も止まる。

        統合すればそのまま main の設定になる。
        """
        tree = self.worktree(self.ws, "w5")
        copy = os.path.join(tree, ".ccnavi", "common", "phases.yml")
        self.assert_denied(
            self.guarded_hook("Write", self.ws, file_path=copy), "builtin-guard-common-layer"
        )

    def test_common_layer_deny_names_only_those_files(self):
        """§11.6: 同じ名前の別のファイルは止めない。

        当てるのはワークスペースルートと、そこから切った作業ツリーの下の同じ相対だけ。
        """
        for path in (
            os.path.join(self.ws, "src", "rules.yml"),
            os.path.join(self.app, "common", "rules.yml"),
        ):
            with self.subTest(path=os.path.relpath(path, self.ws)):
                self.assert_not_denied(self.guarded_hook("Write", self.ws, file_path=path))

    def test_shell_writes_into_a_moved_common_layer_are_denied(self):
        """§11.6: 共通層が既定の名前の外にあっても、シェルからの書き込みは組み込みで止まる。"""
        policy = write(os.path.join(self.ws, "policy", "rules.yml"), read(self.rules))
        for command in (
            "echo x > policy/rules.yml",
            "sed -i s/deny/allow/ policy/rules.yml",
            "cp /tmp/x policy/rules.yml",
            f"echo x > {policy}",
        ):
            with self.subTest(command=command):
                result = self.hook("Bash", self.ws, guard="enable", rules=policy, command=command)
                self.assert_denied(result, "builtin-guard-setting-files")
        # 名前の途中で当たったものは別のファイル。
        result = self.hook(
            "Bash", self.ws, guard="enable", rules=policy, command="echo x > otherpolicy/rules.yml"
        )
        self.assertNotIn("builtin-guard-setting-files", self.reason(result))

    def test_lint_warns_about_new_files_in_a_worktree_project_home(self):
        """§11.6: 作業ツリーの `.ccnavi/` に切り元に無いファイルがあれば --lint warn。"""
        tree = self.worktree(self.lib, "w1")
        write(os.path.join(tree, HOME, "scripts", "new.sh"), "echo new\n")

        warns = self.problems("warn")
        self.assertTrue(any("new.sh" in p["detail"] for p in warns), warns)


@unittest.skipUnless(SHELL, "sh も bash も見つからない")
@unittest.skipUnless(HAS_JQ, "jq が見つからない")
class SetupTest(unittest.TestCase):
    """導入スクリプト（§11.9「導入スクリプト」）。tests/test_setup.py の作りに合わせる。"""

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
        """配布元のふり。実行ファイルと、共通層の rules / risk と、自身の層の phases。

        ゲートの sh 3 本は起動して最初に共通部（ccnavi-common.sh）を読むので、配布元にも
        それを置く。無いと「配布元に無くて配れないもの」として名指しされる。
        """
        src = tempfile.mkdtemp(prefix="ccnavi-union-source-")
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        binary = os.path.join(src, "dist", "ccnavi", "ccnavi")
        os.makedirs(os.path.join(src, "dist", "ccnavi", "_internal"))
        write(binary, "#!/bin/sh\nexit 0\n")
        os.chmod(binary, 0o755)
        # 置き場の名前になる印と、hook が起動する振り分けの sh（ADR-0041）。
        write(os.path.join(src, "dist", "ccnavi.target"), platformtag.host_target() + "\n")
        os.makedirs(os.path.join(src, "scripts"))
        shutil.copy(LAUNCHER, os.path.join(src, "scripts", "ccnavi-launcher.sh"))
        write(os.path.join(src, ".ccnavi", "common", "rules.yml"), "deny: []\n")
        write(os.path.join(src, ".ccnavi", "common", "risk.yml"), COMMON_RISK)
        write(os.path.join(src, HOME, "config", "phases.yml"), COMMON_PHASES)
        for name in ("ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh", "ccnavi-common.sh"):
            write(os.path.join(src, ".ccnavi", "scripts", name), f"# {name}\n")
        return src

    def settings(self):
        with open(os.path.join(self.dir, ".claude", "settings.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_deploy_copies_common_rules_and_risk_and_own_phases(self):
        """§11.9: 共通層に rules.yml と risk.yml、自身の層に phases.yml のひな形を配る。"""
        src = self.make_source()
        result = self.run_setup("--mode", "enable", "--deploy", src)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue(os.path.isfile(os.path.join(self.dir, ".ccnavi", "common", "rules.yml")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, ".ccnavi", "common", "risk.yml")))
        self.assertTrue(os.path.isfile(os.path.join(self.dir, HOME, "config", "phases.yml")))
        self.assertNotIn("まだ無いもの", result.stdout)

    def test_missing_templates_are_named(self):
        """§11.9: 3 本とも「まだ無いもの」の点検に数える。"""
        result = self.run_setup("--mode", "enable", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("まだ無いもの", result.stdout)
        self.assertIn(".ccnavi/common/rules.yml", result.stdout)
        self.assertIn(".ccnavi/common/risk.yml", result.stdout)
        self.assertIn(".ccnavi/config/phases.yml", result.stdout)

    def test_all_writes_project_home(self):
        """§11.9: `--all` の env に `CCNAVI_PROJECT_HOME: ".ccnavi"` を足す。"""
        result = self.run_setup("--all", "--no-deploy")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.settings()["env"]
        self.assertEqual(env.get("CCNAVI_PROJECT_HOME"), ".ccnavi")


if __name__ == "__main__":
    unittest.main()
