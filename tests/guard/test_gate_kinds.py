"""止めている箇所の分類（一般・ゲート・補助）と、サブエージェントの役割の表を確かめる。

- ゲート（GATE）と補助（AUX）は dry-run でも止める。一般の保護は dry-run なら報告だけにする
- 承認済みの置き場と操作記録の置き場への書き込みは、dry-run でも自己防衛の設定を切っても止める
- サブエージェントは、状態を動かす・公開する操作を打てない。読み取りは打てる
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class HookCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-gate-kinds-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        _write(common_path(self.root, "rules"), json.dumps({"version": 1}))

    def hook(self, tool, tool_input, *, mode="enable", agent="", **env_extra):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": self.root,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        if agent:
            payload["agent_id"] = agent
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(env_extra)
        result = run_ccnavi(
            ["--root", self.root, "--mode", mode],
            input=json.dumps(payload),
            cwd=ROOT,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        text = out.get("permissionDecisionReason") or out.get("additionalContext") or ""
        return out.get("permissionDecision", ""), text

    def bash(self, command, **kwargs):
        return self.hook("Bash", {"command": command}, **kwargs)


class DryRunKindsTest(HookCase):
    def test_a_general_protection_only_reports_in_dry_run(self):
        decision, text = self.bash("rm -rf /tmp/some-dir", mode="dry-run")
        self.assertEqual(decision, "")
        self.assertIn("[ccnavi dry-run]", text)
        decision, _ = self.bash("rm -rf /tmp/some-dir")
        self.assertEqual(decision, "deny")

    def test_a_gate_stops_in_dry_run_and_says_so(self):
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"
        decision, text = self.bash(command, mode="dry-run", agent="a1")
        self.assertEqual(decision, "deny", text)
        self.assertIn("dry-run", text)
        self.assertIn("still applies", text)

    def test_raw_git_stops_in_dry_run(self):
        decision, text = self.bash("git status", mode="dry-run")
        self.assertEqual(decision, "deny", text)

    def test_an_env_assignment_before_a_protected_script_stops_in_dry_run(self):
        decision, text = self.bash(
            "CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push", mode="dry-run"
        )
        self.assertEqual(decision, "deny", text)

    def test_the_env_assignment_stop_stays_when_the_core_files_guard_is_off(self):
        decision, _ = self.bash(
            "CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push",
            CCNAVI_GUARD_CORE_FILES="disable",
        )
        self.assertEqual(decision, "deny")


class ClassificationTest(HookCase):
    """分類を決める分岐ごとに、dry-run で止まるか報告だけかを固定する。"""

    def test_a_raw_git_stays_an_aux_stop_when_a_layer_rule_also_hits(self):
        # 層の deny（認証情報）と同時にヒットしても、生の git の補助止めは dry-run で効く。
        decision, text = self.bash("git status; cat .env", mode="dry-run")
        self.assertEqual(decision, "deny", text)
        self.assertIn("still applies", text)

    def test_the_redirect_cap_stops_writes_to_the_places_in_dry_run(self):
        command = (
            "echo hi "
            + "> /dev/null " * 60
            + "> .ccnavi/approved/phases/i0001/1.reviewed ; cat <<EOF"
        )
        decision, text = self.bash(command, mode="dry-run")
        self.assertEqual(decision, "deny", text)

    def test_rewrites_that_can_hide_git_stop_in_dry_run(self):
        for command in ("c=git; $c push origin main", "{git,push,origin,main}"):
            with self.subTest(command=command):
                decision, text = self.bash(command, mode="dry-run")
                self.assertEqual(decision, "deny", text)

    def test_a_path_containing_ccnavi_is_not_an_entry(self):
        decision, text = self.bash("ls /work/ccnavi/{src,tests}", mode="dry-run")
        self.assertNotEqual(decision, "deny", text)
        # enable では書き直しを求めて止める。
        decision, _ = self.bash("ls /work/ccnavi/{src,tests}")
        self.assertEqual(decision, "deny")

    def test_the_subagent_rewrites_stop_in_dry_run(self):
        decision, text = self.bash("ls {src,tests}", mode="dry-run", agent="a1")
        self.assertEqual(decision, "deny", text)

    def test_other_writers_cannot_touch_the_places(self):
        for command in (
            "touch .ccnavi/approved/phases/i0001/1.reviewed",
            "mkdir -p .ccnavi/approved/phases/i0001",
            "rsync -a /tmp/x .ccnavi/approved/",
            "python3 -c \"open('.ccnavi/approved/doing/x.md','w')\"",
            "chmod 000 logs/state/ops/a1.json",
        ):
            with self.subTest(command=command):
                decision, text = self.bash(command, mode="dry-run")
                self.assertEqual(decision, "deny", text)


class PlacesTest(HookCase):
    """承認済みの置き場と操作記録の置き場は、どのモードでも自己防衛を切っても止める。

    場所・モード・自己防衛の値・ツール（Write / Edit / シェルの書き込み / Read）の全部の組み合わせは
    Go（hook/judge の TestThePlacesAreStoppedInAnyMode）が見る。ここでは hook を通した代表だけ。
    """

    def test_the_tools_and_the_shell_cannot_write_the_places_in_dry_run(self):
        rel = ".ccnavi/approved/phases/i0001/1.reviewed"
        decision, text = self.hook(
            "Write",
            {"file_path": os.path.join(self.root, rel)},
            mode="dry-run",
            CCNAVI_GUARD_CORE_FILES="disable",
        )
        self.assertEqual(decision, "deny", text)
        decision, text = self.bash(
            "echo x > logs/state/ops/a1.json", mode="dry-run", CCNAVI_GUARD_CORE_FILES="disable"
        )
        self.assertEqual(decision, "deny", text)


class SubagentRolesTest(HookCase):
    """サブエージェントは状態を動かす・公開する操作を打てない。読み取りは打てる。

    止める形・通す形の表（モードごと、git の副命令を含む）は Go（hook/judge の
    TestTheSubagentRoles）が見る。ここでは hook を通した代表と、実行ファイルを直に打つ形だけ。
    """

    def test_the_subagent_cannot_type_a_ticket_op_and_the_main_agent_can(self):
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"
        decision, text = self.bash(command, mode="dry-run", agent="a1")
        self.assertEqual(decision, "deny", text)
        self.assertIn("DENY_SUBAGENT_TICKET_OP", text)
        _, text = self.bash(command)
        self.assertNotIn("DENY_SUBAGENT_TICKET_OP", text)
        decision, text = self.bash("sh .ccnavi/scripts/ccnavi-ticket.sh status", agent="a1")
        self.assertNotEqual(decision, "deny", text)

    def test_the_executable_called_directly_stops_the_subagent_even_if_the_guard_is_off(self):
        for command in ("ccnavi ticket start i0001", "ccnavi review confirm --phase 1"):
            with self.subTest(command=command):
                decision, text = self.bash(
                    command, agent="a1", CCNAVI_GUARD_TICKET_APPROVAL="disable"
                )
                self.assertEqual(decision, "deny", text)
                decision, text = self.bash(command, CCNAVI_GUARD_TICKET_APPROVAL="disable")
                self.assertNotEqual(decision, "deny", text)


class DryRunGeneralConfigTest(HookCase):
    """AT1：dry-run では、一般の保護の対象の変更は報告だけ。一般の復元は通る。"""

    def test_a_config_change_is_reported_not_stopped_in_dry_run(self):
        for rel in (".claude/settings.json", ".ccnavi/config/common/rules.yml"):
            for tool in ("Write", "Edit"):
                with self.subTest(rel=rel, tool=tool):
                    tool_input = {"file_path": os.path.join(self.root, rel)}
                    decision, text = self.hook(tool, tool_input, mode="dry-run")
                    self.assertEqual(decision, "")
                    self.assertIn("[ccnavi dry-run] enable would have stopped this call", text)
                    decision, _ = self.hook(tool, tool_input)
                    self.assertEqual(decision, "deny")

    def test_a_shell_write_to_the_config_is_reported_in_dry_run(self):
        decision, text = self.bash("echo x > .claude/settings.json", mode="dry-run")
        self.assertEqual(decision, "")
        self.assertIn("enable would have stopped this call", text)
        decision, _ = self.bash("echo x > .claude/settings.json")
        self.assertEqual(decision, "deny")

    def test_the_general_restore_works_as_before(self):
        command = (
            "sh .ccnavi/scripts/ccnavi-git.sh restore --ours -- .ccnavi/config/common/rules.yml"
        )
        for mode in ("enable", "dry-run"):
            with self.subTest(mode=mode):
                decision, text = self.bash(command, mode=mode)
                self.assertNotEqual(decision, "deny", text)


class DryRunSelfApprovalTest(HookCase):
    """AT2：dry-run でも、自己承認の形はメインエージェントでも拒否される。"""

    FORMS = (
        "ccnavi user agree",
        "ccnavi user agree --yes",
        "ccnavi --agree --yes i0001",
        "uv run python -m ccnavi --agree --yes x",
        "sh .ccnavi/scripts/ccnavi-user-agree.sh",
    )

    def test_the_main_agent_cannot_type_them_in_dry_run(self):
        for command in self.FORMS:
            with self.subTest(command=command):
                decision, text = self.bash(command, mode="dry-run")
                self.assertEqual(decision, "deny", text)
                self.assertIn("DENY_TICKET_APPROVAL_CLI", text)


if __name__ == "__main__":
    unittest.main()
