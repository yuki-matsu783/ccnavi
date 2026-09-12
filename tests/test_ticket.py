"""並行するチケット（REQ-TKT）の受入テスト。道具を外から叩いて応答だけを見る。

本物の git リポジトリと作業ツリーを一時ディレクトリに作る。親 1 本と子 2 本を
フェーズ 1 つで通す（requirements.md の受け入れ条件 9）。

見るのは 6 つ。

1. 判定の鍵がファイルの行き先であること。親の cwd から子のツリーへ絶対パスで
   書いても、子のチケットで判定される
2. 子は親の部分集合で、超えた子は承認されないこと
3. 状態の置き場への直接の作成と、サブエージェントからの状態の移動が止まること
4. フェーズが終わるとゲートが閉じ、レビューが済むと開くこと
5. 変更要求のレビューは人の端末からも通せないこと
6. 基準点より後にコミットされた範囲外の変更を、サブエージェントの終了で差し戻すこと
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.inproc import run_ccnavi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = {
    "version": 3,
    "deny": [
        {
            "id": "guard-approved",
            "match": "Write|Edit|NotebookEdit",
            "glob": "*/.claude/ccnavi/*",
            "message": "ガードの設定と写しです。利用者に依頼してください。",
        }
    ],
}


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def ticket_text(
    name,
    *,
    parent="",
    phase=None,
    allow=(),
    ask=(),
    review=True,
    predecessors=(),
    title="作業",
    issue=None,
):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if issue is not None:
        lines.append(f"issue: {issue}")
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if predecessors:
        lines.append("predecessors: [" + ", ".join(predecessors) + "]")
    lines += [
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: テスト",
        f"title: {title}",
        "rationale: |",
        "  理由",
    ]
    for section, globs in (("allow", allow), ("ask", ask)):
        if globs:
            lines.append(f"{section}:")
            for g in globs:
                lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


class TicketTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-ticket-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\nwip/tmp/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")

        self.rules = write(os.path.join(self.root, "rules.yml"), json.dumps(RULES))
        self.approved = os.path.join(self.root, ".claude", "ccnavi", "tickets")
        self.state = os.path.join(self.root, "state")
        self.parent_tree = self.worktree("i0001", "main")

    # ---- 道具

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin="", env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                self.rules,
                "--approved",
                self.approved,
                "--state",
                self.state,
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                # 人の判断の経路の端末要求は切る。テストは端末を持たない。
                # 経路そのものの検査は、個別に enable を渡す。
                "--guard-ticket-approval",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(
        self, event, tool, cwd, mode="enable", agent_id="", guard_ticket_approval="", **tool_input
    ):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        extra = ["--guard-ticket-approval", guard_ticket_approval] if guard_ticket_approval else []
        return self.ccnavi("--mode", mode, *extra, stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, **kw):
        return write(
            os.path.join(self.parent_tree, "wip", "tickets", "todo", name + ".md"),
            ticket_text(name, **kw),
        )

    def approve(self, answer="y"):
        return self.ccnavi("--approve", stdin=answer + "\n")

    def family(self, review=(True, False)):
        """親 1 本と子 2 本をフェーズ 1 で提案し、承認して、子の作業ツリーを作って着手する。"""
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",), review=review[0])
        self.propose("i0001-02", parent="i0001", phase=1, allow=("src/b/*",), review=review[1])
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for child in ("i0001-01", "i0001-02"):
            self.worktree(child, "i0001")
            started = self.ccnavi("ticket", "start", child)
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 状態の移動は親がコミットする。参考にした運用と同じ。
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "start")

    # ---- 1. 判定の鍵は行き先

    def test_writes_are_judged_by_where_they_land(self):
        self.family()
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")

        inside = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(child, "src", "a", "x.py"),
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside), self.reason(inside))
        self.assertEqual(inside.returncode, 0, inside.stderr)

        # 親の cwd から、子のツリーの範囲外へ。子のチケットで止まる。
        outside = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(child, "src", "b", "x.py"),
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))
        self.assertIn("i0001-01", self.reason(outside))

        # 親のツリーは親の範囲で判定される。
        parent_ok = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(self.parent_tree, "src", "z.py")
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(parent_ok))
        parent_out = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(self.parent_tree, "docs", "z.md")
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(parent_out))

        # main はチケットを持たない。ルールだけで判定される。
        main = self.hook(
            "PreToolUse", "Write", self.root, file_path=os.path.join(self.root, "docs", "z.md")
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(main))

    def test_scope_with_uppercase_still_matches(self):
        """大文字を含む範囲が、書いた綴りのまま当たること。

        作業ツリーのルートからの相対パスを normcase した綴りから作っていたので、
        大文字小文字を区別しない機械では `README.md` が `readme.md` になり、
        `README.md` と書いた範囲に永久に当たらなかった。`Dockerfile` や
        `src/Components/*` も同じ。実物の GitLab で流れを通したときに出た。
        """
        self.propose("i0001", allow=("src/*", "README.md", "docs/Design/*"))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "ticket")
        self.assertEqual(self.approve().returncode, 0)
        for name in ("README.md", "docs/Design/plan.md"):
            hit = self.hook(
                "PreToolUse",
                "Write",
                self.parent_tree,
                file_path=os.path.join(self.parent_tree, *name.split("/")),
            )
            self.assertNotIn("DENY_TICKET_SCOPE", self.reason(hit), name)
        outside = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "NOTES.md"),
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    def test_scope_ignores_case_on_every_machine(self):
        """綴りの大文字小文字は、どの機械でも区別しない。

        `docs/Design/*` と書いた範囲に `docs/design/plan.md` が当たる。機械に
        任せると、同じチケットと同じ綴りで、止まる場所が Windows と Linux で
        食い違う。範囲は人が宣言する意図なので、機械の都合ではなく綴りの意味で
        読む（`_fold` と `_entries` の re.IGNORECASE）。
        """
        self.propose("i0001", allow=("src/*", "README.md", "docs/Design/*"))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "ticket")
        self.assertEqual(self.approve().returncode, 0)
        hit = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "docs", "design", "plan.md"),
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(hit))
        # 揃えるのは綴りの大小だけ。別の場所は別の場所のまま止める。
        outside = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "docs", "designs", "plan.md"),
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    def test_no_worktree_means_no_ticket(self):
        """作業ツリーが無い子は効かない。"""
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        self.assertEqual(self.approve().returncode, 0)
        result = self.ccnavi("ticket", "start", "i0001-01")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("作業ツリー", result.stderr)

    @unittest.skipUnless(os.path.normcase("A") == "a", "大文字小文字を区別する機械")
    def test_worktree_name_case_does_not_drop_the_ticket(self):
        """区別しない機械で綴り違いに切った作業ツリーでも、判定は写しで行う。

        案内（SubagentStart）は綴りの違いを吸収するのに判定だけ厳密だと、
        「効いている」と言われながら権限モード任せに落ちる（敵対的レビューで実測）。
        """
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        self.assertEqual(self.approve().returncode, 0)
        child = self.worktree("I0001-01", "i0001")
        outside = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(child, "src", "b", "x.py")
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))
        inside = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(child, "src", "a", "x.py")
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

    # ---- 2. 子は親の部分集合

    def test_child_beyond_parent_is_not_approved(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("docs/*",))
        self.propose("i0001-02", parent="i0001", phase=1, allow=("src/b/*",))
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("超えている", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-02.md")))

    # ---- 2b. 束を識別子で絞る（VS Code 拡張が絞り込みで見えている分だけを渡す）

    def test_approve_only_the_listed_tickets(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        self.propose("i0002", allow=("docs/*",))
        result = self.ccnavi("--approve", "i0001", "i0001-01", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認待ち 3 件のうち、指定の 2 件", result.stdout)
        self.assertNotIn("i0002", result.stdout.split("Ticket 承認リクエスト")[1])
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001.md")))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0002.md")))
        # 残した分は次の --approve の束に載る
        self.assertEqual(self.approve().returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0002.md")))

    def test_listed_child_without_its_pending_parent_is_refused(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        result = self.ccnavi("--approve", "i0001-01", stdin="y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("親 i0001 が承認されていない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001.md")))

    def test_listed_id_with_nothing_pending_is_refused_too(self):
        # 承認待ちが空でも、識別子を並べたなら「無い」は失敗。
        # 終了コードが他の承認待ちの有無で変わらない
        result = self.ccnavi("--approve", "i0001", stdin="y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("承認待ちに無い: i0001", result.stderr)

    def test_listed_id_that_is_not_pending_approves_nothing(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        result = self.ccnavi("--approve", "i0001", "i0009", stdin="y\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("承認待ちに無い: i0009", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001.md")))

    def test_grandchild_is_refused(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        self.propose("i0001-01-01", parent="i0001-01", phase=1, allow=("src/a/*",))
        result = self.approve()
        self.assertIn("2 段", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01-01.md")))

    def test_editing_the_proposal_does_not_widen_the_scope(self):
        self.family()
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        # 承認後に提案を書き足しても、効いているのは写し。
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001-01.md"),
            ticket_text("i0001-01", parent="i0001", phase=1, allow=("src/a/*", "src/b/*")),
        )
        result = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(child, "src", "b", "x.py")
        )
        self.assertIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_parent_and_child_strictest_wins(self):
        self.propose("i0001", allow=("src/a/*",), ask=("src/b/*",))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*", "src/b/*"))
        self.assertEqual(self.approve().returncode, 0)
        child = self.worktree("i0001-01", "i0001")
        result = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(child, "src", "b", "x.py")
        )
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("TICKET_ASK", out["permissionDecisionReason"])

    # ---- 3. 状態の置き場

    def test_state_directories_cannot_be_written_directly(self):
        self.family()
        target = os.path.join(self.parent_tree, "wip", "tickets", "done", "i0001-01.md")
        result = self.hook("PreToolUse", "Write", self.parent_tree, file_path=target)
        self.assertIn("builtin-ticket-state", self.reason(result))
        moved = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            command="mv wip/tickets/doing/i0001-01.md wip/tickets/done/",
        )
        self.assertIn("builtin-ticket-state", self.reason(moved))
        # todo/ への作成は自由。
        todo = os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001-03.md")
        free = self.hook("PreToolUse", "Write", self.parent_tree, file_path=todo)
        self.assertNotIn("builtin-ticket-state", self.reason(free))

    def test_subagent_cannot_move_state(self):
        self.family()
        result = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            agent_id="sub-1",
            command="sh .claude/scripts/ccnavi-ticket.sh done i0001-01",
        )
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(result))
        parent = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            command="sh .claude/scripts/ccnavi-ticket.sh done i0001-01",
        )
        self.assertNotIn("DENY_SUBAGENT_TICKET_OP", self.reason(parent))

    def test_subagent_cannot_push(self):
        """リモートに置く枝は親ブランチ 1 本で、送るのは親の仕事。

        ラッパは cwd のツリーで子を見分けるが、サブエージェントが親のツリーへ
        cd して打てばラッパは通す。素性で止める層を hook に持つ。
        """
        self.family()
        for command in (
            "sh .claude/scripts/ccnavi-git.sh push -u origin i0001-01",
            "cd ../i0001 && sh .claude/scripts/ccnavi-git.sh push origin i0001",
        ):
            with self.subTest(command=command):
                result = self.hook(
                    "PreToolUse",
                    "Bash",
                    self.parent_tree,
                    agent_id="sub-1",
                    command=command,
                )
                self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(result))
                self.assertIn("push", self.reason(result))
        parent = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            command="sh .claude/scripts/ccnavi-git.sh push -u origin i0001",
        )
        self.assertNotIn("DENY_SUBAGENT_TICKET_OP", self.reason(parent))
        # 読むだけの形は、サブエージェントでも通る。
        reading = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            agent_id="sub-1",
            command="sh .claude/scripts/ccnavi-git.sh status",
        )
        self.assertNotIn("DENY_SUBAGENT_TICKET_OP", self.reason(reading))

    def test_done_closes_the_copy_without_approval(self):
        self.family()
        result = self.ccnavi("ticket", "done", "i0001-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(self.parent_tree, "wip", "tickets", "done", "i0001-01.md"))
        )
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "closed", "i0001-01.md")))
        # 閉じた子のツリーへの書き込みは、チケット無しの扱いになる。
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        after = self.hook(
            "PreToolUse", "Write", child, file_path=os.path.join(child, "src", "b", "x.py")
        )
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(after))

    def test_start_records_the_base_point(self):
        self.family()
        with open(os.path.join(self.approved, "i0001-01.md"), encoding="utf-8") as f:
            copy = f.read()
        head = git(os.path.join(self.root, ".claude", "worktrees", "i0001-01"), "rev-parse", "HEAD")
        self.assertIn(head.strip(), copy)
        self.assertIn("started_at:", copy)

    # ---- 4. フェーズとゲート

    def close_phase(self):
        for child in ("i0001-01", "i0001-02"):
            done = self.ccnavi("ticket", "done", child)
            self.assertEqual(done.returncode, 0, done.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "done")

    def test_phase_end_announces_and_gate_closes(self):
        self.family()
        self.close_phase()
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("フェーズ 1 が終わりました", self.reason(said))
        self.assertIn("request", self.reason(said))

        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertIn("DENY_PHASE_GATE", self.reason(spawn))
        shell = self.hook("PreToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("DENY_PHASE_GATE", self.reason(shell))
        exempt = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            command="sh .claude/scripts/ccnavi-git.sh status",
        )
        self.assertNotIn("DENY_PHASE_GATE", self.reason(exempt))
        # 次のフェーズの計画は通る。
        plan = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001-03.md"),
        )
        self.assertNotIn("DENY_PHASE_GATE", self.reason(plan))
        # main からの起動にはゲートが無い。
        elsewhere = self.hook("PreToolUse", "Agent", self.root, description="別の話")
        self.assertNotIn("DENY_PHASE_GATE", self.reason(elsewhere))

    def test_phase_without_review_skips_the_gate(self):
        self.family(review=(False, False))
        self.close_phase()
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("省略", self.reason(said))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.skipped")))
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertNotIn("DENY_PHASE_GATE", self.reason(spawn))

    # ---- レビュー（sh の代わりに、テストがリモートの写しを渡す）

    def remote(self, merge=("i0001-01", "i0001-02")):
        """origin と、合流して push した親ブランチ。リモートの写し（JSON）の置き場を返す。

        sh がリモートから取ってくる形そのもの。テストはネットワークに出ないので、
        sh の代わりにこのファイルを --result で渡す。
        """
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        for child in merge:
            git(self.parent_tree, "merge", "--quiet", "--no-edit", child)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        fixture = os.path.join(self.root, "fixture.json")
        write(
            fixture,
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "https://example/mr/7"}}),
        )
        return fixture

    def request(self, fixture, phase="1"):
        """sh の request と同じ 3 段。prepare → 投稿（写しに書く）→ requested。"""
        body = write(os.path.join(self.root, "body.md"), "見てほしい点\n")
        prepared = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "--body-file", body, "review", "prepare"
        )
        if prepared.returncode != 0:
            return prepared
        # 1 行目が依頼の本文、2 行目がマージリクエストの下書き。
        body_path, draft_path = prepared.stdout.splitlines()[:2]
        self.assertTrue(os.path.exists(draft_path), draft_path)
        with open(body_path, encoding="utf-8") as f:
            text = f.read()
        data = read_json(fixture)
        posted = data.setdefault("comments", [])
        url = f"{data['mr']['url']}#note-{len(posted) + 1}"
        posted.append({"body": text, "url": url, "created_at": "2026-01-01T00:00:00Z"})
        write(fixture, json.dumps(data))
        result = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": data["mr"],
                    "url": url,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        )
        return self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "review", "requested", "--result", result
        )

    def test_request_needs_merged_children_and_check_opens_the_gate(self):
        self.family()
        # 子のツリーに成果を積んでから閉じる。合流していない子を見分けるため。
        for child in ("i0001-01", "i0001-02"):
            tree = os.path.join(self.root, ".claude", "worktrees", child)
            write(os.path.join(tree, "src", child[-1], "work.py"), "x\n")
            git(tree, "add", "-A")
            git(tree, "commit", "--quiet", "-m", "work")
        self.close_phase()
        # 子を 1 本だけ合流した形で、前提の未充足を見る。
        fixture = self.remote(merge=("i0001-01",))
        refused = self.request(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("i0001-02", refused.stderr)
        self.assertIn("取り込まれていない", refused.stderr)
        git(self.parent_tree, "merge", "--quiet", "--no-edit", "i0001-02")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")

        ok = self.request(fixture)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.requested"))
        )
        with open(fixture, encoding="utf-8") as f:
            posted = json.load(f)["comments"]
        self.assertIn("見てほしい点", posted[0]["body"])

        again = self.request(fixture)
        self.assertIn("依頼済み", again.stderr)

        # 未解決のスレッドがある間は開かない。
        data = read_json(fixture)
        data["threads"] = [
            {
                "id": "t1",
                "resolved": False,
                "url": "u1",
                "path": "src/a/x.py",
                "line": 3,
                "body": "ここ",
            }
        ]
        write(fixture, json.dumps(data))
        check = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "check",
            "--result",
            fixture,
        )
        self.assertNotEqual(check.returncode, 0)
        self.assertIn("未解決", check.stderr)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertIn("DENY_PHASE_GATE", self.reason(spawn))

        data["threads"][0]["resolved"] = True
        write(fixture, json.dumps(data))
        check = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "check",
            "--result",
            fixture,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertNotIn("DENY_PHASE_GATE", self.reason(spawn))

    def test_human_accepts_unresolved_but_not_changes_requested(self):
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        data["threads"] = [{"id": "t1", "resolved": False, "url": "u1", "body": "ここ"}]
        data["reviews"] = [{"state": "CHANGES_REQUESTED", "url": "r1"}]
        write(fixture, json.dumps(data))
        refused = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("変更要求", refused.stderr)

        data["reviews"] = []
        write(fixture, json.dumps(data))
        accepted = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("u1", accepted.stdout)
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )

    def test_new_child_in_ended_phase_clears_the_marks(self):
        self.family()
        self.close_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertTrue(os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.pending")))
        self.propose("i0001-03", parent="i0001", phase=1, allow=("src/a/*",))
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("印", result.stdout)
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.pending"))
        )

    # ---- 6. 基準点より後の範囲外

    def test_subagent_stop_bounces_out_of_scope_once(self):
        self.family()
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        write(os.path.join(child, "src", "b", "stray.py"), "x\n")
        git(child, "add", "-A")
        git(child, "commit", "--quiet", "-m", "stray")
        first = self.hook("SubagentStop", "", child, agent_id="sub-1")
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        self.assertIn("POST_TICKET_SCOPE", first.stderr)
        self.assertIn("src/b/stray.py", first.stderr)
        second = self.hook("SubagentStop", "", child, agent_id="sub-1")
        self.assertEqual(second.returncode, 0)
        self.assertIn("POST_TICKET_SCOPE", self.reason(second))
        # 親の cwd からは、開いている子の全部を見る。
        from_parent = self.hook("SubagentStop", "", self.parent_tree, agent_id="sub-2")
        self.assertEqual(from_parent.returncode, 2)

    def test_subagent_start_lists_open_children(self):
        self.family()
        result = self.hook("SubagentStart", "", self.parent_tree, agent_id="sub-1")
        text = self.reason(result)
        self.assertIn("i0001-01", text)
        self.assertIn("i0001-02", text)
        self.assertIn("src/a/*", text)

    def test_subagent_start_says_nothing_outside_the_family(self):
        """渡すのは cwd の作業ツリーに関わる子だけ。

        別のセッションが main や無関係な作業ツリーで調査を委譲したとき、無関係な子の
        範囲を案内すると、調査役が自分の居場所を迷う。
        """
        self.family()
        for cwd in (self.root, self.worktree("research-abc", "main")):
            result = self.hook("SubagentStart", "", cwd, agent_id="sub-r")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.reason(result), "", cwd)
        # 子の作業ツリーからは、その子だけ。
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        text = self.reason(self.hook("SubagentStart", "", child, agent_id="sub-1"))
        self.assertIn("i0001-01", text)
        self.assertNotIn("i0001-02", text)

    # ---- 7. 人の判断の経路

    def test_cli_paths_are_denied_from_the_shell_unless_disabled(self):
        self.family()
        for command in (
            "ccnavi --approve",
            "dist/ccnavi/ccnavi.exe --reviewed 1 --accept-unresolved",
            "uv run python -m ccnavi ticket start i0001-01",
            "ls && ./ccnavi review check --phase 1",
            # 拡張が打つ形（--yes）は、エージェントが打てば止まる（設計 approve-popup §2.3）。
            "uv run python -m ccnavi --approve --yes i0001,i0001-01 --json",
            "ccnavi --approve --preview --json; ccnavi --approve --yes i0001",
            # 同じコマンドに --preview を書き足しても、承認そのものは免除しない。
            "ccnavi --approve --preview --yes i0001 --json",
            "ccnavi --approve --yes i0001 --preview",
        ):
            result = self.hook(
                "PreToolUse",
                "Bash",
                self.parent_tree,
                command=command,
                guard_ticket_approval="enable",
            )
            self.assertIn("DENY_TICKET_APPROVAL_CLI", self.reason(result), command)
        # 読むだけの形と、スクリプト経由は通る。
        for command in (
            "ccnavi --explain",
            "ccnavi --lint",
            "sh .claude/scripts/ccnavi-ticket.sh done i0001-01",
            # 束を見るだけの形は通る。承認は --yes だけで、それは上で止まる。
            "uv run python -m ccnavi --approve --preview --json",
            "echo --approve --preview",
        ):
            result = self.hook(
                "PreToolUse",
                "Bash",
                self.parent_tree,
                command=command,
                guard_ticket_approval="enable",
            )
            self.assertNotIn("DENY_TICKET_APPROVAL_CLI", self.reason(result), command)
        # PowerShell も同じ。PowerShell は shellread で読めないので生の文字列に当たる。
        # 免除の範囲がコマンドをまたぐと、後ろに --preview を書くだけで前の承認が通る。
        for command in (
            "& ccnavi.exe --approve",
            "ccnavi --approve --yes i0001,i0001-01 --json; ccnavi --approve --preview",
            "ccnavi --approve; echo --preview",
            "ccnavi --approve --yes i0001 | findstr --preview",
        ):
            result = self.hook(
                "PreToolUse",
                "PowerShell",
                self.parent_tree,
                command=command,
                guard_ticket_approval="enable",
            )
            self.assertIn("DENY_TICKET_APPROVAL_CLI", self.reason(result), command)
        result = self.hook(
            "PreToolUse",
            "PowerShell",
            self.parent_tree,
            command="ccnavi --approve --preview --json",
            guard_ticket_approval="enable",
        )
        self.assertNotIn("DENY_TICKET_APPROVAL_CLI", self.reason(result))
        # ccnavi の起動でない --yes は当てない。
        result = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            command="apt-get install --yes git",
            guard_ticket_approval="enable",
        )
        self.assertNotIn("DENY_TICKET_APPROVAL_CLI", self.reason(result))
        # 切ると通る。
        result = self.hook("PreToolUse", "Bash", self.parent_tree, command="ccnavi --approve")
        self.assertNotIn("DENY_TICKET_APPROVAL_CLI", self.reason(result))

    def test_approve_and_reviewed_need_a_terminal_unless_disabled(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        refused = self.ccnavi("--approve", "--guard-ticket-approval", "enable", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("端末", refused.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001.md")))
        refused = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--guard-ticket-approval",
            "enable",
            stdin="y\n",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("端末", refused.stderr)
        self.assertEqual(self.approve().returncode, 0)

    def test_subagent_cannot_move_state_from_powershell_either(self):
        self.family()
        result = self.hook(
            "PreToolUse",
            "PowerShell",
            self.parent_tree,
            agent_id="sub-1",
            command="sh .claude/scripts/ccnavi-ticket.sh done i0001-01",
        )
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(result))

    # ---- 8. 親を閉じる順

    def test_parent_cannot_close_while_children_are_open(self):
        self.family()
        refused = self.ccnavi("ticket", "start", "i0001")
        # 親のツリーは作ってあるので start は通る。
        self.assertEqual(refused.returncode, 0, refused.stderr)
        refused = self.ccnavi("ticket", "done", "i0001")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("開いている子", refused.stderr)
        refused = self.ccnavi("ticket", "cancel", "i0001", "--reason", "やめる")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("開いている子", refused.stderr)
        # 子を閉じてもゲートが閉じている間は閉じない。
        self.close_phase()
        refused = self.ccnavi("ticket", "done", "i0001")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ゲート", refused.stderr)

    def test_moving_state_keeps_the_proposal_text(self):
        self.propose("i0001", allow=("src/*", "wip/*"))
        path = os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.replace("---\n", "---\n# 人の覚え書き\n", 1))
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        moved = os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md")
        with open(moved, encoding="utf-8") as f:
            after = f.read()
        self.assertIn("# 人の覚え書き", after)
        self.assertIn("started_at:", after)
        self.assertIn("base_sha:", after)

    # ---- 9. レビューの前提

    def test_check_refuses_when_head_moved_after_request(self):
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        write(os.path.join(self.parent_tree, "src", "later.py"), "x\n")
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "later")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        check = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "check",
            "--result",
            fixture,
        )
        self.assertNotEqual(check.returncode, 0)
        self.assertIn("HEAD が動いている", check.stderr)

    def test_request_refuses_when_a_child_branch_is_gone(self):
        self.family()
        self.close_phase()
        fixture = self.remote()
        git(self.root, "worktree", "remove", "--force", ".claude/worktrees/i0001-01")
        git(self.root, "branch", "-D", "i0001-01")
        refused = self.request(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("i0001-01", refused.stderr)
        self.assertIn("消えている", refused.stderr)

    def test_unresolved_threads_survive_a_new_request(self):
        """指摘が残ったまま依頼をやり直しても、数から消えないこと。

        依頼より後のスレッドだけを数えていた版では、子をもう 1 本足して承認してもらい、
        依頼をやり直すだけで前回の指摘が数から消え、check が通った。人が解決も
        受け入れもしていないのに通る形で、実物の GitLab で流れを通したときに出た。
        """
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        # 依頼より前に付いた指摘。時刻で絞る版では数から落ちていた。
        data["threads"] = [
            {
                "id": "t1",
                "resolved": False,
                "url": "u1",
                "body": "直して",
                "created_at": "2020-01-01T00:00:00Z",
            }
        ]
        write(fixture, json.dumps(data))
        mark = os.path.join(self.approved, "phases", "i0001", "1.requested")

        first = self.check(fixture)
        self.assertNotEqual(first.returncode, 0)
        self.assertIn("未解決", first.stderr)

        # 印を消して依頼をやり直す（新しい子が承認された形）。指摘は残ったまま。
        os.remove(mark)
        self.assertEqual(self.request(fixture).returncode, 0)
        again = self.check(fixture)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("未解決", again.stderr)

        # 人が受け入れれば通り、受け入れた分は次から数えない。
        accepted = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.check(fixture).returncode, 0)

    def test_the_acceptance_survives_a_later_check(self):
        """人が受け入れたスレッドは、あとから走った check で消えないこと。

        受け入れをフェーズの印に書いていた版では、次に通った check が同じ印を
        `accepted: []` で上書きし、記録が飛んだ。人がもう一度同じスレッドを
        受け入れることになる。控えは印と別の場所に置く。
        """
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        data["threads"] = [{"id": "t1", "resolved": False, "url": "u1", "body": "承知で進める"}]
        write(fixture, json.dumps(data))
        accepted = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        kept = os.path.join(self.approved, "phases", "i0001", "accepted.json")
        self.assertIn("u1", read_json(kept)["threads"])

        # check が通ると印は書き換わるが、控えは残る。
        self.assertEqual(self.check(fixture).returncode, 0)
        self.assertIn("u1", read_json(kept)["threads"])

        # 依頼をやり直しても、受け入れた分は数えない。
        os.remove(os.path.join(self.approved, "phases", "i0001", "1.requested"))
        self.assertEqual(self.request(fixture).returncode, 0)
        self.assertEqual(self.check(fixture).returncode, 0)

    def test_prepare_writes_a_merge_request_draft(self):
        """マージリクエストが無ければ sh が作れるように、下書きを書き出すこと。"""
        self.propose("i0001", allow=("src/*", "wip/*"), title="挨拶の言語を切り替える", issue=12)
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")
        self.assertEqual(self.approve().returncode, 0)
        self.worktree("i0001-01", "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001-01").returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "done", "i0001-01").returncode, 0)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "done")
        self.remote(merge=("i0001-01",))

        prepared = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "--body-file",
            write(os.path.join(self.root, "body.md"), "見てほしい点\n"),
            "review",
            "prepare",
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        draft = prepared.stdout.splitlines()[1]
        with open(draft, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "Draft: 挨拶の言語を切り替える")
        self.assertEqual(lines[1], "")
        self.assertIn("Closes #12", lines)
        self.assertIn("i0001", "\n".join(lines))

    def check(self, fixture, phase="1"):
        return self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "review", "check", "--result", fixture
        )

    def test_only_the_latest_review_per_author_counts(self):
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        data["reviews"] = [
            {"state": "CHANGES_REQUESTED", "author": "a", "submitted_at": "2026-01-01T00:00:00Z"},
            {"state": "APPROVED", "author": "a", "submitted_at": "2026-01-02T00:00:00Z"},
            {"state": "CHANGES_REQUESTED", "author": "b", "submitted_at": "2026-01-01T00:00:00Z"},
            {"state": "DISMISSED", "author": "b", "submitted_at": "2026-01-03T00:00:00Z"},
        ]
        write(fixture, json.dumps(data))
        check = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "review",
            "check",
            "--result",
            fixture,
        )
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_exe_never_reaches_the_remote_and_needs_a_matching_result(self):
        """写しが無ければ動かず、依頼したのと違うマージリクエストの写しでは開かない。"""
        self.family()
        self.close_phase()
        fixture = self.remote()
        bare = self.ccnavi("--cwd", self.parent_tree, "--phase", "1", "review", "check")
        self.assertNotEqual(bare.returncode, 0)
        self.assertIn("--result", bare.stderr)
        # 投稿の url が無い結果では印を置かない。
        prepared = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            "1",
            "--body-file",
            write(os.path.join(self.root, "body.md"), "x\n"),
            "review",
            "prepare",
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        for line in prepared.stdout.splitlines()[:2]:
            self.assertTrue(os.path.exists(line), line)
        unposted = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", "1", "review", "requested", "--result", fixture
        )
        self.assertNotEqual(unposted.returncode, 0)
        self.assertIn("url", unposted.stderr)
        self.assertEqual(self.request(fixture).returncode, 0)
        other = write(
            os.path.join(self.root, "other.json"),
            json.dumps({"host": "fixture", "mr": {"number": 8, "url": "https://example/mr/8"}}),
        )
        check = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", "1", "review", "check", "--result", other
        )
        self.assertNotEqual(check.returncode, 0)
        self.assertIn("違う", check.stderr)

    def test_review_script_keeps_the_port_and_scheme_of_origin(self):
        """origin の綴りから、ホスト・ポート・scheme を落とさずに API の綴りを組むこと。

        以前は host を `[^/:]+` で切っていたのでポートが落ち、落ちたポートが
        プロジェクトのパスの先頭に混ざり（`8929/demo/greeter`）、しかも scheme が
        https に決め打ちだった。手元や社内に平文で立てた GitLab
        （`http://localhost:8929`）はこれで全滅する。
        """
        script = self.script()
        cases = [
            (
                "http://localhost:8929/demo/greeter.git",
                "gitlab",
                "http",
                "localhost:8929",
                "demo/greeter",
            ),
            (
                "https://gitlab.example.com:8443/g/p.git",
                "gitlab",
                "https",
                "gitlab.example.com:8443",
                "g/p",
            ),
            ("git@gitlab.example.com:g/p.git", "gitlab", "https", "gitlab.example.com", "g/p"),
            ("https://github.com/o/r.git", "github", "https", "github.com", "o/r"),
        ]
        git(self.parent_tree, "remote", "add", "origin", "https://example.invalid/x/y.git")
        for url, kind, scheme, host, path in cases:
            git(self.parent_tree, "remote", "set-url", "origin", url)
            read = self.run_script(script, "origin")
            self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
            got = dict(line.split("=", 1) for line in read.stdout.splitlines() if "=" in line)
            self.assertEqual(got.get("kind"), kind, url)
            self.assertEqual(got.get("scheme"), scheme, url)
            self.assertEqual(got.get("host"), host, url)
            self.assertEqual(got.get("path"), path, url)
            expected = "https://api.github.com" if kind == "github" else f"{scheme}://{host}/api/v4"
            self.assertEqual(got.get("api_base"), expected, url)

    def script(self):
        """このリポジトリの ccnavi-review.sh を、テスト用の木へ置く。"""
        where = os.path.join(self.root, ".claude", "scripts", "ccnavi-review.sh")
        os.makedirs(os.path.dirname(where), exist_ok=True)
        shutil.copy(os.path.join(ROOT, ".claude", "scripts", "ccnavi-review.sh"), where)
        return where

    def run_script(self, script, *args, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # curl 経路を選ばせる。gh / glab が入っていても、このホストには認証が無い。
        environment["GITLAB_TOKEN"] = "t"
        environment["GITHUB_TOKEN"] = "t"
        environment["CCNAVI_BIN_PATH"] = sys.executable
        environment.update(env or {})
        return subprocess.run(
            ["sh", script, *args],
            cwd=self.parent_tree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def test_review_script_refuses_an_unreadable_origin(self):
        """sh の前半（場所と道具の解決）が Windows でも通ること。origin が読めなければ止まる。"""
        self.family()
        self.remote()
        script = os.path.join(self.root, ".claude", "scripts", "ccnavi-review.sh")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        shutil.copy(os.path.join(ROOT, ".claude", "scripts", "ccnavi-review.sh"), script)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_BIN_PATH"] = sys.executable
        done = subprocess.run(
            ["sh", script, "fetch"],
            cwd=self.parent_tree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("origin の綴りを読めない", done.stderr)

    # ---- 10. 差し戻しを無視した終了

    def test_parent_hears_about_an_ignored_bounce(self):
        self.family()
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        write(os.path.join(child, "src", "b", "stray.py"), "x\n")
        git(child, "add", "-A")
        git(child, "commit", "--quiet", "-m", "stray")
        self.assertEqual(self.hook("SubagentStop", "", child, agent_id="sub-9").returncode, 2)
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"description": "子"},
            "tool_response": {"agentId": "sub-9"},
        }
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertIn("差し戻されたまま", self.reason(result))

    # ---- 診断

    def test_lint_names_the_gaps(self):
        self.family()
        self.worktree("stray", "main")
        # PostToolUse だけ登録した設定。SubagentStop が無いことを名指しさせる。
        write(
            os.path.join(self.root, ".claude", "settings.json"),
            json.dumps({"hooks": {"PostToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}),
        )
        result = self.ccnavi("--lint", "--mode", "enable", env={"CCNAVI_LEDGER": "x"})
        self.assertIn("stray にチケットが無い", result.stdout)
        self.assertIn("CCNAVI_LEDGER はもう効かない", result.stdout)
        self.assertIn("CCNAVI_GUARD_TICKET_APPROVAL=disable", result.stdout)
        self.assertIn("SubagentStop", result.stdout)
        self.assertIn("origin が無い", result.stdout)
        # 超えている子を、承認の前に名指しする。
        self.propose("i0001-03", parent="i0001", phase=2, allow=("docs/*",))
        result = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("超えている", result.stdout)

    def test_explain_lists_copies_and_phases(self):
        self.family()
        result = self.ccnavi("--explain")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("i0001-01", result.stdout)
        self.assertIn("フェーズ 1", result.stdout)


if __name__ == "__main__":
    unittest.main()
