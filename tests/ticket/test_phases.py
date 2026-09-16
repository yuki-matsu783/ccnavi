"""フェーズの種類と計画（REQ-TKT-26〜35、設計 §9.7）の受入テスト。

見るのは 8 つ。

1. 種類の定義の検証（識別子と表示名の一意、フィードバック対応は mr 固定、参照先の有無）
2. 全体計画の承認と、計画に合わない子の拒否（kind、無い番号）。範囲の上限の超過は拒まず見せる
3. 順序は承認で止まる（前が閉じてレビューが済むまで次の番号は承認されない、overlap は例外）
4. 成果物が無ければフェーズの最後の子を閉じられない
5. 延期したフェーズではレビューで止まらず、次の依頼に含まれる
6. フィードバック計画は最後のレビューの後に 1 回だけ、空でも証跡になる
7. 親はフィードバック計画が承認されるまで閉じられない
8. 残った指摘の切り出しの下書き
9. このセッションで見るフェーズ（`review: chat`）の止め方と締め

範囲の上限（設計 wip/design/approve-carry.md §3・§4）は ScopeLimitTest が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import ROOT, RULES, git, read_json, write

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
  chores:
    kind: work
    title: 片付け
    review: chat
    scope: ["src/*"]
  chores-feedback:
    kind: feedback
    title: 片付けフィードバック対応
    review: chat
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
        lines += ["  - match: Write|Edit", f'    glob: "{g}"']
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
        lines += ["  - match: Write|Edit", f'    glob: "{g}"']
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
        self.state = os.path.join(self.root, "state")
        self.parent_tree = self.worktree("i0001", "main")
        # 写しとマーカーは親のツリーに置かれ、親のブランチに乗る（設計 §9.2）。
        self.approved = os.path.join(self.parent_tree, ".ccnavi", "tickets")

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
                ".ccnavi/tickets",
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
                "--guard-ticket-approval",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(self, event, tool, cwd, mode="enable", agent_id="", **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        return self.ccnavi("--mode", mode, stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, text):
        return write(os.path.join(self.parent_tree, "wip", "tickets", "todo", name + ".md"), text)

    def approve(self):
        """承認して、写しを親のブランチに乗せる。

        写しは親のツリーに置かれるので、コミットするまでワークツリーは汚れたまま。
        本番で `ccnavi-approve.sh` がやることを、テストでも同じ順で踏む。
        """
        result = self.ccnavi("--approve", stdin="y\n")
        if os.path.isdir(self.approved):
            git(self.parent_tree, "add", "-A")
            git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return result

    def commit_parent(self, message="tickets"):
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", message)

    def run_child(self, name, files=()):
        """子のワークツリーを作って着手し、ファイルを置いてコミットし、閉じる。"""
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

    def board_phase(self, number, parent="i0001"):
        """ボードの JSON のフェーズ 1 つを (gate_closed, review_waiting, マーカーの種類) で。"""
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        board = json.loads(result.stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == parent)
        ph = next(p for p in owner["phases"] if p["number"] == number)
        return ph["gate_closed"], ph["review_waiting"], sorted(ph["marks"])

    def family(self, plan=("research", "design"), feedback=None):
        """親を提案して承認する。plan の 1 番目の子もまとめて承認する。"""
        self.propose("i0001", parent_text("i0001", list(plan), feedback))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result


class ApproveOnlyTest(PhaseHarness):
    """`--approve <識別子>...` で承認の対象を絞っても、絞らないときに落ちるものは通らない。"""

    def test_child_cannot_be_approved_without_the_parents_pending_revision(self):
        self.family(plan=("acceptance", "implement"))
        # 親を改版してフェーズ 1 を設計にする。子はまだ旧計画（受入テスト作成）の範囲で出す
        self.propose("i0001", parent_text("i0001", ["design", "acceptance", "implement"]))
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ("tests/x*",)))
        self.commit_parent()
        # 絞らないときは、改版後の計画で検証される。種類の超過は承認を拒まず、承認画面に
        # 「判定で止まるもの」として出る（設計 approve-carry §3.2）。n で何も適用しない
        whole = self.ccnavi("--approve", stdin="n\n")
        self.assertIn("判定で止まるもの", whole.stdout)
        self.assertIn("超えている", whole.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        # 改版を外して子だけ並べても、旧計画で通してはいけない
        only = self.ccnavi("--approve", "i0001-02", stdin="y\n")
        self.assertEqual(only.returncode, 1, only.stdout + only.stderr)
        self.assertIn("改版", only.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))

    def test_child_of_a_rejected_parent_is_not_approved(self):
        # 親が落ちたら（置き場に無いプロジェクト）、その子も親が承認されていないので落ちる。
        # 子自身は正しいので、落ちた親を池に残すと子だけ承認済みチケットになる
        parent = parent_text("i0001", ["design"]).replace("plan:", "project: nope\nplan:", 1)
        self.propose("i0001", parent)
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/design/*",)))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("i0001 は承認の対象にしない", result.stderr)
        self.assertIn("親 i0001 が承認されていない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01.md")))


class PhaseTest(PhaseHarness):
    # ---- 1. 種類の定義

    def test_phase_types_must_be_unique_and_well_formed(self):
        from ccnavi import phasetypes

        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: 同じ, kind: work}\n  b: {title: 同じ, kind: work}\n"
        )
        self.assertTrue(any("表示名" in p.detail for p in problems), problems)
        # フィードバック対応は人が見ない道を作らない。見る場所は chat でも mr でもよい。
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  fb: {title: 対応, kind: feedback, review: none}\n"
        )
        self.assertTrue(any("review: none" in p.detail for p in problems), problems)
        types, problems = phasetypes.parse(
            "version: 1\nphases:\n  fb: {title: 対応, kind: feedback, review: chat}\n"
        )
        self.assertEqual(problems, [])
        self.assertEqual(types["fb"].review, phasetypes.REVIEW_CHAT)
        _, problems = phasetypes.parse(
            "version: 1\nphases:\n  a: {title: A, kind: work, overlap: [nope]}\n"
        )
        self.assertTrue(any("nope" in p.detail for p in problems), problems)
        types, problems = phasetypes.parse(PHASES)
        self.assertEqual(problems, [])
        self.assertEqual(
            set(types),
            {
                "research",
                "design",
                "acceptance",
                "implement",
                "implement-feedback",
                "chores",
                "chores-feedback",
            },
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
        """種類の範囲の超過は承認を拒まず、承認画面で見せる。計画に無い番号は今までどおり拒む。

        超過は判定が切り詰めるので、承認で止める理由が無い（設計 approve-carry §3.1）。
        チケットの形の誤り（計画に無い番号）は判定で補えないので、承認で止める。
        """
        self.family(plan=["research", "design"])
        # 種類の範囲を超える子。承認でき、承認済みチケットが置かれる。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["src/a/*"]))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertIn("判定で止まるもの", approved.stdout)
        self.assertIn("超えている", approved.stdout)
        self.assertIn("調査", approved.stdout)
        self.assertNotIn("i0001-01 は承認の対象にしない", approved.stderr)
        # 計画に無い番号。
        os.remove(os.path.join(self.parent_tree, "wip", "tickets", "todo", "i0001-01.md"))
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ["wip/research/*"]))
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("計画に無い", refused.stderr)

    def test_phase_scope_ignores_letter_case_on_every_machine(self):
        """種類の範囲の上限は、子チケットの範囲と同じく大文字小文字を区別しない。

        機械ごとに変えると、同じ提案が Linux では「種類の上限を超えている」で
        承認を拒まれ、Windows では通る。範囲は人が宣言する意図なので、綴りの
        意味で読む（子 ⊆ 種類 ⊆ 親 の 3 つを 1 つの規則で揃える）。
        """
        self.family(plan=["research", "design"])
        # 種類は `wip/research/*`。子は綴りだけ違う `WIP/Research/*` を宣言する。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["WIP/Research/*"]))
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertNotIn("超えている", result.stderr)

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
        # レビュー不要の種類なので、マーカーは skipped。2 番目が承認される。
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

    def test_a_batch_does_not_pass_a_later_child_over_one_that_reopens_an_earlier_phase(self):
        """前のフェーズに足す子と次のフェーズの子を一緒に承認しても、次の子は通さない（issue #31）。

        1 本ずつ承認すれば、前の子の承認でフェーズが開き直り、次の子は落ちる。まとめて承認しても
        同じ答えにする。
        識別子の順（-02 が -03 より先）で検査すると、開き直す前の状態で次の子が通っていた。
        """
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/research/summary.md", "まとめ\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        # フェーズ 1 は閉じてレビュー不要。次のフェーズの子と、フェーズ 1 に足す子を一緒に出す。
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        self.propose(
            "i0001-03", child_text("i0001-03", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("propose 02 03")
        result = self.approve()
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-03.md")), result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        self.assertIn("同じ承認で i0001-03 を足すので開き直る", result.stderr)

    def test_a_new_parent_and_a_later_phase_child_together_still_keep_the_order(self):
        """承認済みチケットの無い親と、2 番目のフェーズの子を一緒に承認しても、子は通さない。

        1 本ずつなら、親を承認したあと子は「1 が閉じるまで」で落ちる。一緒に出すと親の計画が
        ディスクに無く、フェーズが 1 つも並ばないので、順序の検査が何も見ずに通っていた。
        """
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001.md")), result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertIn("1（調査） が閉じるまで承認しない（子がまだ無い）", result.stderr)

    def test_a_new_parent_with_children_in_two_phases_passes_only_the_first(self):
        """親・1 番目の子・2 番目の子を一緒に承認すると、1 番目の子までが通り、2 番目は落ちる。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose(
            "i0001-01", child_text("i0001-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["wip/design/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001.md")), result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")), result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "i0001-02.md")))
        self.assertIn("同じ承認で i0001-01 を足すが、まだ閉じていない", result.stderr)

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
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(spawn))
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
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
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
        # ボードの JSON は「依頼済みで止まったまま」を review_waiting で言う。レビューが
        # 済んで止まらなくなれば false に戻り、依頼のマーカーは残る。
        self.assertEqual(self.board_phase(1), (True, True, ["requested"]))
        self.assertEqual(self.check(fixture, 1).returncode, 0)
        self.assertEqual(self.board_phase(1), (False, False, ["requested", "reviewed"]))
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
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
        # フィードバック計画: 実装フィードバック対応を 1 本。承認がレビューの合意になり、
        # 止まっていたのが解ける。指摘は消えず、フィードバック作業フェーズの check が数える。
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["design"], feedback=["implement-feedback"]),
        )
        planned = self.approve()
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertIn("済んだ扱い", planned.stdout)
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(spawn))
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
        # 締めた記録は運び方によらず置く（REQ-TKT-47）。
        record = read_json(os.path.join(self.approved, "phases", "i0001", "closed.json"))
        self.assertEqual(record["reviews"], {"1": "mr"})
        self.commit_parent("状態の移動")
        git(self.parent_tree, "rm", "-r", "-q", "wip")
        git(self.parent_tree, "commit", "--quiet", "-m", "chore: wip を片付ける")
        # push していなければまだ外せない。
        refused = self.ready(fixture)
        self.assertIn("push されていない", refused.stderr)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # 片付いて push 済み。ready が通り、マーカーと note の下書きができる。
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
            "--guard-ticket-approval",
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
            "sh .ccnavi/scripts/ccnavi-review.sh wrapup --reason x",
            "sh .ccnavi/scripts/ccnavi-review.sh ready",
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
        from tests.ticket.test_ticket import ticket_text

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


def scoped_child_text(name, parent, phase, allow=(), ask=(), regex=()):
    """`child_text` に ask と regex の項を足せる版。regex は allow の項として書く。"""
    lines = [
        "---",
        "version: 1",
        f"ticket: {name}",
        f"parent: {parent}",
        f"phase: {phase}",
        "human_review:",
        "  required: true",
        "  reason: t",
        f"title: 子 {name}",
        "rationale: r",
    ]
    if allow or regex:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
        for r in regex:
            lines += ["  - match: Write|Edit", f"    regex: '{r}'"]
    if ask:
        lines.append("ask:")
        for g in ask:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


# 種類 research を消した phases.yml。親の計画の 1 番目の種類が引けなくなる。
PHASES_WITHOUT_RESEARCH = PHASES.split("  research:", 1)[0] + (
    "  design:" + PHASES.split("  design:", 1)[1]
)

# scope を inherit にした作業の種類だけを持つ phases.yml。
PHASES_INHERIT = """
version: 1
phases:
  open:
    kind: work
    title: 自由
    review: none
    scope: inherit
"""


class ChatReviewTest(PhaseHarness):
    """このセッションで見るフェーズ（REQ-TKT-45〜47、設計 §9.8、ADR-0051）。"""

    def chat_phase(self, plan=("chores", "design")):
        """`review: chat` のフェーズを 1 つ終わらせて、告知の文を返す。"""
        self.family(plan=list(plan))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("src/a1.py", "x\n")])
        closed = self.close_child("i0001-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        return self.reason(self.hook("PostToolUse", "Bash", self.parent_tree, command="ls"))

    def test_chat_phase_closes_the_gate_and_the_human_opens_it_from_the_terminal(self):
        said = self.chat_phase()
        # 告知は push も request も言わない。言うのは「見てもらって待て」と開け方。
        self.assertIn("差分を見てもらって", said)
        self.assertIn("--reviewed 1 --chat", said)
        self.assertNotIn("request --phase", said)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.pending")))
        # レビューが要るフェーズなので、mr のときと同じに止まる。
        spawn = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))
        self.assertIn("--reviewed 1 --chat", self.reason(spawn))
        # n と答えればマーカーは置かれない。
        refused = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="n\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("i0001-01", refused.stdout)
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )
        # y でマーカーが置かれ、止まっていたのが解ける。写しも依頼の記録も要らない。
        passed = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        mark = read_json(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        self.assertEqual(mark["by"], "chat")
        self.assertEqual(mark["tickets"], ["i0001-01"])
        opened = self.hook("PreToolUse", "Agent", self.parent_tree, description="次の子")
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(opened))
        # 2 度打っても通る（同じことを言うだけ）。
        again = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("すでにレビュー済み", again.stdout)

    def test_chat_does_not_open_a_phase_that_is_declared_for_a_merge_request(self):
        """緩める側だけ止める。`mr` の宣言を安い経路で通させない。"""
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        refused = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("マージリクエスト", refused.stderr)
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )
        # 逆向きは通る。`chat` の宣言をマージリクエストに出すのは勧める向き。
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)

    def test_chat_does_not_open_a_phase_that_was_already_requested(self):
        """依頼を出したあとは、付いた指摘を数えずに開けない。"""
        self.chat_phase(plan=["chores"])
        fixture = self.remote()
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        refused = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("依頼済み", refused.stderr)
        self.assertIn("check --phase 1", refused.stderr)
        self.assertEqual(self.check(fixture, 1).returncode, 0)

    def test_a_chat_phase_that_covers_a_deferred_merge_request_phase_is_seen_in_the_merge_request(
        self,
    ):
        """厳しい側が勝つ。延期を引き受けた側が、引き受けた分より緩い場所で見ない。"""
        self.family(plan=[("design", "defer"), "chores"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        # 延期したフェーズでは止まらない。
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02", [("src/a1.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02")
        said = self.reason(self.hook("PostToolUse", "Bash", self.parent_tree, command="ls"))
        self.assertIn("request --phase 2", said)
        refused = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "2", "--chat", stdin="y\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("マージリクエスト", refused.stderr)

    def test_the_approval_screen_says_where_each_phase_is_seen(self):
        """承認の時点で、どのフェーズをどこで見るかが人に見える。"""
        result = self.family(plan=["chores", "design"])
        self.assertIn("レビュー要（このセッションで）", result.stdout)
        self.assertIn("レビュー要（マージリクエスト）", result.stdout)

    def test_chat_and_accept_unresolved_together_are_refused(self):
        """chat に未解決スレッドは無い。取り違えを黙って通さない。"""
        self.chat_phase(plan=["chores"])
        refused = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--reviewed",
            "1",
            "--chat",
            "--accept-unresolved",
            stdin="y\n",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--accept-unresolved", refused.stderr)
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )

    def test_a_covered_phase_falls_back_to_a_merge_request_when_the_type_is_unreadable(self):
        """覆っている分の種類が読めなくなっても、延期したレビューは消えない。"""
        self.family(plan=[("design", "defer"), "chores"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02", [("src/a1.py", "x\n")])
        self.assertEqual(self.close_child("i0001-02").returncode, 0)
        self.commit_parent("close 02")
        self.merge("i0001-02")
        # 承認のあとで種類が読めなくなる（人が phases.yml を触っている最中、層の切り替え）。
        thin = write(os.path.join(self.root, "phases-thin.yml"), "version: 1\nphases: {}\n")
        refused = self.ccnavi(
            "--cwd", self.parent_tree, "--reviewed", "2", "--chat", stdin="y\n", phases=thin
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("マージリクエスト", refused.stderr)
        # 止まったままになる。読めないことでレビューが消える道は作らない。
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"description": "次の子"},
        }
        spawn = self.ccnavi("--mode", "enable", stdin=json.dumps(payload), phases=thin)
        self.assertIn("DENY_PHASE_REVIEW", self.reason(spawn))

    def test_a_feedback_phase_can_be_seen_in_the_session_too(self):
        """フィードバック対応も chat で回せる。人が見ない道にはなっていない。"""
        self.chat_phase(plan=["chores"])
        self.assertEqual(
            self.ccnavi(
                "--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n"
            ).returncode,
            0,
        )
        # フィードバック計画を承認して、2 番目（フィードバック対応）を回す。
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["chores"], feedback=["chores-feedback"]),
        )
        self.assertEqual(self.approve().returncode, 0)
        # 親を着手にすると、次の hook が承認済みチケットへ started_at を写す。写した跡を
        # 残したまま先へ進むと、実行後の監視がそれを報告して告知が読めなくなる。
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")
        self.commit_parent("フィードバック計画")
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["src/b*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-02", [("src/b1.py", "y\n")])
        self.assertEqual(self.close_child("i0001-02").returncode, 0)
        # 承認済みチケットの更新まで入れて commit する。残すと実行後の監視がそちらを報告し、
        # フェーズの告知が読めない（実行後の監視は stderr、告知は stdout の JSON）。
        self.commit_parent("close 02")
        self.merge("i0001-02")
        said = self.reason(self.hook("PostToolUse", "Bash", self.parent_tree, command="ls"))
        self.assertIn("--reviewed 2 --chat", said)
        passed = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "2", "--chat", stdin="y\n")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        mark = read_json(os.path.join(self.approved, "phases", "i0001", "2.reviewed"))
        self.assertEqual(mark["by"], "chat")

    def test_closing_a_chat_only_parent_records_where_each_phase_was_seen(self):
        """締めた事実は親のブランチに残る。案内は Draft ではなく統合先へ戻すところまで。"""
        self.chat_phase(plan=["chores"])
        passed = self.ccnavi("--cwd", self.parent_tree, "--reviewed", "1", "--chat", stdin="y\n")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        # フィードバック計画（対応が無くても空で）を承認してから親を閉じる。
        self.assertEqual(self.ccnavi("ticket", "start", "i0001").returncode, 0)
        write(
            os.path.join(self.parent_tree, "wip", "tickets", "doing", "i0001.md"),
            parent_text("i0001", ["chores"], feedback=[]),
        )
        self.assertEqual(self.approve().returncode, 0)
        closed = self.ccnavi("ticket", "done", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("統合先", closed.stdout)
        self.assertNotIn("ready", closed.stdout)
        self.assertNotIn("squash", closed.stdout)
        record = read_json(os.path.join(self.approved, "phases", "i0001", "closed.json"))
        self.assertEqual(record["reviews"], {"1": "chat"})


class ScopeLimitTest(PhaseHarness):
    """範囲の上限（設計 wip/design/approve-carry.md §3・§4、§6.1〜§6.2）。

    承認は範囲の超過を拒まず「判定で止まるもの」として見せる。判定は子 → 親 → 種類の
    厳しい側で切り詰め、外へ出した上限を `limit:` 行で名指しする。
    親の範囲は既定で `src/*`, `wip/*`, `tests/*`、フェーズ 1 の種類 research は `wip/research/*`。
    """

    def approved_child(self, text, name="i0001-01", plan=("research", "design")):
        """親を承認し、子を提案して承認し、子のワークツリーを作って着手する。ワークツリーを返す。"""
        self.family(plan=plan)
        self.propose(name, text)
        self.commit_parent("propose child")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, name + ".md")))
        self.last_approval = approved
        return self.run_child(name)

    def write_to(self, tree, rel, mode="enable"):
        return self.hook(
            "PreToolUse",
            "Write",
            tree,
            mode=mode,
            file_path=os.path.join(tree, *rel.split("/")),
            content="x\n",
        )

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", "")

    # ---- 6.1 承認

    def test_regex_child_is_approved_and_shown_as_stopped_by_the_judge(self):
        """4. regex の子は承認でき、「判定で止まるもの」に出る。"""
        self.family(plan=["research", "design"])
        self.propose(
            "i0001-01",
            scoped_child_text("i0001-01", "i0001", 1, regex=("^(wip/research|src/a|docs)/",)),
        )
        self.commit_parent("propose child")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "i0001-01.md")))
        self.assertIn("判定で止まるもの", approved.stdout)
        self.assertIn("regex", approved.stdout.split("判定で止まるもの", 1)[1])
        self.assertNotIn("承認の対象にしない", approved.stderr)

    # ---- 6.2 判定

    def test_write_beyond_the_phase_type_is_denied_and_names_the_type(self):
        """7. enable: 種類の上限の外で子の範囲の中への Write は DENY_TICKET_SCOPE。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        inside = self.write_to(tree, "wip/research/note.md")
        self.assertNotEqual(self.decision(inside), "deny", self.reason(inside))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        denied = self.write_to(tree, "src/a/x.py")
        self.assertEqual(denied.returncode, 0, denied.stderr)
        self.assertEqual(self.decision(denied), "deny", denied.stdout)
        reason = self.reason(denied)
        self.assertIn("DENY_TICKET_SCOPE", reason)
        self.assertIn("limit: phase type 調査 (research): wip/research/*", reason)

    def test_dry_run_lets_the_write_through_and_says_which_limit(self):
        """8. dry-run: 同じ Write は通り、enable なら止めたことと `limit: phase type` が出る。

        dry-run の文面は今の judge.decide_before のもの（設計 §4.4「新しい処理は足さない」）。
        """
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        result = self.write_to(tree, "src/a/x.py", mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        text = self.reason(result)
        self.assertIn("[ccnavi dry-run] enable would have", text)
        self.assertIn("DENY_TICKET_SCOPE", text)
        self.assertIn("limit: phase type", text)

    def test_ask_inside_the_phase_type_stays_ask(self):
        """9. 種類の上限の中で子が `ask` と書いた場所は TICKET_ASK のまま。外なら止まる。"""
        tree = self.approved_child(
            scoped_child_text(
                "i0001-01",
                "i0001",
                1,
                allow=("wip/research/a/*",),
                ask=("wip/research/b/*", "src/b/*"),
            )
        )
        asked = self.write_to(tree, "wip/research/b/x.md")
        self.assertEqual(self.decision(asked), "ask", asked.stdout)
        self.assertIn("TICKET_ASK", self.reason(asked))
        # 種類は allow か外しか言わない。ask と書いた場所でも種類の外なら外が勝つ。
        beyond = self.write_to(tree, "src/b/x.py")
        self.assertEqual(self.decision(beyond), "deny", beyond.stdout)
        self.assertIn("limit: phase type", self.reason(beyond))

    def test_inherit_scope_does_not_cut_by_the_type(self):
        """10. 種類の scope が inherit なら、種類では切り詰めない。"""
        write(self.phases, PHASES_INHERIT)
        tree = self.approved_child(child_text("i0001-01", "i0001", 1, ["src/a/*"]), plan=["open"])
        result = self.write_to(tree, "src/a/x.py")
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))
        self.assertNotIn("limit:", self.reason(result))

    def test_no_phases_file_anywhere_does_not_cut_by_type(self):
        """11. どの層にも phases.yml が無ければ種類では切り詰めない（番号だけの挙動）。"""
        from tests.ticket.test_ticket import ticket_text

        self.phases = os.path.join(self.root, "no-phases.yml")
        self.propose("i0001", ticket_text("i0001", allow=("src/*", "wip/*")))
        self.propose(
            "i0001-01", ticket_text("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        )
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.run_child("i0001-01")
        inside = self.write_to(tree, "src/a/x.py")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("読めない", self.reason(inside))
        # 子自身の範囲の外は今までどおり止まり、`limit:` 行は足さない。
        outside = self.write_to(tree, "src/b/x.py")
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))
        self.assertNotIn("limit:", self.reason(outside))

    def test_unreadable_type_does_not_cut_by_type_but_says_so(self):
        """12. 親が計画を持ち番号の種類が読めないとき、種類では切り詰めず notice。親では止まる。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*", "docs/*"])
        )
        for label, text in (
            ("種類を消した", PHASES_WITHOUT_RESEARCH),
            ("phases.yml が壊れた", "version: 1\nphases: [\n"),
        ):
            with self.subTest(label):
                write(self.phases, text)
                inside = self.write_to(tree, "src/a/x.py")
                self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
                self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))
                self.assertIn("research", self.reason(inside))
                self.assertIn("種類の上限では切り詰めていない", self.reason(inside))
                beyond_parent = self.write_to(tree, "docs/x.md")
                self.assertEqual(self.decision(beyond_parent), "deny", beyond_parent.stdout)
                self.assertIn("DENY_TICKET_SCOPE", self.reason(beyond_parent))
                self.assertIn("limit: parent i0001", self.reason(beyond_parent))

    # ---- 判定が落ちない（チケット approve-carry-04 の 6〜8）

    def assert_answered(self, result):
        """hook が例外で終わらず、判定の答えとして読める形で返したこと。"""
        self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        if result.stdout.strip():
            json.loads(result.stdout)

    def break_phases_encoding(self):
        """共通層の phases.yml に、UTF-8 として読めないバイト列を混ぜる。"""
        with open(self.phases, "wb") as f:
            f.write(PHASES.encode("utf-8").replace("調査".encode(), b"\xff\xfe\x80"))

    def test_undecodable_phases_file_does_not_crash_the_write_judge(self):
        """6. phases.yml が UTF-8 として読めなくても、Write の判定は例外で終わらない。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.break_phases_encoding()
        result = self.write_to(tree, "src/a/x.py")
        self.assert_answered(result)
        # 読めない種類は「壊れている」と同じ扱い。種類では切り詰めず、そう言う。
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))
        self.assertIn("種類の上限では切り詰めていない", self.reason(result))

    def test_undecodable_phases_file_does_not_crash_the_bash_judge(self):
        """7. 同じ状態で、Bash の実行前の判定（止めるかどうかの経路）も例外で終わらない。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.break_phases_encoding()
        for command in (
            "ls",
            "echo x > src/a/x.py",
            "sh ../../../.ccnavi/scripts/ccnavi-ticket.sh done i0001-01",
        ):
            with self.subTest(command):
                self.assert_answered(self.hook("PreToolUse", "Bash", tree, command=command))

    def test_no_phases_file_with_a_plan_says_nothing_about_the_type(self):
        """8. 親が計画を持ち番号が計画にあっても、phases.yml がどの層にも無ければ注記しない。

        「種類が読めない」は phases.yml が在って読めないときだけ。無いのは番号だけの挙動
        （設計 §4.2 の表の 1 行目）で、注記を出すと毎回の Write に余計な 1 行が載る。
        """
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.phases = os.path.join(self.root, "no-phases.yml")
        self.assertFalse(os.path.exists(self.phases))
        for rel in ("wip/research/note.md", "src/a/x.py"):
            with self.subTest(rel):
                result = self.write_to(tree, rel)
                self.assert_answered(result)
                self.assertNotEqual(self.decision(result), "deny", result.stdout)
                self.assertNotIn("切り詰めていない", self.reason(result))
                self.assertNotIn("読めない", self.reason(result))
                self.assertNotIn("limit:", self.reason(result))

    def test_regex_child_is_judged_by_the_real_path(self):
        """14. regex の子: 実際のパスで親と種類に当てる。"""
        tree = self.approved_child(
            scoped_child_text("i0001-01", "i0001", 1, regex=("^(wip/research|src/a|docs)/",))
        )
        inside = self.write_to(tree, "wip/research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        beyond_type = self.write_to(tree, "src/a/x.py")
        self.assertEqual(self.decision(beyond_type), "deny", beyond_type.stdout)
        self.assertIn("limit: phase type 調査 (research)", self.reason(beyond_type))

        beyond_parent = self.write_to(tree, "docs/x.md")
        self.assertEqual(self.decision(beyond_parent), "deny", beyond_parent.stdout)
        self.assertIn("limit: parent i0001", self.reason(beyond_parent))

    def test_regex_child_ignores_case_like_the_glob_child(self):
        """14. regex の子: 範囲の綴りは glob の子と同じく大文字小文字を区別しない。

        範囲は人が宣言する意図なので、`regex` で書いても同じ場所を指す（設計 §9.3）。
        区別が要るなら `(?-i:...)` で囲む。
        """
        tree = self.approved_child(
            scoped_child_text("i0001-01", "i0001", 1, regex=("^wip/research/",))
        )
        inside = self.write_to(tree, "wip/Research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

    def test_regex_child_can_keep_the_distinction_with_an_inline_flag(self):
        """14. regex の子: `(?-i:...)` で囲んだ範囲は書いた綴りのとおりに当たる。

        ルールの側（tests/config/test_config_union_holes.py）と同じ逃げ道が、
        チケットの範囲でも書けることを見る。
        """
        tree = self.approved_child(
            scoped_child_text("i0001-01", "i0001", 1, regex=("^wip/(?-i:research)/",))
        )
        inside = self.write_to(tree, "wip/research/x.md")
        self.assertNotEqual(self.decision(inside), "deny", inside.stdout)
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(inside))

        # 種類の上限（glob）には当たる綴りだが、子の範囲が区別するので止まる。
        outside = self.write_to(tree, "wip/Research/x.md")
        self.assertEqual(self.decision(outside), "deny", outside.stdout)
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    def test_post_monitoring_reports_a_shell_write_beyond_the_type(self):
        """15. 実行後の監視: Bash が種類の上限の外に書くと POST_TICKET_SCOPE。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        self.hook("UserPromptSubmit", "", tree)
        first = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        # 種類の中への書き込みは言わない。
        write(os.path.join(tree, "wip", "research", "gen.md"), "ok\n")
        quiet = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(quiet.returncode, 0, quiet.stdout + quiet.stderr)
        self.assertNotIn("POST_TICKET_SCOPE", quiet.stdout + quiet.stderr)
        # 子の範囲の中だが種類の外。
        write(os.path.join(tree, "src", "a", "gen.py"), "x\n")
        after = self.hook("PostToolUse", "Bash", tree, command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_TICKET_SCOPE", after.stderr)
        self.assertIn("src/a/gen.py", after.stderr.replace("\\", "/"))
        self.assertIn("調査", after.stderr)

    def test_subagent_stop_bounces_a_change_beyond_the_type_and_names_it(self):
        """16. SubagentStop: 種類の上限の外の変更で差し戻し、上限の名指しが付く。"""
        tree = self.approved_child(
            child_text("i0001-01", "i0001", 1, ["wip/research/*", "src/a/*"])
        )
        write(os.path.join(tree, "wip", "research", "ok.md"), "ok\n")
        write(os.path.join(tree, "src", "a", "stray.py"), "x\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "stray")
        result = self.hook("SubagentStop", "", tree, agent_id="sub-1")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POST_TICKET_SCOPE", result.stderr)
        self.assertIn("src/a/stray.py", result.stderr)
        self.assertIn("（種類 調査 の上限の外）", result.stderr)
        self.assertNotIn("wip/research/ok.md", result.stderr)


if __name__ == "__main__":
    unittest.main()
