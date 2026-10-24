"""ccnavi 自身の保護と認証情報の保護が組み込みにあることの受入テスト（実行前）。

共通のルールは、テスト用の最小の組（残す側のルールも入れない）にする。止める側は組み込みだけなので、
当たったルールの id を、集合と順序で確かめる。「deny された」だけで済ませない。

1. 名前で書くツールの 4 本（`builtin:guard-hooks`・`-settings`・`-scripts`・`-ccnavi-config`）:
   4 種類のツリー、書き込みの例外（直下の `wip/handoff/` の普通の写し）、自己防衛を切ったとき、
   通常時の並び（専用の id が `guard-project-home` / `guard-common-layer` より前で、二重に入らない）
2. 共通のルールファイルが読めない・無い間: 4 本は外れない。直し方は下書きの案内
3. 認証情報（`builtin:credentials`）: 5 つのツールで止まり、Grep / Glob / PowerShell は対象外。
   failsafe（判定に達せなかったときの許可リスト）は、通常の判定が止める入力を安全扱いしない
4. 文面: 差し込みの目印が残らない。空白を含むルートで引用される。案内するのはワークスペースの sh
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.hook import failsafe
from ccnavi.infra import hookio, settings
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

HOOKS = "builtin:guard-hooks"
SETTINGS = "builtin:guard-settings"
SCRIPTS = "builtin:guard-scripts"
CONFIG = "builtin:guard-ccnavi-config"
HOME = "builtin:guard-project-home"
COMMON = "builtin:guard-common-layer"
LINKS = "builtin:guard-handoff-links"
CREDENTIALS = "builtin:credentials"
NAMED = (HOOKS, SETTINGS, SCRIPTS, CONFIG)

# テスト用の最小の共通ルール。ここにある deny は、この検査のどの入力にも当たらない。
MINIMAL_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "blocked-fetch",
            "match": "WebFetch",
            "glob": "*://blocked.example/*",
            "message": "取りに行かない。",
        }
    ],
}

TREES = ("", ".claude/worktrees/w", "projects/lib", "projects/lib/.claude/worktrees/w")
CONFIG_FILES = tuple(f".ccnavi/config/common/{n}.yml" for n in ("rules", "phases", "risks"))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Workspace(unittest.TestCase):
    prefix = "ccnavi-named-"

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix=self.prefix))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "rules"), json.dumps(MINIMAL_RULES))
        self.log = os.path.join(self.root, "logs", "decisions.jsonl")

    def at(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def hook(self, tool, tool_input, *, guard="enable", cwd=None, mode="enable", tickets=None):
        """(判定, 理由, 当たったルールの id の並び)。"""
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": "s1",
            "cwd": self.root if cwd is None else cwd,
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        if tickets is not None:
            environment[settings.TICKET_CONTROL_ENV] = tickets
        result = run_ccnavi(
            ["--root", self.root, "--mode", mode, "--guard-core-files", guard],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        if not result.stdout.strip():
            return "", "", self.last_ids()
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return (
            out.get("permissionDecision", ""),
            out.get("permissionDecisionReason") or "",
            self.last_ids(),
        )

    def last_ids(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except OSError:
            return []
        return json.loads(lines[-1]).get("rules", []) if lines else []

    def write_to(self, rel, **kw):
        return self.hook("Write", {"file_path": self.at(rel), "content": "x\n"}, **kw)


def expected(tree, rel):
    """通常時に、この書き込みで当たるはずの組み込みの id の並び。"""
    if rel.startswith(".claude/hooks/"):
        return [HOOKS]
    if rel.startswith(".claude/settings"):
        return [SETTINGS]
    if rel.startswith(".ccnavi/scripts/"):
        return [SCRIPTS, HOME]
    # 共通のルールの追う範囲は、ワークスペースルートとそこから切ったワークツリー。
    if tree in ("", ".claude/worktrees/w"):
        return [CONFIG, COMMON, HOME]
    return [CONFIG, HOME]


class NamedGuardsTest(Workspace):
    prefix = "ccnavi-named-pre-"

    def targets(self):
        return (
            ".claude/hooks/guard.sh",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".ccnavi/scripts/ccnavi-x.sh",
            *CONFIG_FILES,
        )

    def test_each_place_is_stopped_by_its_builtin_in_every_tree(self):
        for tree in TREES:
            for rel in self.targets():
                full = f"{tree}/{rel}" if tree else rel
                with self.subTest(path=full):
                    decision, reason, ids = self.write_to(full)
                    self.assertEqual(decision, "deny", reason)
                    self.assertEqual(ids, expected(tree, rel))
                    self.assertEqual(len(ids), len(set(ids)), "同じ id が二重に入る")

    def test_the_other_tools_are_stopped_too(self):
        for tool, field in (("Edit", "file_path"), ("NotebookEdit", "notebook_path")):
            with self.subTest(tool=tool):
                decision, _, ids = self.hook(tool, {field: self.at(".claude/hooks/guard.sh")})
                self.assertEqual(decision, "deny")
                self.assertEqual(ids, [HOOKS])

    def test_the_dedicated_ids_come_before_the_general_ones(self):
        for rel in (*CONFIG_FILES, ".ccnavi/scripts/ccnavi-x.sh"):
            with self.subTest(path=rel):
                _, _, ids = self.write_to(rel)
                named = next(i for i in ids if i in NAMED)
                for general in (HOME, COMMON):
                    if general in ids:
                        self.assertLess(ids.index(named), ids.index(general))

    def test_a_plain_copy_in_the_handoff_place_is_written_in_every_tree(self):
        for tree in TREES:
            for rel in self.targets():
                copy = f"{tree}/wip/handoff/{rel}" if tree else f"wip/handoff/{rel}"
                with self.subTest(path=copy):
                    decision, reason, ids = self.write_to(copy)
                    self.assertNotEqual(decision, "deny", reason)
                    self.assertEqual(ids, [])

    def test_the_handoff_exception_is_only_for_the_place_right_under_a_tree(self):
        for rel in (
            "docs/wip/handoff/.claude/hooks/x.sh",
            "wip/handoffx/.claude/hooks/x.sh",
            "wip/handoff/../.claude/hooks/x.sh",
        ):
            with self.subTest(path=rel):
                decision, _, ids = self.write_to(rel)
                self.assertEqual(decision, "deny")
                self.assertIn(HOOKS, ids)
        decision, _, ids = self.write_to("docs/wip/handoff/.ccnavi/scripts/x.sh")
        self.assertEqual(decision, "deny")
        self.assertEqual(ids, [SCRIPTS, HOME])
        decision, _, ids = self.write_to("docs/wip/handoff/.claude/settings.json")
        self.assertEqual((decision, ids), ("deny", [SETTINGS]))

    def test_a_hard_linked_copy_is_not_written(self):
        write(self.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
        os.makedirs(self.at("wip/handoff/.claude/hooks"))
        try:
            os.link(self.at(".claude/hooks/guard.sh"), self.at("wip/handoff/.claude/hooks/g.sh"))
        except OSError:
            self.skipTest("ハードリンクを作れない機械")
        decision, _, ids = self.write_to("wip/handoff/.claude/hooks/g.sh")
        self.assertEqual(decision, "deny")
        self.assertEqual(ids, [HOOKS, LINKS])

    def test_reading_is_not_stopped(self):
        write(self.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
        for tool, field in (("Read", "file_path"), ("Grep", "path"), ("Glob", "path")):
            with self.subTest(tool=tool):
                decision, _, ids = self.hook(tool, {field: self.at(".claude/hooks/guard.sh")})
                self.assertNotEqual(decision, "deny")
                self.assertFalse(set(ids) & set(NAMED))

    def test_switching_the_self_guard_off_drops_the_four(self):
        for rel in self.targets():
            with self.subTest(path=rel):
                _, _, ids = self.write_to(rel, guard="disable")
                self.assertFalse(set(ids) & set(NAMED), ids)
                self.assertNotIn(HOME, ids)

    def test_the_named_rules_are_not_added_twice(self):
        # 通常時は add_rules の後に 1 度だけ足す。同じ id が 2 本入ると、同じ文面が併記される。
        _, reason, ids = self.write_to(".claude/hooks/guard.sh")
        self.assertEqual(ids, [HOOKS])
        self.assertEqual(reason.count("hook 本体です"), 1)


class UnreadableRulesTest(Workspace):
    """共通のルールファイルが読めない間と、無い間。4 本は外れず、直し方は下書きになる。"""

    prefix = "ccnavi-named-broken-"
    DRAFT = "wip/handoff/.ccnavi/config/common/rules.yml"

    def broken(self):
        write(common_path(self.root, "rules"), "deny:\n  - id: [unclosed\n<<<<<<< HEAD\n")

    def absent(self):
        os.remove(common_path(self.root, "rules"))

    def test_the_four_hold_while_the_rules_are_broken(self):
        self.broken()
        places = (
            (".claude/hooks/guard.sh", HOOKS),
            (".claude/settings.json", SETTINGS),
            (".ccnavi/scripts/ccnavi-x.sh", SCRIPTS),
            *((rel, CONFIG) for rel in CONFIG_FILES),
        )
        for tree in TREES:
            for rel, named in places:
                full = f"{tree}/{rel}" if tree else rel
                with self.subTest(path=full):
                    decision, reason, ids = self.write_to(full)
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn(named, ids)

    def test_the_common_rules_cannot_be_repaired_by_write_or_edit(self):
        self.broken()
        for tool, tool_input in (
            ("Write", {"file_path": self.at(".ccnavi/config/common/rules.yml"), "content": "a"}),
            (
                "Edit",
                {
                    "file_path": self.at(".ccnavi/config/common/rules.yml"),
                    "old_string": "a",
                    "new_string": "b",
                },
            ),
            ("NotebookEdit", {"notebook_path": self.at(".ccnavi/config/common/rules.yml")}),
        ):
            with self.subTest(tool=tool):
                decision, reason, ids = self.hook(tool, tool_input)
                self.assertEqual(decision, "deny", reason)
                self.assertEqual(ids[0], CONFIG)
                # 下書きに置いてユーザに取り込みを頼む、と言う。
                self.assertIn(self.DRAFT, reason)
                self.assertIn("ccnavi-user-handoff.sh", reason)

    def test_the_draft_can_be_written_while_the_rules_are_broken(self):
        self.broken()
        decision, reason, ids = self.write_to(self.DRAFT)
        self.assertNotEqual(decision, "deny", reason)
        self.assertEqual(ids, [])

    def test_the_shell_cannot_break_or_fix_the_rules_while_they_are_broken(self):
        self.broken()
        decision, reason, ids = self.hook(
            "Bash", {"command": "echo x > .ccnavi/config/common/rules.yml"}
        )
        self.assertEqual(decision, "deny")
        self.assertIn("builtin:guard-config-via-bash", ids)
        # 「Write か Edit で書いてください」とは言わない。下書きとユーザの取り込みを案内する。
        self.assertNotIn("Write か Edit のツールで書いて", reason)
        self.assertIn(self.DRAFT, reason)
        self.assertIn("ccnavi-user-handoff.sh", reason)

    def test_the_four_hold_without_the_common_rules(self):
        self.absent()
        for rel in (".claude/hooks/guard.sh", ".claude/settings.json", *CONFIG_FILES):
            with self.subTest(path=rel):
                decision, _, ids = self.write_to(rel)
                self.assertEqual(decision, "deny")
                self.assertTrue(set(ids) & set(NAMED), ids)

    def test_the_four_are_off_with_the_self_guard_off_even_when_broken(self):
        self.broken()
        _, _, ids = self.write_to(".claude/hooks/guard.sh", guard="disable")
        self.assertFalse(set(ids) & set(NAMED), ids)


class CredentialsTest(Workspace):
    prefix = "ccnavi-named-cred-"
    PATHS = (
        ".env",
        ".env.example",
        ".aws/x",
        "x.pem",
        "x.pem.bak",
        "x.key.json",
        "secrets/a",
        "credentials/token",
        "credentials.json",
    )
    FILE_TOOLS = (
        ("Read", "file_path"),
        ("Write", "file_path"),
        ("Edit", "file_path"),
        ("NotebookEdit", "notebook_path"),
    )

    def test_the_five_tools_stop_each_path(self):
        for rel in self.PATHS:
            full = self.at(rel)
            for tool, field in self.FILE_TOOLS:
                with self.subTest(tool=tool, path=rel):
                    decision, _, ids = self.hook(tool, {field: full})
                    self.assertEqual(decision, "deny")
                    self.assertEqual(ids, [CREDENTIALS])
            with self.subTest(tool="Bash", path=rel):
                decision, _, ids = self.hook("Bash", {"command": f"cat {full}"})
                self.assertEqual(decision, "deny")
                self.assertEqual(ids, [CREDENTIALS])

    def test_the_sample_env_is_stopped_too(self):
        # 見本の `.env.example` も土台が止める。設定で外せないため、誤検知が出ても外せない。
        for tool, field in self.FILE_TOOLS:
            with self.subTest(tool=tool):
                _, reason, ids = self.hook(tool, {field: self.at(".env.example")})
                self.assertEqual(ids, [CREDENTIALS])
                self.assertIn("認証情報", reason)

    def test_the_base_stays_on_when_the_self_guard_is_off(self):
        _, _, ids = self.hook("Read", {"file_path": self.at(".env")}, guard="disable")
        self.assertEqual(ids, [CREDENTIALS])

    def test_grep_glob_and_powershell_are_not_matched(self):
        for rel in self.PATHS:
            with self.subTest(path=rel):
                for tool, tool_input in (
                    ("Grep", {"path": self.at(rel)}),
                    ("Glob", {"path": self.at(rel)}),
                    ("PowerShell", {"command": f"Get-Content {self.at(rel)}"}),
                ):
                    _, _, ids = self.hook(tool, tool_input)
                    self.assertNotIn(CREDENTIALS, ids, tool)

    def test_names_that_only_look_alike_are_not_stopped(self):
        for command in (
            "jq -r '.hooks[0],.env.X' a.json",
            "echo process.env.HOME",
            "cat README.md",
            "cat src/keyboard.py",
        ):
            with self.subTest(command=command):
                _, _, ids = self.hook("Bash", {"command": command})
                self.assertNotIn(CREDENTIALS, ids)


class FailsafeTest(Workspace):
    """判定に達せなかったときの許可リスト（failsafe）は、通常の判定が止める入力を安全扱いしない。"""

    prefix = "ccnavi-named-failsafe-"

    PATHS = CredentialsTest.PATHS

    def safe(self, tool, tool_input, cwd=None):
        payload = hookio.Input(
            event=hookio.PRE_TOOL_USE,
            tool_name=tool,
            tool_input=tool_input,
            cwd=self.root if cwd is None else cwd,
        )
        return failsafe.is_safe(payload, self.root)

    def spellings(self, rel):
        """生の値・絶対パス・相対パス・`./` 付きの綴り。"""
        return {rel, f"./{rel}", self.at(rel)}

    def test_what_the_normal_judgement_stops_is_not_safe_for_read(self):
        for rel in self.PATHS:
            for spelled in self.spellings(rel):
                with self.subTest(path=spelled):
                    decision, _, _ = self.hook("Read", {"file_path": spelled})
                    self.assertEqual(decision, "deny")
                    self.assertFalse(self.safe("Read", {"file_path": spelled}))

    def test_a_relative_path_is_not_safe_whatever_the_cwd(self):
        sub = self.at("sub")
        os.makedirs(sub)
        for rel in self.PATHS:
            with self.subTest(path=rel):
                self.assertFalse(self.safe("Read", {"file_path": rel}, cwd=sub))
                self.assertFalse(self.safe("Read", {"file_path": rel}, cwd=self.root))

    def test_grep_and_glob_are_not_safe_on_these_paths(self):
        for rel in self.PATHS:
            for spelled in self.spellings(rel):
                with self.subTest(path=spelled):
                    self.assertFalse(self.safe("Grep", {"path": spelled}))
                    self.assertFalse(self.safe("Grep", {"glob": spelled}))
                    self.assertFalse(self.safe("Glob", {"path": spelled}))

    def test_a_single_cat_is_not_safe(self):
        for rel in self.PATHS:
            for spelled in self.spellings(rel):
                with self.subTest(path=spelled):
                    self.assertFalse(self.safe("Bash", {"command": f"cat {spelled}"}))

    def test_ordinary_files_stay_safe(self):
        for rel in ("README.md", "src/keyboard.py", "docs/credentials-policy.md"):
            with self.subTest(path=rel):
                self.assertTrue(self.safe("Read", {"file_path": self.at(rel)}))
                self.assertTrue(self.safe("Bash", {"command": f"cat {rel}"}))


class ApprovedPreTest(Workspace):
    """承認済みの置き場は、チケット制御の保護。自己防衛の値に依らず、実行前は拒否する。"""

    prefix = "ccnavi-named-approved-"
    DOING = ".ccnavi/approved/doing/i0001.md"
    APPROVED = "builtin:ticket-approved"

    def test_enabled_tickets_deny_whatever_the_self_guard(self):
        for guard, want in (("enable", [HOME, self.APPROVED]), ("disable", [self.APPROVED])):
            with self.subTest(guard=guard):
                decision, reason, ids = self.write_to(self.DOING, guard=guard, tickets="enable")
                self.assertEqual(decision, "deny", reason)
                # 並びは今のまま。guard-project-home が先で、builtin:ticket-approved が後。
                self.assertEqual(ids, want)

    def test_a_global_dry_run_still_denies_as_a_gate(self):
        for guard in ("enable", "disable"):
            with self.subTest(guard=guard):
                decision, _, ids = self.write_to(
                    self.DOING, guard=guard, tickets="enable", mode="dry-run"
                )
                self.assertEqual(decision, "deny")
                self.assertEqual(ids[-1], self.APPROVED)

    def test_disabled_tickets_leave_it_to_the_project_home_rule(self):
        decision, _, ids = self.write_to(self.DOING, guard="enable", tickets="disable")
        self.assertEqual((decision, ids), ("deny", [HOME]))
        decision, _, ids = self.write_to(self.DOING, guard="disable", tickets="disable")
        self.assertNotEqual(decision, "deny")
        self.assertEqual(ids, [])

    def test_the_message_guides_status_finish_and_propose(self):
        _, reason, _ = self.write_to(self.DOING, tickets="enable")
        ticket = settings.script_command(self.root, "ccnavi-ticket.sh")
        for text in (
            f"{ticket} status",
            f"{ticket} finish <識別子>",
            f"{ticket} propose <識別子>",
            "ccnavi-user-agree.sh",
            "wip/proposals/todo/",
        ):
            self.assertIn(text, reason)
        self.assertNotIn("{root}", reason)
        self.assertNotIn("{script:", reason)

    def test_a_handoff_copy_in_the_approved_place_is_not_exempt(self):
        decision, _, ids = self.write_to(f"wip/handoff/{self.DOING}", tickets="enable")
        self.assertEqual(decision, "deny")
        self.assertIn(self.APPROVED, ids)


class MessageTest(Workspace):
    """文面に差し込みの目印が残らず、案内がワークスペースの sh を指す。"""

    prefix = "ccnavi named with space-"

    def reasons(self):
        out = []
        for rel in (
            ".claude/hooks/guard.sh",
            ".claude/settings.json",
            ".ccnavi/scripts/ccnavi-x.sh",
            ".ccnavi/config/common/rules.yml",
        ):
            out.append((rel, self.write_to(rel)[1]))
        return out

    def test_no_placeholder_is_left(self):
        for rel, reason in self.reasons():
            with self.subTest(path=rel):
                self.assertNotIn("{root}", reason)
                self.assertNotIn("{script:", reason)

    def test_the_root_with_a_space_is_quoted(self):
        self.assertIn(" ", self.root)
        stage = settings.script_command(self.root, "ccnavi-stage.sh")
        self.assertIn('"', stage)
        for rel, reason in self.reasons():
            with self.subTest(path=rel):
                self.assertIn(stage, reason)

    def test_the_guide_points_to_the_workspace_script(self):
        stage = settings.script_command(self.root, "ccnavi-stage.sh")
        self.assertTrue(stage.endswith('/.ccnavi/scripts/ccnavi-stage.sh"'))
        self.assertIn(os.path.realpath(self.root).replace("\\", "/"), stage)


if __name__ == "__main__":
    unittest.main()
