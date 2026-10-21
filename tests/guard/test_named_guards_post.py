"""ccnavi 自身の保護と承認済みの置き場の保護が、実行後にも効くことの受入テスト。

実行前の `tests/guard/test_named_guards.py` と対になる。実際の git リポジトリ（ワークスペース、
ワークツリー、プロジェクト、プロジェクトのワークツリー）を作り、追跡済みのファイルを書き換えてから
hook の payload を渡す。共通のルールは最小の組（残す側のルールも入れない）にして、
検知を支えるのが組み込みだけであることを、記録に残る `rules` の集合で確かめる。

1. 4 本（hooks・settings・scripts・共通の設定 3 本）: PostToolUse / PostToolUseFailure で検知し、
   実効値が enable なら復元する。全体モードが dry-run なら復元しない。Stop は報告だけ。
   prompt / SessionStart は前からある変更とユーザが受け入れた変更を差し戻さない。
   直下の `wip/handoff/` の普通の写しは検知しない。自己防衛を切れば足されない
2. 承認済みの置き場: チケット制御が有効なら、自己防衛の値に依らず検知・復元する。
   正規の書き込み（ccnavi のスクリプトが書く形）は外れ、不正な変更は外れない
3. 旧ルール（固定データ）との比較: 代表パスの表で、保護対象と写しの例外を失っていない
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

import yaml

from ccnavi.hook import events
from ccnavi.infra import settings
from ccnavi.policy import rules
from ccnavi.records import audit
from tests import ROOT
from tests.guard.test_post import DOING, STARTED, TICKET, git, write
from tests.inproc import run_ccnavi

HOOKS = "builtin:guard-hooks"
SETTINGS = "builtin:guard-settings"
SCRIPTS = "builtin:guard-scripts"
CONFIG = "builtin:guard-ccnavi-config"
APPROVED = "builtin:ticket-approved"

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

TARGETS = {
    ".claude/hooks/guard.sh": HOOKS,
    ".claude/settings.json": SETTINGS,
    ".claude/settings.local.json": SETTINGS,
    ".ccnavi/scripts/ccnavi-x.sh": SCRIPTS,
    ".ccnavi/config/common/rules.yml": CONFIG,
    ".ccnavi/config/common/phases.yml": CONFIG,
    ".ccnavi/config/common/risks.yml": CONFIG,
}
TREES = ("ws", "worktree", "project", "project-worktree")


def initial(rel):
    if rel.endswith("rules.yml"):
        return json.dumps(MINIMAL_RULES) + "\n"
    if rel.endswith(".json"):
        return "{}\n"
    return "# committed\n"


def changed(rel):
    if rel.endswith(".json"):
        return '{"changed": true}\n'
    return initial(rel) + "# changed by a build\n"


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Fixture(unittest.TestCase):
    """4 種類のツリーを持つワークスペースと、hook の payload を 1 回渡す道具。

    自己防衛の既定は dry-run にしてある。enable だと、設定ファイルを守る側（selfguard）が
    settings と共通の設定 3 本を先に戻してしまい、組み込みの 4 本の検知を単独で見られない。
    dry-run は disable ではないので 4 本は足され、selfguard は戻さず報告するだけになる。
    enable との組み合わせは `test_with_the_self_guard_enabled_the_files_end_up_restored` が見る。
    """

    def setUp(self):
        base = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-named-post-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root = os.path.join(base, "ws")
        self.state = os.path.join(self.root, "logs", "state")
        self.log = os.path.join(self.root, "logs", "decisions.jsonl")
        os.makedirs(self.root)
        git(self.root, "init", "--quiet")
        write(os.path.join(self.root, ".gitignore"), ".claude/worktrees/\nprojects/\nlogs/\n")
        self.populate(self.root)
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        lib = os.path.join(self.root, "projects", "lib")
        os.makedirs(lib)
        git(lib, "init", "--quiet")
        self.populate(lib)
        git(lib, "add", "-A")
        git(lib, "commit", "--quiet", "-m", "init")
        worktrees = os.path.join(self.root, ".claude", "worktrees")
        git(self.root, "worktree", "add", "--quiet", os.path.join(worktrees, "w"), "-b", "w")
        git(lib, "worktree", "add", "--quiet", os.path.join(worktrees, "pw"), "-b", "pw")
        self.trees = {
            "ws": self.root,
            "worktree": os.path.join(worktrees, "w"),
            "project": lib,
            "project-worktree": os.path.join(worktrees, "pw"),
        }

    def populate(self, repo):
        for rel in TARGETS:
            write(os.path.join(repo, *rel.split("/")), initial(rel))
        write(os.path.join(repo, *DOING.split("/")), TICKET)
        write(os.path.join(repo, "src", "app.py"), "print(1)\n")

    def path(self, tree, rel):
        return os.path.join(self.trees[tree], *rel.split("/"))

    def run_hook(
        self,
        *,
        cwd=None,
        event="PostToolUse",
        tool="Bash",
        mode="enable",
        restore="enable",
        guard="dry-run",
        tickets="enable",
        session="s1",
        source=None,
        prompt=None,
        **tool_input,
    ):
        body = {
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": session,
            "cwd": cwd or self.root,
        }
        if prompt is not None:
            body["prompt"] = prompt
        if source is not None:
            body["source"] = source
        if event == "PostToolUseFailure":
            body["error"] = "exit 1"
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment[settings.TICKET_CONTROL_ENV] = tickets
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                mode,
                "--restore-if-deny",
                restore,
                "--guard-core-files",
                guard,
            ],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )

    def last_record(self):
        with open(self.log, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        return json.loads(lines[-1])

    def baseline(self, **options):
        """セッションの基準とターンの基準を、何も変わっていないうちに取る。"""
        self.run_hook(command="ls", **options)
        self.run_hook(event="UserPromptSubmit", prompt="続きをお願いします", **options)

    def said(self, result):
        return result.stdout + result.stderr


class NamedPostTest(Fixture):
    def test_each_place_is_detected_with_its_builtin_and_restored(self):
        self.baseline()
        for tree in TREES:
            for rel, rule_id in TARGETS.items():
                with self.subTest(tree=tree, path=rel):
                    path = self.path(tree, rel)
                    write(path, changed(rel))

                    result = self.run_hook(cwd=self.trees[tree], command="python build.py")

                    self.assertEqual(result.returncode, 2, self.said(result))
                    self.assertIn(rel, result.stderr.replace(os.sep, "/"))
                    self.assertEqual(self.last_record()["rules"], [rule_id])
                    self.assertEqual(read(path), initial(rel), "復元される")

    def test_post_tool_use_failure_detects_and_restores_too(self):
        self.baseline()
        for tree in TREES:
            with self.subTest(tree=tree):
                for rel in TARGETS:
                    write(self.path(tree, rel), changed(rel))

                result = self.run_hook(
                    cwd=self.trees[tree], event="PostToolUseFailure", command="false"
                )

                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                for rel in TARGETS:
                    self.assertIn(rel, context.replace(os.sep, "/"))
                    self.assertEqual(read(self.path(tree, rel)), initial(rel))
                self.assertEqual(
                    self.last_record()["rules"], sorted({HOOKS, SETTINGS, SCRIPTS, CONFIG})
                )

    def test_restore_off_reports_without_restoring(self):
        for restore in ("dry-run", "disable"):
            for tree in TREES:
                with self.subTest(restore=restore, tree=tree):
                    session = f"{restore}-{tree}"
                    self.baseline(session=session)
                    rel = ".claude/hooks/guard.sh"
                    write(self.path(tree, rel), changed(rel))

                    result = self.run_hook(
                        cwd=self.trees[tree],
                        command="python build.py",
                        restore=restore,
                        session=session,
                    )

                    self.assertEqual(result.returncode, 2, self.said(result))
                    self.assertEqual(read(self.path(tree, rel)), changed(rel))
                    self.assertEqual(self.last_record()["rules"], [HOOKS])
                    git(self.trees[tree], "checkout", "--quiet", "--", rel)

    def test_with_the_self_guard_enabled_the_files_end_up_restored(self):
        # 設定ファイルを守る側（selfguard）が先に戻すものがあるので、検知した側は問わない。
        self.baseline(guard="enable")
        for tree in TREES:
            for rel in TARGETS:
                write(self.path(tree, rel), changed(rel))
            result = self.run_hook(cwd=self.trees[tree], command="python build.py", guard="enable")
            self.assertIn(".claude/hooks/guard.sh", self.said(result).replace(os.sep, "/"))
            with self.subTest(tree=tree):
                for rel in TARGETS:
                    self.assertEqual(read(self.path(tree, rel)), initial(rel), rel)

    def test_a_global_dry_run_reports_without_restoring_even_when_restore_is_enable(self):
        self.run_hook(command="ls", mode="dry-run")
        for tree in TREES:
            with self.subTest(tree=tree):
                rel = ".claude/settings.json"
                write(self.path(tree, rel), changed(rel))

                result = self.run_hook(
                    cwd=self.trees[tree], command="python build.py", mode="dry-run"
                )

                self.assertIn(rel, self.said(result).replace(os.sep, "/"))
                self.assertEqual(read(self.path(tree, rel)), changed(rel))
                self.assertEqual(self.last_record()["rules"], [SETTINGS])

    def test_stop_reports_without_restoring(self):
        for tree in TREES:
            with self.subTest(tree=tree):
                self.run_hook(event="UserPromptSubmit", prompt="続き", session=f"s-{tree}")
                for rel in TARGETS:
                    write(self.path(tree, rel), changed(rel))

                result = self.run_hook(event="Stop", session=f"s-{tree}")

                message = json.loads(result.stdout)["systemMessage"]
                for rel in TARGETS:
                    self.assertIn(rel, message.replace(os.sep, "/"))
                    self.assertEqual(read(self.path(tree, rel)), changed(rel), "復元しない")
                self.assertEqual(
                    self.last_record()["rules"], sorted({HOOKS, SETTINGS, SCRIPTS, CONFIG})
                )
                for rel in TARGETS:
                    git(self.trees[tree], "checkout", "--quiet", "--", rel)

    def test_changes_that_were_there_before_are_not_reverted(self):
        for tree in TREES:
            with self.subTest(tree=tree):
                for rel in TARGETS:
                    write(self.path(tree, rel), changed(rel))
                session = f"pre-{tree}"
                self.run_hook(event="SessionStart", source="startup", session=session)

                result = self.run_hook(cwd=self.trees[tree], command="ls", session=session)

                self.assertEqual(result.returncode, 0, self.said(result))
                for rel in TARGETS:
                    self.assertEqual(read(self.path(tree, rel)), changed(rel))
                for rel in TARGETS:
                    git(self.trees[tree], "checkout", "--quiet", "--", rel)

    def test_changes_the_user_made_are_not_reverted_after_a_prompt(self):
        for tree in TREES:
            with self.subTest(tree=tree):
                session = f"user-{tree}"
                self.run_hook(event="SessionStart", source="startup", session=session)
                for rel in TARGETS:
                    write(self.path(tree, rel), changed(rel))
                self.run_hook(event="UserPromptSubmit", prompt="直しました", session=session)

                result = self.run_hook(cwd=self.trees[tree], command="ls", session=session)

                self.assertEqual(result.returncode, 0, self.said(result))
                for rel in TARGETS:
                    self.assertEqual(read(self.path(tree, rel)), changed(rel))
                for rel in TARGETS:
                    git(self.trees[tree], "checkout", "--quiet", "--", rel)

    def test_a_plain_handoff_copy_is_not_detected(self):
        self.baseline()
        for tree in TREES:
            with self.subTest(tree=tree):
                for rel in TARGETS:
                    write(self.path(tree, f"wip/handoff/{rel}"), changed(rel))

                result = self.run_hook(cwd=self.trees[tree], command="python build.py")

                self.assertEqual(result.returncode, 0, self.said(result))
                self.assertEqual(result.stderr, "")
                for rel in TARGETS:
                    self.assertTrue(os.path.exists(self.path(tree, f"wip/handoff/{rel}")))

    def test_a_copy_outside_the_place_right_under_a_tree_is_detected(self):
        self.baseline()
        rel = ".claude/hooks/guard.sh"
        for where in ("docs/wip/handoff", "wip/handoffx"):
            with self.subTest(where=where):
                write(self.path("ws", f"{where}/{rel}"), changed(rel))

                result = self.run_hook(command="python build.py")

                self.assertEqual(result.returncode, 2, self.said(result))
                self.assertEqual(self.last_record()["rules"], [HOOKS])

    def test_a_hard_linked_copy_is_detected(self):
        self.baseline()
        rel = ".claude/hooks/guard.sh"
        copy = self.path("ws", f"wip/handoff/{rel}")
        os.makedirs(os.path.dirname(copy))
        try:
            os.link(self.path("ws", rel), copy)
        except OSError:
            self.skipTest("ハードリンクを作れない機械")

        result = self.run_hook(command="python build.py")

        self.assertEqual(result.returncode, 2, self.said(result))
        self.assertIn(HOOKS, self.last_record()["rules"])

    def test_switching_the_self_guard_off_drops_the_four(self):
        self.baseline(guard="disable")
        for tree in TREES:
            with self.subTest(tree=tree):
                for rel in TARGETS:
                    write(self.path(tree, rel), changed(rel))

                result = self.run_hook(
                    cwd=self.trees[tree], command="python build.py", guard="disable"
                )

                self.assertEqual(result.returncode, 0, self.said(result))
                self.assertEqual(result.stderr, "")
                for rel in TARGETS:
                    git(self.trees[tree], "checkout", "--quiet", "--", rel)

    def test_stop_with_the_self_guard_off_reports_nothing_for_the_four(self):
        self.run_hook(event="UserPromptSubmit", prompt="続き", guard="disable")
        for rel in TARGETS:
            write(self.path("ws", rel), changed(rel))

        result = self.run_hook(event="Stop", guard="disable")

        self.assertNotIn("systemMessage", json.loads(result.stdout) if result.stdout else {})


class UnreadableRulesPostTest(Fixture):
    """共通のルールファイルが読めない間も、実行後の保護範囲から 4 本は外れない。"""

    def test_the_four_stay_while_the_common_rules_are_broken(self):
        write(self.path("ws", ".ccnavi/config/common/rules.yml"), "deny:\n  - id: [unclosed\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "broken rules")
        self.baseline()
        for rel, rule_id in (
            (".claude/hooks/guard.sh", HOOKS),
            (".ccnavi/scripts/ccnavi-x.sh", SCRIPTS),
        ):
            write(self.path("ws", rel), changed(rel))

            result = self.run_hook(command="python build.py")

            self.assertEqual(result.returncode, 2, self.said(result))
            self.assertEqual(self.last_record()["rules"], [rule_id])
            self.assertEqual(read(self.path("ws", rel)), initial(rel), "復元される")


class SessionRejectsTest(Fixture):
    """ticket の範囲の退けは、組み込みの保護（ルールファイルに無い）も数える。"""

    def denied(self, rel):
        from ccnavi.tickets import session_rejects

        conf, _ = settings.load(self.root)
        target = session_rejects.tree.Tree("w", self.trees["worktree"], "", "worktree")
        rule_set = session_rejects._rule_set(conf, self.root, target)
        rule = session_rejects._denied_by(rule_set, self.path("worktree", rel))
        return rule.id if rule else None

    def test_builtin_protections_are_counted(self):
        self.assertEqual(self.denied(".claude/hooks/guard.sh"), HOOKS)
        self.assertEqual(self.denied(".ccnavi/config/common/rules.yml"), CONFIG)
        self.assertEqual(self.denied(DOING), APPROVED)
        self.assertIsNone(self.denied("src/app.py"))


class ApprovedPostTest(Fixture):
    """承認済みの置き場は、チケット制御の保護。自己防衛の値には依らない。"""

    def widen(self, tree):
        write(self.path(tree, DOING), TICKET.replace('glob: "src/*"', 'glob: "*"'))

    def test_enabled_tickets_detect_and_restore_whatever_the_self_guard(self):
        for guard in ("enable", "disable"):
            self.baseline(guard=guard, session=f"g-{guard}")
            for tree in TREES:
                with self.subTest(guard=guard, tree=tree):
                    self.widen(tree)

                    result = self.run_hook(
                        cwd=self.trees[tree],
                        command="python build.py",
                        guard=guard,
                        session=f"g-{guard}",
                    )

                    self.assertEqual(result.returncode, 2, self.said(result))
                    self.assertEqual(self.last_record()["rules"], [APPROVED])
                    self.assertEqual(read(self.path(tree, DOING)), TICKET, "復元される")

    def test_a_global_dry_run_reports_only(self):
        self.run_hook(command="ls", mode="dry-run")
        self.widen("ws")

        result = self.run_hook(command="python build.py", mode="dry-run")

        self.assertIn(DOING, self.said(result))
        self.assertEqual(self.last_record()["rules"], [APPROVED])
        self.assertNotEqual(read(self.path("ws", DOING)), TICKET, "復元しない")

    def test_every_event_sees_the_approved_place(self):
        self.baseline()
        self.widen("ws")
        failure = self.run_hook(event="PostToolUseFailure", command="false")
        self.assertIn(DOING, failure.stdout)
        self.assertEqual(read(self.path("ws", DOING)), TICKET)

        self.widen("ws")
        write(self.path("ws", DOING), TICKET.replace('glob: "src/*"', 'glob: "**"'))
        stop = self.run_hook(event="Stop", restore="disable")
        self.assertIn(DOING, json.loads(stop.stdout)["systemMessage"])

    def test_the_stop_does_not_restore(self):
        self.run_hook(event="UserPromptSubmit", prompt="続き")
        self.widen("ws")

        result = self.run_hook(event="Stop")

        self.assertIn(DOING, json.loads(result.stdout)["systemMessage"])
        self.assertNotEqual(read(self.path("ws", DOING)), TICKET)

    def test_preexisting_and_user_changes_are_not_reverted(self):
        self.widen("ws")
        self.run_hook(event="SessionStart", source="startup", session="pre")
        result = self.run_hook(command="ls", session="pre")
        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertNotEqual(read(self.path("ws", DOING)), TICKET)

        git(self.root, "checkout", "--quiet", "--", DOING)
        self.run_hook(event="SessionStart", source="startup", session="user")
        self.widen("ws")
        self.run_hook(event="UserPromptSubmit", prompt="直しました", session="user")
        result = self.run_hook(command="ls", session="user")
        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertNotEqual(read(self.path("ws", DOING)), TICKET)

    def test_disabled_tickets_drop_the_post_protection(self):
        for guard in ("enable", "disable"):
            with self.subTest(guard=guard):
                self.baseline(guard=guard, tickets="disable", session=f"d-{guard}")
                self.widen("ws")

                result = self.run_hook(
                    command="python build.py",
                    guard=guard,
                    tickets="disable",
                    session=f"d-{guard}",
                )

                self.assertEqual(result.returncode, 0, self.said(result))
                self.assertEqual(result.stderr, "")
                self.assertNotEqual(read(self.path("ws", DOING)), TICKET)
                git(self.root, "checkout", "--quiet", "--", DOING)

    def test_a_handoff_copy_in_the_approved_place_is_not_exempt(self):
        self.baseline()
        write(self.path("ws", f"wip/handoff/{DOING}"), TICKET)

        result = self.run_hook(command="python build.py")

        self.assertEqual(result.returncode, 2, self.said(result))
        self.assertEqual(self.last_record()["rules"], [APPROVED])

    def test_the_message_guides_status_finish_and_propose(self):
        self.baseline()
        self.widen("ws")
        result = self.run_hook(command="python build.py")
        ticket = settings.script_command(self.root, "ccnavi-ticket.sh")
        for text in (
            f"{ticket} status",
            f"{ticket} finish",
            f"{ticket} propose",
            "ccnavi user agree",
        ):
            self.assertIn(text, result.stderr)


class ScriptWritesTest(Fixture):
    """ccnavi のスクリプトが書く形は外れ、不正な変更は外れない（ADR-0075）。

    承認済みの置き場の保護が組み込みに移っても、`post.check` の `places` の見分けは変わらない。
    見分けはコマンド名ではなく、変更の中身の形で行う。
    """

    def setUp(self):
        super().setUp()
        self.baseline()

    def run_script(self):
        return self.run_hook(command="sh .ccnavi/scripts/ccnavi-ticket.sh start i0001")

    def test_start_fields_are_not_reported_or_restored(self):
        write(self.path("ws", DOING), STARTED)
        # 着手の欄は、コミット済みの版にまだ無い欄だけ。
        result = self.run_script()
        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertEqual(read(self.path("ws", DOING)), STARTED)

    def test_a_marker_is_not_reported(self):
        marker = ".ccnavi/approved/phases/i0001/1.requested"
        write(self.path("ws", marker), '{"mr": 1}\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, self.said(result))
        self.assertTrue(os.path.exists(self.path("ws", marker)))

    def test_finish_move_is_not_reported(self):
        os.remove(self.path("ws", DOING))
        write(
            self.path("ws", "wip/proposals/review/i0001.md"),
            TICKET.replace("---\nbody", 'completed_at: "2026-09-22T01:00:00Z"\n---\nbody'),
        )
        result = self.run_script()
        self.assertEqual(result.returncode, 0, self.said(result))

    def test_forged_changes_are_reported_and_restored(self):
        cases = {
            "scope": TICKET.replace('glob: "src/*"', 'glob: "*"'),
            "body": TICKET.replace("body\n", "body with more\n"),
            "parent": TICKET.replace("id: i0001\n", "id: i0001\nparent: i0000\n"),
            "phase": TICKET.replace("id: i0001\n", "id: i0001\nphase: implement\n"),
        }
        for name, text in cases.items():
            with self.subTest(change=name):
                write(self.path("ws", DOING), text)
                result = self.run_script()
                self.assertEqual(result.returncode, 2, self.said(result))
                self.assertEqual(self.last_record()["rules"], [APPROVED])
                self.assertEqual(read(self.path("ws", DOING)), TICKET)

    def test_a_new_fake_ticket_is_reported(self):
        write(self.path("ws", ".ccnavi/approved/doing/i0002.md"), TICKET.replace("i0001", "i0002"))
        result = self.run_script()
        self.assertEqual(result.returncode, 2, self.said(result))
        self.assertEqual(self.last_record()["rules"], [APPROVED])

    def test_a_plain_deletion_is_reported_and_restored(self):
        os.remove(self.path("ws", DOING))
        result = self.run_script()
        self.assertEqual(result.returncode, 2, self.said(result))
        self.assertEqual(read(self.path("ws", DOING)), TICKET)

    def test_a_move_to_the_todo_place_is_reported(self):
        os.remove(self.path("ws", DOING))
        write(self.path("ws", "wip/proposals/todo/i0001.md"), TICKET)
        result = self.run_script()
        self.assertEqual(result.returncode, 2, self.said(result))


class OldRulesComparisonTest(Fixture):
    """実行後の保護範囲が、旧ルール（固定データ）の保護対象と写しの例外を失っていないこと。

    旧ルールの `guard-hooks` / `guard-scripts` / `guard-ccnavi-config` / `guard-settings` /
    `guard-approved-tickets` と、今の保護範囲（`events.watched_for` の組）を、代表パスの表で比べる。
    今に足されたのは、NotebookEdit での hooks / scripts と、ハードリンクでつながった写しの扱いだけ。
    """

    TOOLS = ("Write", "Edit", "NotebookEdit")

    def old_rules(self):
        with open(
            os.path.join(ROOT, "tests", "fixtures", "rules-before-builtin-guards.yml"),
            encoding="utf-8",
        ) as f:
            data = yaml.safe_load(f)
        data["deny"] = [r for r in data["deny"] if r["id"].startswith("guard-")]
        built, problems = rules.parse(data, self.root)
        self.assertEqual([p for p in problems if p.severity == rules.SEVERITY_ERROR], [])
        return built

    def new_rules(self):
        watched = events.watched_for(
            io.StringIO(),
            settings.load(self.root)[0],
            self.root,
            audit.Record(),
            None,
        )
        by_tree = {w.tree.root: w for w in watched}
        return by_tree[self.root].rule_set

    def test_the_table(self):
        old, new = self.old_rules(), self.new_rules()
        rows = []
        for tree in TREES:
            base = self.trees[tree]
            for rel in dict.fromkeys(
                [
                    *TARGETS,
                    *(f"wip/handoff/{r}" for r in TARGETS),
                    *(f"docs/wip/handoff/{r}" for r in TARGETS),
                    *(f"wip/handoffx/{r}" for r in TARGETS),
                    DOING,
                    ".ccnavi/approved/done/i0001.md",
                    ".ccnavi/approved/phases/i0001/1.requested",
                    f"wip/handoff/{DOING}",
                    "src/app.py",
                    ".claude/skills/x/SKILL.md",
                    ".ccnavi/docs/a.md",
                    ".claude/settings.json.bak",
                ]
            ):
                full = os.path.join(base, *rel.split("/"))
                for tool in self.TOOLS:
                    was = any(r.matches(tool, full) for r in old.deny)
                    now = any(r.matches(tool, full) for r in new.deny + new.ask)
                    rows.append((tree, tool, rel, was, now))
        lost = [r for r in rows if r[3] and not r[4]]
        self.assertEqual(lost, [], "旧ルールが守っていた場所を失った")
        added = [r for r in rows if r[4] and not r[3]]
        for tree, tool, rel, _, _ in added:
            self.assertTrue(
                tool == "NotebookEdit"
                and ("/" + rel).rsplit("/", 1)[0].endswith(("/.claude/hooks", "/.ccnavi/scripts")),
                (tree, tool, rel),
            )
        if os.environ.get("CCNAVI_PRINT_TABLE"):
            for tree, tool, rel, was, now in rows:
                print(f"{tree:17} {tool:12} {rel:55} 旧={int(was)} 今={int(now)}")


if __name__ == "__main__":
    unittest.main()
