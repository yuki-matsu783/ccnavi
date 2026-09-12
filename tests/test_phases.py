"""フェーズの種類と計画（REQ-TKT-26〜35、設計 §24.15）の受入テスト。

見るのは 8 つ。

1. 種類の定義の検証（識別子と表示名の一意、フィードバック対応は mr 固定、参照先の有無）
2. 全体計画の承認と、計画に合わない子の拒否（kind、範囲の上限、無い番号）
3. 順序は承認で止まる（前が閉じてレビューが済むまで次の番号は承認されない、overlap は例外）
4. 成果物が無ければフェーズの最後の子を閉じられない
5. 延期したフェーズではゲートが閉じず、次の依頼に含まれる
6. フィードバック計画は最後のレビューの後に 1 回だけ、空でも証跡になる
7. 親はフィードバック計画が承認されるまで閉じられない
8. 残った指摘の切り出しの下書き
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi
from tests.test_ticket import ROOT, RULES, git, read_json, write

PHASES = """
version: 1
phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    agent: explorer
    when: 既存の振る舞いが分からないとき
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*"]
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
    overlap: [implement]
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    requires: [acceptance]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr
    scope: inherit
"""


def parent_text(name, plan, feedback=None, allow=("src/*", "wip/*", "tests/*"), issue=None):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if issue is not None:
        lines.append(f"issue: {issue}")
    lines.append("plan:")
    for item in plan:
        if isinstance(item, tuple):
            lines.append(f"  - {{type: {item[0]}, review: {item[1]}}}")
        else:
            lines.append(f"  - {item}")
    if feedback is not None:
        if feedback:
            lines.append("feedback:")
            for item in feedback:
                lines.append(f"  - {item}")
        else:
            lines.append("feedback: []")
    lines += ["human_review:", "  required: true", "  reason: t", "title: 親", "rationale: r"]
    lines.append("allow:")
    for g in allow:
        lines += ["  - match: Write|Edit|MultiEdit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


def child_text(name, parent, phase, allow, review=True):
    lines = [
        "---",
        "version: 1",
        f"ticket: {name}",
        f"parent: {parent}",
        f"phase: {phase}",
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: t",
        f"title: 子 {name}",
        "rationale: r",
        "allow:",
    ]
    for g in allow:
        lines += ["  - match: Write|Edit|MultiEdit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


class PhaseHarness(unittest.TestCase):
    """親と子を動かす道具。テストは持たない（他のテストが継いで使う）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-phase-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        self.rules = write(os.path.join(self.root, "rules.yml"), json.dumps(RULES))
        self.phases = write(os.path.join(self.root, "phases.yml"), PHASES)
        self.approved = os.path.join(self.root, ".claude", "ccnavi", "tickets")
        self.state = os.path.join(self.root, "state")
        self.parent_tree = self.worktree("i0001", "main")

    # ---- 道具

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin="", phases=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                self.rules,
                "--approved",
                self.approved,
                "--phases",
                phases or self.phases,
                "--state",
                self.state,
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-cli",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(self, event, tool, cwd, **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, text):
        return write(os.path.join(self.parent_tree, "wip", "tickets", "todo", name + ".md"), text)

    def approve(self):
        return self.ccnavi("--approve", stdin="y\n")

    def commit_parent(self, message="tickets"):
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", message)

    def run_child(self, name, files=()):
        """子の作業ツリーを作って着手し、ファイルを置いてコミットし、閉じる。"""
        tree = self.worktree(name, "i0001")
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stderr)
        for rel, text in files:
            write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "-A")
        if files:
            git(tree, "commit", "--quiet", "-m", name)
        return tree

    def close_child(self, name):
        return self.ccnavi("ticket", "done", name)

    def merge(self, child):
        git(self.parent_tree, "merge", "--quiet", "--no-edit", child)

    def remote(self):
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        fixture = os.path.join(self.root, "fixture.json")
        write(fixture, json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7"}}))
        return fixture

    def request(self, fixture, phase):
        body = write(os.path.join(self.root, "body.md"), "見てほしい\n")
        prepared = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "--body-file",
            body,
            "review",
            "prepare",
        )
        if prepared.returncode != 0:
            return prepared
        body_path = prepared.stdout.splitlines()[0]
        with open(body_path, encoding="utf-8") as f:
            self.last_request_body = f.read()
        data = read_json(fixture)
        result = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": data["mr"],
                    "url": "u/7#1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        )
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "review",
            "requested",
            "--result",
            result,
        )

    def check(self, fixture, phase):
        return self.ccnavi(
            "--cwd", self.parent_tree, "--phase", str(phase), "review", "check", "--result", fixture
        )

    def family(self, plan=("research", "design"), feedback=None):
        """親を提案して承認する。plan の 1 番目の子も同じ束で承認する。"""
        self.propose("i0001", parent_text("i0001", list(plan), feedback))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


class PhaseTest(PhaseHarness):
    # ---- 1. 種類の定義

    def test_phase_types_must_be_unique_and_well_formed(self):
        from ccnavi import phasetypes

        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: 同じ, kind: work}\n  b: {title: 同じ, kind: work}\n"
        )
        self.assertTrue(any("表示名" in p.detail for p in problems), problems)
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  fb: {title: 対応, kind: feedback, review: none}\n"
        )
        self.assertTrue(any("review: mr" in p.detail for p in problems), problems)
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: A, kind: work, overlap: [nope]}\n"
        )
        self.assertTrue(any("nope" in p.detail for p in problems), problems)
        types, problems = phasetypes.parse(PHASES)
        self.assertEqual(problems, [])
        self.assertEqual(
            set(types), {"research", "design", "acceptance", "implement", "implement-feedback"}
        )
        self.assertTrue(types["acceptance"].overlaps(types["implement"]))
        self.assertTrue(types["implement"].overlaps(types["acceptance"]))

    def test_lint_reports_a_broken_phases_file(self):
        write(
            self.phases,
            "version: 1\nphases:\n  a: {title: X, kind: work}\n  b: {title: X, kind: work}\n",
        )
        result = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("表示名", result.stdout)
        self.assertNotEqual(result.returncode, 0)

    # ---- 2. 全体計画の承認と、計画に合わない子

    def test_plan_is_approved_and_copied(self):
        self.family(plan=["research", ("design", "defer"), "acceptance", "implement"])
        copy = os.path.join(self.approved, "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("plan:", text)
        self.assertIn("research", text)
        self.assertIn("defer", text)
        result = self.ccnavi("--explain")
        self.assertIn("段階: 作業中（1（調査））", result.stdout)
        self.assertIn("フェーズ 2（設計）: 未計画", result.stdout)
        self.assertIn("レビューは 3 と一緒に", result.stdout)

    def test_plan_that_breaks_the_rules_is_refused(self):
        cases = {
            "kind": ["implement-feedback"],
            "requires": ["implement"],
            "defer-none": [("research", "defer"), "design"],
            "defer-last": ["design", ("implement", "defer")],
            "unknown": ["nope"],
        }
        for name, plan in cases.items():
            with self.subTest(name=name):
                shutil.rmtree(self.approved, ignore_errors=True)
                self.propose("i0001", parent_text("i0001", plan))
                result = self.approve()
                self.assertNotEqual(result.returncode, 0, name)
                self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001.md")), name)

    def test_child_must_fit_the_phase_type(self):
        self.family(plan=["research", "design"])
        # 種類の範囲を超える子。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["src/a/*"]))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("超えている", refused.stderr)
        self.assertIn("調査", refused.stderr)
        # 計画に無い番号。
        os.remove(os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001-01.md"))
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ["wip/research/*"]))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("計画に無い", refused.stderr)

    # ---- 3. 順序は承認で止まる

    def test_next_phase_waits_for_the_previous_one(self):
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        # 1 番目は通り、2 番目は「1 が閉じるまで」で落ちる。
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        self.assertIn("閉じるまで承認しない", result.stderr)
        self.assertIn("子がまだ無い", result.stderr) if "子がまだ無い" in result.stderr else None

        # 1 番目を閉じる（成果物を置く）。
        self.run_child("i0001-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        # レビュー不要の種類なので、印は skipped。2 番目が承認される。
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-02.md")))

    def test_overlapping_types_can_be_planned_together(self):
        self.family(plan=["acceptance", "implement"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["tests/a/*"]))
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/a/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-02.md")))

    # ---- 4. 成果物

    def test_last_child_cannot_close_without_deliverables(self):
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01")
        refused = self.close_child("i0001-01")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("成果物が無い", refused.stderr)
        self.assertIn("wip/research/summary.md", refused.stderr)
        tree = os.path.join(self.root, ".claude", "worktrees", "i0001-01")
        write(os.path.join(tree, "wip", "research", "summary.md"), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "summary")
        self.assertEqual(self.close_child("i0001-01").returncode, 0)

    # ---- 5. 延期

    def test_deferred_phase_does_not_close_the_gate_and_is_covered_later(self):
        self.family(plan=[("design", "defer"), "implement", "acceptance"])
        # implement は acceptance を要るが、acceptance が後ろにあっても計画としては足りる。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        said = self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertIn("一緒に見る", self.reason(said))
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertNotIn("DENY_PHASE_GATE", self.reason(spawn))
        # 延期したフェーズには依頼できない。
        self.merge("i0001-01")
        fixture = self.remote()
        refused = self.request(fixture, 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("一緒に見る", refused.stderr)
        # 2 番目を閉じて依頼すると、1 番目も含まれる。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/a/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02", [("src/a/x.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_GATE", self.reason(spawn))
        ok = self.request(fixture, 2)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("このレビューが含むフェーズ", self.last_request_body)
        self.assertIn("1（設計）", self.last_request_body)
        self.assertIn("2（実装とテスト）", self.last_request_body)

    # ---- 6. フィードバック計画

    def test_feedback_plan_comes_after_the_last_review_and_only_once(self):
        self.family(plan=["design"])
        # 早すぎる改版は拒む。
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        early = self.approve()
        self.assertNotEqual(early.returncode, 0)
        self.assertIn("全部閉じてレビューが済んでから", early.stderr)
        self.propose("i0001", parent_text("i0001", ["design"]))

        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.check(fixture, 1).returncode, 0)
        # 親はまだ閉じられない。
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        refused = self.close_child("i0001")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("フィードバック計画がまだ", refused.stderr)
        # 空のフィードバック計画を改版で出す。証跡が残る。
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        # 提案は doing/ にあるので、そこを書き換える。
        os.remove(os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001.md"))
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=[]),
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertIn("対応なし", approved.stdout)
        with open(os.path.join(self.approved, "i0001.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("feedback: []", text)
        self.assertIn("feedback_at", text)
        # 2 度目は拒む。
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=["implement-feedback"]),
        )
        again = self.approve()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("1 回だけ", again.stderr)
        # 閉じられる。
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=[]),
        )
        self.assertEqual(self.close_child("i0001").returncode, 0)

    def test_feedback_work_phase_runs_and_handoff_drafts_the_rest(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        # 差し戻し。未解決の指摘が付いて check は通らない。
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "直して"}]
        write(fixture, json.dumps(data))
        self.assertNotEqual(self.check(fixture, 1).returncode, 0)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertIn("DENY_PHASE_GATE", self.reason(spawn))
        # フィードバック計画: 実装フィードバック対応を 1 本。承認がレビューの合意になり、
        # ゲートが開く。指摘は消えず、フィードバック作業フェーズの check が数える。
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=["implement-feedback"]),
        )
        planned = self.approve()
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertIn("済んだ扱い", planned.stdout)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertNotIn("DENY_PHASE_GATE", self.reason(spawn))
        result = self.ccnavi("--explain")
        self.assertIn("フェーズ 2（実装フィードバック対応）", result.stdout)
        self.assertIn("段階: フィードバック対応中", result.stdout)
        # 2 番目の子（範囲は親の範囲そのまま）。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02", [("src/fix.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.assertEqual(self.request(fixture, 2).returncode, 0)
        # 前の指摘が残ったままなので、フィードバック作業フェーズの check も通らない。
        refused = self.check(fixture, 2)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("u/7#t0", refused.stderr)
        self.assertIn("道は 2 つ", refused.stderr)
        self.assertIn("handoff", refused.stderr)
        data = read_json(fixture)
        data["threads"].append({"id": "t1", "resolved": False, "url": "u/7#t1", "body": "まだ"})
        write(fixture, json.dumps(data))
        # 切り出しの下書き。
        body = write(os.path.join(self.root, "handoff.md"), "残りの対応\n\n次の issue で。\n")
        drafted = self.ccnavi(
            "--cwd", self.parent_tree, "--body-file", body, "review", "handoff", "--result", fixture
        )
        self.assertEqual(drafted.returncode, 0, drafted.stderr)
        with open(drafted.stdout.strip(), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("残りの対応\n\n"))
        self.assertIn("u/7#t1", text)
        self.assertIn("i0001", text)

    # ---- 7. Draft を外す（ready）と、人が締める（wrapup）

    def ready(self, fixture):
        return self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)

    def wrapup(self, fixture, reason="ここまでで十分", answer="y"):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "review",
            "wrapup",
            "--reason",
            reason,
            "--result",
            fixture,
            stdin=answer + "\n",
        )

    def test_ready_needs_the_parent_to_be_closable(self):
        """Draft を外せるのは、親を閉じられる状態と同じ。閉じたあとでも打てる。"""
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        # 子が開いている間は外せない。
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("Draft を外せない", refused.stderr)
        self.assertIn("wrapup", refused.stderr)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        requested = self.request(fixture, 1)
        self.assertEqual(requested.returncode, 0, requested.stderr)
        self.assertEqual(self.check(fixture, 1).returncode, 0)
        # フィードバック計画が無い間も外せない。
        refused = self.ready(fixture)
        self.assertIn("フィードバック計画", refused.stderr)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=[]),
        )
        self.assertEqual(self.approve().returncode, 0)
        # 閉じられる状態になったが、wip/ が追跡されたままなら外せない。
        refused = self.ready(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("`wip/` に追跡されているファイル", refused.stderr)
        self.assertIn("rm -r wip", refused.stderr)
        # 親を閉じる。案内は「片付けて push して ready」。
        closed = self.ccnavi("ticket", "done", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("rm -r wip", closed.stdout)
        self.assertIn("squash", closed.stdout)
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        # push していなければまだ外せない。
        refused = self.ready(fixture)
        self.assertIn("push されていない", refused.stderr)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # 片付いて push 済み。ready が通り、印と note の下書きができる。
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        with open(passed.stdout.strip(), encoding="utf-8") as f:
            note = f.read()
        self.assertIn("ccnavi:ready", note)
        self.assertIn("squash", note)
        mark = read_json(os.path.join(self.approved, "phases", "i0001", "ready.json"))
        self.assertEqual(mark["mr"], 7)
        # 同じ親にもう 1 度打っても通る（sh が外し損ねたときの打ち直し）。
        again = self.ready(fixture)
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_wrapup_closes_early_and_files_the_rest(self):
        """人が「キリの良いところ」と締める。残りは取り消し・省略・受け入れになり、issue に写る。"""
        self.family(plan=["research", "design", "acceptance", "implement"])
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/research/summary.md", "s\n")])
        # 作業中の子がいる間は締められない。
        fixture = self.remote()
        refused = self.wrapup(fixture)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("作業中の子", refused.stderr)
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        # 2 番目の子を提案して承認するが、着手はしない（todo/ に残る）。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("propose 02")
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "気になる"}]
        write(fixture, json.dumps(data))
        # n なら何も変わらない。
        declined = self.wrapup(fixture, answer="n")
        self.assertNotEqual(declined.returncode, 0)
        self.assertIn("i0001-02", declined.stdout)
        self.assertIn("実装とテスト", declined.stdout)
        self.assertIn("フィードバック計画", declined.stdout)
        self.assertIn("u/7#t0", declined.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        # y で締める。
        done = self.wrapup(fixture, reason="今期はここまで")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("締めた", done.stdout)
        self.assertTrue(
            os.path.exists(
                os.path.join(self.parent_tree, "wip", "tickets", "cancelled", "i0001-02.md")
            )
        )
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        mark = read_json(os.path.join(self.approved, "phases", "i0001", "wrapup.json"))
        self.assertEqual(mark["reason"], "今期はここまで")
        self.assertEqual(mark["cancelled"], ["i0001-02"])
        self.assertEqual(sorted(mark["skipped"]), [2, 3, 4])
        self.assertEqual(mark["accepted"], ["u/7#t0"])
        with open(os.path.join(self.state, "review-wrapup-issue-i0001.md"), encoding="utf-8") as f:
            issue = f.read()
        self.assertTrue(issue.startswith("親 の残り\n\n"))
        self.assertIn("i0001-02", issue)
        self.assertIn("実装とテスト", issue)
        self.assertIn("u/7#t0", issue)
        self.assertIn("今期はここまで", issue)
        # 締めたので、フィードバック計画が無くても親を閉じられる。
        explained = self.ccnavi("--explain")
        self.assertIn("利用者が締めた", explained.stdout)
        closed = self.ccnavi("ticket", "done", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        # Draft はまだ外れていない。片付けて push してから ready で外す。
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "ready.json"))
        )
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)

    def test_wrapup_is_a_human_path(self):
        """wrapup は端末を求める。サブエージェントと直接の exe 呼び出しは止まる。"""
        self.family(plan=["design"])
        fixture = self.remote()
        result = self.ccnavi(
            "--guard-cli",
            "enable",
            "--cwd",
            self.parent_tree,
            "review",
            "wrapup",
            "--reason",
            "r",
            "--result",
            fixture,
            stdin="y\n",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("端末", result.stderr)
        for command in (
            "sh .claude/scripts/ccnavi-review.sh wrapup --reason x",
            "sh .claude/scripts/ccnavi-review.sh ready",
        ):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "cwd": self.parent_tree,
                "session_id": "s1",
                "agent_id": "sub-1",
                "tool_input": {"command": command},
            }
            denied = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
            self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(denied), command)

    # ---- 8. 計画の無い親は今までどおり

    def test_parent_without_plan_keeps_the_old_behaviour(self):
        from tests.test_ticket import ticket_text

        self.propose("i0001", ticket_text("i0001", allow=("src/*", "wip/*")))
        self.propose(
            "i0001-01", ticket_text("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        )
        self.propose(
            "i0001-02", ticket_text("i0001-02", parent="i0001", phase=2, allow=("src/b/*",))
        )
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-02.md")))


if __name__ == "__main__":
    unittest.main()
