"""止めている箇所の分類（一般・関門・補助）と、サブエージェントの役割の表を確かめる。

- 関門（GATE）と補助（AUX）は dry-run でも止める。一般の保護は dry-run なら報告だけにする
- 承認済みの置き場と操作記録の置き場への書き込みは、dry-run でも自己防衛の設定を切っても止める
- サブエージェントは、状態を動かす・公開する操作を打てない。読み取りは打てる
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ccnavi.tickets import manual_settle
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
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
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
        # 層の deny（認証情報）と同時に当たっても、生の git の補助止めは dry-run で効く。
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
    PLACES = (
        ".ccnavi/approved/phases/i0001/1.reviewed",
        ".ccnavi/approved/phases/i0001/accepted.json",
        ".ccnavi/approved/done/i0001-01-01.md",
        ".ccnavi/approved/doing/i0001-01-01.md",
        ".ccnavi/approved/flows/i0001.md",
        "logs/state/ops/a1.json",
    )

    def test_the_tools_cannot_write_the_places_in_any_mode(self):
        for rel in self.PLACES:
            for mode in ("enable", "dry-run"):
                for core in ("enable", "disable"):
                    for tool in ("Write", "Edit"):
                        with self.subTest(rel=rel, mode=mode, core=core, tool=tool):
                            decision, text = self.hook(
                                tool,
                                {"file_path": os.path.join(self.root, rel)},
                                mode=mode,
                                CCNAVI_GUARD_CORE_FILES=core,
                            )
                            self.assertEqual(decision, "deny", text)

    def test_the_shell_cannot_write_the_places_in_any_mode(self):
        for rel in self.PLACES:
            for command in (f"echo x > {rel}", f"rm {rel}", f"cp /tmp/x {rel}", f"tee {rel}"):
                for mode in ("enable", "dry-run"):
                    with self.subTest(command=command, mode=mode):
                        decision, text = self.bash(
                            command, mode=mode, CCNAVI_GUARD_CORE_FILES="disable"
                        )
                        self.assertEqual(decision, "deny", text)

    def test_reading_the_places_is_not_stopped_by_them(self):
        for rel in self.PLACES:
            decision, text = self.hook(
                "Read", {"file_path": os.path.join(self.root, rel)}, mode="dry-run"
            )
            self.assertNotIn("builtin:ticket-approved", text)
            self.assertNotIn("builtin:ticket-ops", text)
            self.assertNotEqual(decision, "deny", text)


class SubagentRolesTest(HookCase):
    STOPPED = (
        "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "sh .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01",
        "sh .ccnavi/scripts/ccnavi-ticket.sh cancel i0001-01-01",
        "sh .ccnavi/scripts/ccnavi-ticket.sh propose i0001",
        "sh .ccnavi/scripts/ccnavi-ticket.sh record-risk i0001",
        "sh .ccnavi/scripts/ccnavi-ticket.sh judge i0001",
        "sh .ccnavi/scripts/ccnavi-ticket.sh unknown-sub",
        "sh .ccnavi/scripts/ccnavi-ticket.sh",
        "bash -x .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01",
        "./.ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "/work/.ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "'/work/.ccnavi/scripts/ccnavi-ticket.sh' start i0001",
        "exec sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "command sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "source .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        ". .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "cd x && sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "env X=1 sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
        "sh .ccnavi/scripts/ccnavi-review.sh request --phase 1",
        "sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1",
        "sh .ccnavi/scripts/ccnavi-review.sh comment 3",
        "sh .ccnavi/scripts/ccnavi-review.sh ready",
        "sh .ccnavi/scripts/ccnavi-review.sh decide 3",
        "sh .ccnavi/scripts/ccnavi-review.sh unknown-sub",
        "sh .ccnavi/scripts/ccnavi-git.sh push origin x",
        "sh .ccnavi/scripts/ccnavi-git.sh worktree add x",
        "sh .ccnavi/scripts/ccnavi-git.sh integrate",
        "sh .ccnavi/scripts/ccnavi-git.sh integrate-done",
        "sh .ccnavi/scripts/ccnavi-start.sh",
        "sh .ccnavi/scripts/ccnavi-sync.sh",
        "sh .ccnavi/scripts/ccnavi-worktree.sh add x",
        "sh .ccnavi/scripts/ccnavi-clean.sh --worktree i0001-01-01",
        "sh .ccnavi/scripts/ccnavi-launcher.sh worktree add x",
        "sh .ccnavi/scripts/ccnavi-launcher.sh review comment 3",
        "sh .ccnavi/scripts/ccnavi-launcher.sh",
    )
    PASSED = (
        "sh .ccnavi/scripts/ccnavi-ticket.sh status",
        "sh .ccnavi/scripts/ccnavi-ticket.sh next-id",
        "sh .ccnavi/scripts/ccnavi-ticket.sh --help",
        "sh .ccnavi/scripts/ccnavi-ticket.sh learn",
        "sh .ccnavi/scripts/ccnavi-review.sh fetch",
        "sh .ccnavi/scripts/ccnavi-review.sh origin",
        "sh .ccnavi/scripts/ccnavi-review.sh merged",
        "sh .ccnavi/scripts/ccnavi-review.sh decide 3 --preview",
        "sh .ccnavi/scripts/ccnavi-review.sh --help",
        "sh .ccnavi/scripts/ccnavi-git.sh status",
        "sh .ccnavi/scripts/ccnavi-git.sh add -- a.py",
        "sh .ccnavi/scripts/ccnavi-fetch.sh",
        "sh .ccnavi/scripts/ccnavi-branches.sh",
        "sh .ccnavi/scripts/ccnavi-launcher.sh --docs x",
        "sh .ccnavi/scripts/ccnavi-launcher.sh --lint",
        "sh .ccnavi/scripts/ccnavi-launcher.sh --explain x",
    )

    def test_the_subagent_cannot_type_them_in_any_mode(self):
        for command in self.STOPPED:
            for mode in ("enable", "dry-run"):
                with self.subTest(command=command, mode=mode):
                    decision, text = self.bash(command, mode=mode, agent="a1")
                    self.assertEqual(decision, "deny", text)

    def test_the_subagent_can_type_the_reads(self):
        for command in self.PASSED:
            with self.subTest(command=command):
                decision, text = self.bash(command, agent="a1")
                self.assertNotIn("DENY_SUBAGENT_TICKET_OP", text)
                self.assertNotEqual(decision, "deny", text)

    def test_the_main_agent_is_not_stopped_by_the_roles(self):
        for command in self.STOPPED[:8]:
            with self.subTest(command=command):
                _, text = self.bash(command)
                self.assertNotIn("DENY_SUBAGENT_TICKET_OP", text)

    def test_the_executable_called_directly_stops_the_subagent_even_if_the_guard_is_off(self):
        for command in ("ccnavi ticket start i0001", "ccnavi review confirm --phase 1"):
            with self.subTest(command=command):
                decision, text = self.bash(
                    command, agent="a1", CCNAVI_GUARD_TICKET_APPROVAL="disable"
                )
                self.assertEqual(decision, "deny", text)
                decision, text = self.bash(command, CCNAVI_GUARD_TICKET_APPROVAL="disable")
                self.assertNotEqual(decision, "deny", text)


class GitRolesInChildTreeTest(HookCase):
    """AT44：状態を動かさない git の副命令は、役割の表で止めない。"""

    def test_the_subagent_git_rm_mv_restore_stash_reset_are_not_stopped_by_the_roles(self):
        for sub in ("rm", "mv", "restore", "stash", "reset"):
            for mode in ("enable", "dry-run"):
                with self.subTest(sub=sub, mode=mode):
                    decision, text = self.bash(
                        f"sh .ccnavi/scripts/ccnavi-git.sh {sub} -- a.py", mode=mode, agent="a1"
                    )
                    self.assertNotIn("DENY_SUBAGENT_TICKET_OP", text)
                    self.assertNotEqual(decision, "deny", text)

    def test_the_subagent_git_push_family_is_still_stopped(self):
        for sub in ("push", "worktree add x", "integrate"):
            with self.subTest(sub=sub):
                decision, text = self.bash(f"sh .ccnavi/scripts/ccnavi-git.sh {sub}", agent="a1")
                self.assertEqual(decision, "deny", text)


class PreToolUseReadsNoHistoryTest(HookCase):
    """PreToolUse は git の履歴を読まない。手動移動の判定を例外にしても判定は返る。"""

    def test_pre_tool_use_never_calls_the_manual_settle_assessment(self):
        calls = []

        def boom(*args, **kwargs):
            calls.append(args)
            raise RuntimeError("履歴を読んではいけない")

        with (
            mock.patch.object(manual_settle, "assess", side_effect=boom),
            mock.patch.object(manual_settle, "_shared", side_effect=boom),
        ):
            decision, _ = self.bash("rm -rf /tmp/some-dir")
            self.assertEqual(decision, "deny")
            decision, _ = self.bash("git status")
            self.assertEqual(decision, "deny")
            decision, text = self.hook(
                "Write",
                {"file_path": os.path.join(self.root, ".ccnavi/approved/done/i0001-01-01.md")},
            )
            self.assertEqual(decision, "deny", text)
            decision, text = self.hook("Write", {"file_path": os.path.join(self.root, "notes.md")})
            self.assertNotEqual(decision, "deny", text)
            decision, text = self.bash("ls")
            self.assertNotEqual(decision, "deny", text)
        self.assertEqual(calls, [])

    def test_the_pre_tool_use_modules_do_not_reach_the_history_readers(self):
        """上の mock は親の依頼済みフェーズが無いと空振りしうる。参照そのものも固定する。"""
        for name in ("judge.py", "failsafe.py"):
            with open(os.path.join(ROOT, "src", "ccnavi", "hook", name), encoding="utf-8") as f:
                text = f.read()
            for reader in ("manual_settle", "ops_stop_request"):
                with self.subTest(module=name, reader=reader):
                    self.assertNotIn(reader, text)


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
        "sh .ccnavi/scripts/ccnavi-agree.sh",
    )

    def test_the_main_agent_cannot_type_them_in_dry_run(self):
        for command in self.FORMS:
            with self.subTest(command=command):
                decision, text = self.bash(command, mode="dry-run")
                self.assertEqual(decision, "deny", text)
                self.assertIn("DENY_TICKET_APPROVAL_CLI", text)


if __name__ == "__main__":
    unittest.main()
